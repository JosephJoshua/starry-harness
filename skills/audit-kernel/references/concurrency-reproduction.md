# Concurrency Bug Reproduction Techniques for StarryOS

Concurrency bugs — races, deadlocks, livelocks, atomicity violations — are
non-deterministic. They appear on one run and vanish on the next. The standard
term is *Heisenbug*: observing the system changes the timing and masks the
defect. Debugging printfs shift scheduling enough to hide the race.
Single-stepping in GDB serializes execution and eliminates the window entirely.

The core approach is **controlled amplification**: construct conditions that make
a target bug class overwhelmingly likely to manifest, then run enough
iterations to catch it with statistical confidence. Each technique below widens
a specific class of race window or forces a specific contention pattern.

---

## 1. SMP Sweeping

Run the same test binary across multiple SMP configurations to isolate
concurrency from logic defects.

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test> --runs 100 --smp 1,2,4
```

**Procedure:**

1. Execute the test at SMP=1 for 100 runs. Record the pass/fail count.
2. Execute at SMP=2 for 100 runs. Record again.
3. Execute at SMP=4 for 100 runs. Record again.

**Interpretation:**

- SMP=1 passes 100/100, SMP=4 fails 7/100 → confirmed concurrency bug. The
  SMP=1 result proves the test logic itself is correct; failures at higher core
  counts prove a timing-dependent defect.
- SMP=1 also fails → the bug is not concurrency-related (or there are
  multiple bugs). Fix the sequential failure first.
- Failure rate increases with core count → the race window is small and
  requires genuine parallelism to hit. Failure rate is constant across SMP=2
  and SMP=4 → the race only needs two threads and additional cores do not
  help.

SMP=1 is the **control group**. Never skip it.

---

## 2. Repeat Amplification

Run the same test many times — at minimum 100 iterations.

Even a 1% failure rate proves the bug exists. A single failure in 100 runs is
sufficient evidence to file a bug report.

**Statistical reasoning:**

- 0 failures in 100 runs → approximately 95% confidence the true failure rate
  is below 3%. This does not prove correctness; it only bounds the rate.
- 1 failure in 100 runs → the bug is real. Investigate.
- For higher confidence, increase to 500 or 1000 runs. At 0/1000, confidence
  that the true rate is below 0.3% is approximately 95%.

**Report format:**

> fails 3/100 at SMP=4, 0/100 at SMP=1

This format is compact and immediately communicates the concurrency
relationship.

**Deadlock detection:**

Set a timeout for each run. If the test does not complete within N seconds
(typically 30s for simple tests, 120s for filesystem-heavy tests), classify the
run as a deadlock. `stress-test.sh` handles this via QEMU timeout:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test> --runs 100 --smp 4 --timeout 60
```

Timeout-based deadlock detection is imperfect (a slow test is not deadlocked),
so set the timeout conservatively — at least 5x the normal completion time.

---

## 3. Yield Injection (Targeted Race Reproduction)

When source-level analysis identifies a suspected race between two code points
A and B, but normal stress testing fails to reproduce it:

1. Read the kernel source. Identify the exact instructions where the race
   window opens and closes.
2. At point A (the start of the window), temporarily insert
   `axtask::yield_now()`. This forces an immediate context switch.
3. The yield widens the race window from nanoseconds to a full scheduling
   quantum (typically milliseconds).
4. Run the test. If the bug manifests under yield injection, the race is
   confirmed.
5. **Remove the yield injection after confirming.** It is a diagnostic tool,
   not a fix. Leaving it in masks the race from future testing.
6. Document the race precisely:
   > Race between [code point A] and [code point B], confirmed by yield
   > injection at [file:line]

**When to use:** After source-level analysis suggests a race but
`stress-test.sh` at 100+ runs produces 0 failures. Yield injection can turn a
0.001% race into a 100% reproduction.

**Example: fork page table race.**

Suspect a race in `fork()` between parent and child accessing a shared page
table. The parent may free a page while the child still references it through
the not-yet-COW-protected mapping.

1. Locate the page table duplication in `fork()`.
2. Insert `axtask::yield_now()` after page table duplication but before COW
   flag setup completes.
3. Run a test where the child immediately reads a page the parent writes.
4. If the child faults on a page the parent freed → race confirmed. The
   window between page table copy and COW setup is unprotected.

---

## 4. Memory Pressure Injection

Reduce QEMU memory to force allocation failures, page reclamation, and OOM
code paths.

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test> --runs 50 --memory 128M
```

Or for extreme pressure:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh <test> --runs 50 --memory 64M --smp 4
```

**What this exposes:**

- Error handling paths in allocation that never execute with 1G RAM. Many
  kernel allocators have untested failure paths that corrupt state or panic
  with an unhelpful message.
- Memory leaks. With 1G of headroom, a 4KB-per-fork leak takes 262144
  iterations to exhaust memory. With 64M, it takes 16384. Faster feedback.
- Page fault storms under concurrency. With less physical memory, the kernel
  reclaims pages more aggressively, increasing the rate of page faults and
  exposing races in the page fault handler.

Combine with repeat amplification for maximum coverage of error paths.

---

## 5. Signal Bombing

Send signals to a process continuously during critical sections to expose
signal-safety violations.

**Design:**

1. Fork a child process that performs work (e.g., allocates memory, writes
   files, calls `mmap`).
2. The parent sends `SIGUSR1` to the child every 1 millisecond in a tight
   loop.
3. The child's signal handler increments a counter (nothing else — the
   handler must be trivially async-signal-safe).
4. The child completes its work and verifies correctness despite continuous
   signal delivery.

**What this exposes:**

- Signal handlers interrupting non-reentrant kernel code paths (e.g.,
  `malloc` internal state, file descriptor table modifications).
- Incorrect signal mask management — signals delivered during system calls
  that should have been blocked.
- `EINTR` handling failures — system calls that restart incorrectly or lose
  data on interruption.

**Test assertion:** The child completes all work correctly. The signal count is
greater than zero (proving signals were actually delivered).

---

## 6. Fork Bombing (Bounded)

Create and destroy many processes rapidly to stress process lifecycle
management.

**Procedure:**

1. Fork 100 children. Each child calls `_exit(0)` immediately.
2. The parent calls `waitpid()` for each child, collecting all exit statuses.
3. Repeat the entire cycle 100 times.
4. Verify: all 10,000 total children were created, exited, and reaped.

**What this detects:**

- Zombie accumulation: if `wait()` does not properly clean up, the process
  table fills.
- PID exhaustion or wrap-around bugs: 10,000 PID allocations and
  deallocations will exercise PID recycling.
- Process table corruption: concurrent insertions and deletions in the task
  list.
- Memory leaks in task cleanup: kernel stack, page tables, file descriptor
  tables not freed on exit.

**Critical:** Keep the fork count bounded. The test must use known counts, not
unbounded recursion. Each cycle waits for all children before starting the
next.

---

## 7. File Descriptor Stress

Open many file descriptors, then close them all. Fork with many open FDs.
Test `CLOEXEC` behavior.

**Procedure:**

1. Open 1000+ file descriptors (e.g., via `open("/dev/null", O_RDONLY)`).
2. Verify all opens succeeded.
3. Fork. In the child, verify the FD count matches the parent's.
4. Close all FDs in both parent and child.
5. After cleanup, verify that only stdin (0), stdout (1), and stderr (2)
   remain open. On Linux, inspect `/proc/self/fd`. On StarryOS, iterate
   `fcntl(fd, F_GETFD)` from 0 to 1023.

**CLOEXEC variant:**

1. Open 1000 FDs with `O_CLOEXEC`.
2. Fork and `execve` a helper binary.
3. In the helper, verify that none of the 1000 FDs are open.

**What this detects:**

- FD table corruption under concurrent open/close.
- FD leaks (descriptors not freed on close).
- Incorrect `CLOEXEC` handling (FDs surviving across exec).
- Off-by-one errors in FD table expansion.

---

## 8. Lock Contention Amplification

Create N threads that all contend on a single lock in a tight loop.

**Procedure:**

1. Allocate a shared counter initialized to 0.
2. Spawn N threads (vary N from 2 to the number of CPUs).
3. Each thread acquires the lock, increments the counter, releases the lock,
   100,000 times.
4. Join all threads.
5. Verify the counter equals N * 100,000.

**What this detects:**

- Broken mutual exclusion: if the final count is wrong, the lock does not
  provide exclusion.
- Starvation: if any thread stops making progress, the test times out. Add
  per-thread counters and verify each thread completed its share.
- Deadlock: the test hangs. The timeout mechanism in `stress-test.sh`
  catches this.
- Livelock: threads run but make no progress. The timeout catches this too,
  but distinguish from deadlock by checking CPU usage (livelock = 100% CPU,
  deadlock = 0% CPU).

Vary N across runs. Two-thread contention and N-thread contention trigger
different bugs.

---

## 9. Differential Configuration Testing

Beyond SMP count, vary other QEMU and kernel parameters to isolate
configuration-specific bugs.

**Parameters to sweep:**

| Parameter         | Values                        | Detects                                |
|-------------------|-------------------------------|----------------------------------------|
| Architecture      | riscv64, aarch64              | Arch-specific memory model bugs        |
| SMP count         | 1, 2, 4, 8                   | Concurrency bugs                       |
| Memory            | 64M, 128M, 512M, 1G          | Memory pressure bugs                   |
| Scheduler policy  | sched-rr, sched-fifo          | Scheduler-dependent races              |
| Kernel features   | With/without specific modules | Feature interaction bugs               |

**Interpretation:**

- Test passes with `sched-rr` but fails with `sched-fifo` → bug in the
  FIFO scheduling code path, or a latent race that RR's timeslicing hides.
- Test passes on riscv64 but fails on aarch64 → check memory ordering.
  RISC-V has a relaxed memory model (RVWMO); ARM has its own relaxed model.
  A missing fence may manifest on one but not the other.

Report the exact configuration triple: `(arch, smp, memory, features)`.

---

## 10. Combining Techniques

The most powerful reproduction strategy is combining multiple amplifiers.
Recommended combinations:

| Combination                                | Target bug class                    |
|--------------------------------------------|-------------------------------------|
| SMP=4 + memory=128M + 100 runs            | Memory allocator races              |
| Signal bombing + SMP=4 + 100 runs         | Signal delivery races               |
| Fork stress + SMP=4 + 100 runs            | Process lifecycle concurrency bugs  |
| FD stress + fork stress + SMP=4 + 100 runs| FD table corruption under fork      |
| Lock contention + SMP=4 + memory=64M      | Lock implementation bugs under OOM  |

```bash
# Memory allocator race hunting
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh test_mmap_concurrent \
  --runs 100 --smp 4 --memory 128M

# Signal delivery race hunting
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh test_signal_bomb \
  --runs 100 --smp 4

# Process lifecycle race hunting
bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh test_fork_bomb \
  --runs 100 --smp 4
```

Document the combined configuration in every bug report so that the bug is
reproducible by others.

---

## Writing Concurrency Tests in C

All templates below use the `starry_test.h` harness (`EXPECT` macros) and
compile with:

```bash
riscv64-linux-musl-gcc -static -pthread -o test test.c
```

### Multi-threaded Shared Counter (Race Detector)

Intentionally racy version (no lock) alongside a correct version (with lock).
If both produce the same result, the kernel's thread scheduling is not
providing true parallelism. If the racy version produces a wrong result, the
test confirms that the kernel schedules threads concurrently.

```c
#include "starry_test.h"
#include <pthread.h>
#include <stdatomic.h>

#define NUM_THREADS  4
#define INCREMENTS   100000

static volatile long racy_counter = 0;
static long locked_counter = 0;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static void *racy_worker(void *arg) {
    (void)arg;
    for (int i = 0; i < INCREMENTS; i++) {
        racy_counter++;
    }
    return NULL;
}

static void *locked_worker(void *arg) {
    (void)arg;
    for (int i = 0; i < INCREMENTS; i++) {
        pthread_mutex_lock(&lock);
        locked_counter++;
        pthread_mutex_unlock(&lock);
    }
    return NULL;
}

int main(void) {
    pthread_t threads[NUM_THREADS];

    /* Racy counter — expect data race. */
    racy_counter = 0;
    for (int i = 0; i < NUM_THREADS; i++)
        pthread_create(&threads[i], NULL, racy_worker, NULL);
    for (int i = 0; i < NUM_THREADS; i++)
        pthread_join(threads[i], NULL);

    long racy_result = racy_counter;
    long expected = (long)NUM_THREADS * INCREMENTS;

    /* Locked counter — must be exact. */
    locked_counter = 0;
    for (int i = 0; i < NUM_THREADS; i++)
        pthread_create(&threads[i], NULL, locked_worker, NULL);
    for (int i = 0; i < NUM_THREADS; i++)
        pthread_join(threads[i], NULL);

    EXPECT_EQ(locked_counter, expected);

    /*
     * The racy counter may or may not equal expected. If it does equal
     * expected on every run, threads are not truly concurrent — investigate
     * SMP configuration.
     */
    if (racy_result == expected) {
        TEST_LOG("WARNING: racy counter matched expected — "
                 "threads may not be running concurrently");
    } else {
        TEST_LOG("racy counter = %ld, expected = %ld — "
                 "race detected as expected", racy_result, expected);
    }

    TEST_PASS();
    return 0;
}
```

### Fork + Pipe Ping-Pong

Test process concurrency and IPC correctness under repeated context switches.

```c
#include "starry_test.h"
#include <unistd.h>
#include <sys/wait.h>

#define PING_PONG_COUNT 10000

int main(void) {
    int parent_to_child[2];
    int child_to_parent[2];

    EXPECT_EQ(pipe(parent_to_child), 0);
    EXPECT_EQ(pipe(child_to_parent), 0);

    pid_t pid = fork();
    EXPECT_GE(pid, 0);

    if (pid == 0) {
        /* Child: read from parent, echo back incremented. */
        close(parent_to_child[1]);
        close(child_to_parent[0]);

        for (int i = 0; i < PING_PONG_COUNT; i++) {
            int val;
            ssize_t n = read(parent_to_child[0], &val, sizeof(val));
            if (n != sizeof(val)) _exit(1);
            val++;
            n = write(child_to_parent[1], &val, sizeof(val));
            if (n != sizeof(val)) _exit(2);
        }

        close(parent_to_child[0]);
        close(child_to_parent[1]);
        _exit(0);
    }

    /* Parent: send value, read back incremented value. */
    close(parent_to_child[0]);
    close(child_to_parent[1]);

    for (int i = 0; i < PING_PONG_COUNT; i++) {
        int val = i;
        ssize_t n = write(parent_to_child[1], &val, sizeof(val));
        EXPECT_EQ(n, (ssize_t)sizeof(val));

        int result;
        n = read(child_to_parent[0], &result, sizeof(result));
        EXPECT_EQ(n, (ssize_t)sizeof(result));
        EXPECT_EQ(result, i + 1);
    }

    close(parent_to_child[1]);
    close(child_to_parent[0]);

    int status;
    waitpid(pid, &status, 0);
    EXPECT_EQ(WIFEXITED(status), 1);
    EXPECT_EQ(WEXITSTATUS(status), 0);

    TEST_PASS();
    return 0;
}
```

### Signal Handler with Concurrent Modification Detection

Detect signal-safety violations by bombing a process with signals during work.

```c
#include "starry_test.h"
#include <signal.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <string.h>

static volatile sig_atomic_t signal_count = 0;

static void handler(int sig) {
    (void)sig;
    signal_count++;
}

#define WORK_ITERATIONS 100000

int main(void) {
    pid_t pid = fork();
    EXPECT_GE(pid, 0);

    if (pid == 0) {
        /* Child: install signal handler, do work under bombardment. */
        struct sigaction sa;
        memset(&sa, 0, sizeof(sa));
        sa.sa_handler = handler;
        sa.sa_flags = SA_RESTART;
        sigaction(SIGUSR1, &sa, NULL);

        /*
         * Use setitimer for self-bombardment as a fallback.
         * The parent also sends signals externally.
         */
        struct itimerval itv;
        itv.it_interval.tv_sec = 0;
        itv.it_interval.tv_usec = 500;  /* every 500us */
        itv.it_value = itv.it_interval;
        setitimer(ITIMER_REAL, &itv, NULL);

        struct sigaction sa_alrm;
        memset(&sa_alrm, 0, sizeof(sa_alrm));
        sa_alrm.sa_handler = handler;
        sa_alrm.sa_flags = SA_RESTART;
        sigaction(SIGALRM, &sa_alrm, NULL);

        /* Work: allocate, write, verify patterns. */
        volatile int accumulator = 0;
        for (int i = 0; i < WORK_ITERATIONS; i++) {
            accumulator += i;
        }

        /* Verify work completed correctly despite signals. */
        long expected = (long)(WORK_ITERATIONS - 1) * WORK_ITERATIONS / 2;
        if (accumulator != (int)expected) {
            _exit(1);
        }

        /* Verify signals were actually delivered. */
        if (signal_count == 0) {
            _exit(2);  /* No signals delivered — test is vacuous. */
        }

        _exit(0);
    }

    /* Parent: bombard child with SIGUSR1. */
    for (int i = 0; i < 50000; i++) {
        kill(pid, SIGUSR1);
        /* No sleep — send as fast as possible. */
    }

    int status;
    waitpid(pid, &status, 0);
    EXPECT_EQ(WIFEXITED(status), 1);
    EXPECT_EQ(WEXITSTATUS(status), 0);

    TEST_PASS();
    return 0;
}
```

### mmap Shared Memory Between Processes

Test inter-process concurrency through shared memory with atomic operations.

```c
#include "starry_test.h"
#include <sys/mman.h>
#include <unistd.h>
#include <sys/wait.h>
#include <stdatomic.h>

#define NUM_CHILDREN  4
#define INCREMENTS    100000

int main(void) {
    /*
     * Allocate shared memory visible to parent and all children.
     * MAP_SHARED + MAP_ANONYMOUS = shared anonymous mapping.
     */
    _Atomic long *counter = mmap(
        NULL, sizeof(_Atomic long),
        PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS,
        -1, 0
    );
    EXPECT_NE(counter, MAP_FAILED);

    atomic_store(counter, 0);

    pid_t children[NUM_CHILDREN];
    for (int i = 0; i < NUM_CHILDREN; i++) {
        pid_t pid = fork();
        EXPECT_GE(pid, 0);

        if (pid == 0) {
            /* Child: atomically increment shared counter. */
            for (int j = 0; j < INCREMENTS; j++) {
                atomic_fetch_add(counter, 1);
            }
            _exit(0);
        }

        children[i] = pid;
    }

    /* Parent: wait for all children. */
    for (int i = 0; i < NUM_CHILDREN; i++) {
        int status;
        waitpid(children[i], &status, 0);
        EXPECT_EQ(WIFEXITED(status), 1);
        EXPECT_EQ(WEXITSTATUS(status), 0);
    }

    long final_value = atomic_load(counter);
    long expected = (long)NUM_CHILDREN * INCREMENTS;
    EXPECT_EQ(final_value, expected);

    munmap(counter, sizeof(_Atomic long));

    TEST_PASS();
    return 0;
}
```

### Futex-Based Synchronization Barrier

Implement a manual barrier using futex syscalls to test the kernel's futex
implementation under contention.

```c
#include "starry_test.h"
#include <pthread.h>
#include <stdatomic.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <limits.h>

#define NUM_THREADS 4
#define ROUNDS      1000

static _Atomic int barrier_counter;
static _Atomic int barrier_generation;

static int futex_wait(int *uaddr, int expected) {
    return (int)syscall(SYS_futex, uaddr, FUTEX_WAIT, expected, NULL, NULL, 0);
}

static int futex_wake(int *uaddr, int count) {
    return (int)syscall(SYS_futex, uaddr, FUTEX_WAKE, count, NULL, NULL, 0);
}

/*
 * Manual barrier: all threads must reach this point before any proceed.
 * Uses futex for blocking/waking rather than pthread_barrier.
 */
static void barrier_wait(void) {
    int gen = atomic_load(&barrier_generation);
    int arrived = atomic_fetch_add(&barrier_counter, 1) + 1;

    if (arrived == NUM_THREADS) {
        /* Last thread to arrive: reset counter, advance generation, wake all. */
        atomic_store(&barrier_counter, 0);
        atomic_fetch_add(&barrier_generation, 1);
        futex_wake((int *)&barrier_generation, INT_MAX);
    } else {
        /* Not last: wait until generation advances. */
        while (atomic_load(&barrier_generation) == gen) {
            futex_wait((int *)&barrier_generation, gen);
        }
    }
}

static _Atomic long shared_sum;

static void *worker(void *arg) {
    int id = *(int *)arg;

    for (int round = 0; round < ROUNDS; round++) {
        /* Phase 1: all threads write. */
        atomic_fetch_add(&shared_sum, id + 1);

        barrier_wait();

        /* Phase 2: thread 0 verifies sum, others wait. */
        if (id == 0) {
            long expected = 0;
            for (int i = 0; i < NUM_THREADS; i++)
                expected += (i + 1);
            expected *= (round + 1);

            /*
             * Cumulative sum: after `round+1` rounds, each thread has added
             * its (id+1) value (round+1) times.
             */
            long actual = atomic_load(&shared_sum);
            if (actual != expected) {
                /* Cannot use EXPECT from non-main thread; signal failure. */
                return (void *)1;
            }
        }

        barrier_wait();
    }

    return NULL;
}

int main(void) {
    atomic_store(&barrier_counter, 0);
    atomic_store(&barrier_generation, 0);
    atomic_store(&shared_sum, 0);

    pthread_t threads[NUM_THREADS];
    int ids[NUM_THREADS];

    for (int i = 0; i < NUM_THREADS; i++) {
        ids[i] = i;
        pthread_create(&threads[i], NULL, worker, &ids[i]);
    }

    for (int i = 0; i < NUM_THREADS; i++) {
        void *retval;
        pthread_join(threads[i], &retval);
        EXPECT_EQ((long)retval, 0L);
    }

    long expected_total = 0;
    for (int i = 0; i < NUM_THREADS; i++)
        expected_total += (i + 1);
    expected_total *= ROUNDS;

    EXPECT_EQ(atomic_load(&shared_sum), expected_total);

    TEST_PASS();
    return 0;
}
```

---

## Interpreting Results

### Reading stress-test.sh JSON Output

`stress-test.sh` produces JSON output summarizing the run:

```json
{
  "test": "test_mutex_contention",
  "total_runs": 100,
  "results": {
    "smp_1": { "pass": 100, "fail": 0, "timeout": 0 },
    "smp_2": { "pass": 98,  "fail": 1, "timeout": 1 },
    "smp_4": { "pass": 93,  "fail": 4, "timeout": 3 }
  }
}
```

Read the `results` object by SMP configuration. Each entry contains:

- `pass`: runs that completed with exit code 0.
- `fail`: runs that completed with a non-zero exit code.
- `timeout`: runs that did not complete within the time limit.

Compute the failure rate per SMP level. Compare SMP=1 (control) against SMP>1
(treatment). A statistically significant difference confirms concurrency as the
root cause.

### What Failure Rates Mean

| Observation          | Interpretation                                             |
|----------------------|------------------------------------------------------------|
| 0/100 fail           | ~95% confidence true failure rate < 3%. Not a proof of correctness. |
| 1/100 fail           | The bug is real. Investigate immediately.                  |
| 5/100 fail           | Easily reproducible race. Good candidate for yield injection to pinpoint. |
| 50/100 fail          | Large race window. Likely a missing lock or wrong lock scope. |
| 100/100 fail at SMP>1| Deterministic under concurrency. A missing synchronization primitive. |

### Identifying Bug Category from Failure Pattern

**Timeout (test does not complete):**
→ Deadlock. Two or more threads/processes are blocked waiting for each other.
Examine lock acquisition ordering. Check for circular wait conditions. Look
for missing unlock on error paths.

**Crash (segfault, illegal instruction, kernel panic):**
→ Race leading to undefined behavior. A pointer was used after free, an index
went out of bounds, or memory was corrupted by concurrent unsynchronized
writes. The race corrupts state; the crash is a downstream symptom.

**Wrong value (test asserts correct result but gets wrong one):**
→ Atomicity violation. An operation that should be atomic was interrupted.
Classic example: read-modify-write on a shared variable without a lock. The
test's `EXPECT_EQ` catches the incorrect result.

**Intermittent wrong value with consistent pass at SMP=1:**
→ Memory ordering bug. Missing memory barriers or fence instructions. The
hardware reorders stores and a concurrent reader sees stale data.

### When to Escalate from Stress Testing to Yield Injection

Escalate when:

1. Source-level analysis of the code identifies a plausible race, but
   `stress-test.sh` at 100+ runs produces 0 failures across all SMP
   configurations.
2. The suspected race window is extremely narrow (a few instructions).
3. There is a bug report from users describing non-deterministic failure that
   local stress testing cannot reproduce.

Do not escalate prematurely. Run at least 500 iterations at SMP=4 before
concluding that stress testing is insufficient. Many races reproduce at
0.1%-1% rates, which requires 500-1000 runs to observe.

### Writing a Reproducible Bug Report for a Non-Deterministic Bug

Every concurrency bug report must include:

1. **Failure rate and configuration:**
   > fails 7/100 at SMP=4, 0/100 at SMP=1, 0/100 at SMP=2

2. **Exact test binary and source:**
   > test: `test_fork_mmap_race.c`, compiled with
   > `riscv64-linux-musl-gcc -static -pthread -o test test_fork_mmap_race.c`

3. **Exact kernel commit:**
   > kernel: rcore-os/arceos@abc1234

4. **QEMU configuration:**
   > `qemu-system-riscv64 -machine virt -smp 4 -m 128M`

5. **Failure mode:**
   > Timeout after 60s (deadlock) in 3/7 failures.
   > Wrong result (expected 400000, got 399987) in 4/7 failures.

6. **Reproduction command:**
   ```bash
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/stress-test.sh test_fork_mmap_race \
     --runs 100 --smp 1,2,4 --memory 128M --timeout 60
   ```

7. **Analysis (if available):**
   > Suspected race between page table fork (mm/fork.c:142) and COW fault
   > handler (mm/fault.c:87). Confirmed by yield injection at mm/fork.c:145.

This format enables anyone to reproduce the failure on the same kernel commit
and provides enough context for targeted debugging.
