#!/bin/sh
# proofgate notify-throttle (PreToolUse Bash).
# Fail open: any failure exits 0 silently (allow) and never blocks the session.
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/notify_throttle.py" ] || exit 0
python3 "$dir/notify_throttle.py" 2>/dev/null
exit 0
