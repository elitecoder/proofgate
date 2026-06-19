#!/usr/bin/env python3
"""proofgate PostToolUse recorder: append mutating actions to the session
ledger so the Stop gate can cross-reference claims. Fails open."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pg_common as pg

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RECORDED = {"git_commit", "push", "send", "test_run", "deferral_artifact"}


def main():
    if pg.is_disabled():
        return  # kill switch: hard no-op, record nothing
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        return
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}
    sid = pg.sanitize_id(data.get("session_id"))
    ts = time.time()
    entries = []

    if tool in EDIT_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if path:
            entries.append({"ts": ts, "kind": "edit", "path": path,
                            "test": pg.is_test_path(path)})
    elif tool == "Bash":
        cmd = str(ti.get("command") or "")
        if cmd:
            resp = data.get("tool_response")
            ok = not (isinstance(resp, dict) and
                      (resp.get("is_error") or resp.get("interrupted")))
            for kind in sorted(pg.bash_classes(cmd) & RECORDED):
                entries.append({"ts": ts, "kind": kind, "cmd": cmd[:200],
                                "ok": ok})

    if entries:
        pg.append_ledger(pg.data_dir(), sid, entries)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
