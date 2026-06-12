"""Shared helpers for injector hook tests, plus script hygiene checks."""
import json
import os
import subprocess
from pathlib import Path

INJECTORS = Path(__file__).resolve().parent.parent / "scripts" / "injectors"
SH_SCRIPTS = [
    "turn-context.sh",
    "notify-throttle.sh",
    "agent-file-lint.sh",
    "scope-budget.sh",
]


def run_hook(script, payload, data_dir=None, env_extra=None, timeout=30):
    """Run an injector hook the way the harness does: JSON on stdin via sh."""
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    if data_dir is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    if env_extra:
        env.update(env_extra)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        ["sh", str(INJECTORS / script)],
        input=stdin.encode(),
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def test_scripts_exist_and_are_executable():
    for name in SH_SCRIPTS:
        path = INJECTORS / name
        assert path.is_file(), name
        assert os.access(path, os.X_OK), name


def test_scripts_pass_posix_sh_syntax_check():
    for name in SH_SCRIPTS:
        proc = subprocess.run(
            ["sh", "-n", str(INJECTORS / name)], capture_output=True
        )
        assert proc.returncode == 0, (name, proc.stderr)


def test_all_scripts_fail_open_on_empty_stdin_and_no_data_dir():
    for name in SH_SCRIPTS:
        proc = run_hook(name, "")
        assert proc.returncode == 0, (name, proc.stderr)


def test_all_scripts_fail_open_on_garbage_stdin():
    for name in SH_SCRIPTS:
        proc = run_hook(name, "{this is : not json", data_dir="/nonexistent/x")
        assert proc.returncode == 0, (name, proc.stderr)
