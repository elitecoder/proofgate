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

# The LLM judge subsumes the two claim-vs-text keyword tiers (checkable_claim,
# promissory): it reads the summary against the session evidence and judges
# whether an action-claim is unsupported, instead of firing on a bare keyword.
# So those two default OFF when llm_judge is on. The remaining tiers are cheap
# deterministic ledger checks the judge does not replace, and stay on. If the
# LLM call fails it fails open (no block); set llm_judge False + the keyword
# tiers True to fall back to the pure-deterministic gate.
DEFAULT_GATES = {
    "llm_judge": True,
    "checkable_claim": False,
    "promissory": False,
    "ship_state": True,
    "red_green": True,
    "deferral": True,
    "vacuous_test": False,
}

PUSH_CLAIM_RE = re.compile(r"\b(pushed|merged|shipped)\b", re.I)
SEND_CLAIM_RE = re.compile(r"\b(sent|delivered|posted)\b", re.I)
TESTS_CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:unit\s+|e2e\s+|integration\s+)?tests?\s+"
    r"(?:are\s+|all\s+|now\s+|still\s+)*(?:pass(?:ing|ed|es)?|green)\b"
    r"|\btest\s+suite\s+(?:passes|passed|is\s+green)\b"
    r"|\be2e\s+pass(?:ed|es|ing)?\b",
    re.I)

# A STRONGER claim than "tests pass": an explicit assertion that the real code
# path was exercised end-to-end. This is a PRECISION-tuned tripwire for the
# common overclaim phrasing, NOT an airtight classifier — it is deliberately
# easy to phrase around (recall is low by design; see failure mode 7). It must
# fire on the VALIDATION-VERB claim ("validated end-to-end", "exercised the
# real code path"), never on a noun phrase that describes a test artifact
# ("added an end-to-end test") or on thorough-unit phrasing ("fully tested
# locally") — those are benign and would make this a noisy regex.
#
# Two earlier-drafted branches were CUT after adversarial measurement on an
# affirmative benign corpus: a bare "fully <verb>" branch (fired on the common
# honest "fully tested locally") and a broad "real/production <noun>" branch
# with nouns like server/api/endpoint (fired on "the production API key",
# "restarted the real server"). The trigger now anchors on the canonical
# strong phrases only: "<verb> ... end-to-end" and "(real|actual|production|
# live) (code) path/pipeline ... <exercise verb>". Negation is stripped by the
# shared NEG_TAIL_RE; active future by STRONG_FUTURE_RE. Measured benign
# fire-rate is in the PR and pinned by test_verifygate_vacuous_fprate.py.
_RPATH = r"(?:real|actual|production|live)\s+(?:code[\s-]?)?(?:path|paths|pipeline)"
STRONG_CLAIM_RE = re.compile(
    # verb-of-validation + (optional short object) + "end-to-end"
    r"\b(?:validat|verif|exercis|confirm|tested)\w*\s+"
    r"(?:it\s+|this\s+|that\s+|the\s+(?:\w+\s+){0,2}|everything\s+|fully\s+)?"
    r"end[\s-]?to[\s-]?end\b"
    # exercise-verb + "the real/production code path/pipeline"
    r"|\b(?:exercis\w*|cover\w*|hit|ran|run|tested|validat\w*|verif\w*)\s+"
    r"(?:against\s+)?(?:the\s+|a\s+)?" + _RPATH + r"\b"
    # "the real code path is/was exercised/covered/tested/run"
    r"|\b(?:the\s+)?" + _RPATH + r"\s+"
    r"(?:is|was|are|were|now)\s+(?:fully\s+|now\s+)?"
    r"(?:exercis\w*|cover\w*|tested|run|hit|validat\w*|verif\w*)",
    re.I)

# The strong claim's verbs share a stem across tenses (validat\w* matches both
# "validated" and "validate"), so active future ("I will validate ...", "going
# to verify ...") leaks past the shared NEG_TAIL_RE, which only knows the
# passive form ("will be validated"). Broadening the shared guard would
# re-tune the push/send/tests gates; instead suppress active future locally,
# just for this trigger.
STRONG_FUTURE_RE = re.compile(
    r"(?:\bI'?ll|\bwe'?ll|\bwill|\bgoing\s+to|\bplan(?:ning|s)?\s+(?:to|on)"
    r"|\bintend(?:ing|s)?\s+to|\bneed\s+to|\bwant\s+to)"
    r"\s*(?:\w+[\s,]+){0,2}$",
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


def strong_claim_match(text):
    """A live (non-negated, non-future) strong end-to-end claim, or None.
    Adds an active-future guard ("I will validate ...") on top of the shared
    negation guard, because the strong verbs share a stem across tenses."""
    for m in STRONG_CLAIM_RE.finditer(text):
        prefix = text[max(0, m.start() - 40):m.start()]
        if NEG_TAIL_RE.search(prefix) or STRONG_FUTURE_RE.search(prefix):
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


def _norm(p):
    return str(p or "").replace("\\", "/").strip().rstrip("/")


def _paths_match(a, b):
    """True if two file paths plausibly name the same file: equal after
    normalization, or one is a path-suffix of the other on a path-segment
    boundary (so src/foo.py matches /repo/src/foo.py but NOT src/barfoo.py).
    Basename-only equality is intentionally NOT enough — pkg_a/models.py and
    pkg_b/models.py are different files (adversarial finding C1)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return hi.endswith("/" + lo)


def _covered_paths(recs):
    """Paths a prove-cov receipt recorded as executed (>0 lines). Plain prove
    receipts have no 'covered' key and contribute nothing."""
    out = []
    for r in recs:
        cov = r.get("covered")
        if isinstance(cov, list):
            out.extend(str(c) for c in cov if c)
    return out


def _prove_cmd():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return os.path.join(root, "bin", "prove") if root else "bin/prove"


LLM_JUDGE_PROMPT = """You are a verification gate for a coding agent. The agent \
just finished a turn and wrote a final summary to its user. Your job: decide whether \
the summary makes a factual claim about an ACTION IT TOOK that the evidence does NOT \
support.

Block ONLY a claim of a concrete, verifiable action that the evidence contradicts or \
leaves unsupported — e.g. "merged the PR", "pushed the branch", "sent the message", \
"tests pass", "deployed". Do NOT block: plans ("I'll next..."), descriptions of what \
code does, analysis, questions, hedged/UNVERIFIED statements, or a turn that simply \
didn't do any such action (most turns write no PR and push nothing — that is normal \
and fine, not a violation).

Default to PASS. Block only when a specific action-claim is clearly unbacked by the \
evidence below. When unsure, PASS.

=== AGENT FINAL SUMMARY ===
%(summary)s

=== EVIDENCE (what actually happened this session) ===
Actions recorded this session (durable ledger): %(ledger)s
Command classes seen this turn: %(classes)s
Git: %(git)s

=== YOUR VERDICT ===
Reply with EXACTLY one line:
  PASS
or
  BLOCK <one sentence: which claim is unsupported and what to do>
"""


def _git_state(cwd):
    """One-line upstream summary for the judge; '' if not a git repo."""
    try:
        n = _unpushed_count(cwd)
        if n is None:
            return "not a git repo / no upstream"
        return "%d local commit(s) not pushed to upstream" % n
    except Exception:
        return "unknown"


def llm_judge_verdict(text, ledger, session_classes, cwd, cfg):
    """Ask an LLM to judge the summary against the evidence.

    Returns a block-reason string, or None to PASS. Fails open (None) on any
    error so the gate never wedges the session on a flaky model call.
    """
    cmd = cfg.get("llm_judge_cmd") or "claude --bare -p --model haiku"
    actions = [e.get("kind") for e in ledger
               if e.get("kind") in ("push", "send", "git_commit", "test_run")]
    digest = ", ".join(sorted(set(actions))) or "(none recorded)"
    prompt = LLM_JUDGE_PROMPT % {
        "summary": text[:4000],
        "ledger": digest,
        "classes": ", ".join(sorted(session_classes)) or "(none)",
        "git": _git_state(cwd),
    }
    try:
        r = subprocess.run(cmd, shell=True, input=prompt,
                           capture_output=True, text=True, timeout=45)
    except Exception:
        return None  # fail open
    out = (r.stdout or "").strip()
    # Take the last non-empty line as the verdict (models may preamble).
    verdict = ""
    for line in reversed(out.splitlines()):
        if line.strip():
            verdict = line.strip()
            break
    if verdict.upper().startswith("BLOCK"):
        reason = verdict[5:].strip(" :-") or "claim not supported by session evidence"
        return "LLM verify-gate: " + reason
    return None  # PASS, or unparseable → fail open


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

    # Production (non-test) files edited this session, by basename. The
    # vacuous_test tier asks: was the real code path actually exercised, or
    # only a unit harness around it?
    prod_edit_paths = set()
    prod_edit_idxs = []
    for t in tools:
        if t["name"] in EDIT_TOOLS and t["ok"]:
            p = str((t["input"] or {}).get("file_path") or
                    (t["input"] or {}).get("notebook_path") or "")
            if p and not pg.is_test_path(p):
                prod_edit_paths.add(p)
                prod_edit_idxs.append(t["idx"])
    # Only an e2e/browser runner counts as real-path evidence here. A
    # send-class command (curl/mail) is NOT accepted: it proves a request was
    # made, not that the edited code path produced it, and `curl --help` would
    # trivially clear the claim. The honest escapes are an e2e run, a covering
    # prove-cov receipt, honest phrasing, or UNVERIFIED:.
    e2e_run_idxs = [t["idx"] for t in ok_bash if "e2e_run" in t["classes"]]

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

    # Fold the durable ledger's recorded action kinds into session_classes.
    # session_classes is built from the live transcript, which is truncated by
    # context compaction — so a push/send/commit that ran in an EARLIER turn
    # (then got compacted out) vanishes from the transcript and the claim reads
    # as unverified, blocking a legitimate summary. The PostToolUse recorder
    # (mark_dirty) persists those same kinds (push/send/git_commit/test_run) to
    # the session ledger, which survives compaction. Trust it as evidence the
    # action happened this session. (ledger kinds use the same class names.)
    for _e in ledger:
        _k = _e.get("kind")
        if _k in ("push", "send", "git_commit", "test_run"):
            session_classes.add(_k)

    # LLM judge (default tier). When on, an LLM reads the summary together with
    # the session evidence (ledger actions, command classes, git state) and
    # decides whether any action-claim is unsupported — instead of the brittle
    # keyword tiers below, which fire on the word "merged" even in a turn that
    # legitimately pushed nothing. The judge is told most turns take no such
    # action and that is fine. Fails open (None) on any model/parse error, then
    # falls through to the deterministic tiers as a backstop only if explicitly
    # left on. With llm_judge on and the regex tiers off (the default config),
    # a clean PASS returns here and the keyword tiers never run.
    if gates.get("llm_judge"):
        verdict = llm_judge_verdict(text, ledger, session_classes, cwd, cfg)
        if verdict:
            return verdict

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

    # c2. VACUOUS-TEST: a STRONG claim ("validated end-to-end", "exercised the
    # real code path") after editing production code, but the only evidence is
    # a unit runner. A mocked unit test goes green without touching the real
    # subprocess/server/browser, so a unit pass cannot back an end-to-end
    # claim. Clears on real-path evidence: an e2e/browser runner after the
    # edit, or a prove-cov receipt that covered an edited production file.
    # Off by default (config-gated) — measured fire-rate is in the PR.
    if gates.get("vacuous_test") and prod_edit_paths:
        strong = strong_claim_match(text)
        if strong:
            # The real-path run must come AFTER the last production edit —
            # exercising the old code then editing it proves nothing about the
            # claim (mirrors the tests-pass tier's post-edit ordering).
            last_prod_edit = max(prod_edit_idxs)
            last_real_run = max(e2e_run_idxs) if e2e_run_idxs else -1
            has_real_run = last_real_run > last_prod_edit
            # prove-cov runs in the agent's Bash tool, so it is timestamped,
            # not tool-ordered; accept any covering receipt (the gate cannot
            # align receipt ts with tool idx, and a coverage receipt for the
            # edited file is strong positive evidence regardless). Match by
            # path-suffix, not bare basename, so models.py in one package does
            # not vouch for models.py in another.
            covered = _covered_paths(receipts())
            covers_edit = any(_paths_match(c, e)
                              for c in covered for e in prod_edit_paths)
            if not has_real_run and not covers_edit:
                edited = sorted(os.path.basename(p) for p in prod_edit_paths)
                return ("Unverified claim '%s': you edited production code "
                        "(%s) and claimed the real path ran, but only a unit "
                        "runner did — a mocked unit test passes without "
                        "exercising the real subprocess/server/browser. Prove "
                        "the real path with one of:\n"
                        "  - an e2e/browser run (playwright, cypress), or\n"
                        "  - %s-cov \"end-to-end\" <file> -- <command that runs "
                        "the real path under coverage>\n"
                        "Or, if you only ran unit tests, say so plainly "
                        "(\"unit tests pass\") or prefix with UNVERIFIED:."
                        % (strong.group(0).strip(), ", ".join(edited[:3]),
                           _prove_cmd()))

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
