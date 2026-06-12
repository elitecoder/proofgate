#!/usr/bin/env python3
"""proofgate gatekeeper: data-driven PreToolUse gate.

Parses Bash commands into pipeline segments (comments and heredoc bodies
stripped), extracts each segment's command head, and matches heads against
TSV rules (plugin defaults + user overlay in $CLAUDE_PLUGIN_DATA).

Fail-open contract: any internal error exits 0 with no output, so a broken
gate can never wedge a session.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

TOKEN_TTL_SECS = 15 * 60
HEAD_CORE_TOKENS = 4
SUB_MARK = "__SUB__"  # placeholder for command/process substitutions

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# wrappers are skipped (with their flags) to reach the real command head
WRAPPERS = {"sudo", "doas", "env", "command", "nohup", "time", "exec",
            "xargs", "builtin", "stdbuf", "timeout"}
KEYWORDS = {"if", "then", "else", "elif", "fi", "while", "until", "do",
            "done", "case", "esac", "{", "}", "!", "(", ")", ";"}
ACTIONS = {"deny", "ask", "require-token"}
RULE_TOOLS = {"bash", "edit", "any"}
GUARD_NAMES = ("outside-cwd-tmp", "dirty-repo")

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# bare redirection operator: the next token is its target
_REDIR_BARE_RE = re.compile(r"^(\d*(>>?|<)|&>>?)$")
# self-contained redirection token, e.g. 2>&1, >/dev/null, &>log, <input
_REDIR_ATTACHED_RE = re.compile(r"^(\d*(>>?|<)|&>>?)\S+$")
_SHORT_C_FLAG_RE = re.compile(r"^-[A-Za-z]*c$")


# ---------------------------------------------------------------------------
# shell text scanning


def _scan_single_quote(text, i):
    j = text.find("'", i + 1)
    if j == -1:
        return len(text), text[i:]
    return j + 1, text[i:j + 1]


def _scan_backtick(text, i):
    n = len(text)
    j = i + 1
    buf = []
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n:
            buf.append(text[j + 1])
            j += 2
            continue
        if c == "`":
            return j + 1, "".join(buf)
        buf.append(c)
        j += 1
    return n, "".join(buf)


def _scan_paren(text, i):
    """text[i] == '('; return (index_after_close, inner_text), quote-aware."""
    n = len(text)
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            j, _ = _scan_single_quote(text, j)
            continue
        if c == '"':
            j += 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1, text[i + 1:j]
        j += 1
    return n, text[i + 1:n]


def _consume_heredocs(text, i, pending):
    n = len(text)
    for delim, strip_tabs in pending:
        while i < n:
            j = text.find("\n", i)
            line = text[i:(n if j == -1 else j)]
            i = n if j == -1 else j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
    return i


def split_segments(text, _depth=0):
    """Split shell text into command segments at ; & | && || newlines and
    subshell/substitution boundaries. Comments and heredoc bodies dropped.
    Contents of $(...), `...`, <(...) become segments of their own (they do
    execute); quoted text never spawns segments."""
    if not text or _depth > 6:
        return []
    segs = []
    cur = []
    pending = []  # heredocs queued on the current line: (delim, strip_tabs)
    n = len(text)
    i = 0

    def flush():
        s = "".join(cur).strip()
        del cur[:]
        if s:
            segs.append(s)

    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                cur.append(" ")  # line continuation
            else:
                cur.append(text[i:i + 2])
            i += 2
            continue
        if c == "'":
            i, chunk = _scan_single_quote(text, i)
            cur.append(chunk)
            continue
        if c == '"':
            j = i + 1
            buf = ['"']
            while j < n:
                d = text[j]
                if d == "\\":
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if d == '"':
                    buf.append('"')
                    j += 1
                    break
                if d == "`":
                    j, inner = _scan_backtick(text, j)
                    segs.extend(split_segments(inner, _depth + 1))
                    buf.append(SUB_MARK)
                    continue
                if d == "$" and j + 1 < n and text[j + 1] == "(":
                    j, inner = _scan_paren(text, j + 1)
                    segs.extend(split_segments(inner, _depth + 1))
                    buf.append(SUB_MARK)
                    continue
                buf.append(d)
                j += 1
            cur.append("".join(buf))
            i = j
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t\n;|&("):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "`":
            i, inner = _scan_backtick(text, i)
            segs.extend(split_segments(inner, _depth + 1))
            cur.append(SUB_MARK)
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            i, inner = _scan_paren(text, i + 1)
            segs.extend(split_segments(inner, _depth + 1))
            cur.append(SUB_MARK)
            continue
        if text[i:i + 2] in ("<(", ">("):
            i, inner = _scan_paren(text, i + 1)
            segs.extend(split_segments(inner, _depth + 1))
            cur.append(SUB_MARK)
            continue
        if text[i:i + 2] == "<<" and text[i:i + 3] != "<<<":
            i += 2
            strip_tabs = False
            if i < n and text[i] == "-":
                strip_tabs = True
                i += 1
            while i < n and text[i] in " \t":
                i += 1
            j = i
            while j < n and text[j] not in " \t\n;|&<>()":
                j += 1
            delim = re.sub(r"[\"'\\]", "", text[i:j])
            if delim:
                pending.append((delim, strip_tabs))
            i = j
            continue
        if text[i:i + 3] == "<<<":
            cur.append(" ")  # here-string word becomes a plain argument
            i += 3
            continue
        if c == "\n":
            flush()
            i += 1
            if pending:
                i = _consume_heredocs(text, i, pending)
                pending = []
            continue
        if c == ";":
            flush()
            i += 1
            continue
        if c == "&":
            if text[i:i + 2] == "&&":
                flush()
                i += 2
                continue
            if i > 0 and text[i - 1] in "<>":  # 2>&1 style fd dup
                cur.append(c)
                i += 1
                continue
            if text[i:i + 2] == "&>":  # &>file redirect, not background
                cur.append(c)
                i += 1
                continue
            flush()  # background &
            i += 1
            continue
        if c == "|":
            flush()
            i += 2 if text[i:i + 2] in ("||", "|&") else 1
            continue
        if c in "()":
            flush()
            i += 1
            continue
        cur.append(c)
        i += 1
    flush()
    return segs


# ---------------------------------------------------------------------------
# head extraction


def _strip_redirections(tokens):
    out = []
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if _REDIR_BARE_RE.match(t):
            skip_next = True
            continue
        if _REDIR_ATTACHED_RE.match(t):
            continue
        out.append(t)
    return out


def _strip_prefix(tokens):
    """Drop shell keywords, VAR=val assignments, and command wrappers
    (sudo/env/xargs/...) so the head is the command that actually acts."""
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t in KEYWORDS or t == SUB_MARK or _ASSIGN_RE.match(t):
            i += 1
            continue
        if os.path.basename(t) in WRAPPERS:
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 1
            continue
        break
    return tokens[i:]


def _shell_c_payload(tokens):
    take_next = False
    for t in tokens[1:]:
        if take_next:
            return t
        if t == "-c" or _SHORT_C_FLAG_RE.match(t):
            take_next = True
    return None


def extract_heads(command, _depth=0):
    """Return [(head, guard_args)] for every executable segment.

    head = first HEAD_CORE_TOKENS tokens plus any later -flags (so trailing
    options like 'git push origin main --force' are still visible).
    guard_args = non-flag arguments, used by path guards.
    """
    heads = []
    if _depth > 3:
        return heads
    for seg in split_segments(command):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue  # unparseable segment: fail open
        tokens = _strip_prefix(_strip_redirections(tokens))
        if not tokens:
            continue
        if "/" in tokens[0]:
            tokens = [os.path.basename(tokens[0])] + tokens[1:]
        if tokens[0] in SHELLS:
            inner = _shell_c_payload(tokens)
            if inner:
                heads.extend(extract_heads(inner, _depth + 1))
        head_tokens = tokens[:HEAD_CORE_TOKENS] + [
            t for t in tokens[HEAD_CORE_TOKENS:] if t.startswith("-")]
        args = []
        after_ddash = False
        for t in tokens[1:]:
            if not after_ddash:
                if t == "--":
                    after_ddash = True
                    continue
                if t.startswith("-"):
                    continue
            args.append(t)
        heads.append((" ".join(head_tokens), args))
    return heads


# ---------------------------------------------------------------------------
# guards


def _tmp_roots():
    roots = {"/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp"}
    try:
        t = tempfile.gettempdir()
        roots.add(os.path.normpath(t))
        roots.add(os.path.realpath(t))
    except Exception:
        pass
    for var in ("TMPDIR", "TMP", "TEMP"):
        v = os.environ.get(var)
        if v:
            roots.add(os.path.normpath(v))
    return roots


def _is_under(path, root):
    if not root:
        return False
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def guard_outside_cwd_tmp(args, cwd):
    """True if any resolvable path argument lands outside cwd and temp dirs."""
    roots = _tmp_roots()
    try:
        real_cwd = os.path.realpath(cwd) if cwd else None
    except Exception:
        real_cwd = None
    for a in args:
        if not a.strip() or SUB_MARK in a or a.startswith("$"):
            continue  # unresolvable at parse time: fail open
        p = os.path.expanduser(a)
        if not os.path.isabs(p):
            if not cwd:
                continue
            p = os.path.join(cwd, p)
        p = os.path.normpath(p)
        candidates = {p}
        try:
            candidates.add(os.path.realpath(p))
        except Exception:
            pass
        contained = False
        for cand in candidates:
            if _is_under(cand, cwd) or _is_under(cand, real_cwd):
                contained = True
                break
            if any(_is_under(cand, r) for r in roots):
                contained = True
                break
        if not contained:
            return True
    return False


def guard_dirty_repo(args, cwd):
    if not cwd:
        return False
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=5)
    except Exception:
        return False  # no git / timeout: fail open
    if out.returncode != 0:
        return False
    return bool(out.stdout.strip())


GUARD_FNS = {
    "outside-cwd-tmp": guard_outside_cwd_tmp,
    "dirty-repo": guard_dirty_repo,
}


# ---------------------------------------------------------------------------
# rules


class Rule(object):
    __slots__ = ("id", "tool", "scope", "guard", "rx", "action", "reason")

    def __init__(self, rid, tool, scope, guard, rx, action, reason):
        self.id = rid
        self.tool = tool
        self.scope = scope
        self.guard = guard
        self.rx = rx
        self.action = action
        self.reason = reason


def _parse_rule_line(line):
    parts = line.split("\t", 5)
    if len(parts) < 6:
        return None
    rid = parts[0].strip()
    tool = parts[1].strip().lower()
    scope = parts[2].strip()
    pattern = parts[3].strip()
    action = parts[4].strip().lower()
    reason = parts[5].strip()
    if not rid or not pattern or tool not in RULE_TOOLS or action not in ACTIONS:
        return None
    guard = None
    for g in GUARD_NAMES:
        if pattern.startswith(g + ":"):
            guard = g
            pattern = pattern[len(g) + 1:]
            break
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    return Rule(rid, tool, scope, guard, rx, action, reason)


def load_rules(default_path, overlay_path):
    """Defaults first, then overlay; an overlay row with the same id wins."""
    rules = {}
    for path in (default_path, overlay_path):
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                rule = _parse_rule_line(line)
            except Exception:
                rule = None
            if rule:
                rules[rule.id] = rule
    return list(rules.values())


# ---------------------------------------------------------------------------
# tokens (require-token action)


def token_path(data_dir, rid):
    return os.path.join(data_dir, "tokens", rid + ".token")


def consume_token(data_dir, rid):
    """True iff a fresh token exists; the token is removed either way it is
    used or stale (single use)."""
    path = token_path(data_dir, rid)
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return False
    if age > TOKEN_TTL_SECS or age < -60:
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# evaluation


def _decision(decision, reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}


def _fill(reason, head):
    return reason.replace("{head}", head)


def _scope_ok(rule, cwd):
    if rule.scope in ("", "*"):
        return True
    try:
        return re.search(rule.scope, cwd or "") is not None
    except re.error:
        return False


def _edit_text(tool_input):
    parts = [str(tool_input.get("content") or ""),
             str(tool_input.get("new_string") or "")]
    edits = tool_input.get("edits") or []
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                parts.append(str(e.get("new_string") or ""))
    return "\n".join(p for p in parts if p)


def evaluate(payload, rules, data_dir, plugin_root):
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd") or os.getcwd()

    matches = []  # (rule, matched_text)
    if tool_name in ("Bash", "bash"):
        command = tool_input.get("command") or ""
        if not command:
            return None
        heads = extract_heads(command)
        for rule in rules:
            if rule.tool not in ("bash", "any"):
                continue
            if not _scope_ok(rule, cwd):
                continue
            for head, args in heads:
                try:
                    if not rule.rx.match(head):
                        continue
                    if rule.guard and not GUARD_FNS[rule.guard](args, cwd):
                        continue
                except Exception:
                    continue  # guard/regex blew up: fail open for this head
                matches.append((rule, head))
                break
    elif tool_name in EDIT_TOOLS:
        file_path = str(tool_input.get("file_path") or "")
        content = _edit_text(tool_input)
        for rule in rules:
            if rule.tool not in ("edit", "any"):
                continue
            if not _scope_ok(rule, cwd):
                continue
            try:
                hit = rule.rx.search(file_path) or (
                    content and rule.rx.search(content))
            except Exception:
                hit = None
            if hit:
                matches.append((rule, file_path))
    else:
        return None

    if not matches:
        return None
    for rule, head in matches:
        if rule.action == "deny":
            return _decision("deny", _fill(rule.reason, head))
    for rule, head in matches:
        if rule.action == "require-token":
            if not consume_token(data_dir, rule.id):
                grant = os.path.join(plugin_root, "bin", "pg-grant")
                hint = (
                    " Denied: no valid grant token. A human must run "
                    "CLAUDE_PLUGIN_DATA=%s %s %s "
                    "from a real terminal, then retry within 15 minutes "
                    "(tokens are single-use)." % (
                        shlex.quote(data_dir), shlex.quote(grant), rule.id))
                return _decision("deny", _fill(rule.reason, head) + hint)
    for rule, head in matches:
        if rule.action == "ask":
            return _decision("ask", _fill(rule.reason, head))
    return None


# ---------------------------------------------------------------------------
# entry point


def _plugin_root():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return root
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _data_dir():
    # bin/pg-grant uses the same fallback; keep them in sync
    return os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(
        os.path.expanduser("~"), ".claude", "proofgate")


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        return
    root = _plugin_root()
    data_dir = _data_dir()
    rules = load_rules(
        os.path.join(root, "rules", "defaults.tsv"),
        os.path.join(data_dir, "rules.local.tsv"))
    result = evaluate(payload, rules, data_dir, root)
    if result:
        sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass  # fail open: a broken gate must never wedge the session
    sys.exit(0)
