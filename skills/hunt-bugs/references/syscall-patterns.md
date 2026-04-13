# Syscall Bug Patterns Reference

Common bug patterns found in StarryOS syscall implementations, documented with real examples from this codebase. Use these patterns as templates when scanning for NEW bugs — each pattern described here has likely been replicated elsewhere in the kernel.

---

## Pattern 1: Copy-Paste Errors

**What it looks like.** Two adjacent syscall handlers share nearly identical structure because one was copied from the other. The developer changed the function name and debug message but forgot to change a critical operation in the body.

**How to detect.** Search for pairs of read/write, send/recv, or get/set handlers in the same file. Diff their bodies. Look for operations that contradict the function name.

### Real Example: pwritev2 calls read_at instead of write_at

**File**: `kernel/src/syscall/fs/io.rs`  
**Lines**: 210-222

The `sys_pwritev2` handler was copied from `sys_preadv2` (lines 196-208). The function name, debug message, and signature were all updated correctly. But the body still calls `read_at` instead of `write_at`:

```rust
pub fn sys_pwritev2(
    fd: c_int,
    iov: *const IoVec,
    iovcnt: usize,
    offset: __kernel_off_t,
    _flags: u32,
) -> AxResult<isize> {
    debug!("sys_pwritev2 <= fd: {fd}, iovcnt: {iovcnt}, offset: {offset}, flags: {_flags}");
    let f = File::from_fd(fd)?;
    f.inner()
        .read_at(IoVectorBuf::new(iov, iovcnt)?.into_io(), offset as _)  // BUG: should be write_at
        .map(|n| n as _)
}
```

**Impact**: P0 — Data loss. Any program using `pwritev2` believes it wrote data, but the data was never written. The call returns the number of bytes "written" (actually bytes read from the target), so the caller has no indication of failure.

**Where to look for more.** Scan all files under `kernel/src/syscall/` for pairs of functions where one name contains "read" and the adjacent one contains "write" (or "get"/"set", "send"/"recv"). Diff the bodies and verify that directional operations match the function name. Also check `preadv` vs `pwritev` (without the `2` suffix), `recvmsg` vs `sendmsg`, and `getxattr` vs `setxattr`.

---

## Pattern 2: Variable Shadowing

**What it looks like.** A function parameter is shadowed by a local variable of the same name, causing the original value to become inaccessible. In Rust, this compiles without warning because shadowing is a language feature. The developer may not realize the original value has been discarded.

**How to detect.** Search for `let <param_name> =` inside function bodies where `<param_name>` matches a function parameter. Check whether the original parameter value was still needed after the shadowing point.

### Real Example: mremap shadows the flags parameter

**File**: `kernel/src/syscall/mm/mmap.rs`  
**Lines**: 282-318

The `sys_mremap` function takes a `flags: u32` parameter containing MREMAP_* flags (MREMAP_MAYMOVE, MREMAP_FIXED, etc.). On line 300, a new `let flags` binding shadows the parameter with the memory mapping's permission flags:

```rust
pub fn sys_mremap(addr: usize, old_size: usize, new_size: usize, flags: u32) -> AxResult<isize> {
    // ...
    let flags = aspace.find_area(addr).ok_or(AxError::NoMemory)?.flags();  // SHADOWS parameter
    drop(aspace);
    let new_addr = sys_mmap(
        addr.as_usize(),
        new_size,
        flags.bits() as _,   // Uses MappingFlags, not MREMAP_* flags
        MmapFlags::PRIVATE.bits(),
        -1,
        0,
    )? as usize;
    // ...
}
```

**Impact**: P1 — The MREMAP_MAYMOVE flag is completely ignored. Without MREMAP_MAYMOVE, Linux requires the mapping to grow or shrink in place and returns ENOMEM if that is not possible. StarryOS always creates a new mapping and moves the data, even when the caller explicitly did not permit moving. Programs that depend on pointer stability (e.g., memory allocators that hand out pointers into mmap'd regions) will experience memory corruption.

Additionally, the shadowed `flags` value (MappingFlags) is passed as `prot` to `sys_mmap`, which expects MmapProt bits — a completely different bitfield. This happens to work by accident for common combinations but is semantically wrong.

**Where to look for more.** Search for `let addr =`, `let flags =`, `let len =`, `let offset =`, and `let fd =` inside syscall handler functions. These are common parameter names that are also commonly reused as local variable names. Check whether any of these shadow a parameter whose original value was still meaningful.

---

## Pattern 3: Stub Implementations

**What it looks like.** The syscall handler exists in the dispatch table and has a proper function signature, but the body does nothing meaningful. It returns `Ok(0)` unconditionally, making the caller believe the operation succeeded.

**How to detect.** Search for functions whose body is just `Ok(0)` (possibly preceded by a `debug!()` call and a `// TODO` comment). Also check for functions that accept parameters but prefix them all with `_`.

### Real Example: flock returns Ok(0) without locking

**File**: `kernel/src/syscall/fs/fd_ops.rs`  
**Lines**: 308-312

```rust
pub fn sys_flock(fd: c_int, operation: c_int) -> AxResult<isize> {
    debug!("flock <= fd: {fd}, operation: {operation}");
    // TODO: flock
    Ok(0)
}
```

**Impact**: P1 for multi-process applications — Exclusive locks (LOCK_EX) do not block concurrent access. Any application using flock for coordination (e.g., log file rotation, PID files, database lock files) will see silent data corruption or race conditions. The `operation` parameter is not even validated: passing 0 (which is invalid per the man page and should return EINVAL) returns success.

**Cascading effect of stubs.** The danger of stubs is not just the missing functionality — it is the false success signal. The caller checks the return value, sees 0, and proceeds under the assumption that the lock is held. This makes bugs intermittent and extremely hard to diagnose, because the application logic is correct and the kernel is lying.

**Where to look for more.** Run this search strategy across the entire syscall directory:
1. Find all functions matching `pub fn sys_*` that contain `Ok(0)` as the last expression.
2. Filter to those where the function body is fewer than 10 lines.
3. Cross-reference with the man page: does the syscall have observable side effects? If yes, the stub is a bug.

Known stubs beyond flock include: `sys_madvise` (line 320-323 of mmap.rs, returns Ok(0) for all advice values), `sys_msync` (line 325-328, returns Ok(0) without syncing), `sys_mlock`/`sys_mlock2` (lines 331-337, returns Ok(0) without locking pages). Some of these are harmless (madvise is advisory), but others (msync) can cause data loss.

---

## Pattern 4: Missing Flag Handling

**What it looks like.** A syscall accepts a `flags` parameter but ignores it entirely or only handles a small subset of the documented flags. The unhandled flags are either silently ignored (parameter named `_flags`) or passed through a truncating bitfield parser that drops unknown bits.

**How to detect.** Search for parameters named `_flags` or `flags` followed by no code that reads them. Also search for `from_bits_truncate` — this discards any bits not defined in the bitflags struct, silently ignoring flags the implementation does not know about.

### Real Example: copy_file_range ignores the flags parameter

**File**: `kernel/src/syscall/fs/io.rs`  
**Lines**: 317-352

```rust
pub fn sys_copy_file_range(
    fd_in: c_int,
    off_in: *mut u64,
    fd_out: c_int,
    off_out: *mut u64,
    len: usize,
    _flags: u32,       // Named with underscore — intentionally ignored
) -> AxResult<isize> {
    // ...
    // TODO: check flags          <-- Developer acknowledged this is missing
    // TODO: check both regular files
    // TODO: check same file and overlap
    // ...
}
```

**Impact**: P2 currently (Linux 5.x+ requires flags to be 0 and returns EINVAL otherwise), but the TODO comments reveal two additional missing checks that are P0: no regular-file validation (the call accepts pipes and sockets, which is incorrect) and no same-file overlap check (which causes data corruption — see Pattern 7).

**Where to look for more.** Grep for `_flags` across all syscall handlers. Each hit is a flag parameter being ignored. Then verify against the man page whether that parameter actually matters. Priority targets:
- `splice` (also in io.rs, `_flags` parameter)
- `epoll_create1` (flags should only accept EPOLL_CLOEXEC)
- `dup3` (flags handling exists but verify completeness)
- `clone3` (complex flags field — check all CLONE_* bits)

---

## Pattern 5: Wrong Derivation / Incorrect Data Source

**What it looks like.** The handler computes a return value or internal state from the wrong data source. The code works mechanically (no crashes) but produces semantically incorrect results because it reads from the wrong field or structure.

**How to detect.** For syscalls that return state about a file descriptor or process, trace where the returned value comes from. Compare against the man page's definition of what the value should represent. Check whether the code reads from metadata (permissions, timestamps) when it should read from runtime state (open flags, file position).

### Real Example: fcntl F_GETFL derives access mode from file permissions

**File**: `kernel/src/syscall/fs/fd_ops.rs`  
**Lines**: 256-273

```rust
F_GETFL => {
    let f = get_file_like(fd)?;

    let mut ret = 0;
    if f.nonblocking() {
        ret |= O_NONBLOCK;
    }

    let perm = NodePermission::from_bits_truncate(f.stat()?.mode as _);
    if perm.contains(NodePermission::OWNER_WRITE) {
        if perm.contains(NodePermission::OWNER_READ) {
            ret |= O_RDWR;
        } else {
            ret |= O_WRONLY;
        }
    }

    Ok(ret as _)
}
```

**The bug.** `F_GETFL` must return the file status flags that were passed to `open()` — specifically the access mode (O_RDONLY, O_WRONLY, O_RDWR) and status flags (O_APPEND, O_NONBLOCK, O_DSYNC, etc.). Instead, this code derives the access mode from the inode's permission bits (`st_mode`). This is fundamentally wrong: a file with mode 0644 (owner read+write) opened with O_RDONLY should report O_RDONLY from F_GETFL, not O_RDWR.

**Additional bugs in the same handler.** O_APPEND is not tracked at all. F_SETFL only handles O_NONBLOCK but ignores O_APPEND, O_ASYNC, O_DIRECT, and O_NOATIME. The open flags are never stored in a way that F_GETFL can retrieve them.

**Impact**: P1 — Programs that use `fcntl(fd, F_GETFL)` to check access mode or append status get wrong answers. This breaks libraries like glibc's fdopen() which checks F_GETFL to determine the file's mode string.

**Where to look for more.** Look for any syscall handler that calls `.stat()` or `.metadata()` to produce a return value. Check whether the man page says the return value should come from the file's runtime state rather than its filesystem metadata. Common confusion points:
- File size from stat vs. seek position
- Permission bits vs. effective access (which depends on uid/gid)
- Link count from stat vs. whether the file is actually deleted

---

## Pattern 6: Non-Anonymous Memory Backed by Real Files

**What it looks like.** An API that is supposed to create anonymous (in-memory-only) objects instead creates real filesystem entries. The objects appear to work for basic operations but have wrong behavior for identity checks (link count, visibility in directory listings, persistence after close).

**How to detect.** Search for handlers of anonymous-memory APIs (memfd_create, shmget, epoll_create) and check whether they create files under `/tmp/` or `/dev/shm/` instead of truly anonymous objects.

### Real Example: memfd_create creates real files in /tmp/

**File**: `kernel/src/syscall/fs/memfd.rs`  
**Lines**: 15-32

```rust
pub fn sys_memfd_create(_name: UserConstPtr<c_char>, flags: u32) -> AxResult<isize> {
    // This is cursed
    for id in 0..0xffff {
        let name = format!("/tmp/memfd-{id:04x}");
        let fs = FS_CONTEXT.lock().clone();
        if fs.resolve(&name).is_err() {
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .open(&fs, &name)?
                .into_file()?;
            let cloexec = flags & MFD_CLOEXEC != 0;
            return File::new(file).add_to_fd_table(cloexec).map(|fd| fd as _);
        }
    }
    Err(AxError::TooManyOpenFiles)
}
```

**What is wrong.** The man page states: "The name supplied in name is used as a filename and will be displayed as the target of the corresponding symbolic link in the directory /proc/self/fd/. The displayed name is always prefixed with memfd: and serves only for debugging purposes." The file descriptor is supposed to refer to an anonymous inode with `st_nlink == 0` that is not visible in any directory.

Instead, this implementation:
1. Creates a real file at `/tmp/memfd-XXXX` (visible in directory listings, `st_nlink == 1`)
2. The file persists after the fd is closed (data leakage / disk space exhaustion)
3. The `_name` parameter is ignored entirely (should be used for the debug name)
4. MFD_HUGETLB and MFD_ALLOW_SEALING flags are ignored

**Impact**: P2 — Basic memfd functionality works (read, write, ftruncate, mmap), so Python's multiprocessing module can use it for data transfer. But programs that check `fstat()` on the memfd and expect `st_nlink == 0` will behave incorrectly. The leaked files in `/tmp/` will accumulate over time.

**Where to look for more.** Check whether `epoll_create1`, `eventfd2`, `timerfd_create`, `signalfd4`, and `userfaultfd` create real files instead of anonymous inodes. These all return file descriptors backed by kernel objects, not filesystem files.

---

## Pattern 7: Overlapping Copy Corruption

**What it looks like.** A data-copying syscall uses a naive read-then-write loop with an intermediate buffer. When the source and destination regions overlap within the same file, the read operations see data that was already modified by prior write operations in the same loop iteration.

**How to detect.** Search for copy/transfer syscalls that operate on file descriptors and check whether the implementation detects when `fd_in == fd_out` with overlapping offset ranges. If there is no overlap check, verify whether the copy direction matters (forward copy corrupts on forward overlap, backward copy corrupts on backward overlap).

### Real Example: copy_file_range corrupts data on same-file overlap

**File**: `kernel/src/syscall/fs/io.rs`  
**Lines**: 317-352 (handler) + 263-292 (do_send)

The `sys_copy_file_range` handler delegates to `do_send`, which performs a forward-reading loop:

```rust
fn do_send(mut src: SendFile, mut dst: SendFile, len: usize) -> AxResult<usize> {
    let mut buf = vec![0; 0x1000];       // 4KB intermediate buffer
    let mut total_written = 0;
    let mut remaining = len;

    while remaining > 0 {
        // ...
        let bytes_read = src.read(&mut buf[..to_read])?;
        // ...
        let bytes_written = dst.write(&buf[..bytes_read])?;
        // ...
    }
    Ok(total_written)
}
```

When `fd_in == fd_out` and `off_out > off_in` with `off_out < off_in + len` (forward overlap), the write operations modify bytes that the subsequent read operations will encounter. The result is that the overlapping portion of the destination contains corrupted data — a repeating pattern of the first 4KB of source data instead of the original content.

The Linux kernel handles this by checking for same-file overlap in `vfs_copy_file_range()` and falling back to a `splice`-based or `memmove`-style copy that handles overlap correctly. StarryOS has no such check (the TODO comment on line 337 acknowledges this: `// TODO: check same file and overlap`).

**Impact**: P0 — Silent data corruption. Any program using copy_file_range for in-place file manipulation (e.g., inserting data into a file by shifting content forward) will corrupt data. The corruption is silent because the syscall returns success.

**Where to look for more.** Check all data transfer syscalls for overlap safety:
- `splice` with same pipe on both ends
- `sendfile` with same file (if the kernel allows it)
- `tee` (pipe-to-pipe copy)
- Any ioctl that copies data within a file

---

## Detection Strategy Summary

Use the following search queries systematically across `os/StarryOS/kernel/src/syscall/`:

| Pattern | Search Query | What to Check |
|---------|-------------|---------------|
| Copy-paste | Adjacent `read`/`write` or `get`/`set` function pairs | Directional operations match function name |
| Variable shadowing | `let <param_name> =` inside function body | Original parameter still needed after shadow |
| Stubs | `Ok(0)` as last expression in short functions | Man page says syscall has side effects |
| Missing flags | `_flags` parameter name, `from_bits_truncate` | Man page documents flags that matter |
| Wrong derivation | `.stat()` or `.metadata()` in return-value computation | Man page says value comes from runtime state |
| Non-anonymous | `/tmp/` or filesystem operations in anonymous APIs | Man page says object should be anonymous |
| Overlap corruption | `do_send` or read/write loop without overlap check | Same-fd case with overlapping offsets |

When scanning for new bugs, work through each pattern against every syscall handler file. A single file often contains multiple instances of the same pattern (e.g., io.rs has both the copy-paste bug and the overlap corruption bug). Cross-reference every finding with the man page before filing a bug report — not every pattern match is actually a bug (e.g., ignoring `madvise` flags is acceptable because madvise is advisory).
