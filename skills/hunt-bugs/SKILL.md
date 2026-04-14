---
name: hunt-bugs
description: This skill should be used when the user asks to "find bugs in StarryOS", "hunt bugs", "test syscalls", "discover vulnerabilities", "test starry", "fix syscall", "compare with Linux", "run syscall test", "check Linux compatibility", or wants to systematically discover, test, and fix StarryOS kernel bugs using Linux comparison testing. Supersedes the older test-starry skill.
---

# StarryOS Bug Hunting Harness

Systematic workflow for discovering, testing, and fixing bugs in the StarryOS kernel by comparing behavior against real Linux. This is the core engineering loop of the starry-harness plugin.

## Workflow Overview

Six-phase cycle: **Discover → Test → Compare → Analyze → Fix → Report**. Each phase produces artifacts that feed the next, building a growing knowledge base.

## Phase 1: Discovery

Identify candidate syscalls to test by scanning for suspicious patterns in kernel source.

**Automated pattern scan** — Search `os/StarryOS/kernel/src/syscall/` for:
- Stubs: functions returning `Ok(0)` or `Err(LinuxError::ENOSYS)` without real logic
- Copy-paste: adjacent handlers with near-identical structure (preadv/pwritev, etc.)
- TODO/FIXME/HACK comments indicating incomplete implementations
- Missing flag handling: match arms with `_ => {}` or `_ => Ok(0)` catch-alls
- Ignored arguments: function parameters that are never read

**Man page cross-reference** — For each suspect syscall:
1. Fetch the man page: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/man-lookup.sh <syscall>`
2. Compare documented behavior against the implementation
3. List specific requirements the kernel does NOT implement

**Check the registry** — Read `os/StarryOS/tests/known.json` to skip already-tested syscalls. Focus on fresh targets or known-buggy syscalls that haven't been fixed yet.

**Prioritize targets** by:
- Used by target applications (Nginx, Python, etc.) — check `references/workflow.md`
- Likely severity (data corruption > wrong errno > missing feature)
- Fix difficulty (quick wins first to build momentum)

## Phase 2: Test Generation

Generate a C test case using the `starry_test.h` harness.

**Test case location**: `os/StarryOS/tests/cases/test_<syscall>.c`

**Structure**:
```c
#include "starry_test.h"
#include <sys/...>  // relevant POSIX headers

TEST_BEGIN("syscall_name")

TEST("normal_operation") {
    // Happy path from man page
    EXPECT_OK(result);
} TEND

TEST("error_EINVAL") {
    // Invalid arguments per man page
    EXPECT_ERRNO(result, -1, EINVAL);
} TEND

TEST("edge_case_from_manpage") {
    // Specific edge case documented in man page
} TEND

TEST_END
```

**Rules for good tests**:
- One TEST block per distinct behavior from the man page
- Test both success and every documented error code
- Test flag combinations (e.g., MAP_PRIVATE|MAP_ANONYMOUS)
- Test boundary values (0, -1, SIZE_MAX, page-unaligned addresses)
- Name tests descriptively so PASS/FAIL output is self-documenting

## Phase 3: Linux Comparison

Run the test on both Linux (Docker) and StarryOS to find behavioral divergences.

**Dispatch the linux-comparator agent** for this phase. It will:
1. Run `${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh` for the Docker baseline
2. Run the StarryOS pipeline (`${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh`)
3. Produce a structured comparison report

If not using the agent, run manually:
```bash
# Linux baseline (add --arch riscv64 for cross-arch comparison)
bash ${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh os/StarryOS/tests/cases/test_<name>.c /tmp/linux-ref.txt

# StarryOS (supports --arch riscv64|aarch64|x86_64|loongarch64)
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh <name> --arch riscv64

# Compare
diff /tmp/linux-ref.txt os/StarryOS/tests/results/test_<name>.txt
```

## Phase 4: Root Cause Analysis

For each test that diverges from Linux:

1. **Locate the handler**: `os/StarryOS/kernel/src/syscall/` — find the relevant file and function
2. **Read the code path**: Trace from the syscall dispatch in `mod.rs` through the handler
3. **Identify the divergence**: Compare the code logic against the man page requirement
4. **Classify the bug** — dispatch the **bug-triager agent** to categorize it:
   - Concurrency, Memory, Safety, Semantic, or Correctness
5. **Record in known.json**: Update `os/StarryOS/tests/known.json` with findings

## Phase 5: Fix (MANDATORY review pipeline)

Implement the fix, then run it through the **adaptive review pipeline**. Do NOT skip any step. Do NOT report a fix as "done" until the pipeline converges. See `evolve/references/review-pipeline.md` for the full protocol.

**Minimum rounds (always, non-negotiable):**
1. **Write the fix** in the relevant kernel source file
2. **Self-check**: Re-read the fix against the man page and the test output. Does it address the root cause?
3. **Dispatch kernel-reviewer agent** (fresh context, no anchoring) to verify:
   - Proper Rust idioms, code reuse, safety, API consistency
4. **If kernel-reviewer finds critical issues** → revise the fix, restart from step 2
5. **Re-run the test** via the StarryOS pipeline to verify the fix
6. **Re-run Linux comparison** to confirm behavior now matches
7. **Run regression**: `cargo xtask clippy --package starry-kernel` and `cargo fmt`

**Additional rounds for P0/P1 bugs:**
8. **Independent re-derivation**: Dispatch a separate agent (or Codex if available) with ONLY the bug description + man page (NOT the proposed fix). Compare the independently-derived fix against the proposed one.
9. **If fixes disagree** → dispatch a reconciliation agent to synthesize, then re-review
10. **Record review rounds** in `strategy.json` reviews section with confidence level

**Only report the fix after:**
- All minimum rounds pass (steps 1-7)
- For P0/P1: at least one independent re-derivation (step 8)
- Confidence is "high" (all rounds agree, 0 regressions)
- If confidence is "medium" or "low" → flag for human review, do NOT claim fixed

## Phase 6: Report

Generate structured artifacts for every bug found and fixed.

1. **Bug report**: Write to `docs/starry-reports/bugs/BUG-NNN-<syscall>.md` using template from `references/workflow.md`
2. **Journal entry**: Run `bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh BUG "<title>" "<body>"`
3. **Update known.json**: Mark syscall status as `fixed`, `buggy`, `broken`, or `stub`
4. **Update strategy.json**: Record review rounds and confidence in the reviews section
5. **Update triage**: If multiple bugs found, dispatch bug-triager agent for re-prioritization

## Key File Locations

| Resource | Path |
|----------|------|
| Syscall handlers | `os/StarryOS/kernel/src/syscall/` |
| Test harness header | `os/StarryOS/tests/cases/starry_test.h` |
| Test sources | `os/StarryOS/tests/cases/test_*.c` |
| Test results | `os/StarryOS/tests/results/` |
| Known bugs registry | `os/StarryOS/tests/known.json` |
| Pipeline | `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh --arch <arch>` |
| Bug reports | `docs/starry-reports/bugs/` |
| Work journal | `docs/starry-reports/journal.md` |

## Additional Resources

### Reference Files
- **`references/workflow.md`** — Detailed phase procedures, bug report template, known.json schema
- **`references/syscall-patterns.md`** — Common bug patterns in syscall implementations with examples from this codebase

### Related Skills
- **audit-kernel** — For bugs beyond syscalls (scheduler, memory, concurrency, signals). Use audit-kernel when the bug is in kernel internals rather than syscall behavior.

### Agents
- **linux-comparator** — Docker Linux test runner + structured comparison
- **kernel-reviewer** — Code quality review for kernel changes
- **bug-triager** — Bug classification and prioritization
