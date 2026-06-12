"""Shared fixtures/helpers for verify-gate tests. Contains no tests."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOP = ROOT / "scripts" / "verify-gate" / "stop-gate.sh"
MARK = ROOT / "scripts" / "verify-gate" / "mark-dirty.sh"
PROVE = ROOT / "bin" / "prove"


def run_stop(payload, data_dir, raw=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(["sh", str(STOP)], input=stdin,
                          capture_output=True, text=True, env=env,
                          timeout=60)


def run_mark(payload, data_dir, raw=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(["sh", str(MARK)], input=stdin,
                          capture_output=True, text=True, env=env,
                          timeout=60)


def run_prove(args, cwd, data_dir):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    return subprocess.run(["sh", str(PROVE)] + list(args), cwd=str(cwd),
                          capture_output=True, text=True, env=env,
                          timeout=60)


def block_of(result):
    """Parse stop-gate stdout; None means the stop was allowed."""
    assert result.returncode == 0
    out = result.stdout.strip()
    if not out:
        return None
    obj = json.loads(out)
    assert obj.get("decision") == "block"
    return obj


def stop_payload(transcript_path, cwd, sid="sess1", active=False):
    return {
        "session_id": sid,
        "transcript_path": str(transcript_path),
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "stop_hook_active": active,
    }


def make_transcript(path, items):
    """items: ("text", str) | ("bash", cmd[, ok]) | ("edit", path[, ok])
    | ("write", path[, ok])"""
    lines = []
    tid = 0
    for it in items:
        kind = it[0]
        if kind == "text":
            lines.append({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": it[1]}]}})
            continue
        tid += 1
        tuid = "toolu_%d" % tid
        ok = it[2] if len(it) > 2 else True
        if kind == "bash":
            tu = {"type": "tool_use", "id": tuid, "name": "Bash",
                  "input": {"command": it[1]}}
        elif kind == "edit":
            tu = {"type": "tool_use", "id": tuid, "name": "Edit",
                  "input": {"file_path": it[1], "old_string": "a",
                            "new_string": "b"}}
        else:
            tu = {"type": "tool_use", "id": tuid, "name": "Write",
                  "input": {"file_path": it[1], "content": "x"}}
        lines.append({"type": "assistant", "message": {
            "role": "assistant", "content": [tu]}})
        lines.append({"type": "user", "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tuid,
                         "content": "ok", "is_error": not ok}]}})
    Path(path).write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return Path(path)


def _git(repo, args, **kw):
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    base = ["git"]
    if repo is not None:
        base += ["-C", str(repo), "-c", "user.email=t@example.com",
                 "-c", "user.name=T", "-c", "commit.gpgsign=false"]
    return subprocess.run(base + args, check=True, capture_output=True,
                          text=True, env=env, **kw)


def make_repo(tmp_path, unpushed=0):
    """Clone of a local bare remote with an upstream-tracking branch and
    `unpushed` local-only commits."""
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(None, ["init", "--bare", str(remote)])
    _git(None, ["clone", str(remote), str(repo)])
    (repo / "f.txt").write_text("0\n")
    _git(repo, ["add", "."])
    _git(repo, ["commit", "-m", "init"])
    _git(repo, ["push", "-u", "origin", "HEAD"])
    for k in range(unpushed):
        (repo / "f.txt").write_text("%d\n" % (k + 1))
        _git(repo, ["add", "."])
        _git(repo, ["commit", "-m", "c%d" % k])
    return repo


def ledger_lines(data_dir, sid="sess1"):
    p = Path(data_dir) / "ledger" / ("%s.jsonl" % sid)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
