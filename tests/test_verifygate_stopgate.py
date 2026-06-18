"""Stop-gate tier tests: ship-state, checkable claims, red-green ledger,
promissory endings, deferrals, loop guard, config, fail-open."""
import json

from test_verifygate_helpers import (
    block_of, ledger_lines, make_repo, make_transcript, run_mark, run_prove,
    run_stop, stop_payload)


def _data(tmp_path):
    # Seed a DETERMINISTIC-mode config: llm_judge OFF, the keyword tiers ON.
    # The default gate config now leads with the LLM judge (which would shell
    # out to a real model — neither hermetic nor what these tier tests target),
    # so the deterministic-tier tests pin the gate into pure-deterministic mode.
    # The llm_judge tier has its own tests that opt in with a stub command.
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(json.dumps({"gates": {
        "llm_judge": False,
        "checkable_claim": True,
        "promissory": True,
        "ship_state": True,
        "red_green": True,
        "deferral": True,
    }}))
    return d


# --- fail open -------------------------------------------------------------

def test_fail_open_on_garbage_stdin(tmp_path):
    r = run_stop(None, _data(tmp_path), raw="{{{ not json")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_on_missing_transcript(tmp_path):
    p = stop_payload(tmp_path / "nope.jsonl", tmp_path)
    r = run_stop(p, _data(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_on_garbage_transcript_lines(tmp_path):
    tr = tmp_path / "t.jsonl"
    tr.write_text("not json\n{\"type\": 3}\n[1,2]\n")
    r = run_stop(stop_payload(tr, tmp_path), _data(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --- tier a: ship-state ----------------------------------------------------

def test_ship_state_blocks_on_unpushed_commits(tmp_path):
    repo = make_repo(tmp_path, unpushed=1)
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "git commit -m 'fix'"),
        ("text", "Done — committed and pushed to origin."),
    ])
    out = block_of(run_stop(stop_payload(tr, repo), _data(tmp_path)))
    assert out is not None
    assert "git push" in out["reason"]


def test_ship_state_skips_outside_git_repo(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "git commit -m 'fix'"),
        ("bash", "git push origin main"),
        ("text", "Committed and pushed."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


def test_pushed_claim_verified_by_clean_upstream_state(tmp_path):
    repo = make_repo(tmp_path, unpushed=0)
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Everything is pushed and the branch is in sync."),
    ])
    out = block_of(run_stop(stop_payload(tr, repo), _data(tmp_path)))
    assert out is None


# --- tier b: checkable claims ----------------------------------------------

def test_pushed_claim_without_evidence_blocks(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've pushed the fix."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "push" in out["reason"]
    assert "UNVERIFIED" in out["reason"]


def test_grep_about_push_is_not_evidence(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", 'grep -rn "git push" docs/'),
        ("text", "I've pushed the changes."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "push" in out["reason"]


def test_failed_push_command_is_not_evidence(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "git push origin main", False),
        ("text", "Pushed the branch."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None


def test_sent_claim_without_send_command_blocks(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've sent the report to the team."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "send" in out["reason"]


def test_sent_claim_with_send_command_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "curl -X POST https://example.com/hook -d '{}'"),
        ("text", "I've sent the report to the team."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


def test_tests_pass_claim_with_stale_run_blocks(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "pytest -q"),
        ("edit", "src/app.py"),
        ("text", "All tests pass."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "test" in out["reason"]


def test_tests_pass_claim_with_run_after_edit_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/app.py"),
        ("bash", "pytest -q"),
        ("text", "All tests pass."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


def test_sent_claim_verified_by_prior_turn_ledger_allows(tmp_path):
    # Cross-turn / post-compaction: the send ran in an earlier turn (recorded
    # to the durable ledger by mark_dirty), but the current transcript — after
    # context compaction — no longer contains that tool_use. The claim must
    # still clear off the ledger, not block a legitimate summary.
    w = tmp_path / "w"
    w.mkdir()
    data = _data(tmp_path)
    # Turn 1: a send-class command runs and is recorded to the ledger.
    run_mark({
        "session_id": "sess1", "cwd": str(w),
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "tool_input": {"command": "gh pr comment 11577 --body hi"},
        "tool_response": {},
    }, data)
    # Turn 2 (compacted): transcript holds only the summary claim, no tool_use.
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've posted the comment on the PR."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), data))
    assert out is None


def test_pushed_claim_verified_by_prior_turn_ledger_allows(tmp_path):
    # Same cross-turn story for the push class: a clean upstream isn't the only
    # escape — a ledgered push from an earlier turn also clears the claim.
    repo = make_repo(tmp_path, unpushed=1)  # upstream NOT clean, so only the
    data = _data(tmp_path)                  # ledger can verify the claim.
    run_mark({
        "session_id": "sess1", "cwd": str(repo),
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "tool_input": {"command": "git push origin HEAD"},
        "tool_response": {},
    }, data)
    tr = make_transcript(tmp_path / "t2.jsonl", [
        ("text", "Pushed the branch."),
    ])
    # checkable_claim's push branch keys on "push" in session_classes; the
    # ledgered push now satisfies it regardless of upstream count.
    out = block_of(run_stop(stop_payload(tr, repo), data))
    assert out is None


def test_unverified_escape_always_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "UNVERIFIED: I've sent the report and pushed the fix."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


def test_negated_claim_does_not_fire(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Note: the changes are not pushed yet. The commit is "
                 "local only."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


# --- tier c: red-green ledger ----------------------------------------------

def test_red_green_blocks_then_prove_receipt_clears(tmp_path):
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    run_mark({
        "session_id": "sess1", "cwd": str(w),
        "hook_event_name": "PostToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": str(w / "tests" / "test_app.py"),
                       "old_string": "a", "new_string": "b"},
    }, dd)
    assert any(e["kind"] == "edit" and e["test"] for e in ledger_lines(dd))
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Refactored the helpers."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is not None
    assert "prove" in out["reason"]

    r = run_prove(["tests pass after edits", "--", "echo", "3 passed"], w, dd)
    assert r.returncode == 0

    out2 = block_of(run_stop(stop_payload(tr, w), dd))
    assert out2 is None


def test_red_green_cleared_by_ledgered_test_run(tmp_path):
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    edit = {
        "session_id": "sess1", "cwd": str(w),
        "hook_event_name": "PostToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": str(w / "tests" / "test_app.py"),
                       "old_string": "a", "new_string": "b"},
    }
    run_mark(edit, dd)
    run_mark({
        "session_id": "sess1", "cwd": str(w),
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "tool_response": {"stdout": "3 passed", "stderr": ""},
    }, dd)
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Refactored the helpers."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is None


def _write_ledger(dd, lines, sid="sess1"):
    p = dd / "ledger" / ("%s.jsonl" % sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(line) + "\n" for line in lines))


def test_red_green_is_per_file_not_global_max(tmp_path):
    # A coverage session edits test_a.py and proves it green, then much later
    # edits test_b.py once more without re-running. The OLD global-max design
    # took max(all test-edit ts) and re-flagged BOTH files; only test_b is
    # genuinely unproven. The per-file evaluation must clear test_a and block
    # only test_b. Ledger written directly for deterministic ts ordering.
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    a = str(w / "tests" / "test_a.py")
    b = str(w / "tests" / "test_b.py")
    _write_ledger(dd, [
        {"ts": 100.0, "kind": "edit", "path": a, "test": True},
        {"ts": 110.0, "kind": "test_run", "ok": True, "cmd": "pytest test_a"},
        {"ts": 200.0, "kind": "edit", "path": b, "test": True},
    ])
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Refactored both suites."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is not None
    assert b in out["reason"]
    assert a not in out["reason"]  # proven green in an earlier turn — cleared


def test_red_green_cross_turn_green_clears_later_untouched_files(tmp_path):
    # The reported firefly false positive: every test file was edited and
    # proven green across earlier turns; the final summary restates "tests
    # pass". A green run that post-dates EVERY test edit clears all of them,
    # even though the runs happened in compacted-away earlier turns.
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    a = str(w / "tests" / "test_a.py")
    b = str(w / "tests" / "test_b.py")
    _write_ledger(dd, [
        {"ts": 100.0, "kind": "edit", "path": a, "test": True},
        {"ts": 150.0, "kind": "edit", "path": b, "test": True},
        {"ts": 200.0, "kind": "test_run", "ok": True, "cmd": "pytest -q"},
    ])
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "All suites green; done."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is None


# --- tier d: promissory ending ---------------------------------------------

def test_promissory_ending_blocks(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Done with the refactor. I'll run the tests and report "
                 "back."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "promise" in out["reason"]


def test_promissory_with_question_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I can run the tests next. Want me to proceed?"),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


# --- tier e: deferral ------------------------------------------------------

def test_deferral_without_artifact_blocks(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've deferred the flaky-test fix as a follow-up."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is not None
    assert "defer" in out["reason"].lower()


def test_deferral_with_gh_issue_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "gh issue create -t 'flaky test' -b 'details'"),
        ("text", "I've deferred the flaky-test fix as a follow-up."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path)))
    assert out is None


# --- loop guard ------------------------------------------------------------

def test_loop_guard_allows_after_two_blocks(tmp_path):
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've sent the report to the team."),
    ])
    sid = "loop1"
    assert block_of(run_stop(stop_payload(tr, w, sid=sid), dd)) is not None
    assert block_of(
        run_stop(stop_payload(tr, w, sid=sid, active=True), dd)) is not None
    assert block_of(
        run_stop(stop_payload(tr, w, sid=sid, active=True), dd)) is None


def test_loop_guard_resets_on_new_turn(tmp_path):
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've sent the report to the team."),
    ])
    sid = "loop2"
    # Use up the per-turn budget.
    assert block_of(run_stop(stop_payload(tr, w, sid=sid), dd)) is not None
    assert block_of(
        run_stop(stop_payload(tr, w, sid=sid, active=True), dd)) is not None
    assert block_of(
        run_stop(stop_payload(tr, w, sid=sid, active=True), dd)) is None
    # Fresh turn: stop_hook_active false resets the counter.
    assert block_of(run_stop(stop_payload(tr, w, sid=sid), dd)) is not None


def test_loop_guard_allows_when_counter_untracked(tmp_path):
    # stop_hook_active with no readable counter must allow: a gate that
    # cannot count its own blocks must never risk a stop loop.
    dd = _data(tmp_path)
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've sent the report to the team."),
    ])
    out = block_of(run_stop(stop_payload(tr, w, sid="fresh", active=True), dd))
    assert out is None


# --- config ----------------------------------------------------------------

def test_config_can_disable_a_tier(tmp_path):
    dd = _data(tmp_path)
    # promissory ON would block this ending; disabling it (with llm_judge off so
    # no model is called) must let it pass — proves the config overlay works.
    (dd / "config.json").write_text(json.dumps(
        {"gates": {"llm_judge": False, "promissory": False}}))
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Done with the refactor. I'll run the tests and report "
                 "back."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is None


def test_promissory_on_blocks_when_explicitly_enabled(tmp_path):
    dd = _data(tmp_path)  # _data seeds promissory: True, llm_judge: False
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "Done with the refactor. I'll run the tests and report "
                 "back."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is not None  # promissory tier fires


# --- tier: llm_judge (stubbed model — hermetic) ----------------------------

def _llm_data(tmp_path, judge_cmd):
    # llm_judge ON, keyword tiers OFF, with a STUB command standing in for the
    # model so the test never calls a real LLM.
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(json.dumps({
        "llm_judge_cmd": judge_cmd,
        "gates": {"llm_judge": True, "checkable_claim": False,
                  "promissory": False, "ship_state": False,
                  "red_green": False, "deferral": False},
    }))
    return d


def test_llm_judge_block_verdict_blocks(tmp_path):
    dd = _llm_data(tmp_path, "printf 'BLOCK no merge command ran this session'")
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is not None
    assert "no merge command" in out["reason"]


def test_llm_judge_pass_verdict_allows(tmp_path):
    dd = _llm_data(tmp_path, "printf 'PASS'")
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I reviewed the auth flow; the bug is in token refresh."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is None


def test_llm_judge_fails_open_on_model_error(tmp_path):
    # A non-zero / empty model call must not wedge the session.
    dd = _llm_data(tmp_path, "exit 3")
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    out = block_of(run_stop(stop_payload(tr, w), dd))
    assert out is None


def test_block_output_shape(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I've sent the report."),
    ])
    r = run_stop(stop_payload(tr, w), _data(tmp_path))
    obj = json.loads(r.stdout)
    assert set(obj.keys()) == {"decision", "reason"}
    assert obj["decision"] == "block"
    assert obj["reason"]
