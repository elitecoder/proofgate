#!/bin/sh
# proofgate verify-gate ledger recorder (PostToolUse).
# Fail open: any failure exits 0 silently and never wedges the session.
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/mark_dirty.py" ] || exit 0
python3 "$dir/mark_dirty.py" >/dev/null 2>&1
exit 0
