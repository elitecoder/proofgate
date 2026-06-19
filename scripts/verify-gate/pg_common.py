"""Shared helpers for the proofgate verify-gate hooks. Stdlib only."""
import hashlib
import json
import os
import re


def data_dir():
    d = os.environ.get("CLAUDE_PLUGIN_DATA")
    if d:
        return d
    return fallback_data_dir()


def fallback_data_dir():
    # bin/prove runs inside the agent's Bash tool where CLAUDE_PLUGIN_DATA
    # is usually unset, so both sides must agree on this fallback.
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "proofgate")


def sanitize_id(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s or "unknown"))[:80]


def cwd_key(cwd):
    return hashlib.sha256(cwd.encode("utf-8", "replace")).hexdigest()[:16]


# --- command head parsing -------------------------------------------------
# Substring matching false-positives on greps/heredocs ABOUT commands, so
# every classifier works on the head tokens of each pipeline segment.

_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)(\w+)\1(?!<)")


def _strip_heredocs(cmd):
    if "<<" not in cmd:
        return cmd
    lines = cmd.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC_RE.search(line)
        i += 1
        if m:
            delim = m.group(2)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1
    return "\n".join(out)


def segments(cmd):
    """Split a shell command into pipeline/list segments, quote-aware,
    with comments and heredoc bodies stripped."""
    cmd = _strip_heredocs(cmd or "")
    segs = []
    buf = []
    quote = None
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(cmd[i + 1])
            i += 2
            continue
        if c == "#" and (not buf or buf[-1] in " \t"):
            nl = cmd.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c in ";|&\n)":
            seg = "".join(buf).strip()
            if seg:
                segs.append(seg)
            buf = []
            i += 1
            continue
        if c in "({" and not "".join(buf).strip():
            i += 1
            continue
        buf.append(c)
        i += 1
    seg = "".join(buf).strip()
    if seg:
        segs.append(seg)
    return segs


_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {"sudo", "command", "builtin", "nohup", "time", "nice", "env", "exec"}


def parse_heads(cmd):
    """Yield (head, rest_tokens) for each segment of a shell command."""
    out = []
    for seg in segments(cmd):
        toks = seg.split()
        j = 0
        seen_wrapper = False
        while j < len(toks):
            t = toks[j]
            if _ASSIGN_RE.match(t):
                j += 1
                continue
            base = os.path.basename(t)
            if base in WRAPPERS:
                seen_wrapper = True
                j += 1
                continue
            if seen_wrapper and t.startswith("-"):
                j += 1
                continue
            break
        if j >= len(toks):
            continue
        out.append((os.path.basename(toks[j]), toks[j + 1:]))
    return out


SEND_HEADS = {"curl", "wget", "http", "https", "mail", "mailx", "sendmail",
              "mutt", "msmtp"}
TEST_HEADS = {"pytest", "py.test", "jest", "vitest", "mocha", "tox", "rspec",
              "phpunit", "ctest", "playwright", "behave", "nose2"}

# Runners that drive a real browser / full stack rather than a mockable unit
# harness. A "validated end-to-end" claim is satisfied by one of these (or a
# send-class command, or a prove-cov coverage receipt) — never by a unit
# runner alone. Heuristic allow-list, deliberately narrow; see failure mode 13.
E2E_HEADS = {"playwright", "cypress"}

_VALUE_FLAGS = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "gh": {"-R", "--repo"},
}


def _sub_tokens(head, rest, count=2):
    vf = _VALUE_FLAGS.get(head, set())
    subs = []
    skip = False
    for t in rest:
        if skip:
            skip = False
            continue
        if t in vf:
            skip = True
            continue
        if t.startswith("-"):
            continue
        subs.append(t)
        if len(subs) >= count:
            break
    return subs + [""] * (count - len(subs))


def _is_test_runner(head, sub, sub2, rest):
    if head in TEST_HEADS:
        return True
    if head in {"python", "python3", "python2"}:
        if "-m" in rest:
            k = rest.index("-m")
            return k + 1 < len(rest) and rest[k + 1] in {
                "pytest", "unittest", "nose2", "tox"}
        return False
    if head in {"npm", "pnpm", "yarn", "bun"}:
        if sub in {"test", "t"}:
            return True
        return sub == "run" and sub2.startswith("test")
    if head == "npx":
        return sub in {"jest", "vitest", "mocha", "playwright", "cypress",
                       "ava", "karma"}
    if head in {"go", "cargo", "swift", "dotnet", "rake", "mix", "sbt",
                "lein"}:
        return sub == "test" or (head == "cargo" and sub == "nextest")
    if head in {"make", "just"}:
        return sub in {"test", "tests", "check"}
    if head in {"gradle", "gradlew"}:
        return sub in {"test", "check"}
    if head == "bundle":
        return sub == "exec" and sub2 in {"rspec", "rake", "minitest"}
    if head == "xcodebuild":
        return "test" in rest
    return False


# Sub-commands/flags that mean "don't actually run the suite" — a no-op token
# like `playwright --version`, `cypress info`, or `playwright test --list` must
# not count as a real run when classifying a command for the ledger digest.
_E2E_NOOP = {"--version", "-v", "version", "--help", "-h", "help", "info",
             "install", "--list", "list", "codegen", "open", "--init", "init",
             "--dry-run", "show-report", "merge-reports"}


def _is_e2e_runner(head, sub, sub2, rest):
    """True for runners that drive a real browser / full stack. A strict
    subset of test runners — these cannot be satisfied by mocking out the
    subprocess/network boundary the way a pure unit runner can. Any
    informational token anywhere in the segment (--version, info, --list,
    --dry-run, ...) disqualifies it: that invocation did not run the suite."""
    if any(tok in _E2E_NOOP for tok in rest):
        return False
    if head in E2E_HEADS:
        return True
    if head == "npx":
        return sub in {"playwright", "cypress"}
    if head in {"npm", "pnpm", "yarn", "bun"}:
        # `pnpm e2e`, `npm run test:e2e`, `yarn run e2e:ci`, ...
        if sub in {"e2e", "e2e:ci"}:
            return True
        return sub == "run" and ("e2e" in sub2 or sub2.startswith("test:e2e"))
    return False


def classes_of(head, rest):
    cls = set()
    sub, sub2 = _sub_tokens(head, rest)
    if head == "git":
        if sub == "commit":
            cls.add("git_commit")
        elif sub == "push":
            cls.add("push")
    elif head == "gh":
        if sub == "pr" and sub2 in {"create", "merge"}:
            cls.add("push")
        elif sub == "pr" and sub2 in {"comment", "review"}:
            cls.add("send")
        elif sub == "issue" and sub2 in {"create", "comment"}:
            cls.add("send")
            if sub2 == "create":
                cls.add("deferral_artifact")
        elif sub == "api":
            cls.add("send")
    elif head in SEND_HEADS:
        cls.add("send")
    if _is_test_runner(head, sub, sub2, rest):
        cls.add("test_run")
    if _is_e2e_runner(head, sub, sub2, rest):
        cls.add("e2e_run")
    return cls


def bash_classes(cmd):
    cls = set()
    for head, rest in parse_heads(cmd):
        cls |= classes_of(head, rest)
    if "DEFERRALS" in (cmd or ""):
        cls.add("deferral_artifact")
    return cls


# --- test-file paths ------------------------------------------------------

TEST_PATH_RE = re.compile(
    r"(?:^|/)(?i:tests?|__tests__|specs?)(?:/|$)"
    r"|(?:^|/)test_[^/]*\.\w+$"
    r"|(?:^|/)[^/]*_test\.\w+$"
    r"|(?:^|/)[^/]*\.(?:test|spec)\.\w+$"
    r"|(?:^|/)[^/]*(?:Test|Tests|Spec|Specs)\.\w+$"
)


def is_test_path(path):
    return bool(TEST_PATH_RE.search(path or ""))


# --- session ledger -------------------------------------------------------

def ledger_path(dd, session_id):
    return os.path.join(dd, "ledger", sanitize_id(session_id) + ".jsonl")


def read_ledger(dd, session_id):
    out = []
    try:
        with open(ledger_path(dd, session_id), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except OSError:
        pass
    return out


def append_ledger(dd, session_id, entries):
    path = ledger_path(dd, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
