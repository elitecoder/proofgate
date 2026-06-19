# Architecture

proofgate is hooks plus skills plus local state. No daemon, no network, no telemetry.
Hooks are plain POSIX sh entrypoints calling python3 (stdlib only). Hooks write
persistent state to the plugin data directory; nothing is ever written into the
plugin install directory itself.

## Events → scripts → state

```
Claude Code session
│
├── UserPromptSubmit ──────▶ scripts/injectors/turn-context.sh
│     stdin: {prompt, ...}     always emits a UTC timestamp + 4-line register
│                              card (~30 tokens); direct-order prompts get an
│                              authority card, pushback prompts a reconcile card
│
├── PreToolUse ────────────▶ scripts/gatekeeper/gatekeeper.py
│     stdin: {tool_name,       head-parses the command, matches rules/defaults.tsv
│             tool_input}      + $CLAUDE_PLUGIN_DATA/rules.local.tsv, consumes
│                              tokens minted by pg-grant
│                              stdout: {"hookSpecificOutput":{"hookEventName":"PreToolUse",
│                                       "permissionDecision":"deny|ask", ...}}
│                            scripts/injectors/notify-throttle.sh (Bash only)
│                              one notification per window; deny with rollup
│                              instructions otherwise
│
├── PostToolUse ───────────▶ scripts/verify-gate/mark-dirty.sh
│     stdin: {tool_name,       classifies completed commands/edits, appends to
│             tool_input,      the session ledger
│             tool_response} scripts/injectors/agent-file-lint.sh (Edit|Write)
│                              exit 2 + stderr feedback on rationale prose
│                            scripts/injectors/scope-budget.sh (Edit|Write)
│                              {"decision":"block",...} at configurable dirty-file
│                              thresholds (PROOFGATE_SCOPE_BUDGET / config.json)
│
└── Stop ──────────────────▶ scripts/verify-gate/stop-gate.sh
      stdin: {transcript_path} extracts checkable claims from the final assistant
                               message, cross-references the session ledger,
                               prove receipts, and live git state
                               stdout on mismatch: {"decision":"block","reason":"..."}
```

Hooks are declared in `hooks/hooks.json` and reference scripts as
`"${CLAUDE_PLUGIN_ROOT}"/scripts/...` with explicit timeouts. The manifest is
`.claude-plugin/plugin.json`.

### Enablement

Claude Code loads a marketplace plugin's hooks when the plugin resolves to
*enabled*. The resolution is: an explicit `enabledPlugins` entry wins
(`true`/`false`); with no entry, the marketplace plugin's `defaultEnabled` field
decides, and that field defaults to `true`. proofgate's marketplace manifest
(`.claude-plugin/marketplace.json`) sets `"defaultEnabled": false`, so a bare
directory- or GitHub-source registration does **not** auto-enable the plugin —
enabling it is an explicit act (`"enabledPlugins": {"proofgate@proofgate": true}`).
This is what makes uninstall stick: removing the plugin from `enabledPlugins`
(and removing the marketplace registration) leaves nothing that resolves to
enabled. See the README uninstall contract.

## State layout (`$CLAUDE_PLUGIN_DATA`)

The data directory survives plugin updates. Layout:

```
$CLAUDE_PLUGIN_DATA/
├── config.json                       # Stop-gate tier toggles + notify-throttle + scope_budget settings
├── rules.local.tsv                   # user rule overlay (same id replaces a default)
├── tokens/
│   └── <rule-id>.token               # pg-grant token: epoch timestamp, single use
├── ledger/
│   ├── <session_id>.jsonl            # per-session action ledger
│   └── receipts/
│       └── <cwd-hash>.jsonl          # bin/prove + bin/prove-cov receipts, keyed by sha256(cwd)[:16]
└── state/
    ├── <session_id>.blocks           # stop-gate block counter (2-block loop cap)
    ├── notify-throttle-<session_id>.json
    └── scope-budget-<session_id>.json
```

Two fallbacks exist for processes that run without `$CLAUDE_PLUGIN_DATA` in their
environment: `bin/pg-grant` and the gatekeeper fall back to `~/.claude/proofgate`
(they must agree on token paths), and `bin/prove` / `bin/prove-cov` and the Stop
gate fall back to `$XDG_DATA_HOME/proofgate` (default `~/.local/share/proofgate`)
because the prove helpers run inside the agent's Bash tool, where the hook env is
not exported. The Stop gate reads receipts from both the data dir and the prove
fallback.

### Session ledger (`ledger/<session_id>.jsonl`)

Appended by the PostToolUse recorder (`mark-dirty.sh`). One JSON object per
line; `ts` is epoch seconds. Two record kinds:

```json
{"ts":1765500602.1,"kind":"edit","path":"tests/test_auth.py","test":true}
{"ts":1765500690.4,"kind":"test_run","cmd":"python3 -m pytest tests/test_auth.py","ok":false}
```

Command records are written for the classes `git_commit`, `push`, `send`,
`test_run`, and `deferral_artifact`. Classification happens at PostToolUse from
the parsed command head plus the tool result's error status — never from
assistant prose. `test` on edit records marks test-file paths so the Stop gate
knows which edits demand a post-edit green.

What the Stop gate enforces from the ledger:

- A tests-pass claim requires a successful test run after the last file edit
  (by tool order in the transcript, falling back to ledger timestamps and
  `prove` receipts).
- An edited test file blocks the stop until a green test run or `prove`
  receipt is recorded after the edit. The ledger records reds too, but
  red-*first* discipline is `/repro-test` skill prose in this release, not a
  hook guarantee.
- (`vacuous_test` tier, off by default) A *strong* end-to-end claim
  ("validated end-to-end", "exercised the real code path") made after editing
  a production (non-test) file must be backed by real-path evidence: an
  `e2e_run`-class command (`playwright`, `cypress`) that ran *after* the edit,
  or a `prove-cov` receipt whose `covered` paths include an edited production
  file (matched by path-suffix, not bare basename). A unit runner alone does
  not clear it, an informational no-op (`playwright --version`, `--list`) is
  not an `e2e_run`, and a send-class command is not accepted (a `curl` proves
  a request, not that the edited code made it). The trigger is a precision
  tripwire on validation-verb phrasing (not the noun phrase "an end-to-end
  test"); measured benign fire-rate 0% on a 53-line corpus, recall low by
  design (see failure mode 7). The honest limit: coverage proves a line ran,
  not that it ran un-mocked.

### Prove receipts (`ledger/receipts/<cwd-hash>.jsonl`)

Written by `bin/prove "<claim>" -- <command...>`, which requires exit 0 and
non-empty output before recording:

```json
{"claim":"tests pass after edits","cmd":"python3 -m pytest","exit":0,"sha":"a1b2c3...","cwd":"/home/user/my-app","ts":1765500785.2}
```

`bin/prove-cov "<claim>" <file>... -- <command...>` writes the same shape plus
a `covered` list — the named files coverage.py measured as executing >0 lines
under the command. The `vacuous_test` tier reads `covered` to confirm an
edited production file actually ran; a plain `prove` receipt (no `covered`
key) is exit-0 evidence only and does not clear that tier:

```json
{"claim":"end-to-end","cmd":"-m pytest e2e/","exit":0,"sha":"a1b2c3...","cwd":"/home/user/my-app","ts":1765500799.4,"covered":["src/mutation_probe.py"]}
```

Honest limit: `covered` proves a line executed, not that it executed against
an un-mocked collaborator (a mock behind a covered line is invisible here).

### Grant tokens (`tokens/<rule-id>.token`)

A token file contains a single epoch timestamp. Minted only by `bin/pg-grant`,
which refuses to run without a TTY on stdin — an agent driving a Bash tool has
no TTY, so it cannot mint its own override. Tokens expire after 15 minutes and
are deleted on first use (single use). The gatekeeper consumes one when a
`require-token` rule matches; with no fresh token it denies and prints the
exact `pg-grant` invocation a human must run.

## Gatekeeper: head parsing

Substring matching false-positives on text *about* commands (greps, heredocs,
commit messages). The gatekeeper instead parses what will execute:

1. Split the command into segments at `|`, `;`, `&&`, `||`, `&`, and newlines,
   respecting quotes. Comments and heredoc bodies are dropped. The contents of
   `$(...)`, backticks, and `<(...)` become segments of their own (they do
   execute); quoted text never spawns segments.
2. Strip redirections, shell keywords, and leading `VAR=val` assignments;
   unwrap wrapper commands (`sudo`, `doas`, `env`, `command`, `nohup`, `time`,
   `exec`, `xargs`, `builtin`, `stdbuf`, `timeout`) and recurse into
   `sh -c '...'` payloads.
3. The **head** is the first four tokens of the segment plus any later
   `-flags` (so `git push origin main --force` keeps `--force` visible).
   Rule patterns match the head with a regex anchored at its start.

Measured on the 93,931-command source corpus: 0.73% fire-rate (682 commands),
all true dangerous invocations, zero text-about-commands false positives.

## rules.tsv schema

One schema for both rules files: the shipped `rules/defaults.tsv` and the user
overlay `$CLAUDE_PLUGIN_DATA/rules.local.tsv`. Tab-separated, six columns, one
rule per line; `#` starts a comment; blank lines ignored.

```
id <TAB> tool <TAB> scope <TAB> pattern <TAB> action <TAB> reason
```

| Column | Meaning |
|---|---|
| `id` | unique rule name; `pg-grant <id>` targets it; an overlay row with the same id replaces a shipped one (use a never-matching pattern like `(?!)` to disable a default) |
| `tool` | `bash` (head-matched against commands), `edit` (searched in file path and added text of Edit/Write/MultiEdit/NotebookEdit), or `any` |
| `scope` | regex searched against the session cwd; `*` (or empty) = everywhere |
| `pattern` | Python regex. For `bash`, matched **anchored at the start of each segment head**. Optional guard prefix: `outside-cwd-tmp:` (fires only if a path argument resolves outside cwd and temp dirs) or `dirty-repo:` (fires only if the cwd git repo has uncommitted changes) |
| `action` | `deny`, `ask`, or `require-token` (deny unless a fresh `pg-grant` token exists) |
| `reason` | shown to the agent and the user when the rule fires; `{head}` expands to the matched head |

Example (shipped `rules/defaults.tsv`, abridged):

```
# id	tool	scope	pattern	action	reason
git-push-force	bash	*	git\s+push(?!.*--force-with-lease)(?=.*\s--force\b)	deny	Force push rewrites remote history; use --force-with-lease instead.
git-commit-no-verify	bash	*	git\s+commit(?=.*\s--no-verify\b)	ask	Skips this repo's commit hooks; confirm with the user first.
git-discard-dirty	bash	*	dirty-repo:git\s+reset(?=.*\s--hard\b)	require-token	{head} permanently discards uncommitted changes in a dirty repo.
# Terminal-workspace tools that tear down live sessions — uncomment and adapt
# to whatever tool you use (example shown for cmux):
# workspace-close	bash	*	cmux\s+close-workspace	ask	Closing a workspace (e.g. workspace-7) kills everything running in it.
```

Evaluation order over all matched rules: every `deny` first, then
`require-token` (consuming a token on success, denying otherwise), then `ask`.
Rules load from `rules/defaults.tsv`, then the single overlay file
`$CLAUDE_PLUGIN_DATA/rules.local.tsv`; the overlay is never touched by updates.
Malformed rows are silently skipped — after editing rules, always run the
self-test in `/codify` step 4 so a typo cannot become a rule that silently
does nothing.

## Writing and measuring a custom rule

Never enable a rule you haven't measured — the corpus lesson is that intuition
overestimates precision badly.

1. Draft it in `$CLAUDE_PLUGIN_DATA/rules.local.tsv`:

   ```
   db-drop	bash	*	psql(?=.*(?i:drop\s+(table|database)))	ask	Drops schema objects; confirm with the user.
   ```

2. Measure it against your own history. `/reliability-audit` replays every Bash
   command from your local transcripts through the head parser and reports, per
   rule: fire-count, fire-rate, and every line it would have caught, so you can
   eyeball true vs. false positives. Equivalent CLI (after extracting your
   corpus with the audit's `extract_corpus.py`):

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}"/skills/reliability-audit/scripts/eval_rules.py \
     --rules "$CLAUDE_PLUGIN_DATA"/rules.local.tsv \
     --benign commands.txt --bad labeled_bad.txt --samples 5
   ```

3. Threshold guidance: shipped rules each measure well under 1% fire-rate with
   zero false positives on the source corpus. If your candidate fires above ~1%
   or catches text-about-commands, tighten the head (substring matching is how
   you get there) or the lookahead.

4. Self-test through the live gatekeeper (`/codify` step 4), then enable by
   leaving it in place; disable by commenting it out.

## Fail-open philosophy

A safety layer that can wedge a session gets uninstalled, and then it protects
nothing. Therefore every hook fails open, unconditionally:

- Every entrypoint wraps its logic so that **any** internal error — parse failure,
  missing state, corrupt JSON, unexpected stdin — exits `0` with no output. The
  session proceeds exactly as if proofgate were absent.
- Hook timeouts are declared in `hooks/hooks.json`; a slow gate is a dead gate.
- State readers skip unreadable lines rather than aborting; state writers append
  rather than rewrite.
- The Stop gate caps itself at 2 blocks per turn (the `.blocks` counter), and a
  final message containing `UNVERIFIED` is always allowed through.
- The only deliberate non-zero path is agent-file-lint's exit 2 (feedback, not a
  wedge); blocking decisions are emitted as well-formed JSON with exit 0.

The invariant: proofgate may fail to protect you; it must never be the reason a
command can't run or a session can't stop.
