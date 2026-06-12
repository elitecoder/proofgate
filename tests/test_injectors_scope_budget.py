"""Tests for scripts/injectors/scope-budget.sh (PostToolUse Edit|Write)."""
import json
import subprocess

from test_injectors_common import run_hook

SCRIPT = "scope-budget.sh"


def payload(cwd, sid="s1", tool="Edit"):
    return {
        "session_id": sid,
        "cwd": str(cwd),
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(cwd) + "/f0.txt", "new_string": "x"},
    }


def make_repo(tmp_path, n_files):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    set_file_count(repo, n_files)
    return repo


def set_file_count(repo, n_files):
    for f in repo.glob("f*.txt"):
        f.unlink()
    for i in range(n_files):
        (repo / ("f%d.txt" % i)).write_text("x")


def assert_silent(proc):
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""


def assert_block(proc, count, threshold):
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert str(count) in out["reason"]
    assert str(threshold) in out["reason"]
    assert "scope inventory" in out["reason"]
    return out


def test_below_first_threshold_is_silent(tmp_path):
    repo = make_repo(tmp_path, 11)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=tmp_path / "data"))


def test_first_crossing_of_12_blocks_once(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    assert_block(run_hook(SCRIPT, payload(repo), data_dir=data), 12, 12)
    # same threshold never re-fires in the session
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))
    set_file_count(repo, 15)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))


def test_each_threshold_fires_once(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    assert_block(run_hook(SCRIPT, payload(repo), data_dir=data), 12, 12)
    set_file_count(repo, 30)
    assert_block(run_hook(SCRIPT, payload(repo), data_dir=data), 30, 30)
    set_file_count(repo, 31)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))
    set_file_count(repo, 60)
    assert_block(run_hook(SCRIPT, payload(repo), data_dir=data), 60, 60)
    set_file_count(repo, 99)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))


def test_jump_past_all_thresholds_blocks_once(tmp_path):
    repo = make_repo(tmp_path, 61)
    data = tmp_path / "data"
    assert_block(run_hook(SCRIPT, payload(repo), data_dir=data), 61, 60)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))


def test_count_falling_back_below_does_not_refire(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    run_hook(SCRIPT, payload(repo), data_dir=data)
    set_file_count(repo, 5)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))
    set_file_count(repo, 13)
    assert_silent(run_hook(SCRIPT, payload(repo), data_dir=data))


def test_sessions_tracked_independently(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    assert_block(run_hook(SCRIPT, payload(repo, sid="a"), data_dir=data), 12, 12)
    assert_block(run_hook(SCRIPT, payload(repo, sid="b"), data_dir=data), 12, 12)


def test_non_git_cwd_is_silent(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    for i in range(20):
        (plain / ("f%d.txt" % i)).write_text("x")
    assert_silent(run_hook(SCRIPT, payload(plain), data_dir=tmp_path / "data"))


def test_missing_cwd_is_silent(tmp_path):
    assert_silent(
        run_hook(SCRIPT, payload(tmp_path / "nope"), data_dir=tmp_path / "data")
    )


def test_no_data_dir_is_silent(tmp_path):
    repo = make_repo(tmp_path, 20)
    assert_silent(run_hook(SCRIPT, payload(repo)))


def test_write_tool_also_counted_other_tools_ignored(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    ignored = payload(repo, tool="Bash")
    assert_silent(run_hook(SCRIPT, ignored, data_dir=data))
    assert_block(
        run_hook(SCRIPT, payload(repo, tool="Write"), data_dir=data), 12, 12
    )


def test_corrupt_state_file_fails_open(tmp_path):
    repo = make_repo(tmp_path, 12)
    data = tmp_path / "data"
    run_hook(SCRIPT, payload(repo), data_dir=data)
    for f in (data / "state").iterdir():
        f.write_text("not json")
    # state lost: re-fires rather than crashing
    proc = run_hook(SCRIPT, payload(repo), data_dir=data)
    assert proc.returncode == 0
