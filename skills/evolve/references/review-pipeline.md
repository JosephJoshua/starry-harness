# Adaptive Review Pipeline

Every fix produced by the evolve skill passes through this pipeline before it can be committed or presented to a human. The pipeline is a convergence loop, not a fixed checklist. Rounds run until confidence reaches a threshold. Disagreement between reviewers triggers additional rounds. The pipeline adapts based on bug severity and what each round reveals.

Do not treat this as a sequential gate. Treat it as a feedback system that tightens until it either converges or escalates.

## Convergence Flow

```
Fix produced
    |
    v
Round 1: Self-check
    |-- FAIL --> revise fix, restart Round 1
    |-- PASS
    v
Round 2: kernel-reviewer agent (fresh context)
    |-- critical findings --> revise fix, restart Round 1
    |-- minor findings --> apply, continue
    |-- 0 findings + severity == P3 --> skip re-derivation, jump to Round 3
    |-- PASS
    v
Severity gate: how many re-derivers?
    |-- P3 (cosmetic): 0 --> skip to Round 3
    |-- P2 (moderate): 1 re-deriver
    |-- P1 (significant): 2 re-derivers
    |-- P0 (critical): 3 re-derivers
    v
Round 2+N: Independent re-derivation(s)
    |-- all agree with original fix --> Round 3
    |-- any disagree --> Reconciliation
    v
Reconciliation (if triggered)
    |
    v
Re-review: kernel-reviewer on reconciled fix
    |-- critical findings --> revise, re-reconcile
    |-- PASS
    v
Round 3: Regression check (all tests + clippy + fmt)
    |-- 0 failures --> assess confidence
    |-- any failure --> revise fix, confidence resets, restart Round 1
    v
Confidence assessment
    |-- HIGH: all rounds agree + 0 regressions
    |       --> commit (autonomous) or present (human)
    |-- MEDIUM: partial agreement after reconciliation
    |       --> flag for human review with full history
    |-- LOW: cannot converge after max_rounds (default 8)
            --> escalate, do NOT commit
```

## Minimum Rounds

Run these three rounds for every fix regardless of severity.

### Round 1: Self-Check

Re-read the fix against the man page and the test output that revealed the bug. Verify:

- The fix addresses the root cause described in the bug report, not just the symptom.
- Return values and errno codes match the man page specification.
- Edge cases documented in the man page (null pointers, zero-length buffers, invalid flags) are handled.
- The fix does not introduce undefined behavior for inputs outside the test case.

If the self-check finds a deficiency, revise the fix in place and restart Round 1. Do not proceed with a fix that fails its own author's review.

### Round 2: kernel-reviewer Agent

Dispatch the `kernel-reviewer` agent with fresh context. Provide it the diff, the relevant source files, and the man page. Do NOT provide the fix author's reasoning or the bug report narrative. The reviewer must evaluate the code on its own merits without anchoring to the author's justification.

The kernel-reviewer evaluates:

- Rust idioms: proper use of `Result`, error propagation, no unnecessary `unwrap()` or `unsafe`.
- Safety: lock ordering consistency, no new deadlock paths, correct lifetime annotations.
- Reuse: does the fix duplicate logic that already exists in the kernel? Should it call an existing helper?
- API consistency: does the fix match the patterns used by neighboring syscall handlers?
- Completeness: does the fix handle all documented behaviors, or does it only fix the tested case?

Classify findings as critical (blocks commit) or minor (improve but do not block). If critical findings exist, revise the fix and restart from Round 1.

Early termination: if Round 2 produces 0 findings AND the bug severity is P3, skip re-derivation entirely and proceed to Round 3. A cosmetic fix that passes kernel-reviewer does not need independent re-derivation.

### Round 3: Regression Check

Run ALL existing tests, not just the test for the fixed syscall. A fix that corrects one behavior but breaks another is not a fix.

Execute in this order:

1. Run the full StarryOS test suite: build rootfs, boot QEMU, execute all test binaries in `os/StarryOS/tests/bin/`.
2. Run `cargo xtask clippy --package starry-kernel`.
3. Run `cargo fmt --check` on all modified files.

If any test fails or clippy/fmt produces errors:

- Identify whether the regression is caused by the fix or is a pre-existing failure.
- If caused by the fix: revise the fix and restart from Round 1. Reset confidence to zero.
- If pre-existing: document it and do not count it against the fix.

## Severity-Scaled Rounds

The number of independent re-derivation rounds scales with bug severity.

| Severity | Description | Re-derivers | Total rounds (typical) | Token budget |
|----------|-------------|-------------|----------------------|--------------|
| P3 | Cosmetic: wrong errno, missing flag, off-by-one in non-critical path | 0 | 3 | ~3K |
| P2 | Moderate: incorrect behavior for valid inputs, partial implementation | 1 | 4 | ~6K |
| P1 | Significant: security-relevant, affects multiple syscalls, memory safety | 2 | 5-6 | ~10K |
| P0 | Critical: data corruption, use-after-free, deadlock, concurrency race | 3 + mandatory reconciliation | 7-9 | ~20K |

P0 bugs always trigger the reconciliation protocol even if all three re-derivers agree with the original fix. The risk of correlated blind spots is too high at this severity level.

## Independent Re-Derivation Protocol

Each re-deriver operates in isolation. The goal is genuine independence: if two agents arrive at the same fix without seeing each other's work, confidence in the fix increases. If they diverge, the divergence reveals assumptions that need examination.

### Input provided to each re-deriver

Give each re-deriver ONLY:

- The bug description (what behavior is wrong, what the correct behavior should be).
- The relevant man page section.
- The relevant kernel source file(s) in their current state (before the fix).

Do NOT provide:

- The proposed fix.
- The fix author's reasoning or notes.
- Other re-derivers' outputs.
- The test case (to avoid overfitting the fix to the test).

### Instructions to each re-deriver

Ask the re-deriver to:

1. Read the man page specification for the syscall.
2. Read the kernel source and identify where the behavior deviates from the specification.
3. Produce a diff that corrects the deviation.
4. Explain the reasoning behind each change.

### Comparison

After all re-derivers complete, compare all proposed fixes (including the original):

- **Agreement**: all fixes change the same code locations in semantically equivalent ways. Minor syntactic differences (variable names, formatting, comment wording) do not count as disagreement.
- **Disagreement**: any fix differs in logic, error handling approach, code path, or addresses a different root cause.

If all fixes agree, proceed to Round 3. If any fix disagrees, trigger the reconciliation protocol.

## Reconciliation Protocol

Reconciliation is triggered by disagreement among re-derivers or mandatorily for P0 bugs.

### Reconciliation agent input

A reconciliation agent receives:

- ALL proposed fixes (original + each re-deriver's fix), each with its reasoning.
- The man page.
- The kernel source (pre-fix state).
- A summary of where the fixes agree and where they diverge.

### Reconciliation agent task

The reconciliation agent must:

1. Identify the points of divergence.
2. For each divergence, determine which approach is correct by consulting the man page specification and kernel conventions.
3. Produce a synthesized fix that addresses every concern raised by every proposal. This is not a majority vote. If one re-deriver identified an edge case that the others missed, the synthesis must handle that edge case.
4. Document the rationale for each decision, including why rejected approaches were rejected.

### Post-reconciliation review

The synthesized fix goes back through Round 2 (kernel-reviewer) for a fresh review. The kernel-reviewer has not seen the reconciliation process and evaluates the synthesis on its own merits.

If the kernel-reviewer finds new critical issues with the reconciled fix, another revision cycle begins: revise the synthesis, re-run the kernel-reviewer. This inner loop runs until the kernel-reviewer passes the fix or `max_rounds` is reached.

## External Tool Integration

The pipeline detects available external tools at runtime and uses them to fill re-derivation slots.

### Codex plugin

If the Codex plugin is installed (check for `codex:rescue` in available skills), allocate one re-derivation slot to Codex. A different model provides genuine independence: correlated reasoning failures between Claude and GPT are less likely than between two Claude instances.

Dispatch the Codex re-deriver using `codex:rescue` with the same isolated input (bug description, man page, kernel source). Parse its output as a proposed fix and include it in the comparison.

### Other review tools

If other code review or analysis tools are available (e.g., static analyzers, formal verification tools), they can fill additional re-derivation slots or augment existing ones. The pipeline does not hard-code tool names. Instead, check at runtime what is available and allocate accordingly.

Priority for slot allocation:

1. Codex (different model family, highest independence value).
2. Static analysis tools (different methodology, high independence value).
3. Additional Claude instances (same model, lower independence value but still useful for catching overlooked cases).

## Convergence Rules

### High confidence

All of the following must hold:

- Round 1 (self-check) passed without revision.
- Round 2 (kernel-reviewer) produced 0 critical findings.
- All re-derivers agreed with the fix (or re-derivation was skipped for P3).
- Round 3 (regression) produced 0 fix-caused failures.

Action: in autonomous mode, commit the fix directly. In human-driven mode, present the fix with a recommendation to commit.

### Medium confidence

Any of the following:

- Re-derivers initially disagreed but reconciliation produced a fix that passed kernel-reviewer and regression.
- The kernel-reviewer found minor (non-critical) issues that were addressed but raised questions about broader implications.
- One re-deriver agreed while another disagreed, and the disagreement was resolved but not unanimously.

Action: flag the fix for human review. Provide the full round history so the human can see the disagreement, the reconciliation reasoning, and the final fix. Do not commit automatically.

### Low confidence

Any of the following:

- The pipeline cannot converge after `max_rounds` (default 8) total rounds.
- The regression check fails repeatedly after revisions.
- The reconciliation agent cannot produce a synthesis that passes kernel-reviewer.
- Re-derivers disagree on the root cause itself (not just the fix approach).

Action: escalate. Do not commit. Present the full history and all proposed fixes. The human must decide.

### Regression override

Any regression in Round 3 that is caused by the fix resets confidence to zero regardless of prior round results. A fix that breaks existing tests is not ready, no matter how many reviewers approved it. Revise and restart from Round 1.

## Per-Bug Review Tracking

Track every round for every bug in `strategy.json` under the `reviews` key. This history serves two purposes: it provides an audit trail for competition judges, and it informs future pipeline tuning (which severity levels actually need reconciliation, which tool combinations produce the most agreement).

### JSON Schema

```json
"reviews": {
  "BUG-003": {
    "rounds": [
      {"type": "self-check", "result": "pass"},
      {"type": "kernel-review", "result": "pass", "findings": []},
      {"type": "re-derive", "agent": "claude-1", "agrees": true},
      {"type": "re-derive", "agent": "codex", "agrees": false, "divergence": "codex handles EINVAL for negative offset; original does not"},
      {"type": "reconcile", "synthesis": "add EINVAL check for negative offset before main logic", "inputs": ["original", "claude-1", "codex"]},
      {"type": "kernel-review", "result": "pass", "findings": []},
      {"type": "regression", "result": "0 failures", "tests_run": 17}
    ],
    "confidence": "high",
    "total_rounds": 7
  }
}
```

### Field definitions

| Field | Type | Description |
|-------|------|-------------|
| `rounds` | array | Ordered list of every round executed for this bug |
| `rounds[].type` | string | One of: `self-check`, `kernel-review`, `re-derive`, `reconcile`, `regression` |
| `rounds[].result` | string | `pass`, `fail`, or a summary |
| `rounds[].findings` | array | List of findings from kernel-reviewer (empty if none) |
| `rounds[].agent` | string | Identifier for the re-deriver (`claude-1`, `claude-2`, `codex`, etc.) |
| `rounds[].agrees` | boolean | Whether the re-deriver's fix agrees with the original |
| `rounds[].divergence` | string | Description of how the re-deriver's fix differs (only present when `agrees` is false) |
| `rounds[].synthesis` | string | Description of the reconciled fix |
| `rounds[].inputs` | array | Which proposals fed into the reconciliation |
| `rounds[].tests_run` | integer | Number of tests executed in the regression round |
| `confidence` | string | Final confidence level: `high`, `medium`, or `low` |
| `total_rounds` | integer | Total rounds executed (for budget tracking) |

## Token-Conscious Design

The review pipeline is the most expensive phase of a deep cycle. Control costs by scaling review effort to match risk.

### Budget by severity

| Severity | Typical rounds | Approximate tokens | Justification |
|----------|---------------|-------------------|---------------|
| P3 | 3 (self-check, kernel-review, regression) | ~3K | Low risk. If the kernel-reviewer finds nothing, there is no need for re-derivation. |
| P2 | 4 (add 1 re-deriver) | ~6K | Moderate risk. One independent check catches most oversights. |
| P1 | 5-6 (add 2 re-derivers, possible reconciliation) | ~10K | High risk. Two independent checks plus potential reconciliation are warranted. |
| P0 | 7-9 (add 3 re-derivers, mandatory reconciliation, re-review) | ~20K | Maximum risk. The cost of a missed bug (data corruption, deadlock) far exceeds the cost of extra review rounds. |

### Early termination

Apply these shortcuts to avoid wasting tokens on obviously clean fixes:

- If Round 2 (kernel-reviewer) produces 0 findings AND severity is P3, skip all re-derivation rounds. Proceed directly to Round 3 (regression). This saves ~1K tokens per P3 fix.
- If a re-deriver's output is semantically identical to the original fix within the first 200 tokens of its response, record agreement immediately without waiting for the full reasoning. The agreement is what matters, not the explanation.
- If Round 3 (regression) fails on the first test, abort the remaining tests. The fix needs revision regardless of how many other tests would have passed.

### Budget enforcement

Track cumulative token spend per bug in `strategy.json`. If a single bug's review pipeline exceeds 2x its severity budget (e.g., >40K tokens for a P0), pause and escalate. The pipeline may be stuck in a revision loop, and additional tokens are unlikely to produce convergence. Present the full history to the human and let them decide whether to continue or shelve the fix.

## Pipeline Invariants

These rules hold at all times. Violating any of them is a pipeline bug.

1. **No fix is committed with low confidence.** If the pipeline cannot converge, it escalates. It never commits a fix it is unsure about.
2. **Re-derivers never see the proposed fix.** The moment a re-deriver sees the original fix, it is no longer independent. Guard against accidental context leakage.
3. **Regression checks run the full test suite.** Never run only the test for the fixed syscall. Regressions appear in unexpected places.
4. **Reconciliation is synthesis, not voting.** The reconciliation agent does not pick the majority approach. It produces a new fix that addresses all concerns. A minority opinion that identifies a real edge case outweighs two agreeing proposals that missed it.
5. **Confidence resets on regression failure.** Prior round results do not carry over after a regression. The revised fix is a new fix and must be reviewed from scratch.
6. **P0 bugs always reconcile.** Even if all re-derivers agree, P0 fixes go through reconciliation. The reconciliation agent may catch a shared blind spot.
