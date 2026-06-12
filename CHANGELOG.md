# Changelog

All notable changes to proofgate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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
