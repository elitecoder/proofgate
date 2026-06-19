"""Stop-gate tests: the gate renders the session transcript (tool calls AND
their real outputs) and hands it to an LLM judge. These tests stub the model
with a shell command so they are hermetic — no real LLM call — and assert both
the gate's control flow (PASS/BLOCK/fail-open/loop-cap/UNVERIFIED/disabled) and,
critically, that the judge actually RECEIVES the tool output (the regression
the mechanical-ledger design got wrong: it judged "tests pass" against a lossy
classified ledger and never saw "4 passed").
"""
import json

from test_verifygate_helpers import (
    make_transcript, run_stop, stop_payload)


def _data(tmp_path, cfg):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))
    return d


def _judge(verdict_cmd, **gates):
    """Config with a STUB judge command standing in for the model."""
    g = {"llm_judge": True}
    g.update(gates)
    return {"llm_judge_cmd": verdict_cmd, "gates": g}


# A stub that always blocks / always passes.
BLOCK_CMD = "printf 'BLOCK the merge claim is unsupported'"
PASS_CMD = "printf 'PASS'"
# A stub that captures the prompt it was given (the rendered trace) to a file,
# then passes — lets a test assert WHAT the judge saw.
def _capture_cmd(outfile):
    return "cat > %s; printf 'PASS'" % outfile


# --- fail open -------------------------------------------------------------

def test_fail_open_on_garbage_stdin(tmp_path):
    r = run_stop(None, _data(tmp_path, _judge(BLOCK_CMD)), raw="{{{ not json")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_on_missing_transcript(tmp_path):
    p = stop_payload(tmp_path / "nope.jsonl", tmp_path)
    r = run_stop(p, _data(tmp_path, _judge(BLOCK_CMD)))
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_on_model_error(tmp_path):
    # A non-zero / empty model call must not wedge the session.
    dd = _data(tmp_path, _judge("exit 3"))
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_empty_summary_is_skipped(tmp_path):
    # No final assistant text -> nothing to judge -> never calls the model.
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [("bash", "ls", True, "files")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.stdout.strip() == ""


# --- verdict plumbing ------------------------------------------------------

def test_block_verdict_blocks_with_reason(tmp_path):
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "merge claim is unsupported" in out["reason"]
    assert out["reason"].startswith("LLM verify-gate:")


def test_pass_verdict_allows(tmp_path):
    dd = _data(tmp_path, _judge(PASS_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "I reviewed the auth flow; the bug is in token refresh.")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.stdout.strip() == ""


def test_block_output_shape(tmp_path):
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "I've sent the report.")])
    obj = json.loads(run_stop(stop_payload(tr, tmp_path), dd).stdout)
    assert set(obj.keys()) == {"decision", "reason"}
    assert obj["decision"] == "block"
    assert obj["reason"]


# --- the regression that motivated this redesign --------------------------
# The judge MUST receive the real tool output. The old gate handed it a lossy
# classified ledger and blocked honest "specs green" reports because no
# "test_run" was recorded. Here the stub captures its prompt; we assert the
# Playwright pass line and the command both reached the model.

def test_judge_receives_tool_output_and_command(tmp_path):
    cap = tmp_path / "seen.txt"
    dd = _data(tmp_path, _judge(_capture_cmd(str(cap))))
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "pnpm test:e2e auth.spec.ts", True,
         "Running 4 tests...\n  4 passed (48.6s)\n"),
        ("text", "All 4 auth specs are green."),
    ])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.stdout.strip() == ""  # stub PASSes
    seen = cap.read_text()
    assert "4 passed (48.6s)" in seen, "test output never reached the judge"
    assert "pnpm test:e2e" in seen, "the command never reached the judge"
    assert "All 4 auth specs are green." in seen, "summary missing from prompt"


def test_judge_receives_final_summary_and_trace_sections(tmp_path):
    cap = tmp_path / "seen.txt"
    dd = _data(tmp_path, _judge(_capture_cmd(str(cap))))
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "git push origin main", True, "main -> main"),
        ("text", "Pushed to origin."),
    ])
    run_stop(stop_payload(tr, tmp_path), dd)
    seen = cap.read_text()
    assert "SESSION TRACE" in seen
    assert "git push origin main" in seen
    assert "main -> main" in seen


# --- UNVERIFIED escape hatch ----------------------------------------------

def test_unverified_prefix_skips_judge(tmp_path):
    # A summary containing UNVERIFIED never reaches the model (and a BLOCK stub
    # therefore cannot fire).
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("text", "UNVERIFIED: I believe the PR is merged.")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.stdout.strip() == ""


# --- disabled (the operator's off state) -----------------------------------

def test_disabled_gate_is_a_noop(tmp_path):
    dd = _data(tmp_path, {"gates": {"llm_judge": False},
                          "llm_judge_cmd": BLOCK_CMD})
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    r = run_stop(stop_payload(tr, tmp_path), dd)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --- loop guard ------------------------------------------------------------

def test_loop_guard_allows_after_two_blocks(tmp_path):
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    # A turn's FIRST Stop arrives with active=False (resets the counter to 0,
    # then blocks -> 1); the harness re-invokes with active=True after each
    # block. The third invocation reads count==2 and must allow.
    seen_block = 0
    for i in range(3):
        active = i != 0
        out = run_stop(stop_payload(tr, tmp_path, active=active), dd).stdout.strip()
        if out:
            seen_block += 1
    assert seen_block == 2, "gate must cap at MAX_BLOCKS_PER_TURN (2)"


def test_loop_guard_resets_on_new_turn(tmp_path):
    dd = _data(tmp_path, _judge(BLOCK_CMD))
    tr = make_transcript(tmp_path / "t.jsonl", [("text", "Merged the PR.")])
    # active=False marks a fresh turn and resets the counter to 0.
    first = run_stop(stop_payload(tr, tmp_path, active=False), dd).stdout.strip()
    assert first  # blocks on the fresh turn
    # exhaust the cap
    run_stop(stop_payload(tr, tmp_path, active=True), dd)
    capped = run_stop(stop_payload(tr, tmp_path, active=True), dd).stdout.strip()
    assert capped == ""  # hit the cap
    # a new turn resets
    again = run_stop(stop_payload(tr, tmp_path, active=False), dd).stdout.strip()
    assert again
