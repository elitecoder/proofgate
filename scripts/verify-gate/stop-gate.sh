#!/bin/sh
# proofgate verify-gate (Stop).
# Fail open: any failure exits 0 silently and never wedges the session.
# Kill switch: a stale directory-source registration can keep loading this
# hook after uninstall; PROOFGATE_DISABLED or a DISABLED file in the data dir
# hard-no-ops it (mirrors pg_common.is_disabled).
[ -n "$PROOFGATE_DISABLED" ] && exit 0
[ -n "$CLAUDE_PLUGIN_DATA" ] && [ -f "$CLAUDE_PLUGIN_DATA/DISABLED" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/stop_gate.py" ] || exit 0
out=$(python3 "$dir/stop_gate.py" 2>/dev/null) || exit 0
[ -n "$out" ] && printf '%s\n' "$out"
exit 0
