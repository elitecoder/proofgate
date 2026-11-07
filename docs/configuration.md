# Configuration and uninstall

This page is the full reference for tuning proofgate, turning parts of it off, and
removing it cleanly. For the file layout and schemas behind it, see
[architecture.md](architecture.md).

## Where settings live

proofgate reads one optional file, `config.json`, in the plugin's data directory.
Claude Code exposes that directory to hooks as `$CLAUDE_PLUGIN_DATA`, and anything
in it survives plugin updates.

```json
{
  "gates": {
    "llm_judge": true
  },
  "llm_judge_cmd": "claude --bare -p --model haiku",
  "notify_heads": ["notify-send", "terminal-notifier"],
  "notify_window_seconds": 300,
  "scope_budget": [50, 150]
}
```

Every key is optional. Unknown keys are ignored, so a config written for an older
release still loads.

## The Stop gate (verify-gate)

The Stop gate is an LLM judge, and `gates.llm_judge` is its only switch. It's on by
default; set it to `false` to turn the gate off entirely.

When the agent tries to end a turn, the gate renders the session transcript: every
tool call and its real output (a test runner's `4 passed`, a `git push`'s output,
edits), in order, together with the final summary. It asks the model whether the
summary asserts a specific external-effect action that the transcript doesn't
support. Only four kinds of claims can block:

- pushed, merged, or shipped a branch or PR
- sent, posted, or delivered a message
- tests pass, the suite is green, or e2e passed
- deployed or released to an environment

Everything else passes: local edits, analysis, plans, questions, hedged statements,
and reports of a fact the agent verified some other way, such as reading a PR's
merged state with `gh`. Because the judge reads the actual output rather than a
classified log, an honest "specs green" backed by passing test output goes through.

Other behavior to know:

- **Model.** `llm_judge_cmd` is the shell command the gate pipes its prompt into.
  The default is `claude --bare -p --model haiku`. The gate reads the last non-empty
  line of the command's output and blocks only if it starts with `BLOCK`.
- **Fails open.** If the command errors, times out, or prints something
  unparseable, the gate passes. `hooks/hooks.json` gives the whole Stop hook 15
  seconds. Before you rely on the gate, confirm the judge
  command answers in your shell; if it can't, the gate silently passes every turn.
- **Loop cap.** The gate blocks at most twice per turn, then lets the stop through.
- **Escape hatch.** A final message that contains `UNVERIFIED` is never sent to the
  judge.
- **Compaction backstop.** The session ledger (the kinds `push`, `send`,
  `git_commit`, and `test_run` recorded after each command) goes to the judge too,
  so an action from a turn that compaction trimmed out of the transcript still
  counts. The ledger never decides a verdict on its own.

## The command gatekeeper

The gatekeeper is configured through rules files, not `config.json`. It loads the
shipped `rules/defaults.tsv` first, then your overlay at
`$CLAUDE_PLUGIN_DATA/rules.local.tsv`. Updates never touch the overlay.

- To add a rule, add a row to `rules.local.tsv`.
- To replace a shipped rule, add a row with the same `id`.
- To disable a shipped rule, add a row with its `id` and a pattern that never
  matches, such as `(?!)`.

Malformed rows are skipped without a warning, so self-test every rule you write. The
`/codify` skill does this for you: it pipes a must-catch command and a near-miss
through the live gatekeeper and shows you both results. The
[rules.tsv schema](architecture.md#rulestsv-schema) and the
[measure-before-enable workflow](architecture.md#writing-and-measuring-a-custom-rule)
cover the details.

The shipped rules:

| Rule id | Fires on | Action |
|---|---|---|
| `rm-rf-outside-cwd` | `rm` with recursive and force flags, when a path resolves outside the working directory and temp dirs | deny |
| `git-discard-dirty` | `git reset --hard` or `git clean -f` in a repo with uncommitted changes | require-token |
| `git-push-force` | `git push --force`, `-f`, or a `+refspec`, unless `--force-with-lease` is present | deny |
| `git-commit-no-verify` | `git commit --no-verify` | ask |
| `chmod-777` | recursive `chmod` to `777` | ask |

`defaults.tsv` also carries two commented-out example rules you can copy into your
overlay: a repo-scoped "merge through the bot" rule and a terminal-workspace
teardown rule.

### Override tokens

A `require-token` rule denies the command unless a fresh token exists. A human mints
one with `pg-grant <rule-id>`; the deny message prints the exact command, including
the data directory and the path to `bin/pg-grant`. Tokens:

- can only be minted from a real terminal, because `pg-grant` refuses to run when
  stdin isn't a TTY
- expire after 15 minutes
- are consumed by the first command they let through

## Notification throttle

`notify-throttle` denies a second notification command within one window per
session and tells the agent to buffer updates into one rollup.

- `notify_heads` lists the commands that count as notifications. The defaults are
  `notify-send`, `osascript -e display notification`, and `terminal-notifier`. A
  custom list replaces the defaults entirely.
- `notify_window_seconds` sets the window. The default is 300 seconds.
- A command containing `ACTION:` always goes through, for alerts the user must act
  on now.

## Scope budget

`scope-budget` counts dirty and untracked files in the current git repo after each
edit. The first time the count crosses each threshold in a session, it blocks once
and asks the agent for a one-line scope inventory before editing further. Outside a
git repo it stays silent.

Settings, highest precedence first:

1. The `PROOFGATE_SCOPE_BUDGET` environment variable: comma-separated integers
   (`"50,150"`), a single integer (`"80"`), or `off`, `none`, or `0` to disable.
2. The `scope_budget` key in `config.json`: a list (`[50, 150]`), a single integer,
   `false`, `{"enabled": false}`, or `{"thresholds": [50, 150]}`.
3. The default, `[50, 150]`.

A value that doesn't parse falls through to the next source.

## Prompt injectors without switches

`turn-context` and `agent-file-lint` have no config switches in this release. To
turn one off, remove its entry from `hooks/hooks.json` in your checkout.

- `turn-context` adds a UTC timestamp and a four-line register card to every prompt.
  Prompts with direct-order phrasing ("just do it", "stop asking") also get an
  authority card; pushback phrasing ("are you sure", "that's wrong") gets a
  reconcile card.
- `agent-file-lint` checks text added to `CLAUDE.md`, `AGENTS.md`, `SKILL.md`,
  `*-runner.md`, and files under `.claude/` or `prompts/` directories. It bounces
  rationale markers ("because", "rationale", "for posterity", "why we",
  "background:") and lines over 250 characters, outside fenced code blocks.

## Fail-open guarantee

Every hook fails open. Any internal error exits without output, and the session
carries on as if proofgate weren't installed. The only deliberate non-zero exit is
`agent-file-lint`'s exit 2, which is feedback to the agent, not a wedge. A broken
safety layer must never be the reason a command can't run or a session can't stop.

## Uninstall

```
/plugin uninstall proofgate@proofgate
/plugin marketplace remove proofgate
```

Run both commands. If you added proofgate from a local clone
(`/plugin marketplace add ~/proofgate`), the marketplace registration alone is enough
to keep the hooks loading. Claude Code auto-enables a registered marketplace plugin
when there's no explicit `enabledPlugins` entry for it, because a plugin's
`defaultEnabled` defaults to `true`.

proofgate ships `"defaultEnabled": false` in its marketplace manifest so a bare
directory registration no longer auto-enables it, but a registration left over from
before release 0.2.1 still resolves to enabled. Removing the marketplace, which
clears its `extraKnownMarketplaces` entry, is what actually stops the hooks. A GitHub
install (`/plugin marketplace add elitecoder/proofgate`) behaves the same way.

To keep proofgate registered but turned off, set
`"enabledPlugins": {"proofgate@proofgate": false}`. An explicit `false` always wins
over the marketplace default.

### Clean up local state

If you want a clean slate, delete the data directories. Hooks write under
`$CLAUDE_PLUGIN_DATA`, and two fallbacks hold state written outside hook context:

- `~/.claude/proofgate` holds `pg-grant` tokens and local rules when
  `$CLAUDE_PLUGIN_DATA` is unset.
- `~/.local/share/proofgate` holds `prove` receipts. `prove` runs inside the agent's
  Bash tool, where `$CLAUDE_PLUGIN_DATA` isn't exported.

`/defer` output (DEFERRALS.md lines and GitHub issues) is work product in your repos,
not plugin state, so uninstalling leaves it in place.
