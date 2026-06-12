"""Tests for scripts/injectors/turn-context.sh (UserPromptSubmit)."""
import re

from test_injectors_common import run_hook

SCRIPT = "turn-context.sh"


def payload(prompt):
    return {
        "session_id": "s1",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    }


def test_base_card_always_emitted():
    proc = run_hook(SCRIPT, payload("how do I sort a list in python?"))
    assert proc.returncode == 0
    out = proc.stdout.decode()
    assert re.search(r"now: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", out)
    assert "register/chat" in out
    assert "WHAT, never WHY" in out
    assert "one rollup per batch" in out
    assert "short version" in out


def test_plain_prompt_gets_no_extra_cards():
    proc = run_hook(SCRIPT, payload("explain how this parser works"))
    out = proc.stdout.decode()
    assert "authority:" not in out
    assert "reconcile:" not in out


def test_register_card_is_four_lines():
    proc = run_hook(SCRIPT, payload("hello"))
    lines = [l for l in proc.stdout.decode().splitlines() if l.startswith("register/")]
    assert len(lines) == 4


def test_direct_order_adds_authority_card():
    proc = run_hook(SCRIPT, payload("stop asking questions and just do it"))
    out = proc.stdout.decode()
    assert "authority:" in out
    assert "overrides standing rules" in out
    assert "reconcile:" not in out


def test_order_pattern_with_curly_apostrophe():
    proc = run_hook(SCRIPT, payload("don’t ask, push the change"))
    assert "authority:" in proc.stdout.decode()


def test_pushback_adds_reconciliation_card():
    proc = run_hook(
        SCRIPT, payload("are you sure? I'm looking at the page and it is blank")
    )
    out = proc.stdout.decode()
    assert "reconcile:" in out
    assert "ground truth" in out


def test_pushback_thats_not_true():
    proc = run_hook(SCRIPT, payload("that's not true, the test still fails"))
    assert "reconcile:" in proc.stdout.decode()


def test_both_cards_can_fire_together():
    proc = run_hook(
        SCRIPT, payload("that's not true. go ahead and rerun it yourself")
    )
    out = proc.stdout.decode()
    assert "authority:" in out
    assert "reconcile:" in out


def test_benign_keyword_mentions_do_not_fire():
    # mentions of similar words in neutral positions must not inject cards
    proc = run_hook(
        SCRIPT,
        payload("write docs about how the scheduler applies retries and merges"),
    )
    out = proc.stdout.decode()
    assert "authority:" not in out
    assert "reconcile:" not in out


def test_token_budget_typical_and_worst_case():
    base = run_hook(SCRIPT, payload("hello")).stdout.decode()
    assert len(base.split()) < 60
    worst = run_hook(
        SCRIPT, payload("that's not true, just do it now")
    ).stdout.decode()
    assert len(worst.split()) < 115  # ~120 token ceiling


def test_malformed_json_fails_open_with_base_card():
    proc = run_hook(SCRIPT, "{not json at all")
    assert proc.returncode == 0
    assert b"register/chat" in proc.stdout


def test_missing_prompt_field_fails_open():
    proc = run_hook(SCRIPT, {"session_id": "s1", "hook_event_name": "UserPromptSubmit"})
    assert proc.returncode == 0
    assert b"register/chat" in proc.stdout
