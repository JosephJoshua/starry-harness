---
name: audit-kernel
description: This skill should be used when the user asks to "audit the kernel", "review kernel internals", "find concurrency bugs", "check for deadlocks", "find memory leaks", "audit scheduler", "review memory management", "stress test", "find races", "check lock ordering", "audit signal handling", "find improvements", "review kernel architecture", or wants to go beyond syscall testing to analyze StarryOS kernel internals for bugs, performance issues, and improvement opportunities.
---

# StarryOS Kernel Internal Audit

Systematic workflow for analyzing kernel internals beyond syscall correctness. Covers the scheduler, memory manager, concurrency primitives, signal delivery, filesystem, and process lifecycle — areas where there is no man page to compare against.

## Anti-Hallucination Discipline

Every finding MUST be grounded in verifiable evidence. Consult `references/verification-discipline.md` for the full protocol. The short version:

**Verification Tiers (only report tier 1-5):**

| Tier | Evidence | Example |
|------|----------|---------|
| 1 | Executable — test produces different result | "SMP=1 passes, SMP=4 deadlocks" |
| 2 | Source-level proof — code visibly wrong | "Line 220 calls read_at in a write path" |
| 3 | Property violation — measurable invariant broken | "RSS grew 50MB after 1000 fork+exit cycles" |
| 4 | Differential — behavior changes with config | "Works with 128MB RAM, crashes at 64MB" |
| 5 | Linux source comparison — StarryOS diverges from Linux impl | "Linux's do_mremap handles X; StarryOS doesn't" |

**Never report tier 6-7** (pattern guesses, LLM reasoning) as findings. If a code-reading suspicion arises, **write a test first** to elevate it to tier 1-4 before reporting.

## Workflow: Suspect → Test → Prove → Report

Unlike hunt-bugs (which starts from man page specs), kernel auditing starts from **code reading** and must escalate to **executable evidence**:

1. **Select subsystem** — Pick from the catalog in `references/kernel-audit-areas.md`
2. **Read source** — Understand the implementation, identify suspect patterns
3. **Form hypothesis** — "I suspect X could cause Y under condition Z"
4. **Design test** — Write a C test that creates condition Z and checks for Y
5. **Validate test on Linux** — Run on Docker Linux first. The test MUST pass. If it fails, the test is wrong.
6. **Mutation check** (optional but recommended) — Temporarily simulate the suspected bug in a toy program and verify the test catches it
7. **Run on StarryOS** — Execute via the QEMU pipeline
8. **For concurrency bugs** — Use `${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh` with SMP sweeping
9. **Classify evidence tier** — Only report if tier 1-5
10. **Report** — Bug report + journal entry

## Test Correctness Protocol

Every test goes through this validation chain before its results are trusted:

```
Write test → Run on Linux (MUST pass) → Run on StarryOS → Compare
                  ↓ fails                       ↓ differs
            Fix the test                   Real bug found
            (test was wrong)               (report it)
```

**If a test fails on Linux**, the test has a bug — fix the test, do not report a kernel bug.

**If a test passes on both Linux and StarryOS**, the hypothesis was wrong — document it and move on. Not finding a bug is a valid result.

**Mutation validation** (for high-stakes findings): Write a small standalone program that simulates the suspected bug. Verify the test catches the simulated bug. This proves the test is actually capable of detecting the class of bug being looked for.

## Concurrency Bug Reproduction

Concurrency bugs are non-deterministic. Use **controlled amplification** to make them manifest reliably. Full techniques in `references/concurrency-reproduction.md`. The key tools:

### SMP Sweeping
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test_name> --runs 100 --smp 1,2,4
```
If SMP=1 passes and SMP=4 fails → concurrency bug confirmed (tier 1 evidence).

### Repeat Amplification
Run the test 100+ times. Even a 1% failure rate proves the bug exists. The failure rate itself is the metric — report it as "fails N/100 runs at SMP=4."

### Timeout Deadlock Detection
The stress-test script uses a configurable timeout. If QEMU doesn't exit within the timeout, the test deadlocked. Report the timeout count across runs.

### Yield Injection (for targeted reproduction)
When a specific race is suspected between two code points:
1. Insert `axtask::yield_now()` at point A in the kernel source
2. This forces a context switch, widening the race window
3. Run the test — if the bug manifests, the race is confirmed
4. Remove the yield and document the race window

### Memory Pressure
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test_name> --memory 128M
```
Reduced memory forces more page faults, OOM paths, and allocation failures.

## Property-Based Tests

For kernel internals without specs, test **properties** — invariants that must always hold:

| Property | Test Design | Detects |
|----------|-------------|---------|
| No memory leak | Measure RSS before/after N fork+exit cycles | Memory leaks |
| No zombie accumulation | Count processes before/after fork+wait cycles | Process leaks |
| Scheduler fairness | N threads each count iterations; ratio should be ~1:1 | Starvation |
| Lock-free progress | Thread makes progress within bounded time | Deadlock/livelock |
| Signal delivery | Signal arrives within 1 scheduling period | Signal loss |
| COW correctness | Parent and child see correct data after fork+write | COW bugs |
| File data integrity | Write pattern → read back → compare | FS corruption |

## Kernel Subsystem Audit Areas

See `references/kernel-audit-areas.md` for the full catalog. Summary:

| Subsystem | Key Source Paths | What to Audit |
|-----------|-----------------|---------------|
| Scheduler | `components/axsched/`, `os/arceos/modules/axtask/` | Fairness, starvation, SMP load balance |
| Memory | `components/starry-vm/`, `os/arceos/modules/axmm/` | Leaks, COW, page fault handling, OOM |
| Concurrency | `components/kspin/`, `os/arceos/modules/axsync/` | Lock ordering, deadlock, atomicity |
| Signals | `components/starry-signal/`, `kernel/src/task/signal.rs` | Delivery races, masking, nested signals |
| Process | `components/starry-process/`, `kernel/src/task/` | Zombie leaks, orphan reparenting, exec races |
| Filesystem | `os/arceos/modules/axfs/`, `components/rsext4/` | Data integrity, concurrent access, crash safety |
| Networking | `os/arceos/modules/axnet/`, `components/starry-smoltcp/` | TCP state machine, buffer leaks, connection lifecycle |

## Agents

- **kernel-reviewer** — Read-only source analysis, identifies suspect patterns (tier 5-6)
- **linux-comparator** — Runs tests on both platforms, provides tier 1 evidence
- **bug-triager** — Classifies findings into competition categories

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh` | Multi-run SMP-sweeping test runner with deadlock detection |
| `${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh` | Docker Linux test runner for test validation |
| `${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh` | Journal entry for findings |

## Additional Resources

### Reference Files
- **`references/verification-discipline.md`** — Full anti-hallucination protocol, tier definitions, test correctness chain
- **`references/kernel-audit-areas.md`** — Detailed audit catalog for each kernel subsystem with specific code paths and what to look for
- **`references/concurrency-reproduction.md`** — Techniques for reproducing races, deadlocks, and non-deterministic bugs
