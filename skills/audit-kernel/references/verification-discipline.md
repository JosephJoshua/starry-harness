# Verification Discipline

Anti-hallucination protocol for the audit-kernel skill. Every finding produced by this system must survive the full verification pipeline described below. No exceptions. No shortcuts.

---

## Verification Tier System

All evidence produced or encountered during kernel auditing falls into exactly one of seven tiers. The tier determines whether a finding may be reported, how it must be documented, and what follow-up action is required.

### Tier 1 — Executable Evidence

A test produces different output on Linux versus StarryOS, or a test fails on StarryOS while passing on Linux. This is the strongest form of evidence. The finding is mechanically reproducible: run the test, observe the divergence.

**Example:** `test_mremap` returns `EINVAL` on StarryOS but succeeds and returns a valid address on Linux.

**Requirements:**
- The test must have passed Linux validation (see Test Correctness Chain below).
- The exact command, output on Linux, and output on StarryOS must be recorded.
- The test must be re-runnable. If someone else runs it and gets the same divergence, the finding stands.

### Tier 2 — Source-Level Proof

The code is visibly wrong by reading it. Any competent reviewer can verify the bug by looking at the cited line. No test execution is strictly required, though a confirming test strengthens the finding.

**Example:** Line 220 of `io.rs` calls `read_at` but the enclosing function is `sys_pwritev2` — a copy-paste error where a read operation is performed inside a write syscall implementation.

**Requirements:**
- Cite the exact file path and line number.
- Explain what the code does versus what it should do, referencing the function's contract or the relevant specification.
- The error must be unambiguous. If reasonable people could disagree about whether the code is wrong, this is not tier 2 — it is tier 5 or tier 6.

### Tier 3 — Property Violation

A measurable invariant is broken. The invariant must be defined before running the test — not discovered post-hoc by fishing through output.

**Example:** After 1000 `fork`+`exit` cycles, `/proc/meminfo` shows RSS grew by 50 MB — a memory leak. The invariant ("RSS must not grow monotonically under repeated identical workload") was specified before the test ran.

**Requirements:**
- State the invariant explicitly before presenting results.
- Provide the measurement methodology: what was measured, how, at what points.
- Show the before-value and after-value (or the trend over time for monotonic-growth checks).
- Ensure the workload is identical across iterations. Changing the workload between measurements invalidates the finding.

### Tier 4 — Differential Behavior

Behavior changes between configurations that differ in exactly one variable: SMP count, memory size, architecture, or similar.

**Example:** `test_futex` passes at `SMP=1` but deadlocks at `SMP=4`. The only variable changed is the SMP count.

**Requirements:**
- The two configurations must differ in exactly one variable. If more than one thing changed, the finding is inconclusive.
- Document both configurations fully (QEMU command line, kernel config, etc.).
- Run each configuration multiple times. A single pass/fail pair is insufficient for concurrency-related findings — run at least 10 iterations per configuration and report the failure rate.

### Tier 5 — Linux Source Comparison

The StarryOS implementation diverges from the Linux kernel source in a way that affects correctness. Establishing this requires reading both codebases and citing both source locations.

**Example:** Linux's `mm/mremap.c:move_vma()` handles `MREMAP_MAYMOVE` by checking for overlapping VMAs before relocating the mapping; StarryOS's `mmap.rs` ignores the `MREMAP_MAYMOVE` flag entirely and always returns `EINVAL` when the region cannot be expanded in place.

**Requirements:**
- Cite the Linux source file, function, and line (with kernel version, e.g., Linux 6.6).
- Cite the StarryOS source file, function, and line (with commit hash).
- Explain the semantic difference — not just "the code looks different" but what behavioral consequence the divergence produces.
- Confirm that the divergence affects correctness. A stylistic difference or an optimization that produces the same result is not a finding. The divergence must cause wrong output, a crash, data loss, or a spec violation.

### Tier 6 — Static Pattern Match

Code matches a known-bad pattern. Examples: `unsafe` block without a `SAFETY` comment, a lock acquired inside another lock's critical section, unchecked arithmetic on user-supplied values.

**This tier has a high false-positive rate.** Many pattern matches are intentional, safe, or guarded by invariants not visible at the match site.

**NEVER report a tier 6 match as a confirmed bug.** Use tier 6 matches exclusively to guide test design. The workflow is: observe the pattern, design a tier 1 or tier 3 test that would fail if the pattern represents a real bug, run the test, and report the test result (which will be tier 1 or tier 3).

### Tier 7 — LLM Reasoning

Speculation based on training data and pattern recognition. "I think this could deadlock because..." or "This looks like a TOCTOU race."

**NEVER report tier 7 findings. Ever.** The only valid action upon reaching a tier 7 suspicion is to escalate: design a test that would produce tier 1-4 evidence if the suspicion is correct, then run that test. If the test passes on both platforms, the suspicion was wrong — document that the hypothesis was disproved and move on. If the test reveals a divergence, report the tier 1-4 result, not the original suspicion.

---

## Reporting Rules

These rules are non-negotiable. Violating any of them invalidates the entire audit output.

1. **Only report tier 1-5 findings.** Tier 6 and tier 7 must never appear in a finding report. They may appear in internal working notes as hypotheses under investigation, but never in output delivered to the user.

2. **Every finding must cite exact evidence.** For source-level findings: file path, line number, and the relevant code snippet. For executable findings: test name, the command used to run it, and the verbatim output (or a diff of Linux vs. StarryOS output). For property violations: the metric name, the before-value, the after-value, and the measurement methodology.

3. **Tier 6-7 suspicions trigger test design, not reports.** When a pattern match or reasoning-based suspicion arises, the immediate next step is always: "Design a test to prove or disprove this." Document the suspicion in working notes, design the test, run the test, and report only the test result.

4. **Disproving a hypothesis is a valid result.** If a test designed to surface a suspected bug passes on both Linux and StarryOS, that is useful information. Document: "Hypothesis X was tested by test Y; the test passed on both platforms; no bug confirmed." Then move on. Do not re-litigate disproved hypotheses.

5. **Never use hedging language in findings.** Prohibited phrases: "this could be a bug," "this might cause issues," "this looks suspicious," "potential vulnerability," "possible race condition." Either the evidence proves the finding or it does not. If the evidence is insufficient, do not report — go gather more evidence or drop the finding.

6. **Check for duplicates before reporting.** Every finding must be compared against the contents of `known.json`. If the same root cause is already documented, do not report it again. If the finding is related but distinct (e.g., the same missing feature causes failures in two different syscalls), reference the existing entry and document the new manifestation.

---

## Test Correctness Chain

A finding is only as reliable as the test that produced it. A buggy test produces false findings. The following protocol ensures test correctness.

### Step 1: Linux Validation (Mandatory)

Every test MUST pass on a reference Linux system before its StarryOS results carry any weight. Run the test via `linux-ref-test.sh` in a Docker container running a known-good Linux kernel.

- If the test fails on Linux, the test has a bug. Fix the test. Do not proceed to StarryOS testing until the test passes on Linux.
- If the test produces non-deterministic results on Linux (passes sometimes, fails sometimes), the test has a flakiness bug. Fix the test or, if the non-determinism is inherent to what is being tested (e.g., timing-sensitive concurrency), document the expected failure rate on Linux and run enough iterations to establish a statistical baseline.

### Step 2: Single-Behavior Principle

Each test must test exactly one specific behavior. The test name must describe that behavior. The failure message must make the violated invariant obvious without requiring the reader to study the test source.

- **Good:** `test_mmap_cow_fork` tests that a COW page mapped via `mmap` is properly copied on `fork`, so that writes in the child do not appear in the parent.
- **Bad:** `test_mmap_stuff` tests several mmap behaviors in sequence. If it fails, the failure message says "assertion failed on line 47" and the reader must open the source to understand what went wrong.

When a test covers multiple behaviors, split it. One behavior, one test, one clear failure message.

### Step 3: Explicit Expected Values

Tests must assert specific expected values, not merely the absence of errors.

- **Bad:** `EXPECT_OK(result)` — This only checks that no error was returned. The syscall could return the wrong value and this test would pass.
- **Good:** `EXPECT_EQ(result, 4096)` — This checks that the syscall returned exactly 4096, which is the expected number of bytes written for this specific test case.

The expected value must come from one of two sources: (a) the Linux reference run, or (b) the POSIX/Linux specification. Never guess expected values. Never hard-code values that were derived from running the test on StarryOS — that would make the test tautological.

### Step 4: Mutation Validation (Recommended for Critical Findings)

For findings that will drive significant engineering effort (e.g., "the entire mremap implementation is broken"), validate that the test is actually capable of detecting the class of bug in question.

The procedure:

1. Write a small standalone C program that simulates the bug. For example, if the finding is "mremap returns the wrong address," write a wrapper that intercepts mremap and returns a deliberately wrong address.
2. Run the test against the simulated bug (e.g., via `LD_PRELOAD` on Linux).
3. Verify that the test fails, detecting the simulated bug.
4. This proves the test can catch the real bug if it exists. Without this step, it is possible that the test passes for reasons unrelated to the bug (e.g., it never exercises the buggy code path).

Mutation validation is recommended, not mandatory, but omitting it for high-impact findings weakens the report.

### Step 5: Idempotency

Run every test at least twice in succession. Both runs must produce the same result. If they do not, the test has one of these problems:

- **State leakage:** The test modifies global state (files, shared memory, kernel parameters) and does not clean up. Fix the test to restore initial state in its teardown.
- **Non-deterministic dependency:** The test depends on timing, scheduling order, or random values. Either eliminate the non-determinism (use deterministic seeds, insert synchronization barriers) or explicitly mark the test as a stress test with a documented expected pass rate.

A test that passes on one run and fails on the next is not evidence of a kernel bug. It is evidence of a test bug — until proven otherwise.

---

## Anti-Hallucination Checklist

Before reporting ANY finding, verify every item on this checklist. If any item is not satisfied, the finding must not be reported.

- [ ] **Tier check:** The finding is tier 1-5. Tier 6-7 findings are never reported.
- [ ] **Evidence cited:** The exact evidence is included — file:line for source findings, test name + verbatim output for executable findings, metric before/after for property findings.
- [ ] **Linux baseline established:** The test passed on Linux. The test itself is correct.
- [ ] **Test was actually executed:** The test was run, not merely designed. "I would expect this test to fail" is not a finding. "This test failed with output X" is a finding.
- [ ] **Reproducibility confirmed:** The result was reproduced on a second run, or (for concurrency bugs) a failure rate was established over multiple runs and documented.
- [ ] **Not a duplicate:** The finding does not duplicate an entry already in `known.json`.

---

## Common Hallucination Traps

These are specific failure modes that LLM-driven analysis falls into repeatedly. Recognizing them prevents wasted effort and false reports.

### Trap 1: "This Looks Like It Could Race"

Reading code and observing shared state accessed without a lock is NOT evidence of a data race. Shared state may be protected by a higher-level lock, by architectural invariants (e.g., single-writer guarantees), by atomic operations not visible at the call site, or by the simple fact that the code path is only reachable from a single-threaded context.

**Correct response:** Write a stress test that exercises the suspected race with SMP sweeping (SMP=1, 2, 4, 8). Run each configuration at least 100 times. If the test never fails across all configurations and all iterations, there is no confirmed race. Document the negative result and move on. Do not report "possible race" — that is tier 7.

### Trap 2: "This Function Doesn't Handle X"

The absence of visible handling for case X in a function does not mean case X is unhandled. It may be handled by:

- A caller that filters out case X before invoking the function.
- A trait implementation or vtable dispatch that routes case X to a different function entirely.
- A macro expansion that generates the handling code at compile time.
- A default/fallback path that covers case X implicitly.

**Correct response:** Grep the full codebase for references to the function and the case in question. Trace the call chain from the syscall entry point to the function under analysis. Only after confirming that no path handles case X should a test be designed. And even then, the test result — not the code reading — is the finding.

### Trap 3: "Linux Does Y, So StarryOS Should Too"

The Linux kernel has many behaviors that are implementation-specific, not required by POSIX or any other specification. Examples: specific ordering of entries in `/proc`, exact values of certain `rlimit` defaults, internal heuristics for memory overcommit. Divergence from Linux in these areas is not a bug — it is a design choice.

**Correct response:** Only flag divergences that affect correctness as defined by: wrong return value for a specified interface, crash, data corruption, or deadlock. Consult the relevant specification (POSIX, Linux man pages section 2/3, or the documented StarryOS interface contract) to determine whether the divergent behavior is actually wrong. If no specification mandates a particular behavior, the divergence is not reportable.

### Trap 4: "This Unsafe Block Is Unsound"

Claiming unsoundness in Rust requires proving that safe code — code written without the `unsafe` keyword — can trigger undefined behavior by interacting with the `unsafe` block. The mere presence of `unsafe` is not a bug. Rust kernels necessarily contain `unsafe` blocks for hardware interaction, raw pointer manipulation, and FFI.

**Correct response:** To claim unsoundness, demonstrate a specific sequence of safe API calls that causes the `unsafe` block to violate its invariants. Identify the exact invariant (e.g., "this pointer must be non-null and aligned"), show the safe API path that can violate it (e.g., "calling `resize(0)` followed by `get(0)` passes a dangling pointer to the unsafe block"), and ideally write a test that triggers the UB under Miri or AddressSanitizer. Without this chain of reasoning grounded in concrete API calls, do not claim unsoundness.

### Trap 5: "Memory Leak Detected"

Not all persistent allocations are leaks. A one-time allocation that persists for the lifetime of the kernel (e.g., a slab cache, a routing table, a device driver structure) is not a leak — it is intentional long-lived state. A leak is a monotonically growing allocation under repeated identical workload with no upper bound.

**Correct response:** Measure memory usage before and after with the SAME workload, repeated N times. Plot the measurements. If memory usage is flat (or bounded), there is no leak. If memory usage grows linearly with iteration count and never recedes, that is a leak. Report the slope (bytes per iteration), the workload, and the measurement methodology. A single before/after measurement is insufficient — take at least 10 data points across iterations to establish the trend.

### Trap 6: "Error Path Not Tested"

Observing that an error path exists in the code and asserting that it is "not tested" or "probably broken" is tier 7 reasoning. Error paths may be tested by other tests not yet examined, may be unreachable in practice, or may be correct despite never being exercised.

**Correct response:** Write a test that deliberately triggers the error path (e.g., pass an invalid argument, exhaust memory, inject a fault). Run the test on Linux to confirm the error path behaves as expected. Run it on StarryOS. Report the result, not the speculation.

### Trap 7: "This TODO/FIXME Means It's Broken"

TODO and FIXME comments indicate known technical debt, not confirmed bugs. The annotated code may work correctly for all currently supported use cases. The comment may refer to a future optimization, an edge case that is unreachable in practice, or a known limitation that is already documented.

**Correct response:** Read the TODO/FIXME comment in full context. If it describes a specific failure mode (e.g., "TODO: handle the case where size > 2GB"), write a test that exercises that case. Report the test result. Do not report the TODO comment itself as a finding.

---

## Escalation Protocol

When analysis produces a tier 6 or tier 7 suspicion, follow this escalation sequence without exception:

1. **Document the suspicion internally.** Record what was observed, why it seems problematic, and what the hypothesized bug is.
2. **Design a test.** The test must be capable of distinguishing between "bug exists" and "bug does not exist." It must target the specific hypothesized failure mode.
3. **Validate the test on Linux.** Confirm the test passes on the reference platform.
4. **Run the test on StarryOS.** Record the output verbatim.
5. **Evaluate the result.** If the test reveals a divergence, report it at the appropriate tier (1-4). If the test passes, document the disproved hypothesis and move on.

Never skip steps. Never report before reaching step 5. The entire value of this protocol lies in forcing every suspicion through the gauntlet of empirical verification.

---

## Summary of Absolute Rules

These rules admit no exceptions:

- No tier 6 or tier 7 findings in reports. Ever.
- No hedging language. Prove it or drop it.
- No findings without cited evidence.
- No test results without Linux validation.
- No claims of "possible" bugs. Binary outcomes only: confirmed bug with evidence, or no finding.
- Disproved hypotheses are documented as negative results, not suppressed.
- Every suspicion must be escalated to a test. Reasoning alone is never sufficient.
