"""Kill-switch tests: when proofgate is disabled (PROOFGATE_DISABLED env or a
DISABLED sentinel file in the data dir), every hook entrypoint hard-no-ops.

This is the defense-in-depth that guarantees a stale directory-source
marketplace registration — which Claude Code auto-enables when there is no
explicit enabledPlugins entry — can never block a session after uninstall.

Each test pairs a DISABLED run (must no-op) with an ENABLED control (must
fire), so the assertions fail on the pre-kill-switch code (which always fires)
and pass once the guard is in place.
"""
import json
import os
import subprocess
from pathlib import Path

from test_verifygate_helpers import (
    make_repo, make_transcript, run_stop, stop_payload)

ROOT = Path(__file__).resolve().parents[1]
STOP = ROOT / "scripts" / "verify-gate" / "stop-gate.sh"
MARK = ROOT / "scripts" / "verify-gate" / "mark-dirty.sh"
GATEKEEPER = ROOT / "scripts" / "gatekeeper" / "gatekeeper.py"


def _det_config(data_dir):
    """Deterministic-mode config (llm_judge off, keyword tiers on) so the Stop
    gate blocks without shelling out to a model."""
    (data_dir / "config.json").write_text(json.dumps({"gates": {
        "llm_judge": False, "checkable_claim": True, "promissory": True,
        "ship_state": True, "red_green": True, "deferral": True}}))


def _run(script_path, payload, data_dir, env_extra=None, raw=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    if env_extra:
        env.update(env_extra)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(["sh", str(script_path)], input=stdin,
                          capture_output=True, text=True, env=env, timeout=60)


def _ship_state_case(tmp_path):
    """A transcript+repo that the Stop gate blocks on (committed, unpushed,
    claimed pushed)."""
    data = tmp_path / "data"
    data.mkdir()
    _det_config(data)
    repo = make_repo(tmp_path, unpushed=1)
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "git commit -m 'fix'"),
        ("text", "Done - committed and pushed to origin."),
    ])
    return data, stop_payload(tr, repo)


# --- Stop gate -------------------------------------------------------------

def test_stop_gate_blocks_when_enabled_control(tmp_path):
    data, payload = _ship_state_case(tmp_path)
    r = run_stop(payload, data)
    assert r.returncode == 0
    assert r.stdout.strip(), "control: enabled gate should block this case"
    assert json.loads(r.stdout)["decision"] == "block"


def test_stop_gate_noops_with_env_disabled(tmp_path):
    data, payload = _ship_state_case(tmp_path)
    r = _run(STOP, payload, data, env_extra={"PROOFGATE_DISABLED": "1"})
    assert r.returncode == 0
    assert r.stdout.strip() == "", "disabled gate must not block"


def test_stop_gate_noops_with_sentinel_file(tmp_path):
    data, payload = _ship_state_case(tmp_path)
    (data / "DISABLED").write_text("")
    r = run_stop(payload, data)
    assert r.returncode == 0
    assert r.stdout.strip() == "", "DISABLED sentinel must silence the gate"


# --- mark-dirty (PostToolUse ledger recorder) ------------------------------

def _push_payload():
    return {"session_id": "sess1", "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "tool_response": {"is_error": False}}


def _ledger(data_dir, sid="sess1"):
    p = Path(data_dir) / "ledger" / ("%s.jsonl" % sid)
    return p.read_text() if p.exists() else ""


def test_mark_dirty_records_when_enabled_control(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    r = _run(MARK, _push_payload(), data)
    assert r.returncode == 0
    assert "push" in _ledger(data), "control: enabled recorder writes the push"


def test_mark_dirty_noops_with_env_disabled(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    r = _run(MARK, _push_payload(), data, env_extra={"PROOFGATE_DISABLED": "1"})
    assert r.returncode == 0
    assert _ledger(data) == "", "disabled recorder must write nothing"


def test_mark_dirty_noops_with_sentinel_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "DISABLED").write_text("")
    r = _run(MARK, _push_payload(), data)
    assert r.returncode == 0
    assert _ledger(data) == "", "DISABLED sentinel must silence the recorder"


# --- gatekeeper (PreToolUse, invoked as a bare .py, no shell wrapper) -------

def _force_push_payload(cwd):
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "cwd": str(cwd),
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin main"}}


def _run_gk(payload, data_dir, env_extra=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["python3", str(GATEKEEPER)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=60)


def test_gatekeeper_denies_when_enabled_control(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    r = _run_gk(_force_push_payload(tmp_path), data)
    assert r.returncode == 0
    assert r.stdout.strip(), "control: enabled gatekeeper denies a force push"
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gatekeeper_noops_with_env_disabled(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    r = _run_gk(_force_push_payload(tmp_path), data,
                env_extra={"PROOFGATE_DISABLED": "1"})
    assert r.returncode == 0
    assert r.stdout.strip() == "", "disabled gatekeeper must not deny"


def test_gatekeeper_noops_with_sentinel_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "DISABLED").write_text("")
    r = _run_gk(_force_push_payload(tmp_path), data)
    assert r.returncode == 0
    assert r.stdout.strip() == "", "DISABLED sentinel must silence the gatekeeper"


# --- is_disabled helper unit ------------------------------------------------

def test_is_disabled_helper(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pg_common_kill", ROOT / "scripts" / "verify-gate" / "pg_common.py")
    pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg)
    assert pg.is_disabled(str(tmp_path)) is False
    (tmp_path / "DISABLED").write_text("")
    assert pg.is_disabled(str(tmp_path)) is True
