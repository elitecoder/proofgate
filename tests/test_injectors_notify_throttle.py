"""Tests for scripts/injectors/notify-throttle.sh (PreToolUse Bash)."""
import json
import time

from test_injectors_common import run_hook

SCRIPT = "notify-throttle.sh"


def bash(cmd, sid="s1"):
    return {
        "session_id": sid,
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
    }


def assert_allowed(proc):
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""


def assert_denied(proc):
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "ONE rollup" in hso["permissionDecisionReason"]
    return hso


def test_first_notify_allowed(tmp_path):
    assert_allowed(run_hook(SCRIPT, bash('notify-send "build done"'), data_dir=tmp_path))


def test_second_notify_within_window_denied(tmp_path):
    run_hook(SCRIPT, bash('notify-send "step 1 done"'), data_dir=tmp_path)
    proc = run_hook(SCRIPT, bash('notify-send "step 2 done"'), data_dir=tmp_path)
    assert_denied(proc)


def test_sessions_are_isolated(tmp_path):
    run_hook(SCRIPT, bash("notify-send a", sid="s1"), data_dir=tmp_path)
    assert_allowed(run_hook(SCRIPT, bash("notify-send b", sid="s2"), data_dir=tmp_path))


def test_action_payload_bypasses_throttle(tmp_path):
    run_hook(SCRIPT, bash("notify-send warming-up"), data_dir=tmp_path)
    proc = run_hook(
        SCRIPT, bash('notify-send "ACTION: review the failing deploy"'), data_dir=tmp_path
    )
    assert_allowed(proc)


def test_window_expiry_allows_again(tmp_path):
    run_hook(SCRIPT, bash("notify-send first"), data_dir=tmp_path)
    state_files = list((tmp_path / "state").iterdir())
    assert len(state_files) == 1
    state_files[0].write_text(json.dumps({"last_ts": time.time() - 301}))
    assert_allowed(run_hook(SCRIPT, bash("notify-send second"), data_dir=tmp_path))


def test_grep_about_notify_command_not_matched(tmp_path):
    run_hook(SCRIPT, bash("notify-send armed"), data_dir=tmp_path)
    proc = run_hook(SCRIPT, bash('grep -r "notify-send" docs/'), data_dir=tmp_path)
    assert_allowed(proc)


def test_echo_of_head_string_not_matched(tmp_path):
    run_hook(SCRIPT, bash("notify-send armed"), data_dir=tmp_path)
    assert_allowed(run_hook(SCRIPT, bash('echo "notify-send hi"'), data_dir=tmp_path))


def test_heredoc_body_not_matched(tmp_path):
    run_hook(SCRIPT, bash("notify-send armed"), data_dir=tmp_path)
    cmd = "cat <<'EOF' > notes.txt\nnotify-send hello\nEOF"
    assert_allowed(run_hook(SCRIPT, bash(cmd), data_dir=tmp_path))


def test_comment_line_not_matched(tmp_path):
    run_hook(SCRIPT, bash("notify-send armed"), data_dir=tmp_path)
    assert_allowed(run_hook(SCRIPT, bash("# notify-send reminder\nls -la"), data_dir=tmp_path))


def test_chained_segment_is_matched(tmp_path):
    run_hook(SCRIPT, bash("make build && terminal-notifier -message done"), data_dir=tmp_path)
    proc = run_hook(
        SCRIPT, bash("make test; terminal-notifier -message done"), data_dir=tmp_path
    )
    assert_denied(proc)


def test_osascript_notification_matched_dialog_not(tmp_path):
    run_hook(
        SCRIPT,
        bash("osascript -e 'display notification \"done\" with title \"ci\"'"),
        data_dir=tmp_path,
    )
    denied = run_hook(
        SCRIPT,
        bash("osascript -e 'display notification \"again\"'"),
        data_dir=tmp_path,
    )
    assert_denied(denied)
    dialog = run_hook(
        SCRIPT, bash("osascript -e 'display dialog \"pick one\"'"), data_dir=tmp_path
    )
    assert_allowed(dialog)


def test_env_assignment_prefix_still_matched(tmp_path):
    run_hook(SCRIPT, bash("FOO=1 notify-send a"), data_dir=tmp_path)
    assert_denied(run_hook(SCRIPT, bash("BAR=2 notify-send b"), data_dir=tmp_path))


def test_custom_config_heads(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"notify_heads": ["my-notifier send"], "notify_window_seconds": 300})
    )
    run_hook(SCRIPT, bash("my-notifier send hi"), data_dir=tmp_path)
    assert_denied(run_hook(SCRIPT, bash("my-notifier send again"), data_dir=tmp_path))
    # custom list replaces defaults entirely
    assert_allowed(run_hook(SCRIPT, bash("notify-send hi"), data_dir=tmp_path))


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{broken json")
    run_hook(SCRIPT, bash("notify-send a"), data_dir=tmp_path)
    assert_denied(run_hook(SCRIPT, bash("notify-send b"), data_dir=tmp_path))


def test_corrupt_state_file_fails_open(tmp_path):
    run_hook(SCRIPT, bash("notify-send a"), data_dir=tmp_path)
    for f in (tmp_path / "state").iterdir():
        f.write_text("not json")
    assert_allowed(run_hook(SCRIPT, bash("notify-send b"), data_dir=tmp_path))


def test_non_bash_tool_ignored(tmp_path):
    payload = bash("notify-send hi")
    payload["tool_name"] = "Write"
    assert_allowed(run_hook(SCRIPT, payload, data_dir=tmp_path))
    assert_allowed(run_hook(SCRIPT, payload, data_dir=tmp_path))


def test_no_data_dir_allows(tmp_path):
    assert_allowed(run_hook(SCRIPT, bash("notify-send hi")))


def test_unbalanced_quotes_fail_open(tmp_path):
    proc = run_hook(SCRIPT, bash('echo "unterminated'), data_dir=tmp_path)
    assert proc.returncode == 0
