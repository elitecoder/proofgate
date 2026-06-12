"""bin/prove receipt tests."""
import hashlib
import json
from pathlib import Path

from test_verifygate_helpers import make_repo, run_prove


def _receipts(data_dir):
    rdir = Path(data_dir) / "ledger" / "receipts"
    if not rdir.exists():
        return []
    out = []
    for p in sorted(rdir.glob("*.jsonl")):
        for line in p.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                rec["_file"] = p.name
                out.append(rec)
    return out


def test_prove_success_writes_receipt(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    r = run_prove(["tests pass", "--", "echo", "42 passed"], w, dd)
    assert r.returncode == 0
    assert "42 passed" in r.stdout
    assert "PROVED: tests pass" in r.stdout
    recs = _receipts(dd)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["claim"] == "tests pass"
    assert rec["cmd"] == "echo 42 passed"
    assert rec["exit"] == 0
    assert rec["cwd"].endswith("w")
    assert rec["ts"] > 0
    key = hashlib.sha256(rec["cwd"].encode()).hexdigest()[:16]
    assert rec["_file"] == key + ".jsonl"


def test_prove_records_head_sha_in_git_repo(tmp_path):
    repo = make_repo(tmp_path)
    dd = tmp_path / "data"
    r = run_prove(["it works", "--", "echo", "ok"], repo, dd)
    assert r.returncode == 0
    rec = _receipts(dd)[0]
    assert rec["sha"] and len(rec["sha"]) == 40


def test_prove_nonzero_exit_no_receipt(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    r = run_prove(["claim", "--", "sh", "-c", "echo boom; exit 3"], w, dd)
    assert r.returncode != 0
    assert "PROVE FAILED, no receipt" in r.stdout
    assert "boom" in r.stdout
    assert _receipts(dd) == []


def test_prove_empty_output_no_receipt(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    r = run_prove(["claim", "--", "true"], w, dd)
    assert r.returncode != 0
    assert "PROVE FAILED, no receipt" in r.stdout
    assert _receipts(dd) == []


def test_prove_missing_command_no_receipt(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    r = run_prove(["claim", "--", "definitely-not-a-command-xyz"], w, dd)
    assert r.returncode != 0
    assert "PROVE FAILED" in r.stdout
    assert _receipts(dd) == []


def test_prove_usage_errors(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    assert run_prove(["only a claim"], w, dd).returncode != 0
    assert run_prove(["claim", "echo", "ok"], w, dd).returncode != 0
    assert _receipts(dd) == []


def test_prove_appends_multiple_receipts_same_cwd(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    dd = tmp_path / "data"
    assert run_prove(["a", "--", "echo", "1"], w, dd).returncode == 0
    assert run_prove(["b", "--", "echo", "2"], w, dd).returncode == 0
    recs = _receipts(dd)
    assert [r["claim"] for r in recs] == ["a", "b"]
    assert len({r["_file"] for r in recs}) == 1
