---
name: repro-test
description: Turn a reported bug scenario into a failing test BEFORE any fix. Use when the user reports a bug with reproduction details, or invokes /proofgate:repro-test <scenario>. Red first, then green, then prove.
---

# repro-test

Translate the scenario in `$ARGUMENTS` into a failing test before touching product code. The order is fixed: clause map → red test → fix → green → prove.

## 1. Map the scenario clause by clause

Split the user's scenario into clauses. For each clause, state the assertion it implies. Show the table:

```
clause                                  → assertion
"when I submit an empty form"           → call submit({}) in the test
"the API returns 500"                   → assert current (buggy) response is 500
"it should return 400 with a message"   → assert response == 400 and body.error is non-empty
```

If any clause cannot be asserted mechanically, name it and ask the user how to observe it before proceeding. Do not silently drop clauses.

## 2. Write the failing test

- Put it in the project's existing test layout; name it after the bug (`test_<symptom>_repro`).
- Cover every clause from the table. One scenario, one test; do not pad with unrelated assertions.
- Touch NO product code in this step.

## 3. Run it — require RED

Run the test. It must FAIL on current code, for the reason the user described (not a typo, import error, or fixture problem — read the failure output and confirm it is the bug).

If it PASSES: the test does not capture the bug. Rewrite the test. Do not touch product code to make the test fail. Repeat until you have a genuine red, or report to the user that the scenario does not reproduce and show exactly what you ran.

## 4. Implement the fix

Minimal change that makes the test pass. No drive-by refactors.

## 5. Rerun — require GREEN

Rerun the repro test, then the full test file it lives in. All green or keep fixing.

## 6. Prove it

Run the proof step with a one-line claim and the exact test command:

```sh
"${CLAUDE_PLUGIN_ROOT}/bin/prove" "repro test green after fix" -- <test command>
```

If the prove helper is absent, rerun the exact test command and paste the full, unedited output.

## 7. Report

End with: the clause→assertion table, the red output (trimmed to the failure), the green output, and the proof command used.
