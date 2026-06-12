"""Tests for scripts/injectors/agent-file-lint.sh (PostToolUse Edit|Write)."""
from test_injectors_common import run_hook

SCRIPT = "agent-file-lint.sh"


def write(path, content):
    return {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": path, "content": content},
    }


def edit(path, new_string, old_string="old"):
    return {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": path,
            "old_string": old_string,
            "new_string": new_string,
        },
    }


def test_rationale_marker_in_claude_md_blocks():
    proc = run_hook(
        SCRIPT,
        write("/repo/CLAUDE.md", "Run tests first.\nWe do this because CI is slow.\n"),
    )
    assert proc.returncode == 2
    err = proc.stderr.decode()
    assert "line 2" in err
    assert "because" in err


def test_clean_what_style_content_passes():
    proc = run_hook(
        SCRIPT, write("/repo/CLAUDE.md", "Run `make test` before commit.\nUse tabs.\n")
    )
    assert proc.returncode == 0
    assert proc.stderr == b""


def test_non_target_file_ignored():
    proc = run_hook(
        SCRIPT, write("/repo/README.md", "This exists because users asked for it.")
    )
    assert proc.returncode == 0


def test_long_line_in_skill_md_blocks():
    long_line = "do the thing " * 25  # > 250 chars
    proc = run_hook(SCRIPT, write("/repo/skills/foo/SKILL.md", long_line))
    assert proc.returncode == 2
    assert b"max 250" in proc.stderr


def test_line_at_exactly_250_chars_passes():
    proc = run_hook(SCRIPT, write("/repo/AGENTS.md", "x" * 250))
    assert proc.returncode == 0


def test_line_at_251_chars_blocks():
    proc = run_hook(SCRIPT, write("/repo/AGENTS.md", "x" * 251))
    assert proc.returncode == 2


def test_marker_inside_fenced_code_block_ignored():
    content = "Run checks.\n```python\n# because of caching\nx = 1\n```\nDone.\n"
    proc = run_hook(SCRIPT, write("/repo/CLAUDE.md", content))
    assert proc.returncode == 0


def test_marker_after_fence_closes_blocks():
    content = "```\nsafe because quoted\n```\nreal text because reasons\n"
    proc = run_hook(SCRIPT, write("/repo/CLAUDE.md", content))
    assert proc.returncode == 2
    assert "line 4" in proc.stderr.decode()


def test_background_colon_flagged():
    proc = run_hook(SCRIPT, write("/x/.claude/commands/go.md", "Background: legacy migration"))
    assert proc.returncode == 2


def test_background_without_colon_not_flagged():
    proc = run_hook(SCRIPT, write("/repo/AGENTS.md", "Run the job in the background."))
    assert proc.returncode == 0


def test_word_boundary_no_false_positive():
    # 'becauses'/'why weights' must not trip the word-boundary markers
    proc = run_hook(SCRIPT, write("/repo/AGENTS.md", "why weights matter; becauses\n"))
    assert proc.returncode == 0


def test_for_posterity_and_rationale_flagged():
    proc = run_hook(
        SCRIPT, write("/repo/AGENTS.md", "Rationale: speed.\nkept for posterity\n")
    )
    assert proc.returncode == 2
    err = proc.stderr.decode()
    assert "line 1" in err and "line 2" in err


def test_runner_md_and_prompts_dir_are_targets():
    assert run_hook(SCRIPT, write("/repo/ci-runner.md", "because x")).returncode == 2
    assert run_hook(SCRIPT, write("/repo/prompts/sys.md", "because x")).returncode == 2


def test_dot_claude_dir_is_target():
    assert run_hook(SCRIPT, write("/r/.claude/agents/a.md", "because x")).returncode == 2


def test_edit_new_string_is_linted():
    proc = run_hook(SCRIPT, edit("/repo/CLAUDE.md", "added because of reasons"))
    assert proc.returncode == 2


def test_edit_old_string_is_not_linted():
    proc = run_hook(
        SCRIPT, edit("/repo/CLAUDE.md", "Run lint.", old_string="kept because legacy")
    )
    assert proc.returncode == 0


def test_other_tools_ignored():
    payload = write("/repo/CLAUDE.md", "because x")
    payload["tool_name"] = "Read"
    assert run_hook(SCRIPT, payload).returncode == 0


def test_missing_fields_fail_open():
    assert run_hook(SCRIPT, {"tool_name": "Write"}).returncode == 0
    assert run_hook(SCRIPT, {"tool_name": "Write", "tool_input": {}}).returncode == 0
