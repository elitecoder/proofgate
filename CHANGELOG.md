# Changelog

All notable changes to proofgate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-06-19

### Fixed

- **Uninstalling proofgate did not stop its hooks.** A directory-source
  marketplace registration (`/plugin marketplace add <local-path>`, recorded in
  `extraKnownMarketplaces` + `known_marketplaces.json`) keeps Claude Code loading
  this plugin's `hooks/hooks.json` even with **no** `enabledPlugins` entry,
  because a marketplace plugin's `defaultEnabled` defaults to **true** — "starts
  enabled when the user has no explicit enabled/disabled setting." So a plugin
  that looked uninstalled (absent from `enabledPlugins`, cache orphaned) still
  fired its `Stop`/`PreToolUse`/`PostToolUse` hooks straight from the registered
  directory. The marketplace manifest now sets `"defaultEnabled": false`, so a
  bare directory registration no longer auto-enables the plugin; it must be
  enabled explicitly. The **uninstall contract** is now documented (README):
  `/plugin uninstall` alone is not enough for a directory-source install — you
  must also `/plugin marketplace remove proofgate`.

### Added

- **Kill switch (defense-in-depth).** Every hook entrypoint now hard-no-ops when
  proofgate is disabled, so a stale registration can never block again even
  before it is removed. Two equivalent off signals, checked at both the `sh`
  wrapper and the Python layer: the `PROOFGATE_DISABLED` environment variable, or
  a `DISABLED` sentinel file in the plugin data directory
  (`$CLAUDE_PLUGIN_DATA/DISABLED`). `pg_common.is_disabled()` is the shared
  helper; the standalone gatekeeper mirrors it inline. New
  `tests/test_verifygate_disabled.py` proves each entrypoint (Stop gate,
  mark-dirty recorder, gatekeeper) no-ops under both signals, with an
  enabled-control per case that confirms the same scenario still fires.

## [0.2.0] - 2026-06-18

### Added

- **verify-gate `vacuous_test` tier** (`Stop` hook, **off by default**): catches
  failure mode 7 (vacuous green / mocked end-to-end). A *strong* claim
  ("validated end-to-end", "exercised the real code path") made after editing a
  production file must be backed by real-path evidence — an e2e/browser runner
  (`playwright`, `cypress`) that ran after the edit, or a `prove-cov` coverage
  receipt covering an edited production file (matched by path-suffix, not bare
  basename). A unit-test pass, an informational no-op (`playwright --version`),
  and a send-class command do not clear it. The strong-claim trigger is a
  precision tripwire on validation-verb phrasing (low recall by design);
  measured benign fire-rate 0% on a 53-line corpus that includes the
  affirmative phrasings an earlier broader draft fired on. Gated by
  `config.json` `gates.vacuous_test`.
- **`bin/prove-cov`**: coverage-receipt variant of `bin/prove`. Runs the command
  under coverage.py and records which named files actually executed lines
  (`covered` field on the receipt), so the gate can distinguish a real-path run
  from a mocked unit pass.
- **`e2e_run` command class** in the head parser for real-path runners
  (`playwright`, `cypress`, `pnpm e2e`, `npm run test:e2e`, ...).
- Failure mode 7 (vacuous green) added to the field guide, with its honest
  residual stated: coverage proves a line ran, not that it ran un-mocked.

### Changed

- **The Stop gate now leads with an LLM judge instead of keyword tiers.** The
  `checkable_claim` / `promissory` tiers matched bare words ("merged", "pushed",
  "I'll next…") against mechanical state, which mis-fired on turns that took no
  such action (most turns) and on legitimate cross-turn summaries. A new
  `llm_judge` tier (default ON) sends the final summary **plus the session
  evidence** (durable-ledger actions, command classes, git upstream state) to a
  small model (`claude --bare -p --model haiku` by default, override via
  `llm_judge_cmd`) and asks for a `PASS` / `BLOCK <reason>` verdict. It is told
  most turns legitimately take no action and to default to PASS. `checkable_claim`
  and `promissory` now default OFF (the judge subsumes them); the deterministic
  `ship_state` / `red_green` / `deferral` ledger checks stay on. **Fails open** on
  any model or parse error — a flaky judge never wedges the session. To run the
  pure-deterministic gate with no model calls, set `llm_judge: false` and flip the
  keyword tiers back on.
- **scope-budget thresholds are now configurable and the default is raised.** The
  hard-coded `(12, 30, 60)` first threshold fired on ordinary focused work — a
  37-file coverage PR tripped it on nearly every edit. The default is now
  `[50, 150]`, and the thresholds are settable via the `PROOFGATE_SCOPE_BUDGET`
  env var (`"50,150"`, a single int, or `off`/`0` to disable) or a `scope_budget`
  key in `config.json` (a list, a single int, `false`, or
  `{"enabled": false}` / `{"thresholds": [...]}`). Env overrides config; bad
  values fall through to the next source. The gate still fires once per threshold
  per session and stays silent in non-git directories.

### Fixed

- **Stop gate's `red_green` tier re-flagged files already proven green in earlier
  turns.** The tier collapsed every test edit to a single global max-edit
  timestamp, so a coverage session that proved 36 test files green across many
  turns, then touched one more test file once without re-running it, had **all**
  37 re-blocked as "never proven green" — the same cross-turn false-positive
  class the `checkable_claim` fix addressed, on the one tier that still ran by
  default after the LLM judge passes. It now evaluates **per file**: a green run
  or `prove` receipt clears every test file edited at or before it, and only a
  file whose last edit post-dates the most recent green proof blocks. Added
  regression tests (per-file isolation + cross-turn green clearing) with a
  mutation probe against the old global-max logic.

- **Gatekeeper missed git subcommand rules under `git <global-opts> <subcommand>`.**
  `git`'s global options (`-c name=value`, `-C path`, `--git-dir=…`) sit between
  `git` and the subcommand; combined with the 4-token head window they pushed the
  subcommand out of the matched head entirely, so `git -c user.email=… commit
  --no-verify` (and the same shape for `push --force` / `reset --hard`) slipped
  past silently. Head extraction now strips git's leading global options so the
  real subcommand surfaces. Repairs all three git rules at once; added regression
  tests for the `-c`/`-C`/`--git-dir`/`--work-tree` forms plus benign-subcommand
  controls.
- **Stop gate blocked legitimate summaries after context compaction.** The
  `checkable_claim` tier built its `session_classes` only from the live transcript,
  which compaction truncates — so a `push` / `send` / `git_commit` that ran in an
  earlier turn vanished, and a final summary restating it ("pushed", "posted",
  "merged") read as unverified and was blocked. The PostToolUse recorder already
  persists those kinds to the session ledger (which survives compaction), but the
  Stop gate didn't consult it. Now folds the ledger's recorded kinds
  (`push` / `send` / `git_commit` / `test_run`) into `session_classes`, so a
  cross-turn action verifies the claim. Added regression tests for the
  ledger-backed push and send cases.

## [0.1.0] - 2026-06-11

Initial release.

### Added

- **verify-gate** (`Stop` hook): checkable-claim cross-referencing — pushed → git
  state, sent → send-class command receipt, edited tests → recorded post-edit green
  run or `prove` receipt. Also blocks promissory endings and deferral language with
  no artifact. Optional LLM-judge tier, off by default; tiers toggleable in
  `config.json`.
- **gatekeeper** (`PreToolUse` hook): command-head parsing per pipeline segment with
  six-column TSV rule matching (deny/ask/require-token) and a single user overlay at
  `$CLAUDE_PLUGIN_DATA/rules.local.tsv`.
- **pg-grant**: TTY-gated, time-boxed, single-use override tokens for
  `require-token` rules.
- **injectors**: turn-context (`UserPromptSubmit`: timestamp + register card on every
  prompt; authority card on direct-order prompts, reconcile card on pushback),
  notify-throttle (`PreToolUse` Bash: one notification per window), scope-budget and
  agent-file-lint (`PostToolUse` Edit|Write).
- **Skills**: `/codify`, `/defer`, `/repro-test`, `/reliability-audit` (local-only
  transcript mining and rule measurement).
- Hook state in the plugin data directory; every hook fails open.
- Docs: README, failure-modes field guide, architecture reference.
