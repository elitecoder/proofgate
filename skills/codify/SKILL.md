---
name: codify
description: Turn a user correction into a durable, enforced rule with a mechanical self-test. Use when the user corrects behavior and wants it to stick, says "never do X again" / "always Y", or invokes /proofgate:codify <rule>.
---

# codify

Convert the correction in `$ARGUMENTS` into enforced state. Never edit gatekeeper.py or any plugin script — rules live in data files only.

## 1. Resolve the data dir

```sh
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/proofgate}"
mkdir -p "$DATA"
```

The fallback matches the gatekeeper hook's own fallback. Always pass `CLAUDE_PLUGIN_DATA="$DATA"` when invoking gatekeeper so both sides read the same rules file.

## 2. Classify the rule

- Mechanically checkable (maps to a tool call pattern: a command shape, a file path, a flag) → go to step 3.
- Not mechanically checkable (style, tone, ordering preferences, judgment calls) → go to step 6.

## 3. Normalize into a TSV rule line

Append one line to `$DATA/rules.local.tsv` — tab-separated, exactly 6 columns, create with header comment if absent:

```
# id	tool	scope	pattern	action	reason
npm-publish-ask	bash	*	npm\s+publish\b	ask	codified <YYYY-MM-DD>: confirm with the user before publishing
```

- `id`: unique kebab-case name. A row whose id matches a shipped default replaces that default.
- `tool`: `bash`, `edit`, or `any` — lowercase, nothing else parses.
- `scope`: regex searched against the session cwd; `*` for everywhere.
- `pattern`: regex. For `bash` it is matched ANCHORED at the start of each segment's command head (comments and heredoc bodies stripped, wrappers unwrapped) — write it from the command word out, with lookaheads for flags (e.g. `git\s+push(?=.*\s--force\b)`), never as a bare substring. For `edit` it is searched in the file path and the added text.
- `action`: `deny`, `ask`, or `require-token` (human mints a token via `bin/pg-grant`).
- `reason`: shown when the rule fires; `{head}` expands to the matched head.

Pick the narrowest pattern that still covers the correction. Prefer `ask` over `deny` when the user said "check with me", `deny` when they said "never".

## 4. SELF-TEST — mandatory, both directions

Construct a command the rule MUST catch, pipe a synthetic PreToolUse event through the gatekeeper, and confirm the decision:

```sh
python3 -c 'import json,sys; print(json.dumps({"session_id":"selftest","transcript_path":"/dev/null","cwd":".","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' \
  "npm publish" \
  | CLAUDE_PLUGIN_DATA="$DATA" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gatekeeper/gatekeeper.py"
```

Require output containing `"permissionDecision": "ask"` (or `"deny"`, matching the rule's action).

Then construct a NEAR-MISS — a benign command that merely mentions the dangerous one (e.g. `grep -rn "npm publish" docs/`) — and confirm the gatekeeper prints nothing.

Show the user both raw outputs, labeled "blocked as expected" and "near-miss allowed".

## 5. On self-test failure

- Must-catch case produced no decision → check the column count (6, tab-separated) and the lowercase tool name first — malformed rows are silently skipped; then tighten the pattern. Replace the line you just added, do not stack duplicates, retest.
- Near-miss denied → the pattern is too broad; narrow it and retest.
- Do not declare the rule codified until both checks pass.

## 6. Untestable rules

Not mechanically enforceable → it does not go in the gate. Append to the project's `CLAUDE.md` (or `~/.claude/CLAUDE.md` if the user says it applies everywhere):

```
- <YYYY-MM-DD>: <imperative rule, one line>
```

Tell the user explicitly: "This rule is not mechanically enforceable, so it went to CLAUDE.md as prose the harness injects each session. Prose decays — re-run /proofgate:codify if a mechanical version becomes possible."

## 7. Report

End with: the exact line appended, which file it landed in, and the two self-test results (or the untestable notice).
