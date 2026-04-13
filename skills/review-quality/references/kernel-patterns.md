# StarryOS Kernel Patterns Catalog

Approved patterns for StarryOS kernel development. All kernel changes should conform to these patterns. Deviations require explicit justification in code comments.

---

## Syscall Handler Patterns

### Standard Handler Signature

Every syscall handler follows a consistent signature and return convention. The dispatch table in `kernel/src/syscall/mod.rs` extracts raw arguments from `UserContext` and casts them to typed parameters:

```rust
// In mod.rs dispatch:
Sysno::mmap => sys_mmap(
    uctx.arg0() as _, uctx.arg1() as _, uctx.arg2() as _,
    uctx.arg3() as _, uctx.arg4() as _, uctx.arg5() as _,
),

// Handler signature — typed parameters, not raw UserContext:
pub fn sys_mmap(
    addr: usize,
    length: usize,
    prot: u32,
    flags: u32,
    fd: i32,
    offset: isize,
) -> AxResult<isize> {
    // ...
}
```

Key rules:
- Handlers take concrete typed parameters, not `&UserContext` (except `sys_clone` which needs the full context for stack/register manipulation).
- Return type is always `AxResult<isize>`. The dispatch code converts `Err(AxError)` to a negative errno via `LinuxError::from(err).code()`.
- The `isize` return value represents the Linux convention: non-negative on success, negative errno on failure (handled automatically by the dispatch layer).

### Argument Extraction and Validation

Validate arguments early, before performing any side effects:

```rust
pub fn sys_mmap(addr: usize, length: usize, prot: u32, flags: u32, fd: i32, offset: isize) -> AxResult<isize> {
    // 1. Validate simple constraints first
    if length == 0 {
        return Err(AxError::InvalidInput);  // EINVAL
    }

    // 2. Parse flags into typed wrappers
    let permission_flags = MmapProt::from_bits_truncate(prot);
    let map_flags = MmapFlags::from_bits(flags)
        .ok_or(AxError::InvalidInput)?;  // Or handle unknown flags

    // 3. Validate flag combinations
    if map_flags.contains(MmapFlags::ANONYMOUS) != (fd <= 0) {
        return Err(AxError::InvalidInput);
    }

    // 4. Convert remaining args to domain types
    let offset: usize = offset.try_into().map_err(|_| AxError::InvalidInput)?;

    // 5. Proceed with validated inputs...
}
```

### Error Propagation

The standard chain is: `AxError` (kernel-internal) maps to `LinuxError` (POSIX errno) at the dispatch boundary. Within handlers, use `AxError` variants:

```rust
// Preferred: use AxError variants directly
return Err(AxError::InvalidInput);     // → EINVAL
return Err(AxError::NotFound);         // → ENOENT
return Err(AxError::NoMemory);         // → ENOMEM
return Err(AxError::Unsupported);      // → ENOSYS
return Err(AxError::OperationNotSupported);  // → EOPNOTSUPP

// When a specific Linux errno has no direct AxError variant, convert explicitly:
return Err(AxError::from(LinuxError::ENOSPC));
return Err(AxError::from(LinuxError::EACCES));
return Err(AxError::from(LinuxError::EOWNERDEAD));

// Propagate errors from framework calls with ?
let file = File::from_fd(fd)?;
let bytes = get_file_like(fd)?.read(&mut buf)?;
```

### Flag Validation Pattern

Use the `bitflags!` macro to define type-safe flag sets. Two strategies depending on strictness:

```rust
// Strategy 1: Strict — reject unknown flags
let flags = MmapFlags::from_bits(raw_flags)
    .ok_or(AxError::InvalidInput)?;

// Strategy 2: Tolerant — truncate unknown flags with warning
let flags = match MmapFlags::from_bits(raw_flags) {
    Some(f) => f,
    None => {
        warn!("unknown flags: {raw_flags:#x}");
        MmapFlags::from_bits_truncate(raw_flags)
    }
};

// Strategy 3: Partial — validate critical bits, ignore advisory
let map_type = flags & MmapFlags::TYPE;  // extract type bits
if !matches!(map_type, MmapFlags::PRIVATE | MmapFlags::SHARED) {
    return Err(AxError::InvalidInput);
}
// Advisory flags like MAP_POPULATE can be silently accepted
```

Define bitflags with proper documentation and derive traits:

```rust
bitflags::bitflags! {
    /// `PROT_*` flags for use with [`sys_mmap`].
    #[derive(Debug, Clone, Copy)]
    struct MmapProt: u32 {
        const READ = PROT_READ;
        const WRITE = PROT_WRITE;
        const EXEC = PROT_EXEC;
    }
}
```

---

## Memory Management Patterns

### Address Space Operations via starry-vm

All virtual memory operations go through the `starry-vm` crate and the per-process address space lock:

```rust
let curr = current();
let mut aspace = curr.as_thread().proc_data.aspace.lock();

// Map a region
aspace.map(start, length, flags, backend)?;

// Unmap a region
aspace.unmap(addr, length)?;

// Find free area (try hint first, then fall back to full search)
let start = aspace
    .find_free_area(hint, length, range, align)
    .or(aspace.find_free_area(base, length, range, align))
    .ok_or(AxError::NoMemory)?;
```

Always lock the address space for the shortest duration possible. Do not hold `aspace.lock()` while performing I/O or blocking operations.

### User Pointer Validation

Use the `VmPtr` and `VmMutPtr` traits from `starry-vm` for all user pointer access. Never dereference raw user pointers:

```rust
use starry_vm::{VmPtr, VmMutPtr};

// Reading a value from user space
let value: u64 = user_ptr.vm_read()?;  // Returns AxResult

// Writing a value to user space
user_ptr.vm_write(result_value)?;

// Reading uninitialized memory (for structs from user space)
// SAFETY comment required for assume_init
let kstruct = unsafe { user_ptr.vm_read_uninit()?.assume_init() };

// Handling nullable pointers
if let Some(ptr) = raw_ptr.nullable() {
    ptr.vm_write(old_value)?;
}

// Loading a string from user space
let path = vm_load_string(user_path_ptr)?;

// Loading a byte buffer from user space
let data = vm_load(user_buf_ptr, length)?;

// Writing a byte slice to user space
vm_write_slice(user_buf_ptr, &kernel_slice)?;
```

### Scatter/Gather I/O (IoVec)

For readv/writev family syscalls, use the `IoVectorBuf` abstraction:

```rust
use crate::mm::{IoVec, IoVectorBuf, VmBytes, VmBytesMut};

// For reading into user buffers (readv):
let iovecs: &[IoVec] = /* loaded from user space */;
let mut buf = VmBytesMut::from_iovecs(iovecs);
let bytes_read = file.read(&mut buf)?;

// For writing from user buffers (writev):
let buf = VmBytes::from_iovecs(iovecs);
let bytes_written = file.write(&buf)?;
```

### File-Backed Mapping Setup

When setting up file-backed mappings, determine the backend based on map type and file type:

```rust
let backend = match map_type {
    MmapFlags::SHARED | MmapFlags::SHARED_VALIDATE => {
        if let Some(file) = file {
            let inner = file.inner();
            match inner.backend()?.clone() {
                FileBackend::Cached(cache) => {
                    Backend::new_file(start, cache, inner.flags(), offset, &aspace_ref)
                }
                FileBackend::Direct(loc) => {
                    // Device mmap path
                    let device = loc.entry()...;
                    device.mmap(start, length, offset, permission_flags)?;
                    Backend::new_device(...)
                }
            }
        } else {
            Backend::new_shared(start, SharedPages::new(), &aspace_ref)
        }
    }
    MmapFlags::PRIVATE => {
        // COW private mapping
        Backend::new_private(...)
    }
};
```

---

## Process Management Patterns

### Clone/Fork via CloneArgs

All process creation goes through the unified `CloneArgs` structure. The `sys_clone`, `sys_clone3`, `sys_fork`, and `sys_vfork` handlers construct `CloneArgs` with appropriate defaults and call `do_clone()`:

```rust
// The canonical decomposition:
pub struct CloneArgs {
    pub flags: CloneFlags,
    pub exit_signal: u64,
    pub stack: usize,
    pub tls: usize,
    pub parent_tid: usize,
    pub child_tid: usize,
    pub pidfd: usize,
}

impl CloneArgs {
    fn validate(&self) -> AxResult<()> {
        // All flag combination checks here — keep validation pure
    }

    pub fn do_clone(self, uctx: &UserContext) -> AxResult<isize> {
        self.validate()?;
        // Core logic after validation passes
    }
}

// fork is just clone with default flags:
pub fn sys_fork(uctx: &UserContext) -> AxResult<isize> {
    sys_clone(uctx, SIGCHLD, 0, 0, 0, 0)
}
```

This pattern — separate validation from execution, unify variants through a shared args struct — should be followed for any syscall family with multiple entry points.

### File Descriptor Table Operations

Use the scoped FD table API. Access the current process's FD table through the `FileLike` trait:

```rust
use crate::file::{File, FileLike, get_file_like, close_file_like, FD_TABLE};

// Get a file-like object by fd
let file = get_file_like(fd)?;

// Add a new file to the FD table (returns the fd number)
let new_fd = my_file_like.add_to_fd_table(cloexec)?;

// Close a file descriptor
close_file_like(fd)?;

// Clone FD table for fork (shared vs. independent copy)
if flags.contains(CloneFlags::FILES) {
    FD_TABLE.scope_mut(&mut scope).clone_from(&FD_TABLE);  // shared
} else {
    FD_TABLE.scope_mut(&mut scope).write().clone_from(&FD_TABLE.read());  // copy
}
```

### Signal Delivery via starry-signal

Use the signal infrastructure from `starry-signal` and the helpers in `kernel/src/task/`:

```rust
use starry_signal::{Signo, SignalSet, SignalInfo, SignalStack};
use crate::task::{send_signal_to_process, send_signal_to_thread, send_signal_to_process_group};

// Parse a signal number from user space
let signo = Signo::from_repr(raw_signo as u8).ok_or(AxError::InvalidInput)?;

// Send a signal
send_signal_to_process(pid, SignalInfo::new(signo, SI_USER))?;
send_signal_to_thread(tid, SignalInfo::new(signo, SI_TKILL))?;

// Manipulate signal masks
let sig = &current().as_thread().signal;
let old_mask = sig.blocked();
```

### Exit and Wait

Exit is a non-returning operation. The handler calls `do_exit` which never returns to the caller:

```rust
pub fn sys_exit(exit_code: i32) -> AxResult<isize> {
    do_exit(exit_code << 8, false);  // false = exit this thread only
    Ok(0)  // unreachable but satisfies return type
}

pub fn sys_exit_group(exit_code: i32) -> AxResult<isize> {
    do_exit(exit_code << 8, true);  // true = exit entire process group
    Ok(0)
}
```

The exit code is shifted left by 8 to match Linux's `wait` status encoding (low byte is signal, second byte is exit code).

---

## Concurrency Patterns

### Lock Ordering

StarryOS uses two primary lock types. Consistent ordering prevents deadlocks:

1. **`SpinNoIrq`** (from `ax_kspin`): Non-blocking spinlock that disables interrupts. Use for short critical sections protecting data structures accessed in interrupt context or when blocking is unacceptable.

2. **`Mutex`** (from `ax_sync`): Blocking mutex. Use for longer critical sections where the thread can sleep while waiting.

General ordering rules:
- Acquire `SpinNoIrq` locks before `Mutex` locks when both are needed
- Never acquire a `Mutex` while holding a `SpinNoIrq` (the spin lock disables preemption, so the mutex holder may never get scheduled)
- Acquire address space lock (`aspace.lock()`) before FD table lock
- Acquire process tree lock before individual process data locks

### When to Use SpinNoIrq vs. Mutex

```rust
// SpinNoIrq: interrupt-safe, very short critical sections
use ax_kspin::SpinNoIrq;
let signal_actions: Arc<SpinNoIrq<SignalActions>> = ...;
{
    let actions = signal_actions.lock();
    // Fast operation: read or update a few fields
    // Do NOT do I/O, allocate, or call functions that might block
}

// Mutex: blocking-safe, longer critical sections
use ax_sync::Mutex;
let msg_queue: Arc<Mutex<MessageQueue>> = ...;
{
    let mut queue = msg_queue.lock();
    // May allocate, do moderate computation
    // Still minimize duration — do not hold across syscall boundaries
}
```

### Atomic Operations

Use atomics for simple counters and flags that do not require mutual exclusion:

```rust
use core::sync::atomic::{AtomicU32, AtomicBool, Ordering};

// Counters
static NEXT_ID: AtomicU32 = AtomicU32::new(1);
let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);

// Flags
let cancelled = AtomicBool::new(false);
cancelled.store(true, Ordering::Release);
if cancelled.load(Ordering::Acquire) { ... }
```

Use `Ordering::Relaxed` for statistics and counters where exact ordering does not matter. Use `Acquire`/`Release` pairs when the atomic guards access to other data.

### Critical Section Minimization

Structure code to minimize time spent holding locks:

```rust
// BAD: holding lock across I/O
let mut aspace = proc_data.aspace.lock();
let data = file.read(&mut buf)?;  // blocks while holding aspace lock
aspace.map(...)?;

// GOOD: release lock before I/O, re-acquire after
let mapping_params = {
    let aspace = proc_data.aspace.lock();
    aspace.find_free_area(...)?
    // lock dropped here
};
let data = file.read(&mut buf)?;  // no lock held
{
    let mut aspace = proc_data.aspace.lock();
    aspace.map(mapping_params, ...)?;
}
```

---

## Anti-Patterns to Avoid

### Returning Ok(0) as a Stub

Never return `Ok(0)` from an unimplemented syscall. This silently pretends the operation succeeded, causing subtle bugs in userspace programs that depend on the side effects.

```rust
// BAD: silent stub
pub fn sys_fadvise64(_fd: i32, _offset: i64, _len: i64, _advice: i32) -> AxResult<isize> {
    Ok(0)
}

// GOOD: explicit unsupported error
pub fn sys_fadvise64(_fd: i32, _offset: i64, _len: i64, _advice: i32) -> AxResult<isize> {
    Err(AxError::Unsupported)  // → ENOSYS
}

// ALSO ACCEPTABLE: advisory-only syscalls where Ok(0) is correct per spec
// But add a comment explaining why:
pub fn sys_fadvise64(_fd: i32, _offset: i64, _len: i64, _advice: i32) -> AxResult<isize> {
    // fadvise is advisory only; ignoring the hint is valid per POSIX.
    Ok(0)
}
```

### Ignoring Flags Parameters

Never silently ignore flags. Either handle them, reject unknown flags, or log a warning:

```rust
// BAD: flags parameter completely ignored
pub fn sys_pipe2(fds: *mut [c_int; 2], _flags: u32) -> AxResult<isize> { ... }

// GOOD: parse and act on flags
pub fn sys_pipe2(fds: *mut [c_int; 2], flags: u32) -> AxResult<isize> {
    let cloexec = (flags & O_CLOEXEC) != 0;
    let nonblock = (flags & O_NONBLOCK) != 0;
    if flags & !(O_CLOEXEC | O_NONBLOCK) != 0 {
        return Err(AxError::InvalidInput);  // reject unknown flags
    }
    // ...
}
```

### Copy-Paste from Adjacent Handlers

The preadv/pwritev copy-paste bug is a real example from this codebase: `sys_pwritev` was copied from `sys_preadv` and the `read_at` call was not changed to `write_at`. When implementing a handler similar to an existing one:

1. Extract the shared logic into a helper function parameterized by the operation
2. Write the new handler in terms of the helper
3. Do not copy-paste and edit — this invites exactly this class of bug

```rust
// BAD: copy-paste sys_preadv → sys_pwritev, forget to change read → write
// GOOD: extract shared iovec handling, parameterize the I/O operation
fn do_preadwritev(fd: i32, iovecs: &[IoVec], offset: i64, is_write: bool) -> AxResult<isize> {
    // shared validation and setup
}
```

### Using Raw Integers for Typed Values

Never pass raw `i32` or `usize` through multiple function calls when a typed wrapper exists:

```rust
// BAD: raw integer threaded through the call chain
fn handle_signal(pid: i32, sig: i32) { ... }

// GOOD: use domain types
fn handle_signal(pid: Pid, sig: Signo) { ... }
```

Convert at the boundary (in the syscall handler), then use typed values everywhere else.

### Holding Locks Across Blocking Operations

Never hold a lock while performing an operation that might block or yield:

```rust
// BAD: aspace lock held across file read (file read may block)
let mut aspace = proc_data.aspace.lock();
let data = file.read_at(offset, &mut buf)?;
aspace.map(addr, data.len(), ...)?;

// BAD: SpinNoIrq held while calling block_on (deadlock)
let guard = spin_lock.lock();
block_on(some_future);

// GOOD: acquire lock only for the critical section
let data = file.read_at(offset, &mut buf)?;
let mut aspace = proc_data.aspace.lock();
aspace.map(addr, data.len(), ...)?;
```

### Missing User Pointer Validation

Every pointer from userspace is potentially invalid, null, or pointing to kernel memory. Always validate:

```rust
// BAD: direct dereference of user pointer
let value = unsafe { *user_ptr };

// GOOD: use VmPtr/VmMutPtr
let value = user_ptr.vm_read()?;

// GOOD: handle nullable pointers explicitly
if let Some(ptr) = raw_ptr.nullable() {
    let value = ptr.vm_read()?;
}

// GOOD: validate string pointers
let path = vm_load_string(user_path)?;  // handles null terminator, validates pages
```

---

## Checklist for New Syscall Handlers

When implementing a new syscall handler, verify:

- [ ] Handler signature uses typed parameters, returns `AxResult<isize>`
- [ ] Handler registered in `mod.rs` dispatch table with correct argument extraction
- [ ] Arguments validated before any side effects
- [ ] Flags parsed via `bitflags!` type with proper handling of unknown bits
- [ ] User pointers accessed only through `VmPtr`/`VmMutPtr` methods
- [ ] Error codes match Linux man page for each failure mode
- [ ] No `.unwrap()` or `.expect()` in the handler or called helpers
- [ ] Every `unsafe` block has a `// SAFETY:` comment
- [ ] Locks released before any blocking operation
- [ ] Resources cleaned up on all error paths (fd, mapping, allocation)
- [ ] Adjacent/related handlers checked for shared logic to extract
- [ ] Test case exists in `tests/cases/` covering success and error paths
