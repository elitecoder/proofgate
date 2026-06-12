#!/usr/bin/env python3
"""proofgate scope-budget: PostToolUse(Edit|Write) dirty-file budget.

Counts dirty+untracked files via git in cwd. On the FIRST crossing of
each threshold per session (state in $CLAUDE_PLUGIN_DATA) it emits a
block decision forcing a one-line scope inventory to the user.
Non-git cwd stays silent.
"""
import json
import os
import re
import subprocess
import sys

THRESHOLDS = (12, 30, 60)


def _count_dirty(cwd):
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain", "-uall"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None  # not a git repo
    return sum(1 for line in proc.stdout.splitlines() if line.strip())


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("tool_name") not in ("Edit", "Write"):
        return
    cwd = str(data.get("cwd") or "")
    if not cwd or not os.path.isdir(cwd):
        return
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    if not root:
        return
    count = _count_dirty(cwd)
    if count is None:
        return

    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or "default"))
    state_dir = os.path.join(root, "state")
    state_path = os.path.join(state_dir, "scope-budget-%s.json" % sid)
    state = {}
    try:
        with open(state_path) as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state = loaded
    except Exception:
        state = {}

    crossed = set()
    for t in state.get(cwd) or []:
        try:
            crossed.add(int(t))
        except Exception:
            pass
    newly = [t for t in THRESHOLDS if count >= t and t not in crossed]
    if not newly:
        return
    crossed.update(newly)
    state[cwd] = sorted(crossed)
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)
    except Exception:
        return  # cannot record first-crossing: stay silent over re-blocking

    reason = (
        "Scope budget: %d dirty/untracked files in this repo (crossed the %d-file "
        "threshold). Stop and give the user a one-line scope inventory - what is "
        "changing and why each part belongs to this task - before editing further."
    ) % (count, max(newly))
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
