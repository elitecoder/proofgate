#!/usr/bin/env python3
"""proofgate agent-file-lint: PostToolUse(Edit|Write) gate for agent-facing files.

Fires only on agent-facing paths. Lints just the ADDED text
(tool_input.content or tool_input.new_string) with fenced code blocks
stripped: rationale markers and >250-char lines exit 2 with per-line
feedback on stderr.
"""
import json
import re
import sys

TARGET_BASENAMES = {"CLAUDE.md", "AGENTS.md", "SKILL.md"}
TARGET_DIRS = {".claude", "prompts"}
MAX_LINE = 250
MARKERS = re.compile(
    r"\b(because|rationale|for posterity|why we)\b|background:",
    re.IGNORECASE,
)


def _is_target(path):
    if not path:
        return False
    norm = str(path).replace("\\", "/")
    parts = norm.split("/")
    base = parts[-1]
    if base in TARGET_BASENAMES or base.endswith("-runner.md"):
        return True
    return any(part in TARGET_DIRS for part in parts[:-1])


def _visible_lines(text):
    """(lineno, line) pairs with fenced code blocks removed."""
    out = []
    fence = None
    for n, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        out.append((n, line))
    return out


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("tool_name") not in ("Edit", "Write"):
        return 0
    tool_input = data.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")
    if not _is_target(path):
        return 0
    added = tool_input.get("content")
    if added is None:
        added = tool_input.get("new_string")
    if not isinstance(added, str) or not added:
        return 0

    problems = []
    for n, line in _visible_lines(added):
        m = MARKERS.search(line)
        if m:
            problems.append(
                "line %d: rationale marker '%s' - agent-facing files state "
                "WHAT, never WHY; delete the justification" % (n, m.group(0))
            )
        if len(line) > MAX_LINE:
            problems.append(
                "line %d: %d chars (max %d) - split into short directives"
                % (n, len(line), MAX_LINE)
            )
    if not problems:
        return 0
    sys.stderr.write(
        "agent-file-lint: %s (line numbers are within the added text)\n" % path
    )
    for p in problems:
        sys.stderr.write("agent-file-lint: %s\n" % p)
    return 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 0
    sys.exit(code)
