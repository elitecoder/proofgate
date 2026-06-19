#!/bin/sh
# proofgate agent-file-lint (PostToolUse Edit|Write).
# Exit 2 only on lint findings (stderr is fed back to the model);
# every other outcome fails open with exit 0.
[ -n "$PROOFGATE_DISABLED" ] && exit 0
[ -n "$CLAUDE_PLUGIN_DATA" ] && [ -f "$CLAUDE_PLUGIN_DATA/DISABLED" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
dir=$(dirname -- "$0") || exit 0
[ -f "$dir/agent_file_lint.py" ] || exit 0
rc=0
python3 "$dir/agent_file_lint.py" || rc=$?
if [ "$rc" -eq 2 ]; then
  exit 2
fi
exit 0
