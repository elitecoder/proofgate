---
name: defer
description: The only sanctioned way to defer work. Use when a task is consciously postponed ("later", "not now", "follow-up", "TODO for next sprint") or the user invokes /proofgate:defer <item>. Records the deferral as a durable artifact with a concrete trigger condition.
---

# defer

Record the deferral in `$ARGUMENTS` as durable artifacts. Silent deferral is forbidden — if work is postponed, it goes through this skill or it does not get postponed.

## 1. Demand a concrete trigger condition

A deferral without a trigger is a euphemism for "never". Require one of:

- a date ("after 2026-07-01")
- an event ("when the v3 API ships", "after the next release tag")
- a metric threshold ("when p95 latency exceeds 500ms")
- a dependency ("once the auth refactor lands")

If `$ARGUMENTS` lacks one, ask exactly: "What concrete condition makes this actionable? (date / event / threshold / dependency)" and stop. Do not write anything until you have it. Refuse vague triggers like "someday", "when there's time", "if needed".

## 2. Append to DEFERRALS.md at repo root

```sh
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPO_NAME="$(basename "$REPO_ROOT")"
```

Create `$REPO_ROOT/DEFERRALS.md` if absent with header `# Deferrals` and a blank line. Append:

```
- [ ] <YYYY-MM-DD> | <repo-name> | <item, one line> | trigger: <condition>
```

## 3. Create a GitHub issue when possible

If `gh repo view --json nameWithOwner` succeeds in `$REPO_ROOT`:

```sh
gh label create deferred --description "Deferred via proofgate" --color FBCA04 2>/dev/null || true
gh issue create --title "Deferred: <item>" --label deferred \
  --body "<item>

Trigger condition: <condition>
Logged in DEFERRALS.md on <YYYY-MM-DD>."
```

If `gh` is missing or the repo has no remote, skip this step and say so — the file artifact alone is acceptable.

## 4. Echo both artifacts

End with the exact DEFERRALS.md line appended and the issue URL (or "no GitHub issue: <reason>"). Both must be visible to the user — a deferral the user cannot see did not happen.
