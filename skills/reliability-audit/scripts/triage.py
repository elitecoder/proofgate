#!/usr/bin/env python3
"""Score Claude Code sessions by user frustration / correction signals.

Reads transcript JSONL files under a Claude projects dir
(default ~/.claude/projects/<project>/<session>.jsonl), scores each real
user message with weighted regexes, and writes:

  <out>/report.tsv      one row per session, sorted by score desc
  <out>/digest_NN_<session>.md   for the top N sessions

Stdlib only. All processing is local.
"""

import argparse
import json
import re
import sys
from pathlib import Path

WEIGHTED_PATTERNS = [
    (4, r"\byou\s+(deleted|removed|broke|destroyed|overwrote|ignored|lied)\b"),
    (4, r"\b(wtf|ffs|fuck\w*|goddamn|dammit)\b"),
    (3, r"\bi\s+(already\s+|just\s+)?(said|told\s+you|asked\s+(you\s+)?(for|to))\b"),
    (3, r"\bstill\s+(broken|failing|wrong|not\s+working|doesn'?t\s+work)\b"),
    (3, r"\b(revert|undo)\s+(that|this|it|everything)\b"),
    (3, r"\bstop\b.{0,24}\b(doing|changing|touching|editing|creating)\b"),
    (2, r"\bwhy\s+(did|are|would|do)\s+you\b"),
    (2, r"\bdon'?t\s+(do|touch|change|delete|modify|create)\b"),
    (2, r"\b(that'?s|this\s+is)\s+(wrong|not\s+right|not\s+what)\b"),
    (2, r"\bnot\s+what\s+i\s+(asked|wanted|meant|said)\b"),
    (2, r"\?{2,}|!{2,}"),
    (1, r"^no[.,!\s]"),
    (1, r"\b(again|wrong|incorrect|nope)\b"),
]
COMPILED = [(w, re.compile(p, re.IGNORECASE)) for w, p in WEIGHTED_PATTERNS]
CAPS_RE = re.compile(r"\b[A-Z]{4,}\b")
CORRECTION_RE = re.compile(
    r"^no[,.!\s]|\binstead\b|\bactually\b|\bi\s+meant\b|\bnot\s+that\b", re.IGNORECASE
)
# Synthetic user entries injected by the CLI, not typed by the human.
NON_PROMPT_PREFIXES = ("<command-", "<local-command", "<system-", "Caveat:")


def extract_text(content):
    """Return the plain text of a message content (str or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p)
    return ""


def is_real_user_prompt(record):
    if record.get("type") != "user" or record.get("isMeta"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
    text = extract_text(content).strip()
    if not text or text.startswith(NON_PROMPT_PREFIXES):
        return False
    return True


def score_text(text):
    score = 0
    hits = []
    for weight, rx in COMPILED:
        if rx.search(text):
            score += weight
            hits.append(rx.pattern)
    # Shouting bonus; skip code-looking messages where caps are routine.
    if CAPS_RE.search(text) and "```" not in text:
        score += 1
    return score, hits


def iter_session_files(projects_dir):
    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return
    for project in sorted(projects_dir.iterdir()):
        if not project.is_dir():
            continue
        for f in sorted(project.glob("*.jsonl")):
            yield project.name, f


def analyze_session(path):
    """Return (score, events) where events preserve chronological order."""
    events = []  # ("assistant", text) | ("user", text, msg_score, hits, is_corr)
    total = n_user = n_flagged = n_corr = 0
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return 0, 0, 0, 0, []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            text = extract_text((rec.get("message") or {}).get("content"))
            if text.strip():
                events.append(("assistant", text))
        elif is_real_user_prompt(rec):
            text = extract_text((rec.get("message") or {}).get("content")).strip()
            msg_score, hits = score_text(text)
            is_corr = bool(CORRECTION_RE.search(text))
            n_user += 1
            n_corr += int(is_corr)
            if msg_score > 0:
                n_flagged += 1
                total += msg_score
            events.append(("user", text, msg_score, hits, is_corr))
    return total, n_user, n_flagged, n_corr, events


def clip(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_digest(out_path, project, session_path, score, n_user, n_flagged, events):
    lines = [
        "# Session digest: %s" % session_path.stem,
        "",
        "- project: %s" % project,
        "- file: %s" % session_path,
        "- score: %d  user_msgs: %d  flagged: %d" % (score, n_user, n_flagged),
        "",
        "## Flagged user messages (chronological)",
        "",
    ]
    last_assistant = ""
    for ev in events:
        if ev[0] == "assistant":
            last_assistant = ev[1]
            continue
        _, text, msg_score, hits, _ = ev
        if msg_score <= 0:
            continue
        lines.append("[score %d] %s" % (msg_score, clip(text, 500)))
        if last_assistant:
            lines.append("    prior-assistant: %s" % clip(last_assistant, 240))
        lines.append("    hits: %s" % "; ".join(hits))
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--top", type=int, default=10, help="digests for top N sessions")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for project, f in iter_session_files(args.projects_dir):
        score, n_user, n_flagged, n_corr, events = analyze_session(f)
        if n_user == 0:
            continue
        rows.append((score, n_corr, n_user, n_flagged, project, f, events))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)

    report = out / "report.tsv"
    with report.open("w") as fh:
        fh.write("score\tcorrections\tuser_msgs\tflagged\tproject\tsession\tpath\n")
        for score, n_corr, n_user, n_flagged, project, f, _ in rows:
            fh.write(
                "%d\t%d\t%d\t%d\t%s\t%s\t%s\n"
                % (score, n_corr, n_user, n_flagged, project, f.stem, f)
            )

    for rank, (score, _, n_user, n_flagged, project, f, events) in enumerate(
        rows[: args.top], start=1
    ):
        digest = out / ("digest_%02d_%s.md" % (rank, f.stem))
        write_digest(digest, project, f, score, n_user, n_flagged, events)

    print("sessions: %d  report: %s  digests: %d" % (len(rows), report, min(args.top, len(rows))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
