#!/usr/bin/env python3
"""proofgate notify-throttle: PreToolUse(Bash) gate on notification commands.

Matches on the COMMAND HEAD of each pipeline/;/&& segment (comments
stripped) so greps and heredocs ABOUT notification commands never fire.
More than one notification per window per session is denied with a
buffer-and-rollup reason; an 'ACTION:' payload bypasses the throttle.
"""
import json
import os
import re
import shlex
import sys
import time

# Defaults cover common desktop notifiers; add your own tool's notify
# command via $CLAUDE_PLUGIN_DATA/config.json {"notify_heads": [...]}
# (a custom list replaces these defaults entirely).
DEFAULT_HEADS = [
    "notify-send",
    "osascript -e display notification",
    "terminal-notifier",
]
DEFAULT_WINDOW = 300  # seconds

_SEP_CHARS = set("();<>|&")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"command", "exec", "nohup", "time", "builtin", "env"}


def _segments(cmd):
    """Token lists for each command segment, comments stripped.

    Newlines act as separators. Parsing stops at a heredoc marker:
    heredoc bodies are data, not commands."""
    cmd = cmd.replace("\r\n", "\n").replace("\n", " ; ")
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segs, seg = [], []
    try:
        for tok in lex:
            if tok and all(c in _SEP_CHARS for c in tok):
                if "<<" in tok:
                    break
                if seg:
                    segs.append(seg)
                    seg = []
            else:
                seg.append(tok)
    except ValueError:
        # unbalanced quotes: keep whatever lexed cleanly
        pass
    if seg:
        segs.append(seg)
    return segs


def _head_text(seg):
    """Segment joined from its first real command word, with leading
    env assignments and wrapper commands skipped."""
    i = 0
    while i < len(seg) and (_ASSIGN.match(seg[i]) or seg[i] in _WRAPPERS):
        i += 1
    return " ".join(seg[i:])


def _is_notify(cmd, heads):
    for seg in _segments(cmd):
        text = _head_text(seg)
        for head in heads:
            if text == head or text.startswith(head + " "):
                return True
    return False


def _load_config(root):
    heads, window = list(DEFAULT_HEADS), DEFAULT_WINDOW
    try:
        with open(os.path.join(root, "config.json")) as f:
            cfg = json.load(f)
        raw = cfg.get("notify_heads")
        if isinstance(raw, list):
            cleaned = [str(h).strip() for h in raw if str(h).strip()]
            if cleaned:
                heads = cleaned
        w = cfg.get("notify_window_seconds")
        if isinstance(w, (int, float)) and w > 0:
            window = float(w)
    except Exception:
        pass
    return heads, window


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input") or {}
    cmd = str(tool_input.get("command") or "")
    if not cmd:
        return
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or ""
    if not root:
        return  # no state dir: cannot throttle, allow
    heads, window = _load_config(root)
    if not _is_notify(cmd, heads):
        return
    if "ACTION:" in cmd:
        return  # user-must-act alerts bypass the throttle

    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or "default"))
    state_dir = os.path.join(root, "state")
    state_path = os.path.join(state_dir, "notify-throttle-%s.json" % sid)
    now = time.time()
    last = 0.0
    try:
        with open(state_path) as f:
            last = float(json.load(f).get("last_ts") or 0.0)
    except Exception:
        last = 0.0

    if now - last < window:
        deny = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Notification throttle: one was already sent in the last "
                    "%d seconds. Buffer further updates and send ONE rollup "
                    "notification for the whole batch. Prefix the payload with "
                    "ACTION: only if the user must act right now."
                ) % int(window),
            }
        }
        sys.stdout.write(json.dumps(deny))
        return

    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"last_ts": now}, f)
        os.replace(tmp, state_path)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
