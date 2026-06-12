#!/usr/bin/env python3
"""Measure candidate gate rules against real command corpora.

For each rule in a rules.local.tsv (gatekeeper schema — columns: id, tool,
scope, pattern, action, reason; '#' lines skipped) measure:

  - benign fire-rate: % of benign corpus commands the pattern fires on
  - recall: % of labeled-bad commands the pattern fires on (with --bad)

Matching mirrors the gate's command-HEAD discipline: each command is split
into pipeline segments (| || && ; and newlines), heredoc bodies and comments
are stripped, leading env assignments and wrappers (sudo, env, nohup, time,
command) are removed, then the rule pattern is matched ANCHORED at the start
of each segment. Substring hits inside greps/heredocs about a dangerous
command therefore do not fire. Subshell bodies ($(...), backticks) are not
expanded.

Corpus files are one command per line with \\n / \\\\ escaping as produced
by extract_corpus.py. Stdlib only; all processing is local.
"""

import argparse
import re
import sys
from pathlib import Path

SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\||\n")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
TRAILING_COMMENT = re.compile(r"(^|\s)#.*$")
HEREDOC_OPEN = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
WRAPPERS = ("sudo ", "env ", "nohup ", "time ", "command ")
CONTROL_PREFIXES = ("if ", "then ", "elif ", "else ", "while ", "until ", "do ", "for ")


def unescape_line(s):
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(s[i])
        i += 1
    return "".join(out)


def strip_heredocs(command):
    out_lines = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        out_lines.append(line)
        m = HEREDOC_OPEN.search(line)
        if m:
            tag = m.group(1)
            i += 1
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
        i += 1
    return "\n".join(out_lines)


def segments(command):
    """Yield comment-stripped, wrapper-stripped segment heads."""
    command = strip_heredocs(command)
    cleaned_lines = [TRAILING_COMMENT.sub(r"\1", ln) for ln in command.split("\n")]
    for seg in SEGMENT_SPLIT.split("\n".join(cleaned_lines)):
        seg = seg.strip().lstrip("({ ")
        changed = True
        while changed and seg:
            changed = False
            for prefix in CONTROL_PREFIXES + WRAPPERS:
                if seg.startswith(prefix):
                    seg = seg[len(prefix):].lstrip()
                    changed = True
            m = ENV_ASSIGN.match(seg)
            if m:
                seg = seg[m.end():]
                changed = True
        if seg and not seg.startswith("#"):
            yield seg


def rule_fires(rx, command):
    return any(rx.match(seg) for seg in segments(command))


def load_corpus(path):
    return [unescape_line(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


# Runtime-state guards cannot be evaluated against an offline corpus; strip
# the prefix and measure the pattern alone (an upper bound on the fire-rate).
GUARD_PREFIXES = ("outside-cwd-tmp:", "dirty-repo:")


def load_rules(path):
    rules = []
    for raw in Path(path).read_text().splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 6:
            print("skipping malformed rule line (need 6 columns: id, tool, "
                  "scope, pattern, action, reason): %r" % line, file=sys.stderr)
            continue
        pattern = cols[3]
        for g in GUARD_PREFIXES:
            if pattern.startswith(g):
                print("rule %s: guard %r ignored for offline measurement"
                      % (cols[0].strip(), g.rstrip(":")), file=sys.stderr)
                pattern = pattern[len(g):]
                break
        rules.append(
            {"id": cols[0].strip(), "tool": cols[1].strip(), "scope": cols[2].strip(),
             "pattern": pattern, "action": cols[4].strip()}
        )
    return rules


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rules", required=True, help="candidate rules.local.tsv")
    ap.add_argument("--benign", required=True, help="benign command corpus (commands.txt)")
    ap.add_argument("--bad", help="labeled-bad commands, one per line")
    ap.add_argument("--samples", type=int, default=3, help="benign fired samples to show per rule")
    args = ap.parse_args(argv)

    benign = load_corpus(args.benign)
    bad = load_corpus(args.bad) if args.bad else []
    rules = load_rules(args.rules)

    print(
        "rule\ttool\taction\tbenign_fired\tbenign_total\tfire_rate_pct\tbad_hit\tbad_total\trecall_pct\tpattern"
    )
    for idx, rule in enumerate(rules, start=1):
        if rule["tool"].lower() not in ("bash", "any"):
            print(
                "%d\t%s\t%s\t-\t-\t-\t-\t-\t-\t%s  (non-Bash rule: not measurable on a command corpus)"
                % (idx, rule["tool"], rule["action"], rule["pattern"])
            )
            continue
        try:
            rx = re.compile(rule["pattern"])
        except re.error as exc:
            print("%d\t%s\t%s\t-\t-\t-\t-\t-\t-\tINVALID REGEX: %s" % (idx, rule["tool"], rule["action"], exc))
            continue

        fired = [c for c in benign if rule_fires(rx, c)]
        rate = 100.0 * len(fired) / len(benign) if benign else 0.0
        bad_hits = [c for c in bad if rule_fires(rx, c)]
        recall = 100.0 * len(bad_hits) / len(bad) if bad else 0.0
        print(
            "%d\t%s\t%s\t%d\t%d\t%.2f\t%d\t%d\t%.1f\t%s"
            % (
                idx,
                rule["tool"],
                rule["action"],
                len(fired),
                len(benign),
                rate,
                len(bad_hits),
                len(bad),
                recall,
                rule["pattern"],
            )
        )
        for sample in fired[: args.samples]:
            print("  sample: %s" % " ".join(sample.split())[:200])
        if bad and len(bad_hits) < len(bad):
            for miss in [c for c in bad if not rule_fires(rx, c)][: args.samples]:
                print("  missed-bad: %s" % " ".join(miss.split())[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
