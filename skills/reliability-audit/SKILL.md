---
name: reliability-audit
description: Mine the user's own ~/.claude/projects transcript history for recurring failure patterns and propose measured, data-backed gate rules. Use when the user asks where Claude wastes their time, wants evidence-based rules, or invokes /proofgate:reliability-audit. All processing is local; nothing leaves the machine.
---

# reliability-audit

Mine the user's own session history, cluster the pain, propose rules, and MEASURE every rule before installing it. All processing is local — never upload, summarize-to-API, or paste transcript content anywhere outside this machine.

## 1. Set up

```sh
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/proofgate}"
AUDIT="$DATA/audit/$(date +%Y-%m-%d)"
mkdir -p "$AUDIT"
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/skills/reliability-audit/scripts"
```

## 2. Triage sessions by frustration

```sh
python3 "$SCRIPTS/triage.py" --out "$AUDIT" --top 10
```

Reads `~/.claude/projects` (override with `--projects-dir`). Writes `$AUDIT/report.tsv` (one row per session, sorted by frustration score) and `$AUDIT/digest_*.md` for the top sessions.

## 3. Read the top digests

Read each `digest_*.md`. For every flagged exchange note: what the user corrected, what the assistant had just claimed or done, and whether a mechanical gate could have caught it.

## 4. Cluster pain points

Group findings into named clusters (e.g. "claimed done without running tests", "destructive command without asking", "ignored explicit instruction"). For each cluster: session count, 1-2 short paraphrased examples. Do not paste long transcript excerpts.

## 5. Build the measurement corpus

```sh
python3 "$SCRIPTS/extract_corpus.py" --out "$AUDIT"
```

Writes `commands.txt` (every Bash command ever run, one per line, newlines escaped as `\n`), `prompts.txt`, `pairs.jsonl` (final assistant text → next user reaction).

## 6. Draft candidate rules

For each cluster that maps to a blockable command shape, draft `rules.local.tsv` lines (format: `id<TAB>tool<TAB>scope<TAB>pattern<TAB>action<TAB>reason`, tool lowercase — see the codify skill). Optionally hand-label a small `labeled_bad.txt` (one known-bad command per line) from digest evidence.

## 7. Measure BEFORE installing — non-negotiable

```sh
python3 "$SCRIPTS/eval_rules.py" --rules "$AUDIT/candidate.tsv" \
  --benign "$AUDIT/commands.txt" --bad "$AUDIT/labeled_bad.txt" --samples 5
```

Patterns are matched anchored at the head of each pipeline segment (comments and heredoc bodies stripped), the same discipline the gate uses. For each rule inspect:

- benign fire-rate: reject above ~1% unless every fired sample shown is genuinely dangerous — read the samples, do not trust the number alone.
- recall on labeled-bad: below 100% means the pattern misses real cases — widen or split it.

## 8. Show measurements, then ask

Present to the user: the clusters, each candidate rule, its measured fire-rate with samples, and its recall. Ask which rules to install. Install nothing without confirmation.

## 9. Install confirmed rules

Install each confirmed rule via `/proofgate:codify` so it gets the deny/near-miss self-test, or append to `$DATA/rules.local.tsv` and self-test manually the same way.

## 10. Report

End with: sessions scanned, clusters found, rules proposed vs installed, and the measured fire-rates. Remind the user the audit artifacts live in `$AUDIT` and never left the machine.
