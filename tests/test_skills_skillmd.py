"""Structural and privacy checks for the proofgate skill definitions."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from privacy_tokens import load_forbidden

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
EXPECTED_SKILLS = ["codify", "defer", "repro-test", "reliability-audit"]

# Source-corpus identifiers that must never ship in the public repo. The
# denylist is private and loaded from an uncommitted local file (see
# privacy_tokens.py); the scan skips when it is absent.
FORBIDDEN = load_forbidden()
needs_denylist = pytest.mark.skipif(
    not FORBIDDEN, reason="local privacy denylist absent (pre-publish machine only)"
)

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def skill_files():
    return [SKILLS_DIR / name / "SKILL.md" for name in EXPECTED_SKILLS]


def test_all_skills_exist():
    for path in skill_files():
        assert path.is_file(), "missing %s" % path


def test_frontmatter_has_name_and_description():
    for path in skill_files():
        m = FRONTMATTER_RE.match(path.read_text())
        assert m, "no frontmatter in %s" % path
        fm = m.group(1)
        name = re.search(r"^name:\s*(\S+)", fm, re.MULTILINE)
        desc = re.search(r"^description:\s*(.+)", fm, re.MULTILINE)
        assert name, "no name in %s" % path
        assert desc and len(desc.group(1).strip()) > 20, "weak description in %s" % path
        assert name.group(1) == path.parent.name


def test_audit_scripts_are_stdlib_only():
    allowed = {"argparse", "json", "re", "sys", "pathlib"}
    for script in (SKILLS_DIR / "reliability-audit" / "scripts").glob("*.py"):
        for line in script.read_text().splitlines():
            m = re.match(r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", line)
            if m:
                assert m.group(1) in allowed, "%s imports %s" % (script.name, m.group(1))


@needs_denylist
def test_no_private_identifiers_in_owned_files():
    owned = list(SKILLS_DIR.rglob("*")) + list(REPO_ROOT.glob("tests/test_skills*.py"))
    for path in owned:
        if not path.is_file():
            continue
        text = path.read_text(errors="replace").lower()
        for word in FORBIDDEN:
            assert word not in text, "forbidden string in %s" % path


def test_no_absolute_home_paths_in_owned_files():
    # Files must derive paths from env/HOME, never hardcode a user's home dir.
    rx = re.compile(r"/Users/[a-z]")
    owned = list(SKILLS_DIR.rglob("*.md")) + list(SKILLS_DIR.rglob("*.py"))
    for path in owned:
        assert not rx.search(path.read_text(errors="replace")), "hardcoded home path in %s" % path


def _codify_rule_rows():
    text = (SKILLS_DIR / "codify" / "SKILL.md").read_text()
    return [l for l in text.splitlines() if "\t" in l and not l.lstrip().startswith("#")]


def test_codify_rule_example_matches_gatekeeper_schema():
    rows = _codify_rule_rows()
    assert rows, "codify SKILL.md has no example rule row"
    for row in rows:
        cols = row.split("\t")
        assert len(cols) == 6, "codify example row not 6 columns: %r" % row
        assert cols[1] in {"bash", "edit", "any"}, "bad tool in %r" % row
        assert cols[4] in {"deny", "ask", "require-token"}, "bad action in %r" % row


def test_codify_example_rule_fires_in_gatekeeper(tmp_path):
    """The documented /codify example must actually fire through the real
    gatekeeper when placed in the documented overlay location."""
    row = _codify_rule_rows()[0]
    (tmp_path / "rules.local.tsv").write_text(row + "\n")
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)

    def run(command):
        event = json.dumps({
            "session_id": "selftest", "transcript_path": "/dev/null",
            "cwd": str(tmp_path), "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_input": {"command": command},
        })
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "gatekeeper" / "gatekeeper.py")],
            input=event, capture_output=True, text=True, env=env, timeout=30)

    hit = run("npm publish")
    assert '"permissionDecision": "ask"' in hit.stdout, hit.stdout
    near_miss = run('grep -rn "npm publish" docs/')
    assert near_miss.stdout.strip() == "", near_miss.stdout
