---
name: evolve
description: This skill should be used when the user asks to "run the harness autonomously", "evolve", "auto-hunt", "autonomous mode", "self-evolving loop", "sweep syscalls", "deep dive", "what should I work on next", "pick next target", "run a sweep", "continuous improvement", or wants the starry-harness to autonomously select targets, run analysis cycles, and track progress.
---

# StarryOS Self-Evolving Harness

Orchestrates autonomous and human-driven kernel improvement cycles. Maintains persistent strategy state, selects targets based on coverage gaps and effectiveness history, alternates between broad sweeps and deep investigations, and enforces a mandatory multi-agent review pipeline.

## Non-Negotiable Principles

These are hard constraints. Violating any one of them invalidates the entire round.

1. **Linux defines correctness.** Linux return values, errno, output, side effects, blocking semantics, concurrency semantics, and resource cleanup semantics are the baseline. Never let StarryOS's current behavior retroactively define what is "correct."

2. **Test before fix.** Write a test that proves the bug exists on StarryOS and passes on Linux BEFORE modifying any kernel code. A fix without a pre-existing failing test is not a verified fix.

3. **Evidence before claims.** Any finding without tier 1-4 evidence is a "pending hypothesis," not a confirmed bug. Mark it explicitly as such. The reviewer will reject unsubstantiated claims.

4. **One bug per round.** Each cycle fixes one bug or investigates one target. No bundled changes. No drive-by refactors. No "while I'm here" additions.

5. **Harness before patch.** Every round must produce at least one reusable test asset (a test case, a pattern scanner rule, a regression check). The test outlives the fix.

6. **Deterministic tools first.** Run lock-order-graph.py, pattern-scanner.py, kernel-graph.py, change-tracker.py BEFORE applying LLM reasoning. Their output is ground truth that cannot hallucinate.

7. **Reviewer has veto power.** If the reviewer (kernel-reviewer agent, Codex, or human) says REVISE or REJECT, the round is not done. Address every specific objection.

## Modes

**Autonomous**: The system picks targets, runs cycles, and stops when the session budget is exhausted or no targets remain above minimum value. Invoke with "run autonomously" or "auto-hunt."

**Human-driven**: The system presents priorities and recommendations; the human picks the target. Invoke with "what should I work on next" or "pick next target."

## Startup: Load Strategy

At the start of every evolve session:

1. Read `docs/starry-reports/strategy.json` — if it doesn't exist, generate it from `os/StarryOS/tests/known.json` and the kernel source
2. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/change-tracker.py` — check what kernel files changed since last run
3. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pattern-scanner.py` — any new pattern hits?
4. Read `docs/starry-reports/journal.md` for recent activity
5. Present current status: category gaps, analysis queue, change-tracker findings
6. In human-driven mode: present the top 5 recommended targets and ask
7. In autonomous mode: pick the top target and begin

## Target Selection

Compute priorities in this order:

1. **Category gaps and unexplored areas** — bug categories with 0 coverage, no benchmarks yet, no app-compat yet
2. **Change-tracker findings** — files modified since last run that affect tested syscalls → re-verify
3. **Analysis queue** — targets flagged `needs_deep` from prior sweeps
4. **High-value untested syscalls** — used by target apps but not yet in `known.json`
5. **Coverage expansion** — untested syscalls in order of estimated importance

Within each tier, prefer techniques with higher historical yield (from `strategy.json` effectiveness tracking).

## Sweep Mode (broad, shallow)

Scan 5-10 syscall handlers quickly per batch. For each:

1. Read the handler source in `os/StarryOS/kernel/src/syscall/`
2. Check for obvious patterns: stub (`Ok(0)` without logic), catch-all match arm, ignored parameters, TODO/FIXME
3. If suspicious: generate a minimal test (2-3 test cases), run Linux comparison
4. Classify result:
   - **Clean**: 0 divergences → mark `swept_clean` in strategy
   - **Suspicious**: 1+ divergences or pattern matches → add to `swept_suspicious` with reason
   - **Needs deep**: ≥2 bugs, touches shared state, or concurrency-relevant → add to `needs_deep`

Budget: ~5 minutes per target. Skip full review pipeline — sweep is discovery, not fix.

## Deep Mode (narrow, thorough)

Pick one target from `needs_deep` or `swept_suspicious`. Execute the full cycle:

1. Fetch man page via `${CLAUDE_PLUGIN_ROOT}/scripts/man-lookup.sh`
2. **Establish Linux baseline**: Document expected behavior for normal input, invalid input, boundary conditions, errno values, blocking/concurrency semantics, side effects, resource cleanup
3. Generate comprehensive test case (all documented behaviors, error codes, edge cases)
4. **Run Linux comparison** via `${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh` — test MUST pass on Linux
5. **Run StarryOS pipeline** — capture divergences
6. For concurrency targets: run `${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh` with SMP sweeping
7. **Root cause analysis**: Locate the exact source file:line. Identify which category: missing implementation, semantic divergence, error path bug, boundary handling, concurrency defect. Cite actual code — no guessing.
8. Implement fix — minimal, local, no unrelated changes
9. **Run mandatory review pipeline** (see below)
10. Report: bug report + journal entry + strategy update

Budget: ~30 minutes per target. Full review pipeline required.

## Multi-Architecture Awareness

StarryOS supports 4 architectures: riscv64 (primary test target), aarch64, x86_64, loongarch64. The xtask build system handles all four.

**Default**: Test on riscv64 first (fastest iteration, most tooling support).

**Cross-arch verification** (after a fix passes review on riscv64):
```bash
# Build and test on other architectures
cargo starry build --arch aarch64
cargo starry build --arch x86_64
# Run QEMU tests on each
cargo starry test qemu --target aarch64
cargo starry test qemu --target x86_64
```

**When cross-arch testing is mandatory:**
- Fixes touching `os/StarryOS/kernel/src/config/` (per-arch config)
- Fixes touching `os/arceos/modules/axhal/` (hardware abstraction)
- Fixes involving inline assembly, page table manipulation, or signal trampolines
- Any fix where the root cause is arch-dependent (different struct layouts, endianness, syscall numbers)

**When to skip** (single-arch is sufficient):
- Pure syscall logic bugs (wrong errno, missing check) — these are arch-independent
- File system bugs — arch-independent
- Most semantic/correctness bugs

Always note in the bug report whether the fix was verified single-arch or multi-arch, and flag any cross-arch risks.

## Mandatory Review Pipeline

Every fix MUST go through this pipeline. No exceptions. No shortcuts. The Stop hook enforces this.

### Step 1: Self-check (always)
Re-read the fix against the man page and the test output. Does it address the root cause? Does it handle error paths? Does it break adjacent behavior?

### Step 2: kernel-reviewer agent (always)
Dispatch the kernel-reviewer agent with fresh context. It reviews Rust idioms, safety, code reuse, API consistency. If it finds critical issues → revise the fix, restart from Step 1.

### Step 3: Regression check (always)
Run `${CLAUDE_PLUGIN_ROOT}/scripts/regression-check.sh` to verify no existing tests broke. Run `cargo xtask clippy --package starry-kernel` and `cargo fmt --check`. Any regression → fix must be revised.

### Step 4: Codex independent review (for P0/P1 bugs, or if codex plugin is available)
Dispatch the Codex agent (via the codex:rescue skill or codex:codex-rescue agent) with:
- The bug description and man page
- The proposed fix
- Ask for PASS / REVISE / REJECT with specific reasoning

If Codex says REVISE → address each point and re-submit. If Codex says REJECT → reconsider the approach.

### Step 5: Independent re-derivation (for P0 bugs)
Dispatch a separate agent with ONLY the bug description + man page. NOT the proposed fix. Compare independently-derived fix with the proposed one. If they disagree → reconciliation round.

### Step 6: Convergence assessment
- All steps pass + 0 regressions → **high confidence** → commit (autonomous) or present (human)
- Partial agreement after reconciliation → **medium confidence** → flag for human review
- Cannot converge after max_rounds → **low confidence** → do NOT commit, escalate

Record all review rounds in `strategy.json` reviews section.

## Deterministic Tooling

The evolve skill relies on deterministic scripts for analysis — the LLM interprets results, but the scanning itself is reproducible and hallucination-free.

### Lock Order Graph
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lock-order-graph.py --json /tmp/lock-order.json
```
Rust ownership-aware analysis: distinguishes `let guard = x.lock()` (held) from `x.lock().method()` (temporary dropped at semicolon). Detects `drop()` calls. Cycles in the graph are concrete deadlock evidence.

### Pattern Scanner
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pattern-scanner.py --json /tmp/pattern-hits.json
```
Reads rules from `docs/starry-reports/patterns.json`. Default 9 patterns including negative-to-unsigned casts, Ok(0) stubs, AB/BA lock patterns. **Pattern evolution**: when a new bug class is found, add a grep rule to patterns.json. The scanner finds new instances deterministically.

### Kernel Graph
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kernel-graph.py --json /tmp/kernel-graph.json
```
Maps all 204 syscalls to subsystems, files, locks, unsafe blocks. Shows which untested syscalls touch the most shared state.

### Change Tracker
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/change-tracker.py --json /tmp/changes.json
```
Git-aware: identifies which tests need re-running based on file changes since last run.

## Reflect Phase (cross-run synthesis)

Every 3-5 runs within a session, the loop pauses to reflect:

1. Run the pattern scanner — any new hits since last reflect?
2. Run the lock order graph — any new cycles since last reflect?
3. Read the last N runs' results from strategy.json
4. Identify cross-cutting patterns (e.g., "3 bugs all involve `as _` casts in different syscalls")
5. Generate new pattern scanner rules from discovered bugs (deterministic grep rules, not LLM guesses)
6. Update `docs/starry-reports/patterns.json` with new rules
7. Update priorities based on what techniques actually worked
8. Append insights to `docs/starry-reports/insights.md`

Budget: ~2K tokens. Saves tokens downstream by improving target selection.

## Session Flow

```
Load strategy + run change-tracker + run pattern-scanner
    │
    ▼
Compute priorities (incorporating deterministic scan results)
    │
    ├─ autonomous → pick top target
    └─ human → present top 5, ask
    │
    ▼
Is target a sweep or deep?
    ├─ sweep batch → run sweep mode on 5-10 targets
    └─ deep target → run deep mode on 1 target (includes MANDATORY review pipeline)
    │
    ▼
Update strategy.json (coverage, effectiveness, queue, review rounds)
    │
    ▼
Every 3-5 runs → REFLECT (run scanners, synthesize, update patterns)
    │
    ▼
Check stopping conditions:
    ├─ session budget exhausted (default 5 cycles) → stop
    ├─ no targets above minimum value → stop
    ├─ human requests stop → stop
    └─ otherwise → loop back to "Compute priorities"
```

## Token Budget

- Sweep: ~2K tokens per target (read handler, quick pattern check)
- Deep: ~15K tokens per target (full cycle with review pipeline)
- Reflect: ~2K tokens (run deterministic tools, synthesize)
- Default session budget: 5 deep cycles or 2 sweeps + 3 deeps
- Early termination: if a target shows 0 divergences in sweep, skip it in <500 tokens
- Deterministic tools (pattern scanner, lock graph, etc.) cost 0 LLM tokens

## Additional Resources

### Reference Files
- **`references/review-pipeline.md`** — Full adaptive review protocol with convergence rules
- **`references/strategy-schema.md`** — Complete strategy.json schema and field definitions

### Deterministic Scripts
- **`${CLAUDE_PLUGIN_ROOT}/scripts/lock-order-graph.py`** — Static lock ordering + cycle detection (Rust ownership-aware)
- **`${CLAUDE_PLUGIN_ROOT}/scripts/pattern-scanner.py`** — Regex-based bug pattern scanner with evolving rule set
- **`${CLAUDE_PLUGIN_ROOT}/scripts/kernel-graph.py`** — Kernel architecture graph (204 syscalls mapped)
- **`${CLAUDE_PLUGIN_ROOT}/scripts/change-tracker.py`** — Git-aware change detection
- **`${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh`** — Multi-run SMP-sweeping test runner
- **`${CLAUDE_PLUGIN_ROOT}/scripts/regression-check.sh`** — Full regression suite
- **`${CLAUDE_PLUGIN_ROOT}/scripts/strace-profiler.sh`** — Application syscall profiling
- **`${CLAUDE_PLUGIN_ROOT}/scripts/draft-pr.sh`** — PR draft generator (never auto-submits)
