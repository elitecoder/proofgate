# proofgate

**Agents say "done." proofgate asks for receipts.**

If you run coding agents hard — parallel sessions, hours of autonomy, orchestrators feeding one agent's output to another — you already know the failure that matters most. It is not the agent that errors out. It is the agent that **reports success**. "All tests pass." "Pushed, PR opened." "Notification sent." You merge and move on, and two days later you discover the suite had been broken the whole time, the branch never left the machine, and the message was never sent. Every heavy agent user eventually merges a PR on the strength of a green-tests claim that nothing ever verified.

proofgate is a Claude Code plugin that closes that gap at the harness level. It cross-references the agent's claims against mechanically checkable state before the session is allowed to stop, gates destructive commands by parsing what will actually execute, and injects discipline at the exact prompt patterns where measured data says behavior slips.

## Born from data

proofgate was built from a forensic audit of **5,020 real Claude Code sessions** across six weeks of heavy multi-agent use:

- **540 verified failure incidents**, clustered into **12 recurring failure modes** ([the field guide](docs/failure-modes.md))
- every command gate tuned against **93,931 real Bash commands**
- every claim gate tuned against **6,400 assistant-claim → user-reaction pairs**
- every injector tuned against **13,107 prompts**

The corpus itself is private session data and does not ship. What ships is the measurement pipeline: `/reliability-audit` computes the same statistics from your own local transcripts, so every number above is reproducible against your usage even though it cannot be re-derived from this repo.

Two design lessons from that corpus, stated honestly:

**1. Naive done-claim regexes do not work.** Generic "sounds finished" patterns fired on **26–55% of benign turns** in the corpus — a gate that noisy gets disabled within a day. So proofgate's stop gate only acts on **checkable claims**: claims that map to state it can verify mechanically. Claimed "pushed"? It asks git. Claimed "sent"? It looks for a send-class command in the session. Edited a test file? It demands a recorded green run *after* that edit. No receipt, no stop.

**2. Substring command-matching does not work.** Naive matchers false-positive on greps, heredocs, and commit messages *about* dangerous commands. So the gatekeeper parses **command heads** — the first tokens of every pipeline/`;`/`&&` segment, comments stripped, wrappers unwrapped — and matches those against rules. Measured on the corpus: a **0.73% fire-rate** (682 of 93,931 commands), and every single fire was a true dangerous invocation. Zero false positives from text that merely mentioned `rm -rf`.

## Why hooks, not CLAUDE.md sermons

Instructions decay. In the source data, standing rules had to be re-taught within **3 days** on average — and were violated **minutes after being restated**, while the rule was still in context. Prose asks; hooks enforce. A `Stop` hook that demands a git receipt does not get tired, does not get summarized away in compaction, and does not decide this one time is probably fine.

Harness enforcement is also model-agnostic: the same gates apply whatever model runs the session, today or after the next model swap.

## What's in the box

Read this table as the full disclosure of what the plugin will do to your sessions — including the always-on parts.

| Component | Type | What it does |
|---|---|---|
| **verify-gate** | `Stop` hook | Blocks "done" claims that lack receipts. Tier 1: claim→state cross-reference (pushed → git, sent → send-class command). Tier 2: test ledger — a session that edited test files cannot stop without a recorded post-edit green run or `prove` receipt. Tier 3 (`vacuous_test`, **off by default**): a *strong* claim ("validated end-to-end", "exercised the real code path") after a production-code edit must be backed by real-path evidence — an e2e/browser run after the edit, or a `prove-cov` coverage receipt covering the edited file — not a unit pass. A precision tripwire (low recall by design), measured 0% benign fire-rate. Tier 4: optional LLM judge — **off by default**. Also blocks endings that promise future action and deferral language with no artifact. Caps itself at 2 blocks per turn; `UNVERIFIED:` in the final message is the escape hatch. |
| **gatekeeper** | `PreToolUse` hook | Head-parses every command segment against the rules TSV (shipped defaults + your overlay); deny/ask/require-token on destructive heads. 0.73% measured fire-rate, all true positives. |
| **pg-grant** | CLI | TTY-gated, time-boxed (15 min), single-use override tokens for `require-token` rules. Only a human at a real terminal can mint one — an agent cannot grant itself. |
| **turn-context** | `UserPromptSubmit` hook | Injects a UTC timestamp plus a 4-line communication-register card into **every** prompt (~30 tokens — this is the one always-on injection). Direct-order prompts ("just do it", "stop asking" — 0.4% of corpus prompts) additionally get an authority card; pushback prompts (0.1%) get a re-verify-before-conceding card. |
| **notify-throttle** | `PreToolUse` hook | Denies more than one notification command (`notify-send`, `terminal-notifier`, ... — configurable) per 5-minute window per session, with instructions to buffer and send one rollup. An `ACTION:` payload bypasses it. |
| **scope-budget** | `PostToolUse` hook | When the repo crosses a dirty/untracked-file threshold (default 50, then 150), blocks once per threshold and demands a one-line scope inventory before further edits. Configurable via `PROOFGATE_SCOPE_BUDGET` or `config.json` (`"scope_budget"`); set to `off` to disable. |
| **agent-file-lint** | `PostToolUse` hook | Edits to `CLAUDE.md`/`AGENTS.md`/`SKILL.md` that add rationale prose ("because", "rationale", ...) or >250-char lines are bounced back with line-level feedback — agent-facing files state WHAT, never WHY. |
| **/codify** | skill | Turns the correction you just typed into a self-tested gatekeeper rule instead of a sermon that decays. |
| **/defer** | skill | Logs "I'll do that later" items as durable artifacts (DEFERRALS.md + GitHub issue) so deferred work survives the session instead of evaporating. |
| **/repro-test** | skill | Baseline-red bug fixing: a failing reproduction is recorded before the fix, a green run and a `prove` receipt after. Skill-level discipline (model-followed prose), reinforced by the verify-gate's post-edit green requirement. |
| **prove / prove-cov** | CLI | `prove` records a receipt when a command exits 0 with output. `prove-cov` additionally runs the command under coverage and records *which* files executed lines, so the `vacuous_test` tier can tell a real-path run from a mocked unit pass. Both write receipts the Stop gate reads. |
| **/reliability-audit** | skill | Mines **your** local session transcripts, derives **your** failure modes, measures candidate rules against **your** corpus before you enable them. |

## The part that makes it yours: /reliability-audit

The shipped gates were tuned on one engineer's six weeks. Your failure modes are not identical to theirs.

`/reliability-audit` reads your local Claude Code transcripts, clusters your own incidents, and — before any new rule goes live — replays your entire command history through it and reports the fire-rate with every line it would have caught. You see exactly what a rule costs in friction and what it buys in coverage, measured on your own usage, before it gates anything.

**Privacy: everything stays on your machine.** The audit reads local transcript files, writes local reports, and sends nothing anywhere. proofgate has no telemetry, no network calls, no phone-home.

## Install

From GitHub (requires the repository to be published at that location):

```
/plugin marketplace add elitecoder/proofgate
/plugin install proofgate@proofgate
```

From a local clone (no network needed — also the way to try it before it is published):

```
git clone <repo-url> ~/proofgate
/plugin marketplace add ~/proofgate
/plugin install proofgate@proofgate
```

### Quick start

The hook gates are live immediately after install:

1. Let the agent claim "pushed" when commits never left the machine — the `Stop` gate asks git and blocks the stop with the discrepancy.
2. Watch it reach for `git reset --hard` in a dirty repo — the gatekeeper denies it and prints the exact `pg-grant` command a human must run in a real terminal to mint a single-use, 15-minute token.
3. Ask for a bug fix via `/repro-test` — a skill, not a hook, so it is model-followed discipline: failing repro first, then the fix, then a `prove` receipt the stop gate can see.
4. After a week, run `/reliability-audit` to see what your own sessions say.

### Configuration

proofgate reads one optional config file, `config.json`, in the plugin's data directory (exposed to hooks as `$CLAUDE_PLUGIN_DATA`; state there survives plugin updates). It controls the Stop-gate tiers and the notification throttle:

```json
{
  "gates": {
    "llm_judge": true,
    "checkable_claim": false,
    "promissory": false,
    "ship_state": true,
    "red_green": true,
    "deferral": true,
    "vacuous_test": false
  },
  "llm_judge_cmd": "claude --bare -p --model haiku",
  "notify_heads": ["notify-send", "terminal-notifier"],
  "notify_window_seconds": 300,
  "scope_budget": [50, 150]
}
```

Each `gates` key toggles one Stop-gate check (see [architecture](docs/architecture.md) for what each one verifies). The gate now **leads with an LLM judge** (`llm_judge`, default on): it reads the final summary together with the session evidence (durable-ledger actions, command classes, git upstream state) and blocks only an unsupported external-effect claim, instead of firing on a bare keyword. It **fails open** on any model/parse error. The two keyword tiers it subsumes — `checkable_claim` and `promissory` — default **off**; flip them on (and `llm_judge` off) to run the pure-deterministic gate with no model calls. Override the judge model with `llm_judge_cmd`. `vacuous_test` ships **off**: its trigger measures a 0% benign fire-rate but the signal is a claim/evidence-class mismatch, not proof of a mock (see [failure mode 7](docs/failure-modes.md)) — turn it on for sessions whose agents overclaim "end-to-end". `scope_budget` sets the dirty-file thresholds (default `[50, 150]`); `false` or `0` disables it, and the `PROOFGATE_SCOPE_BUDGET` env var overrides this key. The gatekeeper is configured through its rules files, not `config.json`; the turn-context and agent-file-lint injectors have no config switches in this release — disabling them means removing their `hooks/hooks.json` entries from your checkout.

Add your own command rules in `$CLAUDE_PLUGIN_DATA/rules.local.tsv` — a single overlay file, never touched by updates. A row whose id matches a shipped default replaces it (a never-matching pattern like `(?!)` disables one). See the [rules.tsv schema](docs/architecture.md#rulestsv-schema) and the measure-before-enable workflow.

Every hook **fails open**: any internal error exits silently and the session proceeds as if proofgate were not installed. A broken safety layer must never wedge your session.

### Uninstall

```
/plugin uninstall proofgate@proofgate
```

Then delete the data directories if you want a clean slate. Hooks write under `$CLAUDE_PLUGIN_DATA`, but two documented fallbacks exist for state written outside hook context: `~/.claude/proofgate` (`pg-grant` tokens and local rules when `$CLAUDE_PLUGIN_DATA` is unset) and `~/.local/share/proofgate` (`prove` receipts — `prove` runs inside the agent's Bash tool, where `$CLAUDE_PLUGIN_DATA` is not exported). `/defer` output (DEFERRALS.md lines, GitHub issues) is work product in your repos, not plugin state, and is deliberately left in place.

## Is this for you?

Value scales with how hard you push agents.

- **Casual, single-session use:** modest value. You're watching every turn anyway.
- **Long autonomous sessions:** real value. The claims you can't watch are exactly the ones that need receipts.
- **Parallel agents and orchestration:** maximum value. In a pipeline, one agent's unverified "done" becomes another agent's input, and a single phantom completion propagates through everything downstream. This is the usage pattern the source corpus came from, and the one proofgate was tuned for.

## Roadmap

- **Model-in-the-loop evals with baseline-red discipline:** every eval must demonstrably fail without the plugin before it counts as a test of the plugin.
- **Hook-enforced red→green:** the ledger already records failing runs; a future gate tier will require a recorded red before the green for edited tests, instead of leaving red-first discipline to `/repro-test` prose.
- **Block-reason effectiveness matrix:** measure which block phrasings actually change agent behavior, per model — a block reason that gets argued with is a bad block reason.
- **Community rules:** shared rules-TSV packs, each published with its measured fire-rate so you know the friction cost before you import it.

## Docs

- [The failure modes — an anonymized field guide](docs/failure-modes.md)
- [Architecture: events, scripts, state, schemas](docs/architecture.md)
- [Changelog](CHANGELOG.md)

## License

MIT.
