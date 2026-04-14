# Hunt-Bugs Workflow Reference

Detailed procedures for each phase of the bug-hunting cycle, the bug report template, the known.json schema, and the high-priority syscall target list.

---

## Phase 1: Discovery — Detailed Procedure

### Step 1: Scan for suspicious patterns

Search the syscall handler directory `os/StarryOS/kernel/src/syscall/` for each of the following indicators. Record every hit in a candidate list before moving on.

**Stubs and no-ops.** Search for functions whose body consists solely of `Ok(0)` with no meaningful logic. These are placeholder implementations that silently succeed without performing the required operation. Also search for `Err(AxError::OperationNotSupported)` and `ENOSYS` returns — these are at least honest about being unimplemented, but still need real implementations.

**Copy-paste artifacts.** Identify pairs of syscall handlers that appear near each other in the same file and share nearly identical structure (e.g., `sys_preadv` / `sys_pwritev`, `sys_read` / `sys_write`, `sys_sendto` / `sys_recvfrom`). Check whether the "write" variant actually calls write operations or still calls read operations from being copied.

**TODO / FIXME / HACK comments.** These are explicit markers left by developers indicating incomplete work. Each one is a candidate for a bug or missing feature. Record the comment text and surrounding function name.

**Catch-all match arms.** Search for `_ => Ok(0)`, `_ => {}`, and similar patterns inside match statements. These swallow unknown flags or commands silently instead of returning an error. Pay special attention to `fcntl`, `ioctl`, `prctl`, and `ptrace` handlers where the command space is large.

**Ignored parameters.** Search for function parameters prefixed with `_` (e.g., `_flags: u32`). This naming convention means the developer intentionally ignored the parameter. Cross-reference with the man page to determine whether the parameter matters for correctness.

### Step 2: Cross-reference with man pages

For each candidate syscall, fetch the man page:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/man-lookup.sh <syscall>
```

Read the RETURN VALUE, ERRORS, and DESCRIPTION sections. Build a checklist of behaviors the man page specifies. Walk through the StarryOS implementation and check off each behavior that is correctly implemented. Any unchecked item is a potential bug.

Pay particular attention to:
- Error codes the man page lists that the implementation does not return
- Flag bits the man page documents that the implementation ignores
- Edge cases mentioned in NOTES or BUGS sections of the man page
- Interaction with other syscalls (e.g., mmap behavior depending on open flags)

### Step 3: Check the registry

Read `os/StarryOS/tests/known.json`. For each syscall entry:
- If `status` is `"stub"` or `"buggy"` and no fix has been applied, it remains a valid target for investigation or fixing.
- If `status` is `"fixed"` or `"mostly_ok"`, skip it unless there is reason to believe additional bugs exist.
- If the syscall is not in the registry at all, it has never been tested and is a high-priority candidate.

### Step 4: Prioritize

Rank candidates using this priority order:
1. Syscalls used by target applications (see High-Priority Syscalls section below)
2. Probable data corruption or security bugs (highest severity)
3. Wrong errno returns or missing error checks (medium severity)
4. Missing features that cause fallback behavior (lower severity)
5. Quick fixes (one-line changes) — pick these up opportunistically even if lower priority

---

## Phase 2: Test Generation — Detailed Procedure

### Step 1: Create the test file

Create `os/StarryOS/tests/cases/test_<syscall>.c`. Include the harness header and all relevant POSIX headers.

### Step 2: Structure tests by man page sections

Write one `TEST` block for each distinct behavior documented in the man page:

1. **Happy path**: The basic successful operation with typical arguments.
2. **Each documented error code**: One TEST per EINVAL, EACCES, EBADF, ENOMEM, etc. that the man page lists under ERRORS.
3. **Flag combinations**: Test each documented flag individually and in combinations that the man page describes as meaningful.
4. **Boundary values**: Zero-length buffers, NULL pointers (where applicable), maximum values, page-unaligned addresses.
5. **Interaction tests**: Test behavior that depends on state set by other syscalls (e.g., test that `fcntl(F_GETFL)` returns flags matching what was passed to `open`).

### Step 3: Use appropriate assertion macros

- `EXPECT_EQ(actual, expected)` — for integer comparisons
- `EXPECT_TRUE(condition)` — for boolean checks
- `EXPECT_OK(result)` — for checking that a syscall did not return -1
- `EXPECT_ERRNO(result, expected_return, expected_errno)` — for checking error paths: verifies both the return value and errno
- `EXPECT_BUF_EQ(buf1, buf2, len)` — for comparing memory contents

### Step 4: Naming convention

Name each TEST block descriptively so the PASS/FAIL output is self-documenting. Format: `<behavior_under_test>`. Examples: `"basic_write_read"`, `"error_EINVAL_bad_flags"`, `"overlap_same_file"`, `"anonymous_private_mapping"`.

### Step 5: Verify on host first (if possible)

If the test uses only standard POSIX APIs, compile and run it on the host Linux (or in Docker) to confirm all tests pass on a correct implementation before running on StarryOS.

---

## Phase 3: Linux Comparison — Detailed Procedure

### Step 1: Run the Linux baseline

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh os/StarryOS/tests/cases/test_<name>.c /tmp/linux-ref.txt
```

This cross-compiles the test for the target architecture, runs it inside a Docker container with a real Linux kernel, and captures stdout to the output file. Every line of output is either `PASS: <suite>::<name>` or `FAIL: <suite>::<name> (<details>)`.

If any test FAILs on Linux, that test is itself buggy. Fix or remove it before comparing against StarryOS.

### Step 2: Run on StarryOS

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh <name>
```

This compiles the test, injects it into the rootfs disk image, builds the kernel, boots StarryOS in QEMU, and captures output to `tests/results/test_<name>.txt`.

### Step 3: Produce the comparison

Diff the two output files. Every line that differs represents a behavioral divergence:
- Test passes on Linux but fails on StarryOS: kernel bug.
- Test fails on both: test bug or unsupported feature on both.
- Test passes on StarryOS but fails on Linux: either the test is wrong or StarryOS is accidentally permissive (which can also be a bug — accepting invalid input).

### Step 4: Record results

For each test, record the pass/fail counts in the comparison report. Mark each divergent test with a preliminary classification: likely kernel bug, likely test bug, or unclear.

---

## Phase 4: Root Cause Analysis — Detailed Procedure

### Step 1: Locate the handler

Open `os/StarryOS/kernel/src/syscall/mod.rs` and find the dispatch entry for the syscall. Follow it to the implementation file. The handlers are organized by subsystem under `kernel/src/syscall/`:
- `fs/` — filesystem, I/O, file descriptors
- `mm/` — memory management (mmap, mremap, mprotect, brk)
- `task/` — process/thread management (clone, exit, wait)
- `net/` — networking (socket, bind, listen, accept)
- `sys/` — system info (uname, getpid, getuid)
- `signal/` — signal handling

### Step 2: Trace the code path

Read the handler function. For each failing test, trace the exact code path that would execute given the test's arguments. Note:
- Which branches are taken
- Which helper functions are called
- What the return value would be
- Whether user pointers are validated before use
- Whether locks are acquired and released correctly

### Step 3: Identify the divergence

Compare the traced behavior against the man page requirement that the failing test exercises. State the divergence precisely: "The man page requires X, but the code does Y because of Z."

### Step 4: Classify the bug

Use the five competition categories:

| Category | Chinese | Description |
|----------|---------|-------------|
| Concurrency | 并发 | Race conditions, missing locks, deadlocks, incorrect atomic operations |
| Memory | 内存 | Memory leaks, use-after-free, double-free, incorrect mappings, address space corruption |
| Safety | 安全 | Missing permission checks, unchecked user pointers, buffer overflows, privilege escalation |
| Semantic | 语义 | Wrong return values, incorrect errno, misinterpreted flags, wrong behavior per POSIX spec |
| Correctness | 正确性 | Data corruption, logic errors, copy-paste mistakes, stub implementations, missing functionality |

### Step 5: Update known.json

Add or update the syscall entry in `os/StarryOS/tests/known.json` with the bug description, source location, test file path, and pass/fail counts.

---

## Phase 5: Fix — Detailed Procedure

### Step 1: Write the minimal correct fix

Change the minimum amount of code necessary to make the failing tests pass while preserving existing passing behavior. Do not refactor unrelated code in the same change.

### Step 2: Follow kernel code conventions

- Use `AxResult<isize>` as the return type for syscall handlers.
- Validate user pointers before dereferencing (use `vm_read`, `vm_write`, `UserPtr`, `UserConstPtr`).
- Propagate errors with `?` — do not use `unwrap()` or `expect()` in syscall handlers.
- Use the existing bitflags definitions for flag parsing. Add new flag constants if the man page documents flags not yet defined.
- Add `debug!()` or `warn!()` logging consistent with adjacent handlers.

### Step 3: Run verification

```bash
# Re-run the specific test
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh <name>

# Re-run Linux comparison
bash ${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh os/StarryOS/tests/cases/test_<name>.c /tmp/linux-ref.txt
diff /tmp/linux-ref.txt os/StarryOS/tests/results/test_<name>.txt

# Run clippy and format check
cargo xtask clippy --package starry-kernel
cargo fmt --check
```

### Step 4: Check for regressions

If the fix touches shared infrastructure (e.g., the `SendFile` struct, `do_send`, file descriptor table, address space operations), re-run tests for other syscalls that use the same infrastructure.

---

## Phase 6: Report — Detailed Procedure

### Step 1: Write the bug report

Create `docs/starry-reports/bugs/BUG-NNN-<syscall>.md` using the template below. NNN is a zero-padded sequential number. If multiple bugs exist for the same syscall, use separate report files (e.g., `BUG-001-pwritev2.md`, `BUG-002-mremap-shadowing.md`, `BUG-003-mremap-maymove.md`).

### Step 2: Create a journal entry

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh BUG "<title>" "<body>"
```

### Step 3: Update known.json

Set the `status` field:
- `"fixed"` — bug confirmed and fix verified
- `"buggy"` — bug confirmed, fix not yet applied
- `"broken"` — multiple critical bugs, handler is fundamentally wrong
- `"stub"` — no real implementation exists
- `"mostly_ok"` — works for common cases, minor edge-case bugs remain

### Step 4: Re-prioritize if needed

If multiple new bugs were found in this session, dispatch the bug-triager agent to re-rank the backlog by severity and application impact.

---

## Bug Report Template

```markdown
# BUG-NNN: <Short descriptive title>

## Metadata
- **Category**: <concurrency|memory|safety|semantic|correctness> (<并发|内存|安全|语义|正确性>)
- **Severity**: <P0|P1|P2|P3>
- **Syscall**: <syscall_name>
- **Location**: <file_path>:<line_number>
- **Found**: <YYYY-MM-DD>
- **Status**: <open|fixed>

## Summary

<1-3 sentences describing the bug. State what is wrong, not what the code does.>

## Man Page Reference

<Quote or paraphrase the relevant requirement from the man page. Include the man page section (e.g., "man 2 mmap, DESCRIPTION, paragraph 4").>

## Test Case

- **File**: `os/StarryOS/tests/cases/test_<name>.c`
- **Failing test(s)**: `<test_name_1>`, `<test_name_2>`

## Linux Behavior

<What a correct Linux kernel does for the failing test input. Include actual output if available.>

## StarryOS Behavior

<What StarryOS does instead. Include actual output if available.>

## Root Cause

<Technical explanation of why the bug occurs. Reference specific lines of code. Explain the logic error, not just "the code is wrong.">

## Fix Description

<What was changed (or needs to change) to fix the bug. Reference the specific code change. If not yet fixed, describe the approach.>

## Verification

<How the fix was verified. Include test results: pass/fail counts before and after the fix. Note any regressions checked.>
```

### Severity Definitions

- **P0 — Critical**: Data corruption, security vulnerability, kernel panic/crash. Blocks target application startup.
- **P1 — High**: Wrong behavior that causes application malfunction. Wrong errno that breaks error handling. Missing functionality required by target applications.
- **P2 — Medium**: Edge-case incorrectness unlikely to hit in normal operation. Missing flag support for rarely-used flags. Stub that returns success for an uncommon syscall.
- **P3 — Low**: Cosmetic incorrectness. Missing validation that would only matter with malicious input. Performance issue without correctness impact.

---

## known.json Schema

The file `os/StarryOS/tests/known.json` is the persistent knowledge base for all syscall testing results. It tracks what has been tested, what bugs were found, and their current status.

### Top-level structure

```json
{
  "_comment": "StarryOS syscall test knowledge base. Updated by test pipeline.",
  "syscalls": {
    "<syscall_name>": { ... },
    "<syscall_name>": { ... }
  }
}
```

The `_comment` field is informational. The `syscalls` field is an object mapping syscall names (lowercase, e.g., `"mmap"`, `"pwritev2"`, `"fcntl_getfl"`) to per-syscall objects.

### Per-syscall object fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tested` | boolean | yes | Whether automated tests have been run for this syscall |
| `status` | string | yes | One of: `"fixed"`, `"buggy"`, `"broken"`, `"stub"`, `"mostly_ok"`, `"untested"` |
| `bugs` | array of strings | yes | Human-readable descriptions of each distinct bug found. Empty array if no bugs. |
| `source` | string | yes | Source location of the handler, format: `"kernel/src/syscall/<subsystem>/<file>.rs:<line>"` or `":<start_line>-<end_line>"` for ranges |
| `test` | string | yes | Path to the test file, format: `"tests/cases/test_<name>.c"` |
| `results` | object | no | Test outcome counts: `{ "pass": N, "fail": M }` |
| `results.notes` | string | no | Freeform notes about the results (e.g., "1 fail is test bug not kernel bug") |
| `notes` | string | no | General notes about the syscall implementation |

### Status values

- `"fixed"` — All known bugs have been fixed and verified. Tests pass.
- `"buggy"` — One or more confirmed bugs, not yet fixed. Some tests may pass.
- `"broken"` — Fundamentally incorrect implementation. Most or all tests fail. Requires significant rework.
- `"stub"` — Handler exists but performs no real work (typically just returns `Ok(0)`).
- `"mostly_ok"` — Works correctly for common cases. Minor edge-case bugs remain.
- `"untested"` — Handler exists but no automated tests have been written.

### Update protocol

When updating known.json:
1. Always preserve existing entries unless explicitly overwriting with new test results.
2. Append new bug descriptions to the `bugs` array; do not remove old entries when adding new ones.
3. Update `results` counts after each test run.
4. Change `status` only when the evidence supports it (e.g., after a fix is verified, change from `"buggy"` to `"fixed"`).

---

## High-Priority Syscalls by Target Application

These are the syscalls most critical for running the three target applications in the OS competition. Prioritize bug-hunting efforts on these syscalls, especially where they overlap across multiple applications.

### Nginx

Nginx is a high-performance HTTP server that exercises the networking and I/O subsystems heavily.

| Syscall | Subsystem | Notes |
|---------|-----------|-------|
| `socket` | net | Creates TCP/UDP sockets |
| `bind` | net | Binds to address/port |
| `listen` | net | Marks socket as passive |
| `accept4` | net | Accepts connections with flags (SOCK_CLOEXEC, SOCK_NONBLOCK) |
| `epoll_create1` | fs | Creates epoll instance |
| `epoll_ctl` | fs | Registers/modifies/removes fd watches |
| `epoll_wait` | fs | Waits for I/O events — core of the event loop |
| `sendfile` | fs | Zero-copy file-to-socket transfer — critical for static file serving |
| `writev` | fs | Scatter-gather write for HTTP headers + body |
| `mmap` | mm | Memory allocation, shared memory for worker processes |
| `clone3` | task | Worker process/thread creation |

### Python (CPython interpreter)

Python exercises a broad range of syscalls for memory management, process control, and IPC.

| Syscall | Subsystem | Notes |
|---------|-----------|-------|
| `mmap` | mm | Memory allocation, shared memory for multiprocessing |
| `mprotect` | mm | Guard pages, JIT compilation support |
| `brk` | mm | Heap management (used by malloc) |
| `futex` | task | Threading synchronization (used by pthread) |
| `clone` | task | Process/thread creation (fork, threading) |
| `pipe2` | fs | IPC between parent and child processes |
| `eventfd2` | fs | Event notification for multiprocessing |
| `memfd_create` | fs | Anonymous file for shared memory |
| `execve` | task | Running external programs (subprocess module) |
| `wait4` | task | Waiting for child process completion |

### Rust Compiler (rustc)

The Rust compiler exercises memory management and process control for parallel compilation.

| Syscall | Subsystem | Notes |
|---------|-----------|-------|
| `clone3` | task | Thread creation for parallel compilation |
| `futex` | task | Synchronization between compilation threads |
| `mmap` | mm | Large memory mappings for compiled artifacts |
| `mprotect` | mm | Stack guard pages for threads |
| `getrandom` | sys | Random number generation (used by HashMap seeding) |
| `pipe2` | fs | IPC for compiler driver communication |
| `dup2` | fs | File descriptor manipulation for I/O redirection |

### Overlap analysis

Syscalls that appear in multiple target applications are the highest-priority targets:

- **mmap**: All three applications (Nginx, Python, rustc)
- **clone/clone3**: All three applications
- **futex**: Python and rustc
- **mprotect**: Python and rustc
- **pipe2**: Python and rustc
- **epoll_\***: Nginx (but also used by tokio-based Rust programs)

Focus discovery and testing on these overlapping syscalls first, as a single bug fix can unblock multiple target applications simultaneously.
