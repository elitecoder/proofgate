"""mark-dirty ledger recorder tests: head-parsed classes, test paths,
heredoc/grep immunity, fail-open."""
import sys
from pathlib import Path

from test_verifygate_helpers import ROOT, ledger_lines, run_mark

sys.path.insert(0, str(ROOT / "scripts" / "verify-gate"))
import pg_common as pg


def _mark(tmp_path, tool, tool_input, resp=None, sid="sess1"):
    payload = {
        "session_id": sid, "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse", "tool_name": tool,
        "tool_input": tool_input,
    }
    if resp is not None:
        payload["tool_response"] = resp
    r = run_mark(payload, tmp_path / "data")
    assert r.returncode == 0
    return ledger_lines(tmp_path / "data", sid)


def test_edit_of_test_file_recorded_as_test(tmp_path):
    lines = _mark(tmp_path, "Edit",
                  {"file_path": "/x/tests/test_app.py", "old_string": "a",
                   "new_string": "b"})
    assert lines == [lines[0]]
    assert lines[0]["kind"] == "edit"
    assert lines[0]["test"] is True


def test_edit_of_source_file_not_flagged_test(tmp_path):
    lines = _mark(tmp_path, "Write",
                  {"file_path": "/x/src/app.py", "content": "x"})
    assert lines[0]["kind"] == "edit"
    assert lines[0]["test"] is False


def test_git_commit_recorded(tmp_path):
    lines = _mark(tmp_path, "Bash", {"command": "git commit -m 'fix'"})
    assert [e["kind"] for e in lines] == ["git_commit"]


def test_compound_command_records_push(tmp_path):
    lines = _mark(tmp_path, "Bash",
                  {"command": "echo hi && git push origin main"})
    assert [e["kind"] for e in lines] == ["push"]


def test_grep_about_commands_records_nothing(tmp_path):
    lines = _mark(tmp_path, "Bash",
                  {"command": "grep -rn 'git push origin' docs/ "
                              "# curl mail pytest"})
    assert lines == []


def test_heredoc_body_records_nothing(tmp_path):
    cmd = ("cat <<'EOF' > notes.txt\n"
           "git push origin main\n"
           "pytest -q\n"
           "EOF")
    lines = _mark(tmp_path, "Bash", {"command": cmd})
    assert lines == []


def test_failed_bash_recorded_with_ok_false(tmp_path):
    lines = _mark(tmp_path, "Bash", {"command": "pytest -q"},
                  resp={"stdout": "", "stderr": "boom", "is_error": True})
    assert lines[0]["kind"] == "test_run"
    assert lines[0]["ok"] is False


def test_fail_open_on_garbage_stdin(tmp_path):
    r = run_mark(None, tmp_path / "data", raw="][ nope")
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert ledger_lines(tmp_path / "data") == []


def test_uninteresting_tool_records_nothing(tmp_path):
    lines = _mark(tmp_path, "Read", {"file_path": "/x/src/app.py"})
    assert lines == []


# --- head-parser unit checks (imported module) ------------------------------

def test_parse_heads_splits_segments():
    heads = [h for h, _ in pg.parse_heads(
        "FOO=1 sudo git push; pytest -q | tee log && echo done")]
    assert heads == ["git", "pytest", "tee", "echo"]


def test_parse_heads_strips_comments():
    assert [h for h, _ in pg.parse_heads("# git push\necho ok")] == ["echo"]


def test_quoted_separators_do_not_split():
    heads = [h for h, _ in pg.parse_heads('echo "a; git push; b"')]
    assert heads == ["echo"]


def test_classes_for_gh_pr_merge():
    assert "push" in pg.bash_classes("gh pr merge 42 --squash")


def test_classes_for_npm_test():
    assert "test_run" in pg.bash_classes("npm run test:unit")
    assert "test_run" in pg.bash_classes("go test ./...")
    assert "test_run" in pg.bash_classes("python3 -m pytest -q")


def test_e2e_run_class_for_browser_runners():
    for cmd in ("npx playwright test", "playwright test e2e/",
                "npx cypress run", "pnpm e2e", "npm run test:e2e",
                "yarn run e2e:ci"):
        assert "e2e_run" in pg.bash_classes(cmd), cmd


def test_unit_runner_is_not_e2e_run():
    for cmd in ("python3 -m pytest -q", "npm run test:unit", "go test ./...",
                "vitest run", "jest"):
        assert "e2e_run" not in pg.bash_classes(cmd), cmd


def test_e2e_noop_invocations_are_not_e2e_run():
    # A no-op token must not be mistaken for a real suite run (adversarial H3):
    # appending `npx playwright --version` would otherwise clear the gate.
    for cmd in ("npx playwright --version", "npx playwright info",
                "npx cypress version", "cypress info", "playwright --version",
                "npx playwright test --list", "playwright test --dry-run",
                "playwright show-report"):
        assert "e2e_run" not in pg.bash_classes(cmd), cmd


def test_deferral_artifact_classes():
    assert "deferral_artifact" in pg.bash_classes(
        "gh issue create -t flaky")
    assert "deferral_artifact" in pg.bash_classes(
        "echo 'fix later' >> DEFERRALS.md")


def test_is_test_path():
    assert pg.is_test_path("tests/test_app.py")
    assert pg.is_test_path("pkg/foo_test.go")
    assert pg.is_test_path("src/__tests__/app.tsx")
    assert pg.is_test_path("src/app.spec.ts")
    assert pg.is_test_path("Sources/AppTests/FooTests.swift")
    assert not pg.is_test_path("src/app.py")
    assert not pg.is_test_path("docs/contest.py")
