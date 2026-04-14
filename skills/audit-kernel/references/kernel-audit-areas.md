# Kernel Audit Areas -- StarryOS / tgoskits

This document catalogs every kernel subsystem to audit beyond syscall dispatch,
with exact source paths in the `tgoskits` workspace, what to look for, property
tests to write, concurrency risks, and stress-test configurations.

All paths are relative to the workspace root `tgoskits/`.

## Hidden Debug Features (already implemented, just need enabling)

These features exist in the codebase but are compiled out by default. Enable them for audit sessions:

### Lockdep — Deterministic deadlock detection
```toml
# os/StarryOS/kernel/Cargo.toml
ax-kspin = { version = "0.3.1", features = ["lockdep"] }
```
Runtime lock ordering validator. Panics on first AB/BA deadlock, recursive acquisition, or out-of-order unlock. ~5% overhead. See `concurrency-reproduction.md` section 0.

### Memtrack — Heap allocation tracking with backtraces
```toml
# os/StarryOS/kernel/Cargo.toml, under [features]:
# Enable the existing memtrack feature:
memtrack = ["ax-feat/dwarf", "ax-alloc/tracking", "dep:gimli"]

# Then build with:
# cargo starry build --arch riscv64 --features memtrack
```
Records every heap allocation with its full backtrace (function names + line numbers via DWARF). Exposes `/dev/memtrack` in the pseudofs for reading allocation state. Use for memory leak detection:
1. Enable memtrack, boot kernel
2. Read `/dev/memtrack` to get baseline allocation count and generation
3. Run workload (e.g., 100 fork+exit cycles)
4. Read `/dev/memtrack` again — any allocations from AFTER the baseline that survived = potential leak
5. Backtraces tell you exactly which code path leaked

### Alternative scheduler — CFS
```toml
# To test with CFS instead of round-robin, in ax-feat dependencies:
# Replace "sched-rr" with "sched-cfs"
```
Useful for differential testing: if a bug appears with `sched-rr` but not `sched-cfs` (or vice versa), it's a scheduler-specific issue.

---

## 1. Scheduler

### Source paths

- `components/axsched/src/lib.rs` -- `BaseScheduler` trait definition
- `components/axsched/src/round_robin.rs` -- Round-robin scheduler (default via `sched-rr` feature)
- `components/axsched/src/cfs.rs` -- CFS scheduler with vruntime/nice weights
- `components/axsched/src/fifo.rs` -- FIFO scheduler
- `os/arceos/modules/axtask/src/run_queue.rs` -- Per-CPU run queues, `AxRunQueue`, `RUN_QUEUES` static array
- `os/arceos/modules/axtask/src/timers.rs` -- Per-CPU timer lists, `TaskWakeupEvent`, timer ticket matching
- `os/arceos/modules/axtask/src/task.rs` -- `TaskInner` states, per-task metadata
- `os/arceos/modules/axtask/src/wait_queue.rs` -- Sleep/wake queue mechanics

### What to look for

Audit `pick_next_task` and `put_prev_task` for every scheduler variant. Confirm
that a preempted task goes to the front of the ready queue and a yielding task
goes to the back. A bug here looks like: a preempted task is placed at the back,
causing starvation of high-priority work.

Audit timer tick handling in `task_tick`. For round-robin, verify the time slice
counter decrements correctly and returns `true` when exhausted. For CFS, verify
vruntime advances proportionally to the inverse of the task's weight. A bug
looks like: vruntime calculated with wrong weight index (off-by-one in
`NICE2WEIGHT_POS`/`NICE2WEIGHT_NEG` arrays) causing unfair scheduling.

Audit `TaskWakeupEvent::callback` in `timers.rs`. The ticket-ID mechanism
prevents stale wakeups -- verify that ticket IDs are atomically incremented on
each new sleep and that the comparison uses the correct ordering. A bug looks
like: a task wakes spuriously because a stale timer with an old ticket ID fires
and the comparison uses `Relaxed` instead of `Acquire`.

Audit the `RUN_QUEUES` static array. It uses `MaybeUninit` with a size of
`MAX_CPU_NUM`. Verify that every index is initialized before first access on
each CPU, and that no CPU reads another CPU's run queue without proper
synchronization. A bug looks like: accessing `RUN_QUEUES[cpu_id]` before that
CPU has called `init`, triggering UB from `MaybeUninit::assume_init`.

For SMP, audit load balancing (if any). Check whether tasks are migrated between
CPUs and whether the migration respects CPU affinity masks (`AxCpuMask`). Verify
the `PREV_TASK` weak reference on each CPU does not prevent task deallocation
after exit.

### Property tests

- Spawn N equal-priority threads, each incrementing a per-thread counter for T
  seconds. After T seconds, collect all counters. The ratio of max to min should
  be within 2:1 for round-robin, within 1.5:1 for CFS with equal nice values.
- Thread calls `nanosleep(50ms)`. Measure actual sleep duration. Accept if
  within 50ms +/- 15ms (one tick period plus scheduling jitter).
- Spawn a high-nice and a low-nice thread under CFS. The low-nice thread should
  accumulate at least 3x more CPU time over a 1-second window.

### Concurrency risks

- Concurrent `add_task`/`pick_next_task` on the same per-CPU run queue. The run
  queue is guarded by `SpinNoIrq`, but verify the lock is held for the entire
  pick-and-switch sequence, not just the dequeue.
- Timer list insertion on one CPU while the timer fires on the same CPU from an
  interrupt context. Confirm `TIMER_LIST` access uses `NoPreemptIrqSave`.
- `EXITED_TASKS` per-CPU deque: if a task exits on CPU 0 but was last running on
  CPU 1, verify the exit path enqueues to the correct CPU's deque.

### Stress-test configuration

- SMP=4, 64 threads, 10-second run, with random sleep/yield/spin mix.
- Timer stress: 200 threads each sleeping 1ms in a tight loop for 5 seconds.

---

## 2. Memory Management

### Source paths

- `os/StarryOS/kernel/src/mm/mod.rs` -- Kernel MM entry, `AddrSpace` re-exports
- `os/StarryOS/kernel/src/mm/aspace/mod.rs` -- `AddrSpace` struct (VirtAddrRange, MemorySet, PageTable)
- `os/StarryOS/kernel/src/mm/aspace/backend/mod.rs` -- Backend dispatch
- `os/StarryOS/kernel/src/mm/aspace/backend/cow.rs` -- Copy-on-write backend
- `os/StarryOS/kernel/src/mm/aspace/backend/file.rs` -- File-backed mapping backend
- `os/StarryOS/kernel/src/mm/aspace/backend/linear.rs` -- Linear (identity) mapping backend
- `os/StarryOS/kernel/src/mm/aspace/backend/shared.rs` -- Shared memory backend
- `os/StarryOS/kernel/src/mm/loader.rs` -- ELF loader, segment mapping
- `os/StarryOS/kernel/src/mm/access.rs` -- User-space memory access helpers
- `os/StarryOS/kernel/src/mm/io.rs` -- I/O memory mapping
- `components/starry-vm/src/lib.rs` -- VM allocation/deallocation, thin page wrappers
- `components/starry-vm/src/alloc.rs` -- Physical page allocator for VM
- `components/starry-vm/src/thin.rs` -- Thin page handle abstraction
- `os/arceos/modules/axmm/src/aspace.rs` -- Lower-level address space operations
- `os/arceos/modules/axmm/src/backend/` -- `alloc.rs`, `linear.rs` -- ArceOS-level backends
- `components/axmm_crates/memory_set/src/set.rs` -- `MemorySet` container (interval map of areas)
- `components/axmm_crates/memory_set/src/area.rs` -- `MemoryArea` definition
- `components/axmm_crates/memory_set/src/backend.rs` -- `MappingBackend` trait
- `components/axmm_crates/memory_addr/` -- `VirtAddr`, `PhysAddr`, alignment helpers
- `os/StarryOS/kernel/src/task/user.rs` (lines 32-41) -- Page fault dispatch entry

### What to look for

Audit COW handling in `backend/cow.rs`. On a write fault to a COW page, the
handler must: (1) allocate a new physical page, (2) copy the old page contents,
(3) update the page table entry to point to the new page with write permission,
(4) decrement the refcount on the old page, (5) flush the TLB for the faulting
address. A bug looks like: step 4 is missing, causing a physical page leak; or
step 5 is missing, causing the old read-only mapping to remain cached in the
TLB, so the next write still faults (infinite fault loop).

Audit `MemorySet` operations in `set.rs` for overlapping-region handling. When
`mmap` is called on an address that overlaps an existing mapping, the old
mapping must be split or removed. A bug looks like: the old mapping is removed
but its pages are not unmapped from the page table, leaving stale PTEs.

Audit the `AddrSpace` lock strategy. The `AddrSpace` uses `ax_sync::Mutex` for
its internal areas. Verify that the lock is held across the entire
map/unmap/fault sequence, so two threads cannot race on the same VMA. A bug
looks like: thread A reads the PTE, decides to COW-copy, releases the lock,
then thread B also COW-copies the same page -- both threads allocate a new page
but only one PTE update survives, leaking one page.

Audit the ELF loader in `loader.rs`. Check that BSS segments (memsz > filesz)
are zeroed. Check that segment permissions are applied correctly (read-only data
segments should not have write permission). A bug looks like: a writable data
segment mapped read-only, causing a fault when the program writes to initialized
globals.

Audit `munmap` for partial unmapping. When unmapping the middle of a VMA, the
implementation must split the VMA into two. Verify the split preserves the
backend and flags of the original. A bug looks like: the right-hand split
inherits no backend, so future faults on that region panic.

Audit process exit cleanup. When a process exits, its entire `AddrSpace` must be
dropped, which must unmap all pages and free all physical frames. Search for
`Drop` implementations on `AddrSpace` and backends. A bug looks like: no `Drop`
impl, so physical frames are leaked on every process exit.

### Property tests

- Fork a child, child writes to a COW page, parent reads the same page. Parent
  must see the original data. Run 1000 iterations.
- `mmap(64MB)` then `munmap(64MB)` in a loop, 500 iterations. RSS must return
  to baseline each time. Measure with `/proc/self/statm` if available, or track
  the frame allocator's free count.
- Fork 100 children that each allocate 1MB and exit. After `waitpid` on all
  children, the frame allocator's free count must equal the pre-fork value
  (within one page tolerance for rounding).
- `mmap` a file, read its contents, `munmap`. Verify contents match the file.

### Concurrency risks

- Two threads calling `mmap`/`munmap` on overlapping regions concurrently.
- COW fault racing with `fork` (parent is forking while a COW fault is being
  resolved in a sibling thread).
- `exec` replacing the address space while another thread faults on the old one.

### Stress-test configuration

- SMP=4, 8 threads per process, each doing random `mmap`/`munmap`/`mprotect` in
  a loop for 5 seconds. Memory: 256MB to catch OOM paths.
- Fork bomb: fork 50 children, each forks 5 grandchildren, all exit. Check for
  frame leaks.

---

## 3. Concurrency Primitives

### Source paths

- `components/kspin/src/base.rs` -- `BaseSpinLock`, `BaseSpinLockGuard`
- `components/kspin/src/lib.rs` -- `SpinNoPreempt`, `SpinNoIrq`, `SpinRaw` type aliases
- `components/kspin/src/lockdep.rs` -- Lock dependency tracking (when `lockdep` feature enabled)
- `os/arceos/modules/axsync/src/mutex.rs` -- Sleeping `Mutex` implementation
- `os/arceos/modules/axsync/src/lib.rs` -- Re-exports
- `os/StarryOS/kernel/src/task/futex.rs` -- Futex implementation (BTreeMap + HashMap of wait queues)
- `os/StarryOS/kernel/src/syscall/sync/` -- Syscall wrappers for futex operations

### What to look for

Audit `BaseSpinLock` for single-core correctness. When the `smp` feature is
disabled, the lock field is removed entirely and the lock always "succeeds."
Verify that the `BaseGuard` (e.g., `NoPreemptIrqSave`) is still acquired even
without the atomic. A bug looks like: on a single-core build, an interrupt fires
while the "lock" is held, the ISR also "acquires" the same lock (no atomic to
stop it), and corrupts the protected data.

Audit `SpinNoIrq` for interrupt safety. Confirm that `NoPreemptIrqSave::new()`
actually saves and disables interrupts *before* the spin loop begins, and that
`NoPreemptIrqSave::drop()` restores the previous IRQ state (not unconditionally
enables). A bug looks like: nested `SpinNoIrq` acquisitions where the inner
guard's drop re-enables IRQs prematurely while the outer guard still expects
them disabled.

Audit the `lockdep` feature. When enabled, lock acquisition records the source
location. Check that the dependency graph correctly detects AB-BA deadlocks. If
`lockdep` is not tested in CI, flag it -- untested deadlock detection is
worse than none.

Audit the sleeping `Mutex` in `axsync`. Verify that the wait queue correctly
transfers ownership: when the lock is released, exactly one waiter must be woken,
and that waiter must observe the lock as acquired before returning from `lock()`.
A bug looks like: two waiters are woken simultaneously, both think they hold the
lock, and data corruption follows.

Audit the futex implementation in `futex.rs`. The `WaitQueue` uses
`SpinNoIrq<VecDeque<(Waker, u32)>>`. Verify:
1. `FUTEX_WAIT` atomically checks the futex value *and* enqueues the waiter
   under the same lock. If the check and enqueue are not atomic, a
   `FUTEX_WAKE` can be lost between the check and the enqueue.
2. `FUTEX_WAKE` wakes exactly N waiters, not more. Check for off-by-one.
3. The futex hash map (`HashMap<VirtAddr, WaitQueue>`) grows correctly under
   contention and does not deadlock when rehashing (rehash calls allocator,
   allocator may also use futexes).
4. Private vs. shared futexes: verify that the lookup key correctly
   distinguishes between per-process (virtual address) and shared (physical
   address or file-backed) futexes.

### Property tests

- N threads increment a shared `AtomicU64` guarded by `SpinNoIrq`. Final value
  must equal N * iterations. Run with N=8, iterations=100000.
- Futex ping-pong: two threads alternate waking each other via
  `FUTEX_WAIT`/`FUTEX_WAKE`. After 10000 rounds, both threads must have
  completed exactly 10000 iterations (no lost wakeups).
- Sleeping Mutex: spawn 16 threads, each holding the mutex for 1ms, then
  releasing. Total wall time should be approximately 16 * 1ms, not much worse.
  If it takes 10x longer, suspect thundering-herd or lost-wakeup bugs.

### Concurrency risks

- `SpinNoIrq` held across a `yield` or `schedule` call. The lock disables
  preemption, so a yield while holding it could deadlock (the yielding task
  cannot be re-scheduled because preemption is disabled). Search for `yield`
  calls inside `SpinNoIrq` critical sections.
- Futex wait queue: `FUTEX_WAKE` races with `FUTEX_WAIT` timeout expiry. If
  the waker removes the waiter from the queue at the same moment the timer
  callback tries to wake it, a double-wake or use-after-free may occur.

### Stress-test configuration

- SMP=4, 32 threads, mixed SpinNoIrq/Mutex/futex workloads, 10-second run.
- Deadlock detection: enable `lockdep` feature, run the full test suite, check
  for dependency-cycle warnings.

---

## 4. Signal Handling

### Source paths

- `components/starry-signal/src/lib.rs` -- Signal subsystem entry
- `components/starry-signal/src/action.rs` -- `sigaction` storage and defaults
- `components/starry-signal/src/pending.rs` -- Pending signal queue
- `components/starry-signal/src/types.rs` -- Signal types, `sigset_t`
- `components/starry-signal/src/api/mod.rs` -- Public API (`send_signal`, etc.)
- `components/starry-signal/src/api/process.rs` -- Process-level signal API
- `components/starry-signal/src/api/thread.rs` -- Thread-level signal API
- `components/starry-signal/src/arch/riscv.rs` -- RISC-V signal trampoline/context
- `components/starry-signal/src/arch/aarch64.rs` -- AArch64 signal trampoline
- `components/starry-signal/src/arch/x86_64.rs` -- x86_64 signal trampoline
- `os/StarryOS/kernel/src/task/signal.rs` -- Kernel-side signal dispatch
- `os/StarryOS/kernel/src/file/signalfd.rs` -- signalfd implementation

### What to look for

Audit signal delivery during blocked syscalls. When a thread is sleeping in a
`read`, `write`, `nanosleep`, or `futex_wait`, and a signal arrives, the syscall
must return `-EINTR` (or `-ERESTARTSYS` for restartable syscalls). Trace the
path from signal posting through `pending.rs` to the wakeup of the blocked
thread. A bug looks like: the sleeping thread is woken but does not check for
pending signals before re-entering the wait loop, so it goes back to sleep and
the signal is never delivered.

Audit `sigprocmask` correctness. Verify that masked signals are deferred (added
to the pending set) and delivered exactly once when unmasked. A bug looks like:
a signal is delivered while masked, or a signal is lost when two signals of the
same number arrive while masked (standard signals are not queued, but the
pending bit must remain set if not yet delivered).

Audit the signal trampoline for each architecture. The trampoline must:
1. Save the interrupted context (registers) onto the user stack.
2. Set up the user stack frame with `siginfo_t` and `ucontext_t`.
3. Set the return address to a `sigreturn` trampoline.
4. Jump to the signal handler.
After the handler returns (via `sigreturn`), the original context must be
restored exactly. A bug looks like: a callee-saved register is not saved, so
the interrupted code observes corrupted state after returning from the handler.

Audit nested signal delivery. If signal B arrives while the handler for signal A
is executing, and signal B is not masked by signal A's `sa_mask`, signal B must
be delivered immediately (nested). Verify the kernel correctly pushes a second
frame onto the user stack. A bug looks like: the second signal's `ucontext`
overwrites the first signal's saved context, so `sigreturn` from signal B
restores garbage.

Audit `SIGCHLD` generation. When a child exits, the parent must receive
`SIGCHLD` (unless `SA_NOCLDWAIT` is set). Verify the signal is sent from the
child's exit path in the process lifecycle code. A bug looks like: `SIGCHLD` is
generated only for `exit()` but not for termination by signal (e.g., `SIGKILL`).

### Property tests

- Thread calls `nanosleep(1s)`. After 100ms, another thread sends `SIGUSR1`.
  The `nanosleep` must return with `EINTR` within 100ms + 2 scheduling periods.
- Mask `SIGUSR1`, send `SIGUSR1` 5 times, unmask. The handler must run exactly
  once (standard signals coalesce).
- Install a handler for `SIGALRM` that sets a flag. Call `alarm(1)`. After 1.5
  seconds, verify the flag is set.
- Fork a child that exits with code 42. Parent installs `SIGCHLD` handler that
  records the child's exit status. Verify status equals 42.

### Concurrency risks

- `kill()` from one thread while `sigaction()` modifies the handler from
  another. The signal disposition must be read atomically relative to delivery.
- Signal delivery racing with `execve`. During `execve`, all signal dispositions
  must be reset to default. If a signal is delivered between the disposition
  reset and the new binary's entry, it must use the default disposition.
- Two threads sending signals to the same target thread simultaneously. The
  pending set must not lose a signal.

### Stress-test configuration

- SMP=2, 4 threads: two senders sending `SIGUSR1`/`SIGUSR2` in a tight loop,
  two receivers counting deliveries. Run for 5 seconds. Total deliveries should
  be nonzero and handlers should not crash.
- Signal-during-syscall stress: one thread in a `read(pipe)` loop, another
  thread sending `SIGUSR1` every 1ms. Verify no hangs, no panics.

---

## 5. Process Lifecycle

### Source paths

- `components/starry-process/src/process.rs` -- `Process` struct, parent/child tree, zombie flag
- `components/starry-process/src/process_group.rs` -- Process groups
- `components/starry-process/src/session.rs` -- Sessions
- `components/starry-process/src/lib.rs` -- Re-exports, `INIT_PROC` static
- `os/StarryOS/kernel/src/task/mod.rs` -- Task-level abstractions
- `os/StarryOS/kernel/src/task/ops.rs` -- `do_fork`, `do_exec`, `do_exit`
- `os/StarryOS/kernel/src/task/resources.rs` -- FD table, resource limits
- `os/StarryOS/kernel/src/task/user.rs` -- User-mode entry/return
- `os/StarryOS/kernel/src/task/stat.rs` -- Process statistics
- `os/StarryOS/kernel/src/task/timer.rs` -- Per-process timers (itimer)

### What to look for

Audit zombie cleanup in `do_exit` + `waitpid`. When a process exits, it must
become a zombie (retain its PID and exit status) until the parent calls
`waitpid`. After `waitpid`, the zombie must be fully deallocated. A bug looks
like: the process's `Arc<Process>` is never dropped because a stale reference
in the children `StrongMap` keeps it alive.

Audit orphan reparenting. When a parent exits before its children, all children
must be reparented to init (PID 1). Verify this by checking the exit path in
`process.rs` -- the `children` lock must be acquired and each child's `parent`
field must be updated to `INIT_PROC`. A bug looks like: the reparenting
iterates the child list but does not add the children to init's child list, so
init's `waitpid(-1)` never reaps them.

Audit `do_exec` in a multi-threaded process. POSIX requires that `execve` kill
all other threads in the thread group. Verify that `tg.threads` is iterated and
all threads except the caller are terminated and joined. A bug looks like:
other threads are sent a termination signal but not actually waited on, so the
new binary starts with dangling thread references.

Audit FD inheritance across `fork` and `exec`. On `fork`, the child must
receive a copy of the parent's FD table (not a shared reference). On `exec`,
all FDs marked `O_CLOEXEC` must be closed. A bug looks like: FDs are shared
(not copied) after `fork`, so the child closing an FD also closes it in the
parent.

Audit `exit_group`. It must terminate all threads in the thread group with the
same exit code and must not return to any of them. Verify that the `group_exited`
flag in `ThreadGroup` is set atomically and that no thread can proceed past a
check of this flag after it is set.

### Property tests

- Fork 50 children, each exits with a unique code. Parent calls `waitpid(-1)`
  in a loop. Verify: (a) exactly 50 `waitpid` calls succeed, (b) each returns a
  unique PID, (c) each exit code matches, (d) the 51st `waitpid` returns
  `ECHILD`.
- Fork a child, the child forks a grandchild, the child exits. The grandchild
  calls `getppid()`. Result must be 1 (reparented to init).
- Fork a child, open a pipe in the parent, verify the child inherited both FDs.
  Child closes its read end. Parent writes to the pipe. Must succeed (no
  `EPIPE`). Then child exits. Parent writes again. If no other readers remain,
  must get `EPIPE`.

### Concurrency risks

- `fork` racing with `exit_group`. One thread calls `fork` while another calls
  `exit(0)`. The fork must either fully succeed or not happen at all.
- `exec` racing with `fork` from another thread. The exec should kill the
  forking thread before the fork completes, or the fork should complete and the
  child gets the old binary.
- Concurrent `waitpid` from multiple threads in the parent. Only one should
  reap each child.

### Stress-test configuration

- SMP=4, fork-exit storm: 100 iterations of fork-and-immediately-exit, checking
  for zombie accumulation after each batch.
- Multi-threaded exec: 4 threads, one calls `exec`, others are in a busy loop.
  Verify no thread survives the exec.

---

## 6. File System

### Source paths

- `os/arceos/modules/axfs/src/root.rs` -- Root filesystem mount logic
- `os/arceos/modules/axfs/src/mounts.rs` -- Mount table
- `os/arceos/modules/axfs/src/fops.rs` -- Low-level file operations
- `os/arceos/modules/axfs/src/api/file.rs` -- `File` high-level API
- `os/arceos/modules/axfs/src/api/dir.rs` -- `Directory` API
- `os/arceos/modules/axfs/src/fs/ext4fs.rs` -- Ext4 filesystem integration
- `os/arceos/modules/axfs/src/fs/fatfs.rs` -- FAT filesystem integration
- `os/arceos/modules/axfs/src/dev.rs` -- Block device abstraction
- `os/arceos/modules/axfs/src/partition.rs` -- Partition table parsing
- `components/rsext4/src/` -- Full ext4 implementation (superblock, blockgroup, extents tree, journaling)
- `components/rsext4/src/jbd2/` -- Journaling (JBD2) implementation
- `components/rsext4/src/extents_tree/` -- Extent tree management
- `components/rsext4/src/dir/` -- Directory entry handling
- `components/rsext4/src/file/` -- File read/write paths
- `os/StarryOS/kernel/src/file/fs.rs` -- Kernel file abstraction
- `os/StarryOS/kernel/src/file/pipe.rs` -- Pipe implementation
- `os/StarryOS/kernel/src/file/epoll.rs` -- Epoll kernel-side
- `os/StarryOS/kernel/src/file/event.rs` -- eventfd
- `os/StarryOS/kernel/src/file/pidfd.rs` -- pidfd

### What to look for

Audit write-then-read consistency in `rsext4`. After a `write` followed by
`fsync`, a subsequent `read` of the same offset must return the written data.
Trace the write path through extents allocation, block cache updates, and
journal commits. A bug looks like: the write updates the block cache but `fsync`
does not flush the cache to the block device, so a power failure loses data.

Audit the extent tree in `extents_tree/`. When a file grows beyond its initial
extents, the tree must be split correctly. A bug looks like: a tree split
produces overlapping extents, causing reads to return data from the wrong block.

Audit directory operations for atomicity. `rename` must be atomic from the
perspective of `readdir` -- a file must appear in exactly one directory at all
times. Verify that `rename` within the same directory updates the directory
entry in place, and cross-directory `rename` adds to the target before removing
from the source.

Audit FD reference counting. When a file descriptor is `dup`'d, the underlying
file object's refcount must increment. When all FDs are closed, the refcount
must reach zero and the file object must be dropped. A bug looks like:
`dup2(fd, fd)` decrements and then increments the refcount (net zero), but if
the decrement triggers cleanup prematurely, the file is closed while still
referenced.

Audit pipe implementation in `pipe.rs`. Verify that: (a) a write to a pipe with
no readers returns `EPIPE` and sends `SIGPIPE`, (b) a read from an empty pipe
with writers blocks, (c) a read from an empty pipe with no writers returns 0
(EOF). A bug looks like: the pipe buffer is bounded but writes larger than the
buffer are not split into chunks, causing the write to block forever.

### Property tests

- Write 1MB of known pattern to a file, `fsync`, read back, compare
  byte-for-byte. Run 100 iterations with different patterns.
- Two processes append to the same file with `O_APPEND`. Each writes 1000
  records of 100 bytes. Total file size must equal 200000 bytes (no overwrites).
- Open a file, `dup` it 100 times, close all 101 FDs. The file must be fully
  closed (attempt to read via the path after `unlink` should fail).
- `mkdir`/`rmdir` in a loop, 1000 iterations. Directory must not exist after
  `rmdir`. `readdir` on the parent must not list it.

### Concurrency risks

- Two threads calling `write` on the same file descriptor concurrently (with
  and without `O_APPEND`). Without `O_APPEND`, data may interleave; with
  `O_APPEND`, each write must be atomic up to `PIPE_BUF` bytes.
- `unlink` while another thread has the file open. The file must remain
  accessible via the open FD until the last FD is closed.
- `rename` racing with `open` of the source or target path.

### Stress-test configuration

- SMP=4, 8 threads doing random create/write/read/unlink on 100 files, 10
  seconds. Memory: 512MB to allow block cache growth.
- Journal stress: write 10000 small files, fsync each, then delete all. Check
  for extent tree corruption.

---

## 7. Networking

### Source paths

- `os/arceos/modules/axnet/src/smoltcp_impl/mod.rs` -- Network stack initialization
- `os/arceos/modules/axnet/src/smoltcp_impl/tcp.rs` -- TCP socket implementation
- `os/arceos/modules/axnet/src/smoltcp_impl/udp.rs` -- UDP socket implementation
- `os/arceos/modules/axnet/src/smoltcp_impl/dns.rs` -- DNS resolver
- `os/arceos/modules/axnet/src/smoltcp_impl/listen_table.rs` -- TCP listen/accept table
- `os/arceos/modules/axnet/src/smoltcp_impl/addr.rs` -- Address conversion
- `components/starry-smoltcp/src/` -- Forked smoltcp TCP/IP stack
- `components/starry-smoltcp/src/socket/` -- Socket state machines
- `components/starry-smoltcp/src/iface/` -- Network interface layer
- `components/starry-smoltcp/src/wire/` -- Packet parsing/serialization
- `components/axpoll/src/lib.rs` -- I/O polling (epoll, poll, select)
- `os/StarryOS/kernel/src/file/net.rs` -- Kernel-level socket file abstraction
- `os/StarryOS/kernel/src/file/epoll.rs` -- Epoll implementation

### What to look for

Audit TCP connection lifecycle in `tcp.rs`. Trace: `socket()` -> `bind()` ->
`listen()` -> `accept()` -> `send()`/`recv()` -> `close()`. At each state
transition, verify the smoltcp socket state matches expectations. A bug looks
like: `close()` sends FIN but does not enter `TIME_WAIT`, so a delayed packet
from the peer causes a RST.

Audit `listen_table.rs`. The listen table maps (addr, port) to a queue of
incoming connections. Verify that: (a) the backlog size is respected, (b)
connections beyond the backlog are RST'd, (c) `accept()` removes from the queue
atomically. A bug looks like: two threads calling `accept()` on the same
listening socket both receive the same connection.

Audit socket cleanup on process exit. When a process exits without closing its
sockets, the kernel must close them (sending FIN for TCP). Search the process
exit path for FD table cleanup and verify sockets are included. A bug looks
like: TCP sockets are not closed on process exit, leaving the peer hanging.

Audit epoll in `axpoll/src/lib.rs` and `file/epoll.rs`. For edge-triggered
mode (`EPOLLET`), an event must be reported only once after a state transition.
A bug looks like: edge-triggered epoll reports a socket as readable on every
`epoll_wait` call (level-triggered behavior), causing busy loops in
applications that expect edge semantics.

Audit `SO_REUSEADDR`. Verify that binding to an address in `TIME_WAIT` succeeds
when `SO_REUSEADDR` is set. A bug looks like: `bind()` returns `EADDRINUSE`
even with `SO_REUSEADDR`, preventing server restarts.

### Property tests

- Server accepts 100 sequential connections, each sends 1KB, server echoes back.
  All 100 connections must complete without error. Check the server's open FD
  count before and after -- must be equal (no FD leak).
- Client connects, sends 1MB, server receives all bytes. Compare checksum.
- Client connects then immediately closes. Server's `accept()` must return
  eventually. The accepted socket's `read()` must return 0 (EOF).
- `shutdown(SHUT_WR)` on a connected socket. Peer's `read()` must return 0, but
  the shutting-down side must still be able to `read()` data sent by the peer.

### Concurrency risks

- The smoltcp stack uses a global interface lock. Verify this lock does not
  become a bottleneck or deadlock source when many sockets are active.
- `epoll_ctl(ADD)` racing with `close()` on the same FD. The FD must either be
  added successfully or `EBADF` returned, never a dangling reference.
- `send()` on a TCP socket from two threads simultaneously. Bytes must not
  interleave within the TCP stream (each `send` call's data must be contiguous).

### Stress-test configuration

- SMP=2, 20 concurrent TCP connections each sending/receiving 100KB, 5-second
  run. Memory: 256MB.
- Epoll stress: one epoll instance monitoring 50 sockets, events arriving every
  1ms. Verify no missed events over 5 seconds.

---

## 8. Pseudo-filesystem

### Source paths

- `os/StarryOS/kernel/src/pseudofs/mod.rs` -- Pseudofs registration
- `os/StarryOS/kernel/src/pseudofs/proc.rs` -- `/proc` filesystem
- `os/StarryOS/kernel/src/pseudofs/dir.rs` -- Directory abstraction
- `os/StarryOS/kernel/src/pseudofs/file.rs` -- File abstraction
- `os/StarryOS/kernel/src/pseudofs/fs.rs` -- Filesystem trait
- `os/StarryOS/kernel/src/pseudofs/device.rs` -- Device file abstraction
- `os/StarryOS/kernel/src/pseudofs/tmp.rs` -- `/tmp` tmpfs
- `os/StarryOS/kernel/src/pseudofs/dev/mod.rs` -- `/dev` device files
- `os/StarryOS/kernel/src/pseudofs/dev/tty/mod.rs` -- TTY subsystem
- `os/StarryOS/kernel/src/pseudofs/dev/tty/pty.rs` -- PTY master/slave
- `os/StarryOS/kernel/src/pseudofs/dev/tty/ptm.rs` -- PTY master device
- `os/StarryOS/kernel/src/pseudofs/dev/tty/pts.rs` -- PTY slave device
- `os/StarryOS/kernel/src/pseudofs/dev/tty/ntty.rs` -- N_TTY line discipline
- `os/StarryOS/kernel/src/pseudofs/dev/event.rs` -- Event device
- `os/StarryOS/kernel/src/pseudofs/dev/fb.rs` -- Framebuffer device
- `os/StarryOS/kernel/src/pseudofs/dev/log.rs` -- Log device
- `os/StarryOS/kernel/src/pseudofs/dev/loop.rs` -- Loop device
- `os/StarryOS/kernel/src/pseudofs/dev/memtrack.rs` -- Memory tracking device
- `os/StarryOS/kernel/src/pseudofs/dev/rtc.rs` -- Real-time clock

### What to look for

Audit `/proc/<pid>/` directories. Verify that a directory exists for each live
process and is removed (or returns `ENOENT`) after the process exits. Check
`/proc/self` -- it must resolve to the calling process's PID directory. A bug
looks like: `/proc/self/maps` always shows the maps of PID 1 regardless of the
caller, because the symlink target is hardcoded.

Audit `/proc/<pid>/status` and `/proc/<pid>/stat` for correctness. These files
are read by tools like `ps` and test harnesses. Verify that reported fields
(state, ppid, number of threads, memory usage) match actual kernel state. A bug
looks like: the thread count is off by one because it includes the main thread
twice.

Audit PTY handling in the `tty/` directory. The PTY master/slave pair must
relay data bidirectionally. Verify that: (a) writing to the master appears as
input on the slave, (b) writing to the slave appears as output on the master,
(c) closing the master sends EOF (zero-length read) to the slave, (d) the N_TTY
line discipline processes `\r` -> `\n` translation and echo correctly. A bug
looks like: `tcsetattr` on the slave does not affect the line discipline, so
raw mode never takes effect.

Audit `/tmp` lifecycle. Files created in `/tmp` must persist for the life of the
OS instance (no premature cleanup). Verify that `/tmp` is backed by an in-memory
filesystem, not the block device.

Audit `/dev/null`, `/dev/zero`, `/dev/urandom`. These are trivial but essential.
Verify: (a) reads from `/dev/null` return 0, writes succeed silently; (b) reads
from `/dev/zero` return zero-filled buffers of the requested size; (c) reads
from `/dev/urandom` return non-deterministic data (at minimum, two consecutive
reads should differ). A bug looks like: `/dev/urandom` returns all zeros because
the entropy source is not initialized.

### Property tests

- Read `/proc/self/maps`, verify it contains at least one entry with the current
  stack address.
- Open a PTY pair, write "hello\n" to the master, read from the slave. Data
  must match (modulo line discipline transformations).
- Read 4096 bytes from `/dev/urandom` twice. The two buffers must differ.
- Create a file in `/tmp`, write data, read back, verify, delete. Must succeed.

### Concurrency risks

- `/proc/<pid>/` for a process that is in the middle of exiting. The procfs
  read must not panic or return garbage if the process's task struct is being
  deallocated.
- Two threads opening the same PTY pair and writing simultaneously. Writes must
  not interleave at the byte level (each `write` call's bytes must be
  contiguous).

### Stress-test configuration

- SMP=2, 10 threads reading `/proc/self/stat` in a tight loop for 3 seconds.
  No panics, no garbled output.
- PTY stress: open 20 PTY pairs, each pair transferring 1MB of data
  bidirectionally. Verify all data received correctly.

---

## 9. Hardware Abstraction Layer and Runtime Initialization

### Source paths

- `os/arceos/modules/axhal/src/lib.rs` -- HAL entry
- `os/arceos/modules/axhal/src/irq.rs` -- Interrupt routing
- `os/arceos/modules/axhal/src/mem.rs` -- Physical memory layout, phys_to_virt
- `os/arceos/modules/axhal/src/paging.rs` -- Page table operations, TLB flush
- `os/arceos/modules/axhal/src/percpu.rs` -- Per-CPU variable infrastructure
- `os/arceos/modules/axhal/src/time.rs` -- Timer and clock source
- `os/arceos/modules/axhal/src/tls.rs` -- Thread-local storage
- `os/arceos/modules/axruntime/src/lib.rs` -- Runtime startup sequence
- `os/arceos/modules/axruntime/src/mp.rs` -- Secondary CPU boot
- `os/arceos/modules/axruntime/src/klib.rs` -- Kernel library initialization

### What to look for

Audit the boot sequence in `axruntime`. Verify that initialization happens in
the correct order: physical memory detected -> page allocator initialized ->
kernel page table set up -> per-CPU data initialized -> secondary CPUs started ->
interrupts enabled -> scheduler started. Misordering is a common source of
early-boot panics. A bug looks like: per-CPU data is accessed before the
secondary CPU has initialized its per-CPU area, causing a page fault.

Audit `phys_to_virt` and `virt_to_phys` in `axhal/src/mem.rs`. These must be
correct for the entire physical memory range. A bug looks like: the kernel
direct-map offset is wrong for high physical addresses, causing kernel panics
when accessing memory above 2GB.

Audit TLB flush in `paging.rs`. After updating a page table entry, the TLB must
be flushed. On SMP, a TLB shootdown (IPI to other CPUs) may be required. A bug
looks like: a page table entry is updated but only the local CPU's TLB is
flushed, so another CPU continues using the stale translation and reads/writes
the wrong physical page.

Audit `percpu.rs`. Verify that per-CPU variables are correctly offset for each
CPU and that access functions use the correct CPU ID. A bug looks like:
`this_cpu_id()` returns a stale value after a task migrates to a different CPU
(because the function cached the CPU ID).

### Concurrency risks

- Secondary CPU boot racing with the primary CPU's initialization. The secondary
  must wait for the primary to finish global initialization before proceeding.
- Per-CPU timer setup racing with the first timer interrupt. If the timer fires
  before the per-CPU timer list is initialized, the handler will access
  uninitialized memory.

### Stress-test configuration

- SMP=8 (maximum). Boot and verify all CPUs reach the idle loop.
- Memory: 2GB or more to exercise high-address `phys_to_virt` paths.
