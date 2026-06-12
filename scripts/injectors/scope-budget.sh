#!/bin/sh
# proofgate scope-budget (PostToolUse Edit|Write).
# Fail open: any failure exits 0 silently and never blocks the session.
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/scope_budget.py" ] || exit 0
python3 "$dir/scope_budget.py" 2>/dev/null
exit 0
