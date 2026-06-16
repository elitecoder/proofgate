"""Failure mode 13 — vacuous / mocked end-to-end claim.

The gate fires only on a STRONG claim ("validated end-to-end", "exercised the
real path") after a production-file edit, and clears on real-path evidence: an
e2e/browser runner, a send-class command, or a prove-cov receipt that covered
an edited production file. A plain unit-test pass does NOT clear it.

Includes the committed baseline-red demonstration: each blocking test is
paired with an assertion that the same transcript is ALLOWED with the tier in
its shipped-default (off) state — proof the gate, not the transcript, is what
catches the incident.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_verifygate_helpers import (
    ROOT, block_of, make_transcript, run_prove_cov, run_stop, stop_payload)

sys.path.insert(0, str(ROOT / "scripts" / "verify-gate"))
import pg_common as pg  # noqa: E402

# vacuous_test ON, llm_judge OFF — these tests target the deterministic vacuous
# tier in isolation; the default gate config now leads with the LLM judge, which
# would shell to a real model and judge the text instead.
VAC_ON = {"gates": {"vacuous_test": True, "llm_judge": False}}


def _data(tmp_path, cfg=None):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    if cfg is not None:
        (d / "config.json").write_text(json.dumps(cfg))
    return d


def _write_receipt(dd, cwd, rec):
    rdir = Path(dd) / "ledger" / "receipts"
    rdir.mkdir(parents=True, exist_ok=True)
    key = pg.cwd_key(str(cwd))
    with open(rdir / (key + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# The real incident, reconstructed: edit production code, run a (mocked) unit
# suite that passes, claim validated end-to-end.
INCIDENT = [
    ("edit", "src/mutation_probe.py"),
    ("bash", "python3 -m pytest -q"),
    ("text", "Validated end-to-end — all 1010 tests pass. "
             "The fix is complete and the real code path is exercised."),
]


# --- baseline-red: the gap is real ----------------------------------------

def test_incident_allowed_when_tier_off_is_shipped_default(tmp_path):
    """BASELINE-RED. With the vacuous tier off (and the LLM judge off so no
    model is called), the real incident stops cleanly — exactly the gap that
    motivated mode 13."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    dd = _data(tmp_path, {"gates": {"vacuous_test": False, "llm_judge": False}})
    assert block_of(run_stop(stop_payload(tr, w), dd)) is None


def test_incident_blocked_when_tier_on(tmp_path):
    """AFTER. With the tier enabled, the same transcript is blocked."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON)))
    assert out is not None
    assert "mutation_probe.py" in out["reason"]
    assert "real path" in out["reason"]
    assert "UNVERIFIED" in out["reason"]


# --- the trigger: strong claim vs ordinary claim --------------------------

def test_plain_tests_pass_claim_does_not_trigger_vacuous_tier(tmp_path):
    """An ordinary "all tests pass" claim is NOT a strong claim; the
    vacuous tier leaves it to the checkable-claim tier. With a post-edit run
    present, the stop is allowed."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/app.py"),
        ("bash", "pytest -q"),
        ("text", "All tests pass. The refactor is complete."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is None


def test_strong_claim_without_prod_edit_does_not_fire(tmp_path):
    """No production file edited this session -> nothing to validate against;
    the tier stays silent even on a strong claim."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "tests/test_app.py"),
        ("bash", "pytest -q"),
        ("text", "Validated end-to-end; the real code path is exercised."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is None


def test_honest_unit_phrasing_is_allowed(tmp_path):
    """The escape that is NOT UNVERIFIED: say plainly it was unit-only."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "python3 -m pytest -q"),
        ("text", "Unit tests pass — 1010 green. The subprocess boundary is "
                 "mocked; I have not run the real binary."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is None


def test_unverified_escape_allows(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "python3 -m pytest -q"),
        ("text", "UNVERIFIED: validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is None


# --- clearing on real-path evidence ---------------------------------------

def test_e2e_run_clears_strong_claim(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "npx playwright test"),
        ("text", "Validated end-to-end — the real browser path passes."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is None


def test_unit_runner_alone_does_not_clear_strong_claim(tmp_path):
    """The crux: pytest is a unit runner, not real-path evidence. A strong
    claim backed only by it is blocked."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "python3 -m pytest -q"),
        ("text", "Validated end-to-end; exercised the real subprocess path."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON)))
    assert out is not None


def test_e2e_run_before_prod_edit_does_not_clear(tmp_path):
    """A real-path run that happened BEFORE the production edit proves nothing
    about the edited code; the strong claim is still blocked."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("bash", "npx playwright test"),       # real run, but...
        ("edit", "src/mutation_probe.py"),     # ...edit comes after it
        ("text", "Validated end-to-end — the real browser path passes."),
    ])
    out = block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON)))
    assert out is not None


def test_cov_receipt_covering_edited_file_clears(tmp_path):
    """A prove-cov receipt whose covered set intersects the edited production
    files clears the gate. Synthetic receipt -> no coverage.py dependency."""
    dd = _data(tmp_path, VAC_ON)
    w = tmp_path / "w"
    w.mkdir()
    _write_receipt(dd, w, {
        "claim": "end-to-end", "cmd": "pytest -q", "exit": 0, "sha": None,
        "cwd": str(w), "ts": 9e9, "covered": ["src/mutation_probe.py"]})
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    assert block_of(run_stop(stop_payload(tr, w), dd)) is None


def test_cov_receipt_for_other_file_does_not_clear(tmp_path):
    """A coverage receipt that covered a DIFFERENT file does not vouch for the
    edited one."""
    dd = _data(tmp_path, VAC_ON)
    w = tmp_path / "w"
    w.mkdir()
    _write_receipt(dd, w, {
        "claim": "end-to-end", "cmd": "pytest -q", "exit": 0, "sha": None,
        "cwd": str(w), "ts": 9e9, "covered": ["src/unrelated.py"]})
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    assert block_of(run_stop(stop_payload(tr, w), dd)) is not None


def test_cov_receipt_same_basename_other_dir_does_not_clear(tmp_path):
    """Adversarial C1: a coverage receipt for pkg_b/mutation_probe.py must NOT
    clear an edit to pkg_a/mutation_probe.py — same basename, different file."""
    dd = _data(tmp_path, VAC_ON)
    w = tmp_path / "w"
    w.mkdir()
    _write_receipt(dd, w, {
        "claim": "end-to-end", "cmd": "x", "exit": 0, "sha": None,
        "cwd": str(w), "ts": 9e9,
        "covered": ["pkg_b/mutation_probe.py"]})
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "pkg_a/mutation_probe.py"),
        ("text", "Validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), dd)) is not None


def test_cov_receipt_path_suffix_does_clear(tmp_path):
    """The flip side: a receipt for the absolute path of the edited file (a
    path-suffix match) clears it."""
    dd = _data(tmp_path, VAC_ON)
    w = tmp_path / "w"
    w.mkdir()
    _write_receipt(dd, w, {
        "claim": "end-to-end", "cmd": "x", "exit": 0, "sha": None,
        "cwd": str(w), "ts": 9e9,
        "covered": [str(w / "pkg_a" / "mutation_probe.py")]})
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "pkg_a/mutation_probe.py"),
        ("text", "Validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), dd)) is None


def test_noop_e2e_token_does_not_clear(tmp_path):
    """Adversarial H3: appending `npx playwright --version` must not clear the
    claim — it ran no suite."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "npx playwright --version"),
        ("text", "Validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is not None


def test_send_command_alone_does_not_clear(tmp_path):
    """Adversarial H3: a curl no longer clears an end-to-end claim — it proves
    a request was made, not that the edited code produced it."""
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", "src/mutation_probe.py"),
        ("bash", "curl -X POST https://example.com/run -d '{}'"),
        ("text", "Validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), _data(tmp_path, VAC_ON))) is not None


def test_plain_prove_receipt_without_coverage_does_not_clear(tmp_path):
    """A bare `prove` receipt (no 'covered' key) is exit-0 evidence, not
    real-path evidence; it must not clear the vacuous tier."""
    dd = _data(tmp_path, VAC_ON)
    w = tmp_path / "w"
    w.mkdir()
    _write_receipt(dd, w, {
        "claim": "tests pass", "cmd": "pytest -q", "exit": 0, "sha": None,
        "cwd": str(w), "ts": 9e9})  # no "covered"
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    assert block_of(run_stop(stop_payload(tr, w), dd)) is not None


# --- config / fail-open ----------------------------------------------------

def test_tier_off_explicitly_allows_incident(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    tr = make_transcript(tmp_path / "t.jsonl", INCIDENT)
    dd = _data(tmp_path, {"gates": {"vacuous_test": False, "llm_judge": False}})
    assert block_of(run_stop(stop_payload(tr, w), dd)) is None


# --- prove-cov integration (needs coverage.py) -----------------------------

def _coverage_python():
    """A python that can `import coverage`, or None. Prefer the test runner's
    own interpreter; fall back to a system python3."""
    for py in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if not py:
            continue
        r = subprocess.run([py, "-c", "import coverage"],
                           capture_output=True)
        if r.returncode == 0:
            return py
    return None


coverage_required = pytest.mark.skipif(
    _coverage_python() is None,
    reason="coverage.py not importable; run with `uv run --with coverage`")


@coverage_required
def test_prove_cov_records_covered_file(tmp_path):
    """prove-cov runs a real command under coverage and records the file it
    executed. Uses a bin/ shim so `coverage` resolves to the interpreter that
    has it installed."""
    py = _coverage_python()
    w = tmp_path / "w"
    w.mkdir()
    (w / "thing.py").write_text(
        "def build_cmd(name):\n    return ['echo', name]\n\n"
        "print(build_cmd('hi'))\n")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "coverage").write_text(
        "#!/bin/sh\nexec %s -m coverage \"$@\"\n" % py)
    (shim / "coverage").chmod(0o755)
    dd = tmp_path / "data"
    r = run_prove_cov(["end-to-end", "thing.py", "--", "python3", "thing.py"],
                      w, dd, extra_path=shim)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PROVED" in r.stdout
    rdir = dd / "ledger" / "receipts"
    recs = [json.loads(l) for p in rdir.glob("*.jsonl")
            for l in p.read_text().splitlines() if l.strip()]
    assert len(recs) == 1
    # The receipt records the REAL measured path (not the name passed), so a
    # same-basename file elsewhere can't later satisfy the gate (C1 fix).
    assert len(recs[0]["covered"]) == 1
    assert recs[0]["covered"][0].endswith("/thing.py")
    assert os.path.isabs(recs[0]["covered"][0])


@coverage_required
def test_prove_cov_refuses_when_named_file_not_executed(tmp_path):
    py = _coverage_python()
    w = tmp_path / "w"
    w.mkdir()
    (w / "thing.py").write_text("print('ran')\n")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "coverage").write_text(
        "#!/bin/sh\nexec %s -m coverage \"$@\"\n" % py)
    (shim / "coverage").chmod(0o755)
    dd = tmp_path / "data"
    r = run_prove_cov(["e2e", "never.py", "--", "python3", "thing.py"],
                      w, dd, extra_path=shim)
    assert r.returncode != 0
    assert "PROVE FAILED" in r.stdout
    assert not (dd / "ledger" / "receipts").exists() or not list(
        (dd / "ledger" / "receipts").glob("*.jsonl"))


@coverage_required
def test_prove_cov_receipt_clears_gate_end_to_end(tmp_path):
    """Full loop: edit prod file, prove-cov it under coverage, then the gate
    accepts the end-to-end claim. The 'receipts-or-it-didn't-happen' path,
    proven against real coverage data rather than a synthetic receipt."""
    py = _coverage_python()
    w = tmp_path / "w"
    w.mkdir()
    (w / "mutation_probe.py").write_text(
        "def build_cmd(n):\n    return ['echo', n]\n\nprint(build_cmd('x'))\n")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "coverage").write_text(
        "#!/bin/sh\nexec %s -m coverage \"$@\"\n" % py)
    (shim / "coverage").chmod(0o755)
    dd = _data(tmp_path, VAC_ON)
    r = run_prove_cov(
        ["end-to-end", "mutation_probe.py", "--", "python3", "mutation_probe.py"],
        w, dd, extra_path=shim)
    assert r.returncode == 0, r.stdout + r.stderr
    tr = make_transcript(tmp_path / "t.jsonl", [
        ("edit", str(w / "mutation_probe.py")),
        ("text", "Validated end-to-end against the real path."),
    ])
    assert block_of(run_stop(stop_payload(tr, w), dd)) is None
