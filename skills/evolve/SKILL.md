---
name: evolve
description: This skill should be used when the user asks to "run the harness autonomously", "evolve", "auto-hunt", "autonomous mode", "self-evolving loop", "sweep syscalls", "deep dive", "what should I work on next", "pick next target", "run a sweep", "continuous improvement", or wants the starry-harness to autonomously select targets, run analysis cycles, and track progress toward competition goals.
---

# StarryOS Self-Evolving Harness

Orchestrates autonomous and human-driven kernel improvement cycles. Maintains persistent strategy state, selects targets based on coverage gaps and effectiveness history, and alternates between broad sweeps and deep investigations.

## Modes

**Autonomous**: The system picks targets, runs cycles, and stops when competition goals are met or the session budget is exhausted. Invoke with "run autonomously" or "auto-hunt."

**Human-driven**: The system presents priorities and recommendations; the human picks the target. Invoke with "what should I work on next" or "pick next target."

## Startup: Load Strategy

At the start of every evolve session, load (or initialize) the strategy state:

1. Read `docs/starry-reports/strategy.json` — if it doesn't exist, generate it from `os/StarryOS/tests/known.json` and the kernel source
2. Read `docs/starry-reports/journal.md` for recent activity
3. Present the current status: competition targets (met/unmet), category gaps, analysis queue depth
4. In human-driven mode: present the top 5 recommended targets and ask which to pursue
5. In autonomous mode: pick the top target and begin

## Target Selection

Compute priorities in this order:

1. **Unmet competition targets** — benchmark (≥50% improvement), app-compat (≥1 app), bug categories with 0 coverage (concurrency, memory)
2. **Analysis queue** — targets flagged `needs_deep` from prior sweeps
3. **High-value untested syscalls** — used by target apps (Nginx, PostgreSQL, Python) but not yet in `known.json`
4. **Category gap filling** — prefer syscalls likely to yield bugs in underrepresented categories
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
2. Generate comprehensive test case (all documented behaviors, error codes, edge cases)
3. Run Linux comparison via `${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh`
4. Run StarryOS pipeline
5. For concurrency targets: run `${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh` with SMP sweeping
6. Analyze divergences, identify root causes
7. Implement fix
8. Run adaptive review pipeline (see below)
9. Report: bug report + journal entry + strategy update

Budget: ~30 minutes per target. Full review pipeline required.

## Adaptive Review Pipeline

Every fix goes through a convergence loop that scales with severity. See `references/review-pipeline.md` for full protocol. Summary:

**Minimum rounds (always run):**
- Self-check: re-read fix against man page
- kernel-reviewer agent: fresh-context code quality review
- Regression: run all existing tests + clippy

**Additional rounds (triggered by severity or disagreement):**
- Independent re-derivation: separate agent derives fix from bug description + man page only (no visibility of proposed fix). Scale: 0 for P3, 1 for P2, 2 for P1, 3 for P0.
- If Codex plugin is installed: one re-derivation slot uses Codex for model-level independence
- Reconciliation: if re-derivers disagree, a reconciliation agent sees all proposals and synthesizes
- Re-review: reconciled fix goes back through kernel-reviewer

**Convergence:**
- All rounds agree + 0 regressions → high confidence → commit (autonomous) or present (human)
- Partial agreement after reconciliation → medium confidence → flag for human
- Cannot converge after `max_rounds` (default 8) → low confidence → escalate, do not commit

## Strategy State

Persisted at `docs/starry-reports/strategy.json`. Updated after every cycle.

Structure documented in `references/strategy-schema.md`. Key sections:
- `coverage`: tested/untested syscalls and subsystems
- `effectiveness`: bugs-per-run for each technique
- `targets`: competition goal tracking (met/unmet)
- `analysis_queue`: needs_deep, swept_clean, swept_suspicious
- `reviews`: per-bug review round history and confidence
- `next_priorities`: computed ranked list

## Deterministic Tooling

The evolve skill relies on deterministic scripts for analysis — the LLM interprets results, but the scanning itself is reproducible and hallucination-free.

### Lock Order Graph
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lock-order-graph.py --json /tmp/lock-order.json
```
Parses all `.lock()`, `.read()`, `.write()` calls in the kernel source, builds a directed graph of lock orderings, and detects cycles (potential deadlocks). Any cycle is a concrete finding — the LLM decides whether to investigate, but the cycle detection is fully deterministic.

Run this during sweep mode. Cycles go directly into `analysis_queue.needs_deep` with category hint "concurrency."

### Pattern Scanner
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pattern-scanner.py --json /tmp/pattern-hits.json
```
Reads grep/regex rules from `docs/starry-reports/patterns.json` and scans the kernel source for matches. Default patterns include: negative-to-unsigned casts, Ok(0) stubs, catch-all match arms, TODO/FIXME, ignored flags, unsafe without SAFETY comments.

**Pattern evolution**: When a new bug is found, add a concrete grep rule to `patterns.json` that detects the same class of bug. The scanner finds new instances deterministically. The LLM triages the hits (real bug vs false positive) but does not perform the scanning.

### Unsafe Block Auditor
The lock-order-graph script also reports unsafe blocks missing `// SAFETY:` comments. These are concrete safety audit targets.

## Reflect Phase (cross-run synthesis)

Every 3-5 runs within a session, the loop pauses to reflect. This is not cross-session — it happens within a long-running session between runs.

**Reflect steps:**
1. Run the pattern scanner — any new hits since last reflect?
2. Run the lock order graph — any new cycles since last reflect?
3. Read the last N runs' results from strategy.json
4. Identify cross-cutting patterns (e.g., "3 bugs all involve `as _` casts in different syscalls")
5. Generate new pattern scanner rules from discovered bugs (deterministic grep rules, not LLM guesses)
6. Update `docs/starry-reports/patterns.json` with new rules
7. Update priorities based on what techniques actually worked
8. Append insights to `docs/starry-reports/insights.md`

Budget: ~2K tokens. Saves tokens downstream by improving target selection and catching pattern-scannable bugs without full deep dives.

## Session Flow

```
Load strategy.json + run pattern scanner + run lock-order graph
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
    └─ deep target → run deep mode on 1 target
    │
    ▼
Update strategy.json (coverage, effectiveness, queue)
    │
    ▼
Every 3-5 runs → REFLECT (run scanners, synthesize, update patterns)
    │
    ▼
Check stopping conditions:
    ├─ all competition targets met → generate final report, stop
    ├─ session budget exhausted (default 5 cycles) → stop
    ├─ no targets above minimum value → stop
    └─ otherwise → loop back to "Compute priorities"
```

## Token Budget

To avoid runaway sessions:
- Sweep: ~2K tokens per target (read handler, quick pattern check)
- Deep: ~15K tokens per target (full cycle with review)
- Reflect: ~2K tokens (run deterministic tools, synthesize)
- Default session budget: 5 deep cycles or 2 sweeps + 3 deeps
- Early termination: if a target shows 0 divergences in sweep, skip it in <500 tokens
- Deterministic tools (pattern scanner, lock graph) run as Bash and cost 0 LLM tokens

## Additional Resources

### Reference Files
- **`references/review-pipeline.md`** — Full adaptive review protocol with convergence rules
- **`references/strategy-schema.md`** — Complete strategy.json schema and field definitions

### Deterministic Scripts
- **`${CLAUDE_PLUGIN_ROOT}/scripts/lock-order-graph.py`** — Static lock ordering analysis + cycle detection + unsafe audit
- **`${CLAUDE_PLUGIN_ROOT}/scripts/pattern-scanner.py`** — Regex-based bug pattern scanner with evolving rule set
- **`${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh`** — Multi-run SMP-sweeping test runner
