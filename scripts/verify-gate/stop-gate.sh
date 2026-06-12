#!/bin/sh
# proofgate verify-gate (Stop).
# Fail open: any failure exits 0 silently and never wedges the session.
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/stop_gate.py" ] || exit 0
out=$(python3 "$dir/stop_gate.py" 2>/dev/null) || exit 0
[ -n "$out" ] && printf '%s\n' "$out"
exit 0
