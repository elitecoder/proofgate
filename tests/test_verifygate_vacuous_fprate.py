"""Committed false-positive measurement for the vacuous_test trigger.

proofgate's identity is "measure before you ship a trigger". This test pins
the strong-claim regex's benign fire-rate against a corpus of legitimate
"done" messages (tests/corpus/benign_claims.txt) so a future loosening of the
pattern that reintroduces noise fails CI. The corpus marks two lines as
genuine end-to-end validations (they SHOULD fire and clear via real-path
evidence at the gate); every other line is benign and must NOT fire.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "corpus" / "benign_claims.txt"

# Lines tagged here are intentional, correct end-to-end claims: the trigger
# may fire (they clear at the gate via real-path evidence). Match by a stable
# substring so corpus edits stay readable.
LEGIT_E2E_MARKERS = (
    "ran the Playwright e2e suite end-to-end",
    "Validated end-to-end in the browser",
)


def _load_sg():
    spec = importlib.util.spec_from_file_location(
        "sg_fp", str(ROOT / "scripts" / "verify-gate" / "stop_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _corpus_lines():
    return [ln.strip() for ln in CORPUS.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def _is_legit(line):
    return any(m in line for m in LEGIT_E2E_MARKERS)


def test_strong_claim_trigger_zero_benign_fires():
    sg = _load_sg()
    lines = _corpus_lines()
    benign = [ln for ln in lines if not _is_legit(ln)]
    assert len(benign) >= 30, "corpus too small to be meaningful"
    fired = [ln for ln in benign
             if sg.strong_claim_match(ln.replace("’", "'")) is not None]
    rate = 100.0 * len(fired) / len(benign)
    # Shipped-rule discipline: the gatekeeper rules measure well under 1%.
    # Hold this trigger to the same bar on the benign corpus.
    assert rate < 1.0, (
        "benign fire-rate %.1f%% (%d/%d); offenders: %s"
        % (rate, len(fired), len(benign), fired[:5]))


def test_trigger_does_fire_on_genuine_end_to_end_claims():
    """Guards the other direction: the pattern must still catch the claim it
    exists to catch, including the real incident's phrasing."""
    sg = _load_sg()
    incident = ("Validated end-to-end — all 1010 tests pass. The fix is "
                "complete and the real code path is exercised.")
    assert sg.strong_claim_match(incident) is not None
    legit = [ln for ln in _corpus_lines() if _is_legit(ln)]
    fired = sum(1 for ln in legit
                if sg.strong_claim_match(ln) is not None)
    assert fired >= 1, "trigger fired on none of the genuine e2e claims"


def test_negation_prefix_suppresses_strong_claim():
    """claim_match's negation guard applies to the strong claim too."""
    sg = _load_sg()
    for txt in (
        "I have not validated end-to-end yet.",
        "This still needs to be validated end-to-end.",
        "I will validate end-to-end once the server is up.",
    ):
        assert sg.strong_claim_match(txt) is None, txt
