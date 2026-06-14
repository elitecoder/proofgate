"""Checks for the public-facing docs: README, failure-modes, architecture, CHANGELOG."""
import re
from pathlib import Path

import pytest

from privacy_tokens import load_forbidden

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
FAILURE_MODES = ROOT / "docs" / "failure-modes.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
CHANGELOG = ROOT / "CHANGELOG.md"
OWNED = [README, FAILURE_MODES, ARCHITECTURE, CHANGELOG]

# Privacy hard requirement: no trace of the source corpus's origin (employer,
# repo names, coworkers, hostnames). The denylist is private and loaded from
# an uncommitted local file (see privacy_tokens.py); the scan skips when it
# is absent so the public test suite stays green for contributors.
FORBIDDEN = load_forbidden()
needs_denylist = pytest.mark.skipif(
    not FORBIDDEN, reason="local privacy denylist absent (pre-publish machine only)"
)

HYPE = ["revolutionary", "game-chang", "blazingly", "turbocharge", "unleash"]


def scan(text, tokens):
    low = text.lower()
    return [needle for needle in tokens if needle in low]


def test_scanner_logic_on_synthetic_tokens():
    tokens = ["acme-corp", "jdoe"]
    assert scan("rm -rf my-app && git push origin workspace-7", tokens) == []
    # case-insensitive and substring (not word-boundary) matching
    assert "acme-corp" in scan("deploy to Acme-Corp-prod now", tokens)
    assert "jdoe" in scan("prefixjdoesuffix", tokens)


@needs_denylist
def test_denylist_is_normalized():
    assert all(t and t == t.lower() for t in FORBIDDEN)


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_files_exist_nonempty(path):
    assert path.is_file(), f"missing {path}"
    assert len(path.read_text()) > 200, f"{path} suspiciously short"


@needs_denylist
@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_forbidden_strings(path):
    hits = scan(path.read_text(), FORBIDDEN)
    assert hits == [], f"forbidden strings {hits} in {path}"


@pytest.mark.parametrize("path", OWNED, ids=lambda p: p.name)
def test_no_hype_words(path):
    low = path.read_text().lower()
    hits = [h for h in HYPE if h in low]
    assert hits == [], f"hype words {hits} in {path}"


def test_cmux_only_in_commented_example_rules():
    # cmux is allowed solely as a commented-out example rule line.
    for path in OWNED:
        for line in path.read_text().splitlines():
            if "cmux" in line.lower():
                assert line.lstrip().startswith("#"), f"uncommented cmux mention in {path}: {line!r}"


def test_readme_required_content():
    text = README.read_text()
    for needle in [
        "/plugin marketplace add elitecoder/proofgate",
        "/plugin install proofgate@proofgate",
        "/plugin uninstall proofgate@proofgate",
        "5,020", "93,931", "6,400", "13,107", "540",
        "0.73%", "26–55%",
        "config.json",
        "rules.local.tsv",
        "/reliability-audit", "/codify", "/defer", "/repro-test",
        "pg-grant",
        # every shipped intervention must be disclosed
        "turn-context", "notify-throttle", "scope-budget", "agent-file-lint",
    ]:
        assert needle in text, f"README missing {needle!r}"
    for heading in ["## Install", "## Roadmap", "## Is this for you", "## What's in the box"]:
        assert heading in text, f"README missing heading {heading!r}"
    # off-by-default LLM judge must be stated
    assert re.search(r"off by default", text, re.I)
    assert '"llm_judge": false' in text


def test_failure_modes_has_exactly_13():
    text = FAILURE_MODES.read_text()
    modes = re.findall(r"^### \d+\.", text, re.M)
    assert len(modes) == 13, f"expected 13 numbered modes, found {len(modes)}"
    assert [int(m.split()[1].rstrip(".")) for m in modes] == list(range(1, 14))


def test_failure_modes_each_names_a_proofgate_piece():
    text = FAILURE_MODES.read_text()
    sections = re.split(r"^### \d+\.", text, flags=re.M)[1:]
    assert len(sections) == 13
    for i, sec in enumerate(sections, 1):
        assert "**proofgate piece.**" in sec, f"mode {i} missing proofgate piece"


def test_architecture_essentials():
    text = ARCHITECTURE.read_text()
    for needle in [
        "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT",
        "defaults.tsv", "rules.local.tsv",
        "require-token", "tokens/", "ledger/receipts",
        "PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit",
        "permissionDecision", '"decision":"block"',
        "TTY", "eval_rules.py",
    ]:
        assert needle in text, f"architecture.md missing {needle!r}"
    assert re.search(r"fail[- ]open", text, re.I)
    # never re-document features that do not exist in the code
    for phantom in ["measure.py", "args_regex", "defers.jsonl", "uses_left",
                    "receipts.jsonl"]:
        assert phantom not in text, f"architecture.md documents phantom {phantom!r}"
    # rules schema: documented example lines must be real 6-column TSV rows
    block = re.search(r"```\n(# id\ttool.*?)```", text, re.S)
    assert block, "rules example block missing or not tab-separated"
    rows = [l for l in block.group(1).splitlines() if l and not l.startswith("#")]
    assert rows, "rules example has no live rows"
    for row in rows:
        cols = row.split("\t")
        assert len(cols) == 6, f"rule row not 6 tab-separated columns: {row!r}"
        assert cols[1] in {"bash", "edit", "any"}, f"bad tool in {row!r}"
        assert cols[4] in {"deny", "ask", "require-token"}, f"bad action in {row!r}"


def test_changelog_initial_release():
    text = CHANGELOG.read_text()
    assert "0.1.0" in text
    for needle in ["verify-gate", "gatekeeper", "pg-grant", "/reliability-audit",
                   "require-token"]:
        assert needle in text, f"CHANGELOG missing {needle!r}"
    # the action set has no 'allow'
    assert "deny/ask/allow" not in text
