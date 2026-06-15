# Changelog

All notable changes to proofgate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
