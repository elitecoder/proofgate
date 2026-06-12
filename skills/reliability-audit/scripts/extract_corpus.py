#!/usr/bin/env python3
"""Extract measurement corpora from Claude Code transcript history.

Reads transcript JSONL files under a Claude projects dir
(default ~/.claude/projects/<project>/<session>.jsonl) and writes:

  <out>/commands.txt   every Bash tool command, one per line
                       (backslash and newline escaped as \\\\ and \\n)
  <out>/prompts.txt    every real user prompt, one per line, same escaping
  <out>/pairs.jsonl    {"project","session","assistant","user"} — the final
                       assistant text before each user message, paired with
                       that user reaction

Stdlib only. All processing is local.
"""

import argparse
import json
import sys
from pathlib import Path

ASSISTANT_CLIP = 2000
USER_CLIP = 1000
NON_PROMPT_PREFIXES = ("<command-", "<local-command", "<system-", "Caveat:")


def escape_line(text):
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p)
    return ""


def is_real_user_prompt(record):
    if record.get("type") != "user" or record.get("isMeta"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
    text = extract_text(content).strip()
    if not text or text.startswith(NON_PROMPT_PREFIXES):
        return False
    return True


def iter_session_files(projects_dir):
    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return
    for project in sorted(projects_dir.iterdir()):
        if not project.is_dir():
            continue
        for f in sorted(project.glob("*.jsonl")):
            yield project.name, f


def iter_records(path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            yield rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    n_cmd = n_prompt = n_pair = 0
    with (out / "commands.txt").open("w") as cmd_fh, (out / "prompts.txt").open(
        "w"
    ) as prompt_fh, (out / "pairs.jsonl").open("w") as pair_fh:
        for project, f in iter_session_files(args.projects_dir):
            last_assistant = ""
            for rec in iter_records(f):
                if rec.get("type") == "assistant":
                    content = (rec.get("message") or {}).get("content")
                    if isinstance(content, list):
                        for block in content:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_use"
                                and block.get("name") == "Bash"
                            ):
                                command = (block.get("input") or {}).get("command")
                                if command:
                                    cmd_fh.write(escape_line(command) + "\n")
                                    n_cmd += 1
                    text = extract_text(content)
                    if text.strip():
                        last_assistant = text
                elif is_real_user_prompt(rec):
                    text = extract_text((rec.get("message") or {}).get("content")).strip()
                    prompt_fh.write(escape_line(text) + "\n")
                    n_prompt += 1
                    if last_assistant:
                        pair_fh.write(
                            json.dumps(
                                {
                                    "project": project,
                                    "session": f.stem,
                                    "assistant": last_assistant[:ASSISTANT_CLIP],
                                    "user": text[:USER_CLIP],
                                }
                            )
                            + "\n"
                        )
                        n_pair += 1
                        last_assistant = ""

    print("commands: %d  prompts: %d  pairs: %d  out: %s" % (n_cmd, n_prompt, n_pair, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
