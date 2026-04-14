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

## Session Flow

```
Load strategy.json
    │
    ▼
Compute priorities
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
- Default session budget: 5 deep cycles or 2 sweeps + 3 deeps
- Early termination: if a target shows 0 divergences in sweep, skip it in <500 tokens

## Additional Resources

### Reference Files
- **`references/review-pipeline.md`** — Full adaptive review protocol with convergence rules
- **`references/strategy-schema.md`** — Complete strategy.json schema and field definitions
