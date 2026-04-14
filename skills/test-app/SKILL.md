---
name: test-app
description: This skill should be used when the user asks to "run Nginx on StarryOS", "test application compatibility", "run Python on StarryOS", "test Linux app", "port application", "check app support", "run Rust compiler", "test Wayland", "run PostgreSQL", "run Redis", or wants to test and enable real mainstream Linux applications running on StarryOS.
---

# Test Real Linux Applications on StarryOS

Systematic workflow for selecting, building, testing, and enabling real mainstream Linux applications on StarryOS. The goal is to run ambitious, mainstream applications — not just toy programs. Each application exercises a different slice of the syscall surface, so testing them exposes gaps and validates breadth.

## Workflow Overview

Seven-phase cycle: **Select → Audit → Gap Analysis → Build → Inject & Run → Fix Blockers → Verify & Report**. Each phase produces concrete artifacts. There is no fixed ordering — pick whatever application is most interesting or valuable and go for it.

## Phase 1: Select Application

Choose a target application based on interest, competition value, and what syscall areas need testing.

**Application catalog** (not a rigid ordering — pick any):

| Application | Demonstrates | Syscall Focus |
|-------------|-------------|---------------|
| BusyBox | Core POSIX process model | fork, exec, pipe, signals |
| Nginx | Network server, event loop | sockets, epoll, sendfile, mmap |
| Redis | In-memory DB, persistence | sockets, epoll, fork, fsync |
| PostgreSQL | Full RDBMS, IPC, shared memory | shmget/shmat, semaphores, mmap, socket |
| Python (CPython) | Complex runtime, threading | mmap, futex, clone, signals, pipes |
| Rust Compiler | Full toolchain | clone3, getrandom, flock, execve |
| SQLite | Embedded DB | flock, mmap, fsync, fcntl |
| curl / wget | HTTP client | socket, connect, DNS, TLS |
| Lua | Lightweight scripting | Minimal: mmap, read/write, signals |
| Syzkaller | Kernel fuzzer | Everything — designed to exercise all syscalls |

**Interactive selection**: Present this table to the user and ask which application to target. The user may also specify any other Linux program not on this list — in that case, proceed to Phase 2 (Syscall Audit) to discover the requirements from scratch using `strace` in Docker.

**Selection criteria** (when the user wants a recommendation):
- Check `os/StarryOS/tests/known.json` to see which syscalls are already working
- More unique/ambitious applications (PostgreSQL, rustc) are more impressive than common ones
- Each app should demonstrate a distinct capability (networking, threading, filesystem, IPC)
- Consult `references/app-requirements.md` for full syscall requirements per application
- If the user picks a custom app not in the catalog, skip to Phase 2 — strace it first

**Record the decision**: Run `bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh APP "Selected <app> as next target" "<rationale>"`

## Phase 2: Syscall Audit

Capture the complete syscall profile of the target application by running it under `strace` inside a Docker Linux container.

**Run the strace capture**:
```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace <app-package> &&
  strace -f -o /work/strace-<app>.log <app-command> &&
  # Extract unique syscall names
  awk -F"(" "{print \$1}" /work/strace-<app>.log | sort -u > /work/syscalls-<app>.txt
'
```

For applications that require building from source, use the cross-compilation instructions in `references/app-requirements.md` to build a native x86 binary first, then strace it.

**Alternatively**, for simple captures, use the existing linux-ref-test script:
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh <test-wrapper.c> /tmp/app-baseline.txt
```

**Output**: A `syscalls-<app>.txt` file listing every unique syscall the application invokes during normal operation, including startup and shutdown.

## Phase 3: Gap Analysis

Compare the application's syscall requirements against StarryOS's implemented syscalls.

**Step 1 — Load the syscall dispatch table**: Read `os/StarryOS/kernel/src/syscall/mod.rs` and extract every syscall number handled. This file contains the master `match` dispatch.

**Step 2 — Cross-reference with known.json**: Read `os/StarryOS/tests/known.json` for each required syscall. Classify each as:
- **Implemented and tested** — Exists in dispatch table and passes tests
- **Implemented but untested** — Exists in dispatch but no test or known bugs
- **Stub** — Handler exists but returns `Ok(0)` or `ENOSYS` without real logic
- **Missing** — No handler in the dispatch table at all

**Step 3 — Produce the gap matrix**: Create a table:

| Syscall | Required By | StarryOS Status | Blocking? | Notes |
|---------|-------------|-----------------|-----------|-------|
| epoll_create1 | Nginx | Implemented | No | Passes test |
| sendfile | Nginx | Stub | Yes | Returns Ok(0), no data transfer |
| accept4 | Nginx | Missing | Yes | Not in dispatch |

**Step 4 — Estimate fix effort**: For each blocking syscall, scan the handler code (if it exists) and estimate:
- Quick fix (< 1 hour): wrong errno, missing flag, off-by-one
- Medium fix (1-4 hours): partial implementation needs completing
- Hard fix (> 4 hours): entirely new subsystem or complex interaction

## Phase 4: Build for StarryOS

Cross-compile the application as a static riscv64 binary using musl.

**General approach**:
```bash
# Install cross toolchain
apt-get install -y gcc-riscv64-linux-gnu
# Or use musl cross-compiler for fully static binaries
# See references/app-requirements.md for per-app build instructions
```

**Requirements**:
- All binaries MUST be statically linked — StarryOS does not support dynamic linking for external apps
- Target architecture: riscv64gc (riscv64-linux-musl for musl toolchain)
- Optimize for size when possible (`-Os`) to keep the rootfs small
- Strip debug symbols for the final binary (`riscv64-linux-musl-strip`)

**Per-application build instructions** are in `references/app-requirements.md`. Follow them exactly — each application has specific configure flags and patches needed.

**Output**: A static riscv64 ELF binary (or set of binaries) ready for injection into the StarryOS rootfs.

## Phase 5: Inject and Run

Use the StarryOS rootfs injection pipeline to place the binary and test it on QEMU.

**Step 1 — Inject into rootfs**:
```bash
cd os/StarryOS
# Copy binary into the rootfs tree
cp /path/to/app-binary tests/bin/<app>
# Or use the pipeline injection step
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh <app>
```

**Step 2 — Create a test wrapper**: Write a minimal shell script or C wrapper that launches the application and captures output:
```bash
#!/bin/sh
# /test/run-<app>.sh
/bin/<app> <args>
echo "EXIT_CODE=$?"
```

**Step 3 — Boot and run**:
```bash
cd os/StarryOS
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh <app>
```

**Step 4 — Capture output**: The pipeline writes output to `os/StarryOS/tests/results/test_<app>.txt`. Inspect for:
- Kernel panics or traps
- `ENOSYS` errors (unimplemented syscalls)
- Incorrect behavior vs. Linux baseline
- Successful completion with correct output

## Phase 6: Fix Blockers

For each failing syscall or crash, dispatch the hunt-bugs skill to fix it.

**Triage blockers by impact**:
1. Kernel panics — fix first, they prevent any further testing
2. ENOSYS returns — implement the missing syscall handler
3. Wrong behavior — fix the semantic bug in the existing handler
4. Performance issues — defer unless they cause timeouts

**For each blocker**:
1. Identify the syscall from the error output or strace log
2. Locate the handler in `os/StarryOS/kernel/src/syscall/`
3. Use the hunt-bugs skill workflow: generate a targeted test, compare with Linux, fix, verify
4. Dispatch the **kernel-reviewer agent** for code quality review
5. Re-run the application after each fix to check progress

**Track progress**: Update `os/StarryOS/tests/known.json` as syscalls are fixed. Update the gap matrix from Phase 3 to reflect current status.

**Iterate**: After fixing one blocker, re-run the application. It will likely hit the next missing syscall. Repeat until the application either runs successfully or hits an infeasible blocker.

## Phase 7: Verify End-to-End and Report

Confirm the application runs correctly and generate the competition report.

**Verification checklist**:
- [ ] Application starts without kernel panics
- [ ] Application produces correct output matching the Linux baseline
- [ ] Application handles basic error cases (invalid input, network timeout, etc.)
- [ ] Application shuts down cleanly
- [ ] No regressions in existing syscall tests: `cd os/StarryOS && cargo xtask test arceos`
- [ ] Kernel passes clippy: `cargo xtask clippy --package starry-kernel`

**Dispatch the linux-comparator agent** to run the application on both Docker Linux and StarryOS, capturing output for side-by-side comparison.

**Generate the compatibility report**: Write to `docs/starry-reports/apps/APP-NNN-<name>.md` using this structure:
```markdown
# APP-NNN: <Application Name>

## Summary
- Application: <name and version>
- Status: PASS / PARTIAL / FAIL
- Date: <YYYY-MM-DD>
- Syscalls exercised: <count>
- Syscalls fixed during porting: <list>

## Build
- Toolchain: <compiler and flags>
- Binary size: <size>
- Build notes: <any patches or workarounds>

## Test Results
- Linux baseline: <summary>
- StarryOS: <summary>
- Divergences: <list>

## Syscall Coverage
| Syscall | Status | Notes |
|---------|--------|-------|

## Remaining Issues
- <issue 1>
- <issue 2>
```

**Journal entry**: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh APP "<app> testing complete — <status>" "<summary>"`

## Key File Locations

| Resource | Path |
|----------|------|
| Syscall dispatch | `os/StarryOS/kernel/src/syscall/mod.rs` |
| Syscall handlers | `os/StarryOS/kernel/src/syscall/` |
| Known syscall registry | `os/StarryOS/tests/known.json` |
| Test binaries | `os/StarryOS/tests/bin/` |
| Test results | `os/StarryOS/tests/results/` |
| Pipeline script | `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` |
| App compatibility reports | `docs/starry-reports/apps/` |
| Work journal | `docs/starry-reports/journal.md` |

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh` | Run test in Docker Linux for baseline behavior |
| `${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh` | Append structured entry to the work journal |
| `${CLAUDE_PLUGIN_ROOT}/scripts/man-lookup.sh` | Fetch man page for a syscall |

## Agents

- **linux-comparator** — Run application on Docker Linux and StarryOS side-by-side, produce structured comparison
- **kernel-reviewer** — Review kernel code changes for Rust idioms, safety, and correctness

## Additional Resources

### Reference Files
- **`references/app-requirements.md`** — Per-application syscall requirements, build instructions, strace templates, and feasibility analysis
