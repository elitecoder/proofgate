#!/usr/bin/env python3
"""proofgate scope-budget: PostToolUse(Edit|Write) dirty-file budget.

Counts dirty+untracked files via git in cwd. On the FIRST crossing of
each threshold per session (state in $CLAUDE_PLUGIN_DATA) it emits a
block decision forcing a one-line scope inventory to the user.
Non-git cwd stays silent.

Thresholds are configurable so a legitimate large-but-focused change
(e.g. a coverage PR touching 40+ files) is not hard-blocked. Precedence,
highest first:
  1. env  PROOFGATE_SCOPE_BUDGET  — comma-separated ints ("50,150"), a
     single int ("80"), or "off"/"0"/"none" to disable the gate entirely.
  2. config.json key  "scope_budget"  in $CLAUDE_PLUGIN_DATA:
       false                              -> disabled
       50                                 -> single threshold
       [50, 150]                          -> explicit thresholds
       {"enabled": false}                 -> disabled
       {"thresholds": [50, 150]}          -> explicit thresholds
  3. DEFAULT_THRESHOLDS below.
Bad values fall through to the next source; an empty result disables.
"""
import json
import os
import re
import subprocess
import sys

# Raised from the original (12, 30, 60): 12 fired on ordinary focused work
# (a 37-file coverage PR tripped it). 50 leaves headroom for a large-but-
# focused change while still flagging genuinely runaway scope.
DEFAULT_THRESHOLDS = (50, 150)

_DISABLE_TOKENS = {"off", "none", "false", "no", "disabled", "0"}


def _coerce_thresholds(value):
    """Return a sorted tuple of positive int thresholds from a config value,
    () to mean 'disabled', or None to mean 'not specified / unusable'."""
    if value is None:
        return None
    if value is False:
        return ()
    if isinstance(value, bool):  # True: use defaults
        return None
    if isinstance(value, (int, float)):
        return (int(value),) if value > 0 else ()
    if isinstance(value, dict):
        if value.get("enabled") is False:
            return ()
        return _coerce_thresholds(value.get("thresholds"))
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _DISABLE_TOKENS:
            return ()
        value = [p for p in re.split(r"[,\s]+", s) if p]
    if isinstance(value, (list, tuple)):
        nums = []
        for p in value:
            try:
                n = int(p)
            except (TypeError, ValueError):
                continue
            if n > 0:
                nums.append(n)
        return tuple(sorted(set(nums)))
    return None


def _thresholds(root):
    """Resolve thresholds from env, then config.json, then the default."""
    env = _coerce_thresholds(os.environ.get("PROOFGATE_SCOPE_BUDGET"))
    if env is not None:
        return env
    cfg = {}
    if root:
        try:
            with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg = loaded
        except (OSError, ValueError):
            pass
    conf = _coerce_thresholds(cfg.get("scope_budget"))
    if conf is not None:
        return conf
    return DEFAULT_THRESHOLDS


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
    thresholds = _thresholds(root)
    if not thresholds:
        return  # gate disabled by config/env
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
    newly = [t for t in thresholds if count >= t and t not in crossed]
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
