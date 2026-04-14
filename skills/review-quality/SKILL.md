---
name: review-quality
description: This skill should be used when the user asks to "review code quality", "check kernel code", "review my changes", "code review StarryOS", "check API design", "review Rust patterns", or "ensure code quality". Enforces quality standards for all StarryOS kernel modifications before they are committed.
---

# StarryOS Kernel Code Quality Gate

Code quality gate for all StarryOS kernel modifications. Ensures changes reuse existing framework abstractions, follow idiomatic Rust patterns, maintain safety invariants, and produce ergonomic APIs.

Invoke this skill AFTER writing kernel code and BEFORE committing. Every change to files under `os/StarryOS/kernel/src/` must pass this gate. The skill evaluates five quality dimensions, runs automated checks, and produces a structured verdict.

## Quality Dimensions

### 1. Framework Reuse

Changes must use existing component abstractions rather than reimplementing functionality. The StarryOS kernel is organized into well-defined crates, and each has a clear responsibility:

| Crate | Responsibility |
|-------|---------------|
| `starry-vm` | Virtual memory operations: `vm_load`, `vm_write_slice`, `VmPtr`, `VmMutPtr`, user pointer validation |
| `starry-process` | Process management: `Pid`, process trees, thread groups |
| `starry-signal` | Signal delivery and handling: `Signo`, `SignalSet`, `SignalStack`, `SignalInfo` |
| `axpoll` | I/O polling: `IoEvents`, `Pollable` trait |
| `axsched` / `ax_task` | Scheduler: `current()`, `spawn_task`, task extensions |
| `ax_errno` | Error types: `AxError`, `AxResult`, `LinuxError` |
| `ax_kspin` | Kernel synchronization: `SpinNoIrq` |
| `ax_sync` | Blocking synchronization: `Mutex` |
| `ax_fs` | Filesystem context: `FS_CONTEXT`, `FileFlags`, `OpenOptions` |

Before accepting any hand-rolled utility, search the codebase for existing helpers. Common violations:

- Reimplementing user pointer read/write instead of using `VmPtr::vm_read()` / `VmMutPtr::vm_write()`
- Manually loading user strings instead of calling `vm_load_string()`
- Building IoVec buffers from scratch instead of using `IoVectorBuf`
- Duplicating flag-to-enum conversion that already exists in an adjacent handler

### 2. Rust Idiom Quality

Evaluate Rust usage against these criteria:

- **Result/Option propagation**: Use the `?` operator for propagation. Chain `map`, `and_then`, `ok_or` for transformations. Never use `.unwrap()` or `.expect()` in kernel code paths — these cause kernel panics on failure.
- **Type-safe wrappers**: Raw integers from userspace (`u32`, `usize`, `i32`) should be converted to typed wrappers as early as possible. Use `bitflags!` for flag sets (see `MmapProt`, `MmapFlags`, `CloneFlags` for canonical examples). Use domain types like `Pid`, `Signo`, `FileDescriptor` instead of passing raw integers through the call chain.
- **Enums over booleans**: Function parameters that select between two modes should use enums, not `bool`. Example: `do_exit(code, true)` is unclear; a `ExitScope::Group` enum would be better.
- **Derive traits**: Derive `Debug`, `Clone`, `Copy` on flag types and small value types. Derive `Default` where a zero/empty value is meaningful.
- **Lifetime annotations**: Only annotate lifetimes when the compiler requires it. Do not add unnecessary named lifetimes when elision applies.
- **Pattern matching**: Prefer `match` over `if let` chains when handling enums with multiple variants. Use `let ... else` for early returns from fallible conversions.

### 3. Safety Discipline

Every unsafe operation must be justified and bounded:

- **SAFETY comments**: Every `unsafe` block must have a `// SAFETY:` comment immediately above or inside it, explaining why the invariant holds. No exceptions. Example from the codebase: `unsafe { core::ptr::copy_nonoverlapping(...) }` must document why the source/destination/length are valid.
- **User pointer validation**: All pointers from userspace must be validated before dereferencing. Use the `VmPtr`/`VmMutPtr` wrapper methods (`vm_read()`, `vm_write()`, `vm_read_uninit()`). Use the `.nullable()` method to handle optional pointers. Never dereference a raw user pointer directly.
- **Lock discipline**: Locks must not be held across yield points. Search for `await`, `yield_now()`, `block_on()`, or any blocking operation inside a `lock()` scope. `SpinNoIrq` disables interrupts and must be held for the shortest possible duration. `Mutex` (from `ax_sync`) may block but must still not be held across explicit yield points.
- **Pointer arithmetic**: No raw pointer arithmetic without bounds checking. Use slice operations or `core::ptr::copy_nonoverlapping` with validated lengths.
- **Resource cleanup on error paths**: Every resource acquired (file descriptor, memory mapping, lock guard) must be released on all paths, including error paths. Prefer RAII via `Drop` implementations or scope guards. When manual cleanup is needed, verify every early `return Err(...)` releases prior allocations.

### 4. API Design

Evaluate the public interface of changed code:

- **Clear naming**: Function names follow the `sys_<syscall_name>` convention for handlers. Helper functions use descriptive verb-noun names. No abbreviations unless universally understood (fd, pid, etc.).
- **Specific error types**: Return `AxError` variants that map to the correct Linux errno. Use `AxError::InvalidInput` for EINVAL, `AxError::NotFound` for ENOENT, etc. When needed, convert through `LinuxError` explicitly: `Err(AxError::from(LinuxError::ENOSPC))`.
- **Consistent signatures**: New syscall handlers must follow the established pattern — take typed parameters (not `UserContext` directly), return `AxResult<isize>`. The dispatch in `mod.rs` extracts arguments via `uctx.argN() as _`.
- **Decomposition**: No god-functions. If a handler exceeds ~80 lines, decompose into focused helpers. Validation logic, the core operation, and result marshaling should be separable. See `CloneArgs::validate()` and `CloneArgs::do_clone()` as the canonical decomposition pattern.
- **Domain return types**: Handlers return `AxResult<isize>` at the boundary, but internal helpers should use proper domain types. Avoid returning raw `i64` or `usize` from helpers that produce structured results.

### 5. Architecture

Verify the change respects the layered architecture:

- **Proper layering**: Syscall handler (in `kernel/src/syscall/`) calls domain logic (in `kernel/src/task/`, `kernel/src/mm/`, `kernel/src/file/`), which calls framework abstractions (`starry-vm`, `ax_task`, etc.). Handlers must not reach down to hardware abstractions directly.
- **No circular dependencies**: A change in a syscall handler must not introduce an import cycle. Verify that `use crate::` imports point downward in the module hierarchy.
- **Component boundaries**: Do not bypass crate boundaries. Memory operations go through `starry-vm`. Process operations go through `starry-process`. Signal operations go through `starry-signal`. If the existing crate API is insufficient, extend the crate rather than working around it.
- **Module organization**: New syscall handlers go in the appropriate submodule (`fs/`, `mm/`, `task/`, `net/`, `io_mpx/`, `ipc/`, `sync/`, `signal`, `time`, `sys`, `resources`). Register the handler in `mod.rs` dispatch table.

## Workflow

Execute these steps in order:

### Step 1: Identify Changed Files

Run `git diff --name-only` (or `git diff --cached --name-only` for staged changes) to identify all modified kernel files. Focus the review on files under `os/StarryOS/kernel/src/`.

### Step 2: Deep Analysis

Dispatch the **kernel-reviewer agent** to perform the detailed code review. The agent reads all changed files, cross-references with adjacent handlers and existing abstractions, and produces a structured report with findings categorized as Critical, Important, or Suggestion.

### Step 3: Automated Checks

Run these commands and verify they pass:

```bash
# Clippy — catch common Rust mistakes
cargo xtask clippy --package starry-kernel

# Formatting — ensure consistent style
cargo fmt --check --package starry-kernel

# Build — verify compilation succeeds
cargo starry build --arch riscv64
```

### Step 4: Test Coverage

Verify that tests exist for the changed functionality:

- Check `os/StarryOS/tests/cases/` for a corresponding test file
- If no test exists, flag it as a gap — the hunt-bugs skill should be invoked to generate one
- If a test exists, run it via `bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` to verify it still passes

### Step 5: Verdict

Produce one of three verdicts:

- **PASS**: All quality dimensions satisfied, automated checks pass, test coverage adequate
- **PASS WITH NOTES**: Minor suggestions that do not block committing, but should be addressed in a follow-up
- **FAIL**: Critical or important findings that must be fixed before committing. List each blocker.

## Reference Material

Consult `references/kernel-patterns.md` for a catalog of approved patterns organized by category. Use these patterns as the baseline for evaluating code quality — deviations from established patterns require justification.

## Integration with hunt-bugs

The review-quality skill is automatically invoked during Phase 5 (Fix) of the hunt-bugs cycle. After a bug fix is written, the hunt-bugs workflow dispatches this skill to verify the fix meets quality standards before re-running the test suite. This ensures that bug fixes do not introduce new quality regressions.

When invoked from hunt-bugs:
1. Skip Step 1 (the changed files are already known from the fix phase)
2. Perform Steps 2-4 as normal
3. Return the verdict to the hunt-bugs workflow — a FAIL verdict blocks the fix from proceeding to Phase 6 (Report)
