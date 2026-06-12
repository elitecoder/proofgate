"""Tests for the proofgate PreToolUse gatekeeper.

Covers: head-parsing precision (text ABOUT dangerous commands must not fire),
true invocations firing, scope filtering, overlay precedence, token
mint/expire/consume, and fail-open behavior.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GK_PATH = REPO / "scripts" / "gatekeeper" / "gatekeeper.py"
DEFAULTS = REPO / "rules" / "defaults.tsv"
PG_GRANT = REPO / "bin" / "pg-grant"

_spec = importlib.util.spec_from_file_location("proofgate_gatekeeper", GK_PATH)
gk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gk)

FAKE_CWD = "/home/dev/my-app"  # pure path math in guards: need not exist


# ---------------------------------------------------------------------------
# helpers


def default_rules():
    return gk.load_rules(str(DEFAULTS), None)


def run_eval(command=None, cwd=FAKE_CWD, data_dir="/nonexistent-proofgate-data",
             tool="Bash", tool_input=None, ruleset=None):
    payload = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "cwd": cwd,
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"command": command},
    }
    rules = ruleset if ruleset is not None else default_rules()
    return gk.evaluate(payload, rules, data_dir, str(REPO))


def decision(result):
    return result["hookSpecificOutput"]["permissionDecision"] if result else None


def reason(result):
    return result["hookSpecificOutput"]["permissionDecisionReason"]


def write_overlay(tmp_path, text):
    overlay = tmp_path / "rules.local.tsv"
    overlay.write_text(text)
    return gk.load_rules(str(DEFAULTS), str(overlay))


GIT_ENV = dict(os.environ,
               GIT_CONFIG_GLOBAL="/dev/null",
               GIT_CONFIG_SYSTEM="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          env=GIT_ENV, capture_output=True, text=True, timeout=30)


def make_repo(tmp_path, dirty):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "-q").returncode == 0
    (repo / "tracked.txt").write_text("v1\n")
    assert git(repo, "add", ".").returncode == 0
    assert git(repo, "commit", "-q", "-m", "init").returncode == 0
    if dirty:
        (repo / "tracked.txt").write_text("v2 uncommitted\n")
    return repo


def mint_token(data_dir, rid, age_secs=0):
    tok = Path(data_dir) / "tokens" / (rid + ".token")
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(str(int(time.time())))
    if age_secs:
        past = time.time() - age_secs
        os.utime(tok, (past, past))
    return tok


HAVE_GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git not available")


# ---------------------------------------------------------------------------
# head-parsing precision: text ABOUT dangerous commands must not fire


@pytest.mark.parametrize("cmd", [
    "grep -n 'git push --force' src/deploy.sh",
    'grep -rn "rm -rf" docs/',
    'echo "rm -rf /"',
    "printf '%s' 'sudo rm -rf /'",
    "ls -la # cleanup later: rm -rf /opt/x and git push --force",
    "git log --grep='reset --hard' --oneline",
    "cat docs/why-rm-rf-is-dangerous.md",
    "git commit -m 'verify the fix'",          # 'verify' is not --no-verify
    "echo a#b",                                 # '#' mid-token is not a comment
])
def test_text_about_danger_does_not_fire(cmd):
    assert run_eval(cmd) is None


def test_heredoc_body_is_ignored():
    cmd = ("cat <<'EOF' > notes.md\n"
           "recovering after rm -rf /\n"
           "never git push --force\n"
           "EOF\n"
           "echo done")
    assert run_eval(cmd) is None


def test_tab_indented_heredoc_body_is_ignored():
    cmd = "cat <<-EOF\n\trm -rf /etc\n\tEOF"
    assert run_eval(cmd) is None


@pytest.mark.parametrize("cmd", [
    "rm -rf node_modules",                      # relative, inside cwd
    "rm -rf ./build dist",
    "rm -rf /home/dev/my-app/.cache",           # absolute but inside cwd
    "rm -rf /tmp/scratch-dir",                  # temp dir
    "cd /tmp && rm -rf /tmp/build",
    "rm -f notes.txt",                          # not recursive
    "rm -rf \"$BUILD_DIR\"",                    # unresolvable var: fail open
    "git push origin main",
    "git push --force-with-lease origin main",  # the safe alternative
    "git push -u origin feature",
    "chmod 644 README.md",
    "chmod 777 single-file",                    # not recursive
    "chmod -R 755 public/",
    "git clean -n",                             # dry run, no force
])
def test_benign_invocations_do_not_fire(cmd):
    assert run_eval(cmd) is None


# ---------------------------------------------------------------------------
# true invocations fire


@pytest.mark.parametrize("cmd", [
    "rm -rf /etc/nginx",
    "rm -rf ../other-project",
    "sudo rm -rf /var/lib/docker",
    "echo start && rm -rf /opt/data",
    "true; rm --recursive --force /srv/files",
    "rm -fr /opt/x",
    "rm /a /b /opt/x -rf",                      # flags after the 4-token head
    'bash -c "rm -rf /usr/local/stale"',        # nested shell -c
    'echo "snapshot: $(rm -rf /etc/backup)"',   # command substitution executes
    "if true; then rm -rf /opt/x; fi",          # shell keywords stripped
    "env FOO=1 sudo -E rm -rf /opt/x",          # wrapper chain stripped
])
def test_rm_outside_cwd_denied(cmd):
    res = run_eval(cmd)
    assert decision(res) == "deny", cmd
    assert "rm" in reason(res)


def test_rm_tilde_outside_cwd_denied(monkeypatch):
    monkeypatch.setenv("HOME", "/home/other")
    res = run_eval("rm -rf ~/old-stuff")
    assert decision(res) == "deny"


@pytest.mark.parametrize("cmd", [
    "git push --force",
    "git push -f origin main",
    "git push origin main --force",             # flag beyond the 4-token core
    "git push origin +main",                    # force refspec
])
def test_force_push_denied_with_lease_alternative(cmd):
    res = run_eval(cmd)
    assert decision(res) == "deny", cmd
    assert "--force-with-lease" in reason(res)


def test_no_verify_asks():
    res = run_eval("git commit --no-verify -m 'wip'")
    assert decision(res) == "ask"


@pytest.mark.parametrize("cmd", [
    "chmod -R 777 .",
    "chmod 777 -R public/",
    "chmod --recursive 0777 /srv/www",
])
def test_chmod_777_asks(cmd):
    assert decision(run_eval(cmd)) == "ask", cmd


def test_deny_wins_over_ask():
    res = run_eval("git commit --no-verify -m x && git push --force")
    assert decision(res) == "deny"
    assert "--force-with-lease" in reason(res)


# ---------------------------------------------------------------------------
# head extraction unit tests


def test_extract_heads_splits_segments():
    heads = [h for h, _ in gk.extract_heads(
        "echo hi; rm -rf /x | tee log && git push --force")]
    assert any(h.startswith("echo") for h in heads)
    assert any(h.startswith("rm -rf /x") for h in heads)
    assert any(h.startswith("git push --force") for h in heads)


def test_extract_heads_flattened_quotes_cannot_lead_head():
    heads = [h for h, _ in gk.extract_heads("grep -n 'git push --force' f.sh")]
    assert len(heads) == 1
    assert heads[0].startswith("grep")


def test_extract_heads_strips_redirections():
    heads = [h for h, _ in gk.extract_heads("cat > out.txt 2>&1")]
    assert heads == [("cat")]


def test_extract_heads_heredoc_then_live_command():
    heads = [h for h, _ in gk.extract_heads(
        "cat <<EOF && rm -rf /x\nrm -rf /not-real\nEOF")]
    assert any(h.startswith("rm -rf /x") for h in heads)
    assert not any("/not-real" in h for h in heads)


# ---------------------------------------------------------------------------
# scope filtering and overlay precedence


def test_scope_filtering(tmp_path):
    rs = write_overlay(
        tmp_path,
        "tf-apply\tbash\t/work/special\tterraform\\s+apply\tdeny\tNo manual applies here.\n")
    assert decision(run_eval("terraform apply",
                             cwd="/work/special/infra", ruleset=rs)) == "deny"
    assert run_eval("terraform apply", cwd="/home/dev/elsewhere", ruleset=rs) is None


def test_overlay_overrides_default_by_id(tmp_path):
    rs = write_overlay(
        tmp_path,
        "git-push-force\tbash\t*\tgit\\s+push(?=.*--force\\b)\task\tcustom override\n")
    res = run_eval("git push --force", ruleset=rs)
    assert decision(res) == "ask"
    assert "custom override" in reason(res)


def test_malformed_overlay_rows_skipped(tmp_path):
    rs = write_overlay(
        tmp_path,
        "only-three-cols\tbash\t*\n"
        "bad-regex\tbash\t*\t([unclosed\tdeny\tnope\n"
        "bad-action\tbash\t*\tls\texplode\tnope\n"
        "bad-tool\tnotreal\t*\tls\tdeny\tnope\n")
    ids = {r.id for r in rs}
    assert not ids & {"only-three-cols", "bad-regex", "bad-action", "bad-tool"}
    # defaults still intact and working
    assert decision(run_eval("rm -rf /etc/x", ruleset=rs)) == "deny"


# ---------------------------------------------------------------------------
# edit/write rules (engine capability used by overlays)


def test_edit_rule_matches_file_path(tmp_path):
    rs = write_overlay(
        tmp_path,
        "env-files\tedit\t*\t\\.env(\\.|$)\task\tEditing env files needs confirmation.\n")
    res = run_eval(tool="Write", ruleset=rs,
                   tool_input={"file_path": "/home/dev/my-app/.env", "content": "X=1"})
    assert decision(res) == "ask"
    assert run_eval(tool="Write", ruleset=rs,
                    tool_input={"file_path": "/home/dev/my-app/main.py",
                                "content": "print(1)"}) is None


def test_edit_rule_matches_content(tmp_path):
    rs = write_overlay(
        tmp_path,
        "no-keys\tedit\t*\tBEGIN (RSA|OPENSSH) PRIVATE KEY\tdeny\tNever write private key material.\n")
    res = run_eval(tool="Edit", ruleset=rs,
                   tool_input={"file_path": "/home/dev/my-app/cfg.py",
                               "old_string": "x",
                               "new_string": "-----BEGIN RSA PRIVATE KEY-----"})
    assert decision(res) == "deny"


# ---------------------------------------------------------------------------
# require-token: mint / consume / expire (real git repo for the dirty guard)


@needs_git
def test_require_token_denies_without_token(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    res = run_eval("git reset --hard HEAD", cwd=str(repo),
                   data_dir=str(tmp_path / "data"))
    assert decision(res) == "deny"
    assert "pg-grant" in reason(res)
    assert "git-discard-dirty" in reason(res)


@needs_git
def test_token_allows_once_and_is_consumed(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    data = tmp_path / "data"
    tok = mint_token(data, "git-discard-dirty")
    assert run_eval("git reset --hard", cwd=str(repo), data_dir=str(data)) is None
    assert not tok.exists()  # single use
    res = run_eval("git reset --hard", cwd=str(repo), data_dir=str(data))
    assert decision(res) == "deny"


@needs_git
def test_expired_token_rejected_and_removed(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    data = tmp_path / "data"
    tok = mint_token(data, "git-discard-dirty", age_secs=16 * 60)
    res = run_eval("git reset --hard", cwd=str(repo), data_dir=str(data))
    assert decision(res) == "deny"
    assert not tok.exists()


@needs_git
def test_clean_repo_not_gated(tmp_path):
    repo = make_repo(tmp_path, dirty=False)
    assert run_eval("git reset --hard HEAD", cwd=str(repo),
                    data_dir=str(tmp_path / "data")) is None


@needs_git
def test_git_clean_gated_when_dirty(tmp_path):
    repo = make_repo(tmp_path, dirty=False)
    (repo / "untracked.tmp").write_text("x")  # untracked = dirty for clean -f
    res = run_eval("git clean -fd", cwd=str(repo), data_dir=str(tmp_path / "data"))
    assert decision(res) == "deny"
    assert "pg-grant" in reason(res)


def test_reset_hard_outside_git_repo_not_gated():
    # dirty-repo guard fails open when cwd is not a repo
    assert run_eval("git reset --hard", cwd="/home/dev/not-a-repo") is None


def test_guard_exception_fails_open(monkeypatch):
    def boom(args, cwd):
        raise RuntimeError("guard exploded")
    monkeypatch.setitem(gk.GUARD_FNS, "outside-cwd-tmp", boom)
    assert run_eval("rm -rf /etc/x") is None


# ---------------------------------------------------------------------------
# end-to-end through stdin/stdout (subprocess), incl. fail-open


def run_hook(stdin_text, plugin_root=None, data_dir=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root or REPO)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir or "/nonexistent-proofgate-data")
    return subprocess.run([sys.executable, str(GK_PATH)],
                          input=stdin_text, capture_output=True,
                          text=True, env=env, timeout=30)


def payload_json(command, cwd=FAKE_CWD):
    return json.dumps({"session_id": "s1", "cwd": cwd, "tool_name": "Bash",
                       "tool_input": {"command": command}})


def test_e2e_garbage_stdin_fails_open():
    p = run_hook("this is not json {")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_e2e_empty_stdin_fails_open():
    p = run_hook("")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_e2e_missing_rules_fails_open(tmp_path):
    # plugin root without rules/defaults.tsv: gate stays silent
    p = run_hook(payload_json("rm -rf /etc/x"), plugin_root=tmp_path)
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_e2e_deny_output_contract():
    p = run_hook(payload_json("rm -rf /etc/x"))
    assert p.returncode == 0
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]


def test_e2e_benign_command_is_silent():
    p = run_hook(payload_json("ls -la"))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_e2e_overlay_loaded_from_data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "rules.local.tsv").write_text(
        "git-push-force\tbash\t*\tgit\\s+push(?=.*--force\\b)\task\toverlay says ask\n")
    p = run_hook(payload_json("git push --force"), data_dir=data)
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "overlay says ask" in out["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# pg-grant


def run_grant(args, data_dir, with_tty):
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(data_dir))
    cmd = ["/bin/sh", str(PG_GRANT)] + args
    if with_tty:
        controller, follower = os.openpty()
        try:
            p = subprocess.run(cmd, stdin=follower, capture_output=True,
                               text=True, env=env, timeout=15)
        finally:
            os.close(controller)
            os.close(follower)
        return p
    return subprocess.run(cmd, input="", capture_output=True,
                          text=True, env=env, timeout=15)


def test_pg_grant_refuses_without_tty(tmp_path):
    p = run_grant(["git-discard-dirty"], tmp_path / "data", with_tty=False)
    assert p.returncode != 0
    assert "TTY" in p.stderr
    assert not (tmp_path / "data" / "tokens" / "git-discard-dirty.token").exists()


def test_pg_grant_mints_with_tty(tmp_path):
    p = run_grant(["git-discard-dirty"], tmp_path / "data", with_tty=True)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "data" / "tokens" / "git-discard-dirty.token").exists()


def test_pg_grant_rejects_bad_rule_id(tmp_path):
    p = run_grant(["../evil"], tmp_path / "data", with_tty=True)
    assert p.returncode != 0
    assert not (tmp_path / "data" / "tokens").exists()


def test_pg_grant_requires_exactly_one_arg(tmp_path):
    p = run_grant([], tmp_path / "data", with_tty=True)
    assert p.returncode != 0


@needs_git
def test_pg_grant_token_accepted_end_to_end(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    data = tmp_path / "data"
    p = run_grant(["git-discard-dirty"], data, with_tty=True)
    assert p.returncode == 0, p.stderr
    hook = run_hook(payload_json("git reset --hard", cwd=str(repo)), data_dir=data)
    assert hook.returncode == 0
    assert hook.stdout.strip() == ""  # token consumed, command allowed
    assert not (data / "tokens" / "git-discard-dirty.token").exists()
