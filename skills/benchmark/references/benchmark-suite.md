# Benchmark Suite Catalog

Complete catalog of performance benchmarks for comparing StarryOS against Linux. Each entry includes a C source template, measurement methodology, interpretation guide, and expected Linux baseline ranges.

All benchmarks follow these conventions:
- Timing via `clock_gettime(CLOCK_MONOTONIC)` exclusively
- Machine-parseable output: `BENCH <category> <metric> <value> <unit>`
- Compiled with `-O2 -static` for consistent cross-platform behavior
- Warm-up phase before measurement to prime caches
- Enough iterations to reach at least 100ms total elapsed time

---

## Common Timing Infrastructure

Every benchmark includes this shared timing preamble.

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define WARMUP_ITERS 1000
#define BENCH_PRINT(cat, metric, value, unit) \
    printf("BENCH %s %s %ld %s\n", cat, metric, (long)(value), unit)
```

---

## 1. Syscall Latency

### 1a. getpid() Loop

**What it measures**: Raw syscall entry/exit overhead with the cheapest possible syscall. `getpid()` does no real work — it returns a cached value — so the measured time is almost entirely kernel entry, dispatch, and return.

**C source template** (`bench_syscall_getpid.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/types.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define ITERATIONS 1000000
#define WARMUP     10000

int main(void) {
    struct timespec start, end;
    volatile pid_t p;

    /* Warm up */
    for (int i = 0; i < WARMUP; i++)
        p = getpid();

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < ITERATIONS; i++)
        p = getpid();
    clock_gettime(CLOCK_MONOTONIC, &end);

    long total = elapsed_ns(&start, &end);
    long per_op = total / ITERATIONS;

    printf("BENCH syscall getpid_total_ns %ld ns\n", total);
    printf("BENCH syscall getpid_per_op_ns %ld ns/op\n", per_op);
    printf("BENCH syscall getpid_ops_per_sec %ld ops/s\n",
           ITERATIONS * 1000000000L / total);

    (void)p;
    return 0;
}
```

**How to interpret results**:
- `getpid_per_op_ns`: The core metric. Lower is better. Represents pure syscall overhead.
- On native Linux x86_64: typically 50-150 ns/op (vDSO may optimize this further).
- On QEMU riscv64 Linux: typically 200-800 ns/op depending on emulation overhead.
- StarryOS target: within 2x of QEMU Linux baseline.

**Expected Linux baseline ranges** (QEMU riscv64):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| getpid_per_op_ns | 150 | 400 | 1000 |

**Bottleneck indicators**: If StarryOS is >5x Linux, check:
- Syscall dispatch path for unnecessary indirection
- Whether `getpid()` is doing real work instead of returning a cached value
- Context save/restore overhead on kernel entry/exit

### 1b. clock_gettime() Loop

**What it measures**: Timer syscall overhead, important because benchmarks themselves depend on this call. Also exercises the kernel's timekeeping subsystem.

**C source template** (`bench_syscall_clockgettime.c`):

```c
#include <stdio.h>
#include <time.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define ITERATIONS 1000000
#define WARMUP     10000

int main(void) {
    struct timespec start, end, ts;

    for (int i = 0; i < WARMUP; i++)
        clock_gettime(CLOCK_MONOTONIC, &ts);

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < ITERATIONS; i++)
        clock_gettime(CLOCK_MONOTONIC, &ts);
    clock_gettime(CLOCK_MONOTONIC, &end);

    long total = elapsed_ns(&start, &end);
    long per_op = total / ITERATIONS;

    printf("BENCH syscall clockgettime_total_ns %ld ns\n", total);
    printf("BENCH syscall clockgettime_per_op_ns %ld ns/op\n", per_op);

    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| clockgettime_per_op_ns | 100 | 300 | 800 |

---

## 2. I/O Throughput

### 2a. Sequential Write

**What it measures**: Raw write bandwidth to a file. Tests the filesystem write path, page cache behavior, and block device interaction.

**C source template** (`bench_io_seqwrite.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define BLOCK_SIZE (1024 * 1024)   /* 1 MB */
#define TOTAL_SIZE (100 * BLOCK_SIZE) /* 100 MB */

int main(void) {
    char *buf = malloc(BLOCK_SIZE);
    if (!buf) { perror("malloc"); return 1; }
    memset(buf, 'A', BLOCK_SIZE);

    int fd = open("/tmp/bench_write", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 1; }

    /* Warm up: write 1 MB */
    write(fd, buf, BLOCK_SIZE);
    lseek(fd, 0, SEEK_SET);

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    long total_written = 0;
    for (long i = 0; i < TOTAL_SIZE / BLOCK_SIZE; i++) {
        ssize_t n = write(fd, buf, BLOCK_SIZE);
        if (n < 0) { perror("write"); break; }
        total_written += n;
    }
    fsync(fd);

    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long mbps = total_written * 1000 / (elapsed / 1000000);

    printf("BENCH io seqwrite_bytes %ld bytes\n", total_written);
    printf("BENCH io seqwrite_time_ms %ld ms\n", elapsed / 1000000);
    printf("BENCH io seqwrite_throughput_mbps %ld MB/s\n", mbps);

    close(fd);
    unlink("/tmp/bench_write");
    free(buf);
    return 0;
}
```

**How to interpret results**: `seqwrite_throughput_mbps` is the primary metric. The throughput depends heavily on whether the filesystem buffers writes (page cache) or flushes synchronously. The `fsync()` at the end forces a flush, measuring real write performance including sync. For QEMU with a RAM-backed disk, Linux typically achieves 200-800 MB/s.

**Expected Linux baseline ranges** (QEMU riscv64, virtio-blk):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| seqwrite_throughput_mbps | 50 | 300 | 1000 |

### 2b. Sequential Read

**What it measures**: Read bandwidth from a file. Tests the filesystem read path, page cache hit rates, and readahead efficiency.

**C source template** (`bench_io_seqread.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define BLOCK_SIZE (1024 * 1024)
#define TOTAL_SIZE (100 * BLOCK_SIZE)

int main(void) {
    char *buf = malloc(BLOCK_SIZE);
    if (!buf) { perror("malloc"); return 1; }

    /* Create test file */
    int fd = open("/tmp/bench_read", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open write"); return 1; }
    memset(buf, 'B', BLOCK_SIZE);
    for (long i = 0; i < TOTAL_SIZE / BLOCK_SIZE; i++)
        write(fd, buf, BLOCK_SIZE);
    close(fd);

    /* Read benchmark */
    fd = open("/tmp/bench_read", O_RDONLY);
    if (fd < 0) { perror("open read"); return 1; }

    struct timespec start, end;
    long total_read = 0;

    clock_gettime(CLOCK_MONOTONIC, &start);
    ssize_t n;
    while ((n = read(fd, buf, BLOCK_SIZE)) > 0)
        total_read += n;
    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long mbps = total_read * 1000 / (elapsed / 1000000);

    printf("BENCH io seqread_bytes %ld bytes\n", total_read);
    printf("BENCH io seqread_time_ms %ld ms\n", elapsed / 1000000);
    printf("BENCH io seqread_throughput_mbps %ld MB/s\n", mbps);

    close(fd);
    unlink("/tmp/bench_read");
    free(buf);
    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64, virtio-blk):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| seqread_throughput_mbps | 100 | 500 | 1500 |

### 2c. Random Read

**What it measures**: Random-access read latency. Tests page cache miss handling and seek efficiency. Use a file larger than available RAM to force cache misses, or a smaller file to measure in-cache random access.

**C source template** (`bench_io_randread.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define FILE_SIZE  (10 * 1024 * 1024)  /* 10 MB */
#define READ_SIZE  4096
#define NUM_READS  10000

int main(void) {
    char *buf = malloc(READ_SIZE);
    if (!buf) return 1;

    /* Create test file */
    int fd = open("/tmp/bench_rand", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    memset(buf, 'C', READ_SIZE);
    for (long i = 0; i < FILE_SIZE / READ_SIZE; i++)
        write(fd, buf, READ_SIZE);
    close(fd);

    fd = open("/tmp/bench_rand", O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }

    /* Generate random offsets */
    srand(42);  /* deterministic seed for reproducibility */
    long offsets[NUM_READS];
    for (int i = 0; i < NUM_READS; i++)
        offsets[i] = (rand() % (FILE_SIZE / READ_SIZE)) * READ_SIZE;

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    for (int i = 0; i < NUM_READS; i++) {
        lseek(fd, offsets[i], SEEK_SET);
        read(fd, buf, READ_SIZE);
    }

    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long per_read = elapsed / NUM_READS;

    printf("BENCH io randread_total_ns %ld ns\n", elapsed);
    printf("BENCH io randread_per_op_ns %ld ns/op\n", per_read);
    printf("BENCH io randread_iops %ld ops/s\n",
           (long)NUM_READS * 1000000000L / elapsed);

    close(fd);
    unlink("/tmp/bench_rand");
    free(buf);
    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64, in-cache):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| randread_per_op_ns | 500 | 2000 | 10000 |

---

## 3. Context Switch

### 3a. Pipe Ping-Pong

**What it measures**: Process context switch latency. Two processes exchange single-byte messages through a pipe. Each round-trip requires two context switches (parent to child and back). This is the classic `lmbench lat_ctx` methodology.

**C source template** (`bench_ctxsw_pipe.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define ITERATIONS 50000
#define WARMUP     1000

int main(void) {
    int p2c[2], c2p[2];  /* parent-to-child, child-to-parent */
    if (pipe(p2c) < 0 || pipe(c2p) < 0) { perror("pipe"); return 1; }

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }

    if (pid == 0) {
        /* Child: read from p2c, write to c2p */
        close(p2c[1]);
        close(c2p[0]);
        char byte;
        for (int i = 0; i < WARMUP + ITERATIONS; i++) {
            read(p2c[0], &byte, 1);
            write(c2p[1], &byte, 1);
        }
        close(p2c[0]);
        close(c2p[1]);
        _exit(0);
    }

    /* Parent: write to p2c, read from c2p */
    close(p2c[0]);
    close(c2p[1]);
    char byte = 'x';

    /* Warm up */
    for (int i = 0; i < WARMUP; i++) {
        write(p2c[1], &byte, 1);
        read(c2p[0], &byte, 1);
    }

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    for (int i = 0; i < ITERATIONS; i++) {
        write(p2c[1], &byte, 1);
        read(c2p[0], &byte, 1);
    }

    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    /* Each iteration is one round-trip = 2 context switches */
    long per_switch = elapsed / (ITERATIONS * 2);

    printf("BENCH ctxsw pipe_roundtrips %d trips\n", ITERATIONS);
    printf("BENCH ctxsw pipe_total_ns %ld ns\n", elapsed);
    printf("BENCH ctxsw pipe_per_switch_ns %ld ns/switch\n", per_switch);
    printf("BENCH ctxsw pipe_switches_per_sec %ld sw/s\n",
           (long)(ITERATIONS * 2) * 1000000000L / elapsed);

    close(p2c[1]);
    close(c2p[0]);
    waitpid(pid, NULL, 0);
    return 0;
}
```

**How to interpret results**: `pipe_per_switch_ns` is the primary metric. It includes pipe read/write overhead in addition to the actual context switch, but this is the standard methodology (lmbench). On native Linux: 1-5 microseconds per switch. On QEMU: 5-30 microseconds.

**Expected Linux baseline ranges** (QEMU riscv64):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| pipe_per_switch_ns | 2000 | 10000 | 50000 |

**Bottleneck indicators**: If StarryOS is >5x Linux, check:
- Scheduler `pick_next_task` efficiency
- Pipe buffer implementation (is it copying unnecessarily?)
- Register save/restore overhead on context switch
- Timer interrupt frequency affecting wake-up latency

### 3b. Yield Loop

**What it measures**: Voluntary context switch overhead when a process calls `sched_yield()`. Simpler than pipe ping-pong because it does not involve pipe I/O.

**C source template** (`bench_ctxsw_yield.c`):

```c
#include <stdio.h>
#include <time.h>
#include <sched.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define ITERATIONS 500000
#define WARMUP     10000

int main(void) {
    struct timespec start, end;

    for (int i = 0; i < WARMUP; i++)
        sched_yield();

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < ITERATIONS; i++)
        sched_yield();
    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long per_yield = elapsed / ITERATIONS;

    printf("BENCH ctxsw yield_total_ns %ld ns\n", elapsed);
    printf("BENCH ctxsw yield_per_op_ns %ld ns/op\n", per_yield);

    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64, single process):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| yield_per_op_ns | 200 | 1000 | 5000 |

Note: With only one runnable process, `sched_yield()` returns immediately on Linux (no actual switch). The measurement captures scheduler overhead only. For a real switch, use the pipe ping-pong benchmark.

---

## 4. Memory Allocation

### 4a. mmap/munmap Cycle

**What it measures**: Overhead of mapping and unmapping anonymous pages. Tests the virtual memory subsystem, page table manipulation, and TLB invalidation.

**C source template** (`bench_mem_mmap.c`):

```c
#include <stdio.h>
#include <time.h>
#include <sys/mman.h>
#include <string.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define PAGE_SIZE  4096
#define MAP_SIZE   (16 * PAGE_SIZE)  /* 64 KB per mapping */
#define ITERATIONS 50000
#define WARMUP     1000

int main(void) {
    struct timespec start, end;

    /* Warm up */
    for (int i = 0; i < WARMUP; i++) {
        void *p = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p != MAP_FAILED) munmap(p, MAP_SIZE);
    }

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < ITERATIONS; i++) {
        void *p = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p == MAP_FAILED) { printf("mmap failed at iter %d\n", i); break; }
        /* Touch one page to trigger page fault */
        *(volatile char *)p = 1;
        munmap(p, MAP_SIZE);
    }
    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long per_cycle = elapsed / ITERATIONS;

    printf("BENCH mem mmap_munmap_total_ns %ld ns\n", elapsed);
    printf("BENCH mem mmap_munmap_per_cycle_ns %ld ns/cycle\n", per_cycle);
    printf("BENCH mem mmap_munmap_cycles_per_sec %ld cycles/s\n",
           (long)ITERATIONS * 1000000000L / elapsed);

    return 0;
}
```

**How to interpret results**: `mmap_munmap_per_cycle_ns` measures the full map-touch-unmap cycle. The "touch" triggers a page fault, so this metric includes fault handling time. On Linux, anonymous mmap is highly optimized with lazy allocation.

**Expected Linux baseline ranges** (QEMU riscv64):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| mmap_munmap_per_cycle_ns | 1000 | 5000 | 20000 |

**Bottleneck indicators**:
- Slow VMA (virtual memory area) insertion/removal in the address space tree
- Unnecessary full TLB flush on munmap (should flush only the affected range)
- Page allocator fragmentation causing slow allocation
- Lock contention on the mm_struct lock

### 4b. Page Fault Rate

**What it measures**: Raw page fault handling speed. Maps a large anonymous region, then touches every page sequentially to trigger demand-paging faults.

**C source template** (`bench_mem_pagefault.c`):

```c
#include <stdio.h>
#include <time.h>
#include <sys/mman.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define PAGE_SIZE   4096
#define NUM_PAGES   10000
#define REGION_SIZE (NUM_PAGES * PAGE_SIZE)

int main(void) {
    void *region = mmap(NULL, REGION_SIZE, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) { perror("mmap"); return 1; }

    struct timespec start, end;
    volatile char *p = (volatile char *)region;

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < NUM_PAGES; i++)
        p[i * PAGE_SIZE] = 1;  /* each touch triggers a page fault */
    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long per_fault = elapsed / NUM_PAGES;

    printf("BENCH mem pagefault_total_ns %ld ns\n", elapsed);
    printf("BENCH mem pagefault_per_fault_ns %ld ns/fault\n", per_fault);
    printf("BENCH mem pagefault_faults_per_sec %ld faults/s\n",
           (long)NUM_PAGES * 1000000000L / elapsed);

    munmap(region, REGION_SIZE);
    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| pagefault_per_fault_ns | 500 | 2000 | 10000 |

---

## 5. Filesystem Operations

### 5a. File Create/Delete

**What it measures**: Metadata operation throughput. Creates and then deletes 1000 files in a single directory. Tests inode allocation/deallocation, directory entry management, and the dentry cache.

**C source template** (`bench_fs_createdelete.c`):

```c
#include <stdio.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define NUM_FILES 1000

int main(void) {
    char path[256];
    struct timespec start, end;

    mkdir("/tmp/bench_fs", 0755);

    /* Benchmark: create */
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < NUM_FILES; i++) {
        snprintf(path, sizeof(path), "/tmp/bench_fs/file_%04d", i);
        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) close(fd);
    }
    clock_gettime(CLOCK_MONOTONIC, &end);

    long create_ns = elapsed_ns(&start, &end);
    long create_per = create_ns / NUM_FILES;

    printf("BENCH fs create_total_ns %ld ns\n", create_ns);
    printf("BENCH fs create_per_file_ns %ld ns/file\n", create_per);

    /* Benchmark: stat (metadata read) */
    struct stat st;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < NUM_FILES; i++) {
        snprintf(path, sizeof(path), "/tmp/bench_fs/file_%04d", i);
        stat(path, &st);
    }
    clock_gettime(CLOCK_MONOTONIC, &end);

    long stat_ns = elapsed_ns(&start, &end);
    printf("BENCH fs stat_per_file_ns %ld ns/file\n", stat_ns / NUM_FILES);

    /* Benchmark: delete */
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int i = 0; i < NUM_FILES; i++) {
        snprintf(path, sizeof(path), "/tmp/bench_fs/file_%04d", i);
        unlink(path);
    }
    clock_gettime(CLOCK_MONOTONIC, &end);

    long delete_ns = elapsed_ns(&start, &end);
    long delete_per = delete_ns / NUM_FILES;

    printf("BENCH fs delete_total_ns %ld ns\n", delete_ns);
    printf("BENCH fs delete_per_file_ns %ld ns/file\n", delete_per);
    printf("BENCH fs total_ops %d ops\n", NUM_FILES * 3);

    rmdir("/tmp/bench_fs");
    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64, ext4):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| create_per_file_ns | 5000 | 20000 | 100000 |
| stat_per_file_ns | 500 | 2000 | 10000 |
| delete_per_file_ns | 3000 | 15000 | 80000 |

### 5b. Directory Traversal

**What it measures**: `getdents64` performance when listing a directory with many entries. Tests directory read and dentry cache efficiency.

**C source template** (`bench_fs_readdir.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/stat.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define NUM_FILES  1000
#define ITERATIONS 100

int main(void) {
    char path[256];
    mkdir("/tmp/bench_readdir", 0755);

    /* Populate directory */
    for (int i = 0; i < NUM_FILES; i++) {
        snprintf(path, sizeof(path), "/tmp/bench_readdir/f%04d", i);
        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) close(fd);
    }

    struct timespec start, end;
    int total_entries = 0;

    clock_gettime(CLOCK_MONOTONIC, &start);
    for (int iter = 0; iter < ITERATIONS; iter++) {
        DIR *dir = opendir("/tmp/bench_readdir");
        if (!dir) break;
        struct dirent *de;
        while ((de = readdir(dir)) != NULL)
            total_entries++;
        closedir(dir);
    }
    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long per_iter = elapsed / ITERATIONS;

    printf("BENCH fs readdir_total_entries %d entries\n", total_entries);
    printf("BENCH fs readdir_per_traversal_ns %ld ns/traversal\n", per_iter);
    printf("BENCH fs readdir_per_entry_ns %ld ns/entry\n",
           elapsed / total_entries);

    /* Cleanup */
    for (int i = 0; i < NUM_FILES; i++) {
        snprintf(path, sizeof(path), "/tmp/bench_readdir/f%04d", i);
        unlink(path);
    }
    rmdir("/tmp/bench_readdir");
    return 0;
}
```

**Expected Linux baseline ranges** (QEMU riscv64, ext4, 1000 files):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| readdir_per_entry_ns | 100 | 500 | 3000 |

---

## 6. Multicore Scaling

### 6a. Parallel Computation with Shared Memory

**What it measures**: How well the kernel scales work across multiple cores. Spawns N child processes that each perform a fixed amount of computation, measuring total elapsed time versus single-core time.

**C source template** (`bench_multi_scaling.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/mman.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define WORK_ITERS 5000000

static void do_work(volatile long *result) {
    long sum = 0;
    for (long i = 0; i < WORK_ITERS; i++)
        sum += i * i;
    *result = sum;
}

static long bench_nprocs(int nprocs) {
    long *results = mmap(NULL, nprocs * sizeof(long),
                          PROT_READ | PROT_WRITE,
                          MAP_SHARED | MAP_ANONYMOUS, -1, 0);

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    for (int i = 0; i < nprocs; i++) {
        pid_t pid = fork();
        if (pid == 0) {
            do_work(&results[i]);
            _exit(0);
        }
    }
    for (int i = 0; i < nprocs; i++)
        wait(NULL);

    clock_gettime(CLOCK_MONOTONIC, &end);
    long elapsed = elapsed_ns(&start, &end);

    munmap(results, nprocs * sizeof(long));
    return elapsed;
}

int main(void) {
    int max_cpus = sysconf(_SC_NPROCESSORS_ONLN);
    if (max_cpus < 1) max_cpus = 1;
    if (max_cpus > 8) max_cpus = 8;  /* cap for QEMU */

    long t1 = bench_nprocs(1);
    printf("BENCH multi scaling_1proc_ns %ld ns\n", t1);

    for (int n = 2; n <= max_cpus; n++) {
        long tn = bench_nprocs(n);
        long speedup_pct = t1 * 100 / tn;
        printf("BENCH multi scaling_%dproc_ns %ld ns\n", n, tn);
        printf("BENCH multi scaling_%dproc_speedup_pct %ld %%\n", n, speedup_pct);
    }

    return 0;
}
```

**How to interpret results**: `scaling_Nproc_speedup_pct` should approach `N * 100` for perfect linear scaling. For example, with 4 processes, perfect scaling yields 400%. Real-world overhead (scheduler, memory bus contention, TLB shootdowns) reduces this. On QEMU with 4 vCPUs, Linux typically achieves 350-380% speedup for embarrassingly parallel work.

**Expected Linux baseline ranges** (QEMU riscv64, 4 vCPUs):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| scaling_4proc_speedup_pct | 250 | 370 | 400 |

### 6b. Lock Contention

**What it measures**: Futex-based mutex contention overhead. Multiple processes compete for a shared lock, measuring how much throughput degrades under contention compared to uncontended access.

**C source template** (`bench_multi_lock.c`):

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <stdatomic.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}

#define LOCK_ITERS 100000

/* Simple spinlock using atomics (futex-less for portability) */
typedef _Atomic int spinlock_t;

static void spin_lock(spinlock_t *lock) {
    while (atomic_exchange(lock, 1) != 0)
        sched_yield();
}

static void spin_unlock(spinlock_t *lock) {
    atomic_store(lock, 0);
}

int main(void) {
    int nprocs = sysconf(_SC_NPROCESSORS_ONLN);
    if (nprocs < 1) nprocs = 1;
    if (nprocs > 4) nprocs = 4;

    /* Shared memory for lock and counter */
    void *shared = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                         MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    spinlock_t *lock = (spinlock_t *)shared;
    long *counter = (long *)((char *)shared + 64); /* separate cache line */
    atomic_store(lock, 0);
    *counter = 0;

    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);

    for (int i = 0; i < nprocs; i++) {
        pid_t pid = fork();
        if (pid == 0) {
            for (int j = 0; j < LOCK_ITERS; j++) {
                spin_lock(lock);
                (*counter)++;
                spin_unlock(lock);
            }
            _exit(0);
        }
    }
    for (int i = 0; i < nprocs; i++)
        wait(NULL);

    clock_gettime(CLOCK_MONOTONIC, &end);

    long elapsed = elapsed_ns(&start, &end);
    long total_ops = (long)nprocs * LOCK_ITERS;
    long per_op = elapsed / total_ops;

    printf("BENCH multi lock_nprocs %d procs\n", nprocs);
    printf("BENCH multi lock_total_ops %ld ops\n", total_ops);
    printf("BENCH multi lock_total_ns %ld ns\n", elapsed);
    printf("BENCH multi lock_per_op_ns %ld ns/op\n", per_op);
    printf("BENCH multi lock_counter %ld expected_%ld\n", *counter, total_ops);

    munmap(shared, 4096);
    return 0;
}
```

**How to interpret results**: `lock_per_op_ns` measures the amortized cost of acquiring and releasing a contended lock. The `lock_counter` line verifies correctness; if the counter does not match the expected total, the lock implementation is broken. On Linux, the per-op cost scales roughly linearly with the number of contending processes.

**Expected Linux baseline ranges** (QEMU riscv64, 4 contenders):
| Metric | Low | Typical | High |
|--------|-----|---------|------|
| lock_per_op_ns | 200 | 1000 | 5000 |

---

## Benchmark Report Template

Use this template for reports written to `docs/starry-reports/benchmarks/BENCH-NNN-<category>.md`.

```markdown
# BENCH-NNN: <Category> Performance

**Date**: YYYY-MM-DD
**Category**: <syscall | io | ctxsw | mem | fs | multi>
**Benchmark**: <specific benchmark name>
**Goal**: <what improvement is targeted>

## Methodology

<Describe the benchmark program, iteration counts, warm-up procedure, and any
deviations from the standard template.>

## Environment

- QEMU vCPUs: <N>
- QEMU RAM: <N> MB
- Disk: virtio-blk / 9pfs / ramfs
- StarryOS commit: <hash>
- Linux baseline: Ubuntu 24.04 in Docker

## Results: Before Optimization

| Metric | Linux Baseline | StarryOS | Ratio | Verdict |
|--------|---------------|----------|-------|---------|
| <metric> | <value> <unit> | <value> <unit> | <X.Xx> | <FAST/SLOW/BOTTLENECK> |

## Bottleneck Analysis

<Describe the identified bottleneck with kernel source file and function references.
Explain WHY the bottleneck exists (architectural cause, not just symptoms).>

## Optimizations Applied

### Optimization 1: <title>
- **File**: `<path>`
- **Change**: <concise description>
- **Rationale**: <why this helps>

### Optimization 2: <title>
...

## Results: After Optimization

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| <metric> | <value> | <value> | <+XX%> |

## Summary

<One-paragraph summary of the benchmarking session: what was measured, what was
found, what was improved, and what remains to be done.>

## Next Steps

- [ ] <remaining bottleneck to address>
- [ ] <related benchmark to run>
- [ ] <additional optimization idea>
```

---

## Profiling Tips

### Adding Timing Instrumentation to Kernel Source

Insert timing probes around suspected bottleneck code in the kernel.

**Pattern for Rust kernel code**:
```rust
use axhal::time::monotonic_time_nanos;

let t0 = monotonic_time_nanos();
// ... suspected slow operation ...
let t1 = monotonic_time_nanos();
log::warn!("[PERF] operation took {} ns", t1 - t0);
```

Place probes at:
- Syscall dispatch entry and exit (`mod.rs` in the syscall directory)
- Lock acquisition points (before and after `.lock()` calls)
- Page table manipulation functions
- Memory allocator entry/exit
- Scheduler `pick_next_task` and `switch_to`

### Logging Syscall Entry/Exit Times

Add temporary instrumentation to the main syscall dispatch function to measure per-syscall latency across all calls.

```rust
// In the syscall dispatch function:
let entry_time = monotonic_time_nanos();
let result = match syscall_id {
    SYS_GETPID => sys_getpid(),
    // ...
};
let exit_time = monotonic_time_nanos();
if exit_time - entry_time > 10_000 {  // log calls > 10us
    log::warn!("[PERF] syscall {} took {} ns", syscall_id, exit_time - entry_time);
}
```

Remove all profiling instrumentation before final benchmarking runs. Instrumentation itself adds overhead that distorts measurements.

### Comparing with Linux Source

When a StarryOS operation is significantly slower than Linux, read the corresponding Linux kernel source to understand the fast-path optimizations Linux uses.

Common Linux optimizations to look for:
- **vDSO**: `getpid()`, `clock_gettime()`, and `gettimeofday()` are implemented in userspace via vDSO on Linux, avoiding kernel entry entirely
- **RCU**: Linux uses Read-Copy-Update for read-heavy data structures, avoiding locks on the read path
- **Per-CPU caches**: slab allocator, scheduler run queues, and PID allocators use per-CPU structures to avoid contention
- **Lazy TLB invalidation**: Linux batches TLB invalidation rather than flushing on every munmap
- **Readahead**: The page cache reads ahead of sequential access patterns, reducing fault count
- **Lockless fast paths**: Many syscalls have a fast path that avoids taking any lock for the common case
