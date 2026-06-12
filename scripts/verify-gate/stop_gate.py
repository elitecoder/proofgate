#!/usr/bin/env python3
"""proofgate Stop gate: deterministic claim/state cross-checks.

Only checkable claims are acted on; every tier maps a claim class to
mechanically verifiable evidence. Fails open on any error.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pg_common as pg

MAX_BLOCKS_PER_TURN = 2
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

DEFAULT_GATES = {
    "ship_state": True,
    "checkable_claim": True,
    "red_green": True,
    "promissory": True,
    "deferral": True,
    "llm_judge": False,
}

PUSH_CLAIM_RE = re.compile(r"\b(pushed|merged|shipped)\b", re.I)
SEND_CLAIM_RE = re.compile(r"\b(sent|delivered|posted)\b", re.I)
TESTS_CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:unit\s+|e2e\s+|integration\s+)?tests?\s+"
    r"(?:are\s+|all\s+|now\s+|still\s+)*(?:pass(?:ing|ed|es)?|green)\b"
    r"|\btest\s+suite\s+(?:passes|passed|is\s+green)\b"
    r"|\be2e\s+pass(?:ed|es|ing)?\b",
    re.I)

# A claim preceded by negation/futurity is not a claim.
NEG_TAIL_RE = re.compile(
    r"(?:\bnot|\bnever|n't|\bwithout|\bbefore|\buntil|\bunless|\bonce"
    r"|\bafter|\bto\s+be|\bwill\s+be|\bwould\s+be|\bcan\s+be|\bshould\s+be"
    r"|\bmust\s+be|\bneeds?\s+to\s+be|\byet\s+to\s+be|\bready\s+to"
    r"|\babout\s+to|\bgoing\s+to|\bstill\s+to)"
    r"\s*(?:\w+[\s,]+){0,2}$",
    re.I)

PROMISSORY_RE = re.compile(
    r"\b(?:I'll|I\s+will|Let\s+me|I'm\s+going\s+to|I\s+am\s+going\s+to|Now\s+I'll|Next,?\s+I'll)\s+"
    r"(?:now\s+|just\s+|go\s+ahead\s+and\s+)?"
    r"(?:check|run|verify|test|re-?run|retry|fix|update|push|commit|create"
    r"|add|implement|investigate|look|confirm|make|write|clean|finish"
    r"|continue|proceed|start|kick|follow|handle|take|do|dig|debug|apply"
    r"|merge|open|file|wire|hook|refactor)\w*\b",
    re.I)

WAITING_RE = re.compile(
    r"\blet\s+me\s+know\b|\byour\s+call\b|\bif\s+you\s+(?:want|prefer|like)\b"
    r"|\bwould\s+you\s+like\b|\bshall\s+i\b|\bwant\s+me\s+to\b"
    r"|\bwaiting\s+(?:on|for)\s+you",
    re.I)

DEFER_RE = re.compile(
    r"\bdeferred\b|\bdeferring\b|\bdefer\s+(?:it|this|that|the)\b"
    r"|\bas\s+a\s+follow-?up\b|\bfollow-?up\s+(?:task|item|issue|pr|fix|work)\b"
    r"|\bfile\s+(?:it|this|that|an\s+issue|a\s+ticket|a\s+bug)\s+later\b"
    r"|\b(?:fix|handle|address|do|tackle)\s+(?:it|this|that)\s+later\b"
    r"|\bleave\s+(?:it|this|that)\s+for\s+later\b|\bleft\s+for\s+later\b"
    r"|\bpunt(?:ed|ing)?\s+on\b",
    re.I)


def load_config(dd):
    cfg = {}
    try:
        with open(os.path.join(dd, "config.json"), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            cfg = loaded
    except (OSError, ValueError):
        pass
    gates = dict(DEFAULT_GATES)
    g = cfg.get("gates")
    if isinstance(g, dict):
        gates.update(g)
    return cfg, gates


def parse_transcript(path):
    """Return (final_assistant_text, tool_uses) from a transcript jsonl."""
    texts = []
    tools = []
    results = {}
    idx = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if obj.get("type") == "assistant":
                if isinstance(content, str):
                    if content.strip():
                        texts.append(content)
                    continue
                if not isinstance(content, list):
                    continue
                cur = []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        cur.append(b.get("text") or "")
                    elif b.get("type") == "tool_use":
                        tools.append({"id": b.get("id"),
                                      "name": b.get("name") or "",
                                      "input": b.get("input") or {},
                                      "idx": idx})
                        idx += 1
                if any(s.strip() for s in cur):
                    texts.append("\n".join(cur))
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        results[b.get("tool_use_id")] = not b.get("is_error",
                                                                  False)
    for t in tools:
        t["ok"] = results.get(t["id"], False) is True
    return (texts[-1] if texts else ""), tools


def claim_match(text, rx):
    for m in rx.finditer(text):
        prefix = text[max(0, m.start() - 40):m.start()]
        if NEG_TAIL_RE.search(prefix):
            continue
        return m
    return None


def _unpushed_count(cwd):
    """Commits ahead of upstream; None = not a repo / no upstream / no git."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-list", "--count", "@{u}..HEAD"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return int(r.stdout.strip() or "0")
    except Exception:
        return None


def _receipts_for_cwd(cwd, dd):
    """All bin/prove receipts recorded for this working directory."""
    dirs = []
    for d in (dd, pg.fallback_data_dir()):
        if d and d not in dirs:
            dirs.append(d)
    real = os.path.realpath(cwd)
    recs = []
    for d in dirs:
        rd = os.path.join(d, "ledger", "receipts")
        try:
            names = os.listdir(rd)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            try:
                with open(os.path.join(rd, name), encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if (isinstance(r, dict) and
                                os.path.realpath(str(r.get("cwd") or "")) == real):
                            recs.append(r)
            except OSError:
                continue
    return recs


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _prove_cmd():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return os.path.join(root, "bin", "prove") if root else "bin/prove"


def evaluate(final, tools, cwd, dd, sid, cfg, gates):
    text = final.replace("’", "'")
    ok_bash = []
    session_classes = set()
    for t in tools:
        if t["name"] == "Bash" and t["ok"]:
            cmd = str((t["input"] or {}).get("command") or "")
            t["classes"] = pg.bash_classes(cmd)
            session_classes |= t["classes"]
            ok_bash.append(t)

    edit_idxs = [t["idx"] for t in tools
                 if t["name"] in EDIT_TOOLS and t["ok"]]
    test_run_idxs = [t["idx"] for t in ok_bash if "test_run" in t["classes"]]

    cache = {}

    def unpushed():
        if "n" not in cache:
            cache["n"] = _unpushed_count(cwd)
        return cache["n"]

    def receipts():
        if "r" not in cache:
            cache["r"] = _receipts_for_cwd(cwd, dd)
        return cache["r"]

    ledger = pg.read_ledger(dd, sid)
    push_claim = claim_match(text, PUSH_CLAIM_RE)

    # a. SHIP-STATE: claimed shipped, commit ran, but commits are unpushed.
    if gates.get("ship_state") and push_claim and "git_commit" in session_classes:
        n = unpushed()
        if n:
            return ("Claim/state mismatch: the final message says "
                    "'%s', but %d commit(s) are not on the upstream. "
                    "Run: git push\nVerify: git log --oneline @{u}..\n"
                    "If intentionally local-only, restate prefixed with "
                    "UNVERIFIED:." % (push_claim.group(0), n))

    # b. CHECKABLE-CLAIM cross-reference.
    if gates.get("checkable_claim"):
        if push_claim and "push" not in session_classes:
            # Upstream state is the ground truth: 0 unpushed verifies the
            # claim even without a push command in this session.
            if unpushed() != 0:
                return ("Unverified claim '%s': no git push or gh pr "
                        "command ran in this session. Push now, then "
                        "verify with: git log --oneline @{u}..\n"
                        "Or restate prefixed with UNVERIFIED:."
                        % push_claim.group(0))
        send_claim = claim_match(text, SEND_CLAIM_RE)
        if send_claim and "send" not in session_classes:
            return ("Unverified claim '%s': no send-class command (curl, "
                    "mail, gh ...) ran in this session. Run the send "
                    "command now and show its output, or restate prefixed "
                    "with UNVERIFIED:." % send_claim.group(0))
        if claim_match(text, TESTS_CLAIM_RE):
            last_edit = max(edit_idxs) if edit_idxs else -1
            last_run = max(test_run_idxs) if test_run_idxs else -1
            ok = last_run >= 0 and last_run > last_edit
            if not ok:
                last_edit_ts = max(
                    [_f(e.get("ts")) for e in ledger
                     if e.get("kind") == "edit"] or [0.0])
                ok = any(_f(r.get("ts")) >= last_edit_ts for r in receipts())
            if not ok:
                return ("Unverified claim: tests pass, but no test runner "
                        "ran after the last file edit in this session. Run "
                        "the test suite now and show the result, or restate "
                        "prefixed with UNVERIFIED:.")

    # c. RED-GREEN ledger: test files edited, never proven green.
    if gates.get("red_green"):
        test_edits = [e for e in ledger
                      if e.get("kind") == "edit" and e.get("test")]
        if test_edits:
            last_ts = max(_f(e.get("ts")) for e in test_edits)
            runs_after = [e for e in ledger
                          if e.get("kind") == "test_run"
                          and e.get("ok", True)
                          and _f(e.get("ts")) >= last_ts]
            recs_after = [r for r in receipts()
                          if _f(r.get("ts")) >= last_ts]
            if not runs_after and not recs_after:
                paths = sorted({str(e.get("path") or "") for e in test_edits})
                return ("Test file(s) edited but never proven green: %s. "
                        "Run: %s \"tests pass after edits\" -- <test command>"
                        "\nOr restate prefixed with UNVERIFIED:."
                        % (", ".join(paths[:3]), _prove_cmd()))

    # d. PROMISSORY ending: ends on future action, not a question/handoff.
    if gates.get("promissory") and text.strip():
        tail = text[-300:]
        m = PROMISSORY_RE.search(tail)
        if m and not WAITING_RE.search(tail):
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            if "?" not in " ".join(sentences[-2:]):
                return ("Final message ends with a promise: \"%s...\". "
                        "Either do it now, or end with an explicit handoff "
                        "(what remains, who acts next, or a question)."
                        % m.group(0)[:60])

    # e. DEFERRAL without artifact.
    if gates.get("deferral") and text.strip():
        if DEFER_RE.search(text):
            has_artifact = "deferral_artifact" in session_classes or any(
                t["name"] in EDIT_TOOLS and t["ok"] and
                "DEFERRALS" in str((t["input"] or {}).get("file_path") or "")
                for t in tools)
            if not has_artifact:
                return ("Deferral language with no artifact: nothing was "
                        "filed (no gh issue create, no DEFERRALS append). "
                        "Run /proofgate:defer or create the issue now — or "
                        "just do the deferred work.")

    # Optional LLM-judge tier, off by default.
    if gates.get("llm_judge") and cfg.get("llm_judge_cmd"):
        try:
            r = subprocess.run(cfg["llm_judge_cmd"], shell=True, input=text,
                               capture_output=True, text=True, timeout=30)
            out = (r.stdout or "").strip()
            if out.startswith("BLOCK"):
                return out[5:].strip() or "LLM judge flagged the final claim."
        except Exception:
            pass

    return None


def _count_path(dd, sid):
    return os.path.join(dd, "state", sid + ".blocks")


def read_count(dd, sid, active):
    if not active:
        return 0
    try:
        with open(_count_path(dd, sid), encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except (OSError, ValueError):
        # Cannot track blocks -> never risk a stop loop.
        return MAX_BLOCKS_PER_TURN


def write_count(dd, sid, n):
    try:
        p = _count_path(dd, sid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(n))
    except OSError:
        pass


def main():
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        return
    sid = pg.sanitize_id(data.get("session_id"))
    cwd = str(data.get("cwd") or os.getcwd())
    active = bool(data.get("stop_hook_active"))
    dd = pg.data_dir()
    cfg, gates = load_config(dd)

    count = read_count(dd, sid, active)
    if count >= MAX_BLOCKS_PER_TURN:
        return
    if not active:
        write_count(dd, sid, 0)

    final, tools = parse_transcript(str(data.get("transcript_path") or ""))
    if "UNVERIFIED" in final:
        return

    reason = evaluate(final, tools, cwd, dd, sid, cfg, gates)
    if reason:
        write_count(dd, sid, count + 1)
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
