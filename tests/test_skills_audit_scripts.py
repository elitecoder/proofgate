"""Smoke tests for the reliability-audit helper scripts on synthetic transcripts."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "reliability-audit" / "scripts"


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def user_line(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def assistant_line(blocks):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": blocks}})


def tool_result_line():
    return json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        }
    )


def build_projects(tmp_path):
    """Two synthetic projects: one frustrated session, one calm session."""
    projects = tmp_path / "projects"

    angry = projects / "-home-user-dev-my-app"
    angry.mkdir(parents=True)
    (angry / "sess-angry.jsonl").write_text(
        "\n".join(
            [
                user_line("please add a logout button"),
                assistant_line(
                    [
                        {"type": "text", "text": "Done. I added the button and pushed the change."},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "git push origin main"},
                        },
                    ]
                ),
                tool_result_line(),
                user_line("no!! you deleted my config file. why did you do that?? revert it"),
                assistant_line(
                    [
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Bash",
                            "input": {"command": "rm -rf node_modules\nnpm install"},
                        },
                        {"type": "text", "text": "Reverted and reinstalled."},
                    ]
                ),
                user_line("<command-name>/clear</command-name>"),
                "not json at all {{{",
            ]
        )
        + "\n"
    )

    calm = projects / "-home-user-dev-calm-lib"
    calm.mkdir(parents=True)
    (calm / "sess-calm.jsonl").write_text(
        "\n".join(
            [
                user_line("rename the helper module please"),
                assistant_line([{"type": "text", "text": "Renamed it and updated imports."}]),
                user_line("thanks, looks good"),
            ]
        )
        + "\n"
    )
    return projects


def test_triage_ranks_frustrated_session_first(tmp_path):
    projects = build_projects(tmp_path)
    out = tmp_path / "audit"
    result = run_script(
        "triage.py", "--projects-dir", str(projects), "--out", str(out), "--top", "5"
    )
    assert result.returncode == 0, result.stderr

    report = (out / "report.tsv").read_text().splitlines()
    assert report[0].startswith("score\t")
    rows = [r.split("\t") for r in report[1:]]
    assert len(rows) == 2
    # Frustrated session first with a strictly higher score.
    assert rows[0][5] == "sess-angry"
    assert int(rows[0][0]) > int(rows[1][0])

    digests = sorted(out.glob("digest_*.md"))
    assert digests, "expected at least one digest"
    top = digests[0].read_text()
    assert "you deleted" in top
    assert "prior-assistant" in top  # context from the preceding assistant claim


def test_triage_empty_projects_dir(tmp_path):
    out = tmp_path / "audit"
    result = run_script(
        "triage.py", "--projects-dir", str(tmp_path / "nope"), "--out", str(out)
    )
    assert result.returncode == 0, result.stderr
    assert (out / "report.tsv").read_text().count("\n") == 1  # header only


def test_extract_corpus_outputs(tmp_path):
    projects = build_projects(tmp_path)
    out = tmp_path / "corpus"
    result = run_script("extract_corpus.py", "--projects-dir", str(projects), "--out", str(out))
    assert result.returncode == 0, result.stderr

    commands = (out / "commands.txt").read_text().splitlines()
    assert "git push origin main" in commands
    # Multi-line command is newline-escaped onto one line.
    assert "rm -rf node_modules\\nnpm install" in commands

    prompts = (out / "prompts.txt").read_text().splitlines()
    assert "please add a logout button" in prompts
    assert "thanks, looks good" in prompts
    assert not any("tool_result" in p for p in prompts)
    assert not any(p.startswith("<command-") for p in prompts)

    pairs = [json.loads(line) for line in (out / "pairs.jsonl").read_text().splitlines()]
    claim_pair = [p for p in pairs if "pushed the change" in p["assistant"]]
    assert claim_pair and claim_pair[0]["user"].startswith("no!! you deleted")


def test_eval_rules_head_matching(tmp_path):
    benign = tmp_path / "benign.txt"
    benign.write_text(
        "\n".join(
            [
                'grep -rn "rm -rf" docs/',  # mention inside args: must not fire
                "echo done # rm -rf /tmp",  # comment: must not fire
                "cat <<EOF\\nrm -rf /\\nEOF",  # heredoc body: must not fire
                "ls -la && git status",
                "rm -rf build",  # genuine hit
            ]
        )
        + "\n"
    )
    bad = tmp_path / "bad.txt"
    bad.write_text("rm -rf /tmp/x\nsudo rm -rf /var/y\ngit stash && rm -rf src\n")
    rules = tmp_path / "rules.tsv"
    rules.write_text(
        "# id\ttool\tscope\tpattern\taction\treason\n"
        "rm-rf\tbash\t*\trm\\s+-rf\\b\tdeny\ttest rule\n"
        "env-edit\tedit\t*\t.*\\.env$\tdeny\tnon-bash rule\n"
        "broken\tbash\t*\t(unclosed\tdeny\tbroken regex\n"
    )

    result = run_script(
        "eval_rules.py",
        "--rules", str(rules),
        "--benign", str(benign),
        "--bad", str(bad),
        "--samples", "5",
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()

    rule1 = next(l for l in lines if l.startswith("1\t")).split("\t")
    assert rule1[3] == "1", "exactly one benign command should fire: " + result.stdout
    assert rule1[4] == "5"
    assert rule1[6] == "3" and rule1[7] == "3", "all labeled-bad must be recalled"
    assert rule1[8] == "100.0"
    assert any(l.strip().startswith("sample: rm -rf build") for l in lines)

    assert any(l.startswith("2\t") and "not measurable" in l for l in lines)
    assert any(l.startswith("3\t") and "INVALID REGEX" in l for l in lines)


def test_eval_rules_reports_misses(tmp_path):
    benign = tmp_path / "benign.txt"
    benign.write_text("ls\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("git push --force origin main\ngit push origin main --force\n")
    rules = tmp_path / "rules.tsv"
    # Pattern requires --force immediately after push: misses the reordered flag.
    rules.write_text("force-push\tbash\t*\tgit push --force\tdeny\tnarrow\n")

    result = run_script(
        "eval_rules.py", "--rules", str(rules), "--benign", str(benign), "--bad", str(bad)
    )
    assert result.returncode == 0, result.stderr
    rule1 = next(l for l in result.stdout.splitlines() if l.startswith("1\t")).split("\t")
    assert rule1[6] == "1" and rule1[7] == "2"
    assert rule1[8] == "50.0"
    assert "missed-bad: git push origin main --force" in result.stdout
