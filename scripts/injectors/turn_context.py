#!/usr/bin/env python3
"""proofgate turn-context: UserPromptSubmit context injector.

Always emits a UTC timestamp and a 4-line audience-register card.
Pattern-gated extras keep typical output under ~120 tokens:
direct-order prompts get an authority card, pushback prompts get a
reconciliation card.
"""
import json
import re
import sys
from datetime import datetime, timezone

REGISTER = (
    "register/chat: answer first; simple answers fit in <=6 lines\n"
    "register/agent-facing files: WHAT, never WHY\n"
    "register/notifications: one rollup per batch\n"
    "register/unsure: send the short version"
)

AUTHORITY = (
    "authority: a live user command overrides standing rules that user wrote\n"
    "authority: agent-scoped rules do not bind interactive sessions\n"
    "authority: reversible actions are pre-approved\n"
    "authority: ask only before irreversible external effects"
)

RECONCILE = (
    "reconcile: the user's direct observation is ground truth\n"
    "reconcile: run the reconciling check before replying\n"
    "reconcile: never re-assert a claim without new evidence"
)

ORDER_PAT = re.compile(
    r"\b(just do it|do it now|do it anyway|go ahead|i said|i told you|"
    r"don'?t ask|stop asking|no more questions|"
    r"just (do|run|push|ship|merge|send|delete|fix|apply)\b)",
    re.IGNORECASE,
)

PUSHBACK_PAT = re.compile(
    r"\b(that'?s not true|that is not true|are you sure|i'?m looking at|"
    r"i am looking at|you'?re wrong|that'?s wrong|that didn'?t happen)\b",
    re.IGNORECASE,
)


def main():
    prompt = ""
    try:
        data = json.loads(sys.stdin.read() or "{}")
        prompt = str(data.get("prompt") or "")
    except Exception:
        prompt = ""
    # pasted text often carries curly apostrophes
    prompt = prompt.replace("’", "'")
    parts = [
        "now: " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        REGISTER,
    ]
    if ORDER_PAT.search(prompt):
        parts.append(AUTHORITY)
    if PUSHBACK_PAT.search(prompt):
        parts.append(RECONCILE)
    sys.stdout.write("\n".join(parts) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
