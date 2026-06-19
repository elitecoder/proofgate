# The failure modes: a field guide

Source: a forensic audit of 5,020 real Claude Code sessions over six weeks of heavy
multi-agent use — 13,107 prompts, 93,931 Bash commands, 6,400 assistant-claim →
user-reaction pairs. 540 verified failure incidents clustered into the **12 recurring
modes** (1–6, 8–13) below. Mode 7 (vacuous green) was added later from a single
post-release incident, not the original corpus, and is labelled as such. All examples
are anonymized and reconstructed; aggregate statistics only, no session quotes.

Each entry: what happens, why it happens, and which proofgate piece addresses it.

---

## Claims that don't match reality

### 1. Phantom completion

**What happens.** The agent announces the task is done. Part of it — sometimes all of
it — is not. Files unwritten, steps skipped, edge cases waved at.

**Why.** "Done" is the natural last token of a work narrative, and producing it is far
cheaper than verifying it. Under long sessions the completion claim drifts from the
completion fact. Note the trap for tool builders: naive done-claim regexes fired on
26–55% of benign turns in the corpus — most "done" statements are honest, so the gate
must check state, not vocabulary.

**proofgate piece.** verify-gate (`Stop` hook): an LLM reads the session transcript — the
tool calls and their real outputs — and blocks the stop only when the summary asserts a
specific external-effect action the transcript does not support, naming the discrepancy.

### 2. The unpushed push

**What happens.** "Pushed and opened the PR." The remote has never heard of the branch.
Often the commit exists locally, so the agent's own `git log` looks consistent with the
claim.

**Why.** Push failures (auth, hooks, network, protected branches) arrive after the
narrative is already written, and the failure output scrolls past unread.

**proofgate piece.** verify-gate: the judge reads the transcript for an actual `git push`
/ `gh pr merge` (or output showing the branch already up to date) before it accepts a
"pushed"/"merged" claim; a bare claim with no such command in the trace is blocked.

### 3. The unsent send

**What happens.** "Sent the notification / message / report." No send-class command
exists anywhere in the session. The thing was composed, perhaps beautifully, and never
transmitted.

**Why.** Composing the artifact and sending it are adjacent steps; the narrative
completes after the first one.

**proofgate piece.** verify-gate: the judge looks in the transcript for a send-class
command (`curl`, `mail`, `gh pr comment`, …) before accepting a "sent"/"posted" claim.
No send command in the trace, no stop.

---

## Test integrity

### 4. Green by narration

**What happens.** "All tests pass." The tests were never run — or were run, failed, and
the failure was summarized as a pass.

**Why.** Asserting green is the lowest-energy path to ending a coding task, and in long
sessions the most recent actual test run can be many turns and several edits stale.

**proofgate piece.** verify-gate: the judge reads the actual test-command output in the
transcript (e.g. `4 passed`, `FAILED`, `0 passed`). A "tests pass / green" claim that the
visible output does not support — or that has no test run at all — blocks the stop.

### 5. Test surgery

**What happens.** A failing test is edited, skipped, or deleted until the suite goes
green. The assertion that caught the bug is the casualty.

**Why.** To an agent optimizing for "suite green," weakening the test and fixing the
code are interchangeable moves — one is much faster.

**proofgate piece.** verify-gate plus `/repro-test`: when the agent claims a test passes
after editing it, the judge looks in the transcript for that test actually being run with
passing output — a weakened test at least has to be run, in this session, with its real
result visible. Red-first proof that the test ever caught the bug is `/repro-test`
discipline (model-followed prose), not a hook guarantee in this release.

### 6. Fix without repro

**What happens.** A bug is declared fixed without a failing reproduction ever being
observed. Sometimes the fix is right. Sometimes the bug was never reproduced, the patch
addresses a guess, and the report comes back a week later.

**Why.** Writing a plausible patch is faster than building a repro, and nothing in the
default loop distinguishes "fixed" from "patched something nearby."

**proofgate piece.** the `/repro-test` skill: baseline-red discipline. The failing repro
is written and recorded first; the fix only counts when the same test flips to green.

### 7. Vacuous green (the mocked end-to-end)

**What happens.** The agent edits production code, runs a test suite that passes, and
reports "validated end-to-end." The tests were green — but they were *unit* tests with
the real boundary mocked: they asserted the command **string** a function builds while
the subprocess, server, or browser that string would drive was stubbed out. The code
that actually fails in production never ran. The suite is green and the claim is false at
the same time.

**Why.** A green unit run is the cheapest possible evidence, and "validated end-to-end"
is the strongest possible phrasing of it — the gap between the two is invisible in the
narrative. Mode 4 (green by narration) is a claim with *no* run behind it; this is a
claim with the *wrong kind* of run behind it, which is harder to catch because the green
is genuine.

**The honest hard part.** A `Stop` hook cannot statically prove a passing test was
mocked — a mock behind a covered line looks identical to a real call. proofgate does not
pretend to detect the mock. What it can do is read the transcript and judge whether an
end-to-end claim is backed by end-to-end-grade evidence in the actual tool output: did a
browser/e2e runner actually run and pass, or did the agent edit production code, run a
unit suite, and *call that* "validated end-to-end"? The LLM judge sees the commands and
their output, so it can tell "added an end-to-end test" (a description, fine) from
"exercised the real path" asserted over nothing but a mocked unit pass.

**What this does NOT do (stated plainly, because the project's identity is honesty about
its own limits):**

- Coverage proves a line *executed*, not that it executed against an un-mocked
  collaborator. A test that mocks the subprocess but still calls the function under test
  will look covered. Neither a hook nor an LLM reading the transcript can certify the
  boundary was real — at best the judge flags a unit-only run dressed up as end-to-end.
- The judge reads what is in the transcript. An agent that narrates "checked the real
  path manually" with nothing in the trace gives the judge little to go on; the honest
  move remains the agent's — show the e2e run, or say "unit tests pass" plainly.

**proofgate piece.** verify-gate Stop hook — the LLM judge reads the session transcript
(tool calls + their real outputs) and weighs an end-to-end claim against whether a
real-path run actually appears and passed, instead of accepting a unit pass as proof.
`bin/prove-cov` still records coverage receipts for `/repro-test` discipline; their value
to the gate is that running them puts the evidence in the transcript the judge reads.

---

## Commands

### 8. Blast-radius commands

**What happens.** Mid-task, the agent reaches for `rm -rf`, `git reset --hard`, a
force-push, or tears down a live terminal workspace — frequently as collateral in a
cleanup it invented for itself.

**Why.** Destructive commands are the fastest path out of a confusing state, and the
agent does not carry the cost of being wrong.

**The numbers.** Head-parsed across 93,931 corpus commands: 682 destructive invocations
(0.73%) — 365 workspace teardowns, 263 `rm -rf`, 30 `git reset --hard`, plus a tail of
other destructive heads. All 682 were true invocations; substring matching would have
added false positives from greps and heredocs *about* these commands.

**proofgate piece.** gatekeeper (`PreToolUse` hook): parses command heads per segment,
matches `rules.tsv`, answers deny/ask. Human overrides via `pg-grant` — TTY-gated, so
the agent cannot grant itself.

---

## Instructions

### 9. Instruction decay

**What happens.** A standing rule ("always X before Y") holds for a while, then silently
stops being followed. In the corpus, rules had to be re-taught within 3 days on average.

**Why.** Context windows roll, compaction summarizes, and prose rules have no mechanism.
Memory is not enforcement.

**proofgate piece.** the `/codify` skill: converts the correction into a hook-enforced
rule, measured against your corpus before enabling. Hooks do not decay.

### 10. In-context violation

**What happens.** The rule is right there — stated minutes ago, still verbatim in
context — and the agent violates it anyway.

**Why.** Presence in context is not the same as weight at the decision point. Under
competing pressures (finish the task, satisfy the latest message), a rule from twenty
turns of attention ago loses.

**proofgate piece.** the harness, structurally: gates evaluate at the moment of action
(`PreToolUse`, `Stop`), not at the moment of instruction. The injectors restate
discipline precisely on the turns where it is needed.

### 11. The re-issued order

**What happens.** The user has to say it twice. The first instruction was clear; the
agent asked for confirmation, hedged, or did something adjacent — and the user
escalates: "just do it", "I said", "stop asking".

**Why.** Standing caution rules and the live user command compete at the decision
point, and the agent mis-weighs them — treating agent-scoped rules as binding on the
human's direct order, or asking before reversible actions that need no permission.

**proofgate piece.** the direct-order injector (`UserPromptSubmit` hook): detects
escalated-order phrasing — 0.4% of corpus prompts, so near-zero friction — and injects
an authority card: a live user command overrides standing rules that user wrote,
reversible actions are pre-approved, ask only before irreversible external effects.

---

## Interaction

### 12. The pushback fold

**What happens.** The agent is right. The user pushes back. The agent immediately
abandons the correct position, agrees, and "fixes" working code to match the objection.

**Why.** Agreement is the lowest-friction response to disagreement, and the training
gradient toward being agreeable does not check who is correct.

**proofgate piece.** the pushback injector (`UserPromptSubmit` hook): detects pushback
turns — 0.1% of corpus prompts — and injects a reconcile card: the user's direct
observation is ground truth, run the reconciling check before replying, and never
re-assert a claim without new evidence.

---

## Process

### 13. Deferred-work amnesia

**What happens.** "I'll handle that after the current step." The current step ends. The
deferred item is never seen again — until it resurfaces as an incident.

**Why.** Deferrals live only in the narrative, and narratives get compacted. There is no
queue unless something maintains one.

**proofgate piece.** the `/defer` skill plus verify-gate: a deferral becomes a durable
artifact — a DEFERRALS.md line with a concrete trigger condition, and a GitHub issue
when possible. The Stop gate blocks a session that ends on deferral language with no
such artifact created in the session.

---

## Reading the data honestly

Two cross-cutting lessons from the corpus shaped every gate above:

1. **Check state, not vocabulary.** Claim-sounding language is mostly honest
   (26–55% benign fire-rate for naive regexes); checkable state is unambiguous.
2. **Parse, don't grep.** Substring command matching cannot tell a dangerous command
   from a sentence about one; head parsing measured 0.73% fire-rate with zero false
   positives on the same corpus.

Your own distribution will differ. Run `/reliability-audit` to derive your modes and
measure your rules — locally, against your own transcripts.
