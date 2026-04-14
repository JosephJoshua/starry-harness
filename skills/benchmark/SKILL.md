---
name: benchmark
description: This skill should be used when the user asks to "benchmark StarryOS", "measure performance", "improve performance", "compare speed", "profile syscall latency", "optimize I/O", "optimize scheduler", "optimize memory", "optimize filesystem", "measure throughput", "context switch overhead", "multicore scaling", or wants to run performance benchmarks comparing StarryOS against Linux baselines and identify optimization targets.
---

# StarryOS Performance Benchmarking

Performance benchmarking workflow for comparing StarryOS against Linux Docker baselines, identifying bottlenecks, and driving targeted kernel optimizations. The competition goal: improve performance by at least 50% in one or more areas (I/O throughput, scheduling, memory management, filesystem operations, multicore scaling).

## Benchmark Categories

Six primary categories cover the performance-critical subsystems of the kernel. Each benchmark isolates a specific performance dimension so that bottlenecks can be attributed precisely.

| Category | What It Measures | Key Syscalls / Mechanisms |
|----------|-----------------|--------------------------|
| **Syscall latency** | Raw overhead of entering and exiting the kernel | `getpid()`, `clock_gettime()`, `gettimeofday()` |
| **I/O throughput** | Sequential and random read/write bandwidth | `read()`, `write()`, `pread()`, `pwrite()` |
| **Context switch** | Cost of switching between processes or threads | `pipe()`, `fork()`, `read()`/`write()` ping-pong, `sched_yield()` |
| **Memory allocation** | Page fault handling, mmap/munmap cycle time, brk expansion | `mmap()`, `munmap()`, `brk()`, `madvise()` |
| **Filesystem** | Metadata operations: create, delete, stat, readdir | `open()`, `close()`, `unlink()`, `mkdir()`, `getdents64()` |
| **Multicore scaling** | Parallel throughput and lock contention | `clone()`, `futex()`, shared memory, atomic ops |

## Workflow Overview

Seven-phase cycle: **Select -> Baseline -> Bench -> Compare -> Profile -> Optimize -> Report**. Each phase produces measurable artifacts. Iterate the Profile-Optimize-Bench loop until the target improvement is reached.

## Phase 1: Select Benchmark and Design Test

Choose a benchmark category based on current optimization priorities. Consult `references/benchmark-suite.md` for the full catalog of concrete benchmark programs with C source templates.

**Design the benchmark program** as a self-contained C file using `clock_gettime(CLOCK_MONOTONIC)` for all timing measurements.

```c
#include <stdio.h>
#include <time.h>

static inline long elapsed_ns(struct timespec *start, struct timespec *end) {
    return (end->tv_sec - start->tv_sec) * 1000000000L +
           (end->tv_nsec - start->tv_nsec);
}
```

**Rules for valid benchmarks**:
- Use `CLOCK_MONOTONIC` exclusively (not `CLOCK_REALTIME` which can jump)
- Warm up with a small pre-run to prime caches and TLB before measuring
- Run enough iterations to reach at least 100ms total elapsed time to amortize timer overhead
- Report both total time and per-operation time (nanoseconds per op)
- Print results in a machine-parseable format: `BENCH <category> <metric> <value> <unit>`
- Compile with `-O2 -static` for consistent behavior across Linux and StarryOS

**Place benchmark sources** at: `os/StarryOS/tests/cases/bench_<category>.c`

## Phase 2: Linux Docker Baseline

Run the benchmark on real Linux inside a Docker container to establish the reference performance baseline. This provides the ground truth that StarryOS is measured against.

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/linux-ref-test.sh \
    os/StarryOS/tests/cases/bench_<category>.c \
    /tmp/linux-bench-<category>.txt
```

Parse the output to extract the `BENCH` lines. Record baseline values in the comparison table. If Docker is unavailable, report the error and suggest installation; do not fabricate baseline numbers.

**Expected baseline ranges** are documented per benchmark in `references/benchmark-suite.md`. If the measured Linux baseline is far outside the documented range, suspect a measurement error and re-run with increased iteration counts.

## Phase 3: Run Benchmark on StarryOS

Execute the same benchmark program on StarryOS via the QEMU test pipeline.

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh bench_<category>
```

This runs the full pipeline: cross-compile the C benchmark, inject the binary into the ext4 rootfs, build the kernel, boot QEMU, run the binary, and capture output.

Extract the `BENCH` lines from `os/StarryOS/tests/results/bench_<category>.txt`.

**QEMU caveats**: StarryOS runs in QEMU emulation, so absolute numbers are not comparable to bare-metal Linux. The meaningful comparison is *relative* performance — the ratio of StarryOS time to Linux time for the same operation on the same QEMU configuration. Run the Linux baseline inside QEMU as well (via `linux-ref-test.sh` with the Docker container using the same resource constraints) to produce an apples-to-apples comparison.

## Phase 4: Compare Results and Identify Bottlenecks

Build a structured comparison table for each metric within the benchmark category.

```markdown
| Metric | Linux (ns/op) | StarryOS (ns/op) | Ratio | Verdict |
|--------|--------------|-------------------|-------|---------|
| getpid latency | 120 | 850 | 7.1x | BOTTLENECK |
| clock_gettime  | 45  | 200 | 4.4x | SLOW |
```

**Classification thresholds**:
- `FAST`: StarryOS within 1.5x of Linux — no action needed
- `SLOW`: 1.5x to 5x — worth investigating if effort is low
- `BOTTLENECK`: over 5x — high-value optimization target
- `BROKEN`: benchmark fails, hangs, or produces impossible numbers — investigate correctness first

**Dispatch the linux-comparator agent** if detailed behavioral comparison is needed alongside performance data (e.g., if the benchmark also checks that operations produce correct results, not just speed).

For each BOTTLENECK or SLOW metric, hypothesize the likely cause:
- Excessive memory copies in the syscall path
- Lock contention on global data structures
- Unoptimized page table operations
- Missing fast-path for common cases
- Unnecessary TLB flushes or cache invalidation
- Inefficient scheduler wake-up path

## Phase 5: Profile and Optimize

Read the relevant kernel source to understand the hot path for each bottleneck.

**Key source locations** (same as hunt-bugs):

| Resource | Path |
|----------|------|
| Syscall handlers | `os/StarryOS/kernel/src/syscall/` |
| Process management | `os/StarryOS/kernel/src/process/` |
| Memory subsystem | `os/StarryOS/kernel/src/mm/` or crate `starry-vm` |
| Filesystem layer | `os/StarryOS/kernel/src/fs/` or crate `starry-fs` |
| Scheduler | `os/StarryOS/kernel/src/sched/` or crate `arceos-sched` |
| Test harness header | `os/StarryOS/tests/cases/starry_test.h` |
| Test sources | `os/StarryOS/tests/cases/bench_*.c` |
| Test results | `os/StarryOS/tests/results/` |
| Pipeline script | `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` |
| Benchmark reports | `docs/starry-reports/benchmarks/` |
| Work journal | `docs/starry-reports/journal.md` |

**Profiling techniques**:

1. **Instrumented timing** — Add `clock_gettime` calls inside the kernel at suspected bottleneck points. Log timestamps at syscall entry, at each major phase (validation, locking, operation, unlock, return), and at syscall exit. Compute per-phase durations to locate the slowest segment.

2. **Syscall entry/exit logging** — Temporarily add printk-style logging to the syscall dispatch path to measure time spent in each handler. Remove before benchmarking.

3. **Lock contention analysis** — Wrap lock acquisitions with timing to measure wait duration versus hold duration. High wait/hold ratio signals contention.

4. **Allocation counting** — Count the number of memory allocations per syscall invocation. Each allocation potentially triggers page table walks and TLB operations.

5. **Comparison with Linux source** — Read the corresponding Linux kernel source (via web search or man page references) to understand how Linux achieves its performance. Identify fast-paths, caching strategies, or lock-free techniques that StarryOS lacks.

**Implement optimizations** in the kernel source. Common optimization patterns:
- Add fast-path for the common case (e.g., `getpid()` can cache the value)
- Reduce lock scope: hold locks for the minimum required duration
- Batch operations: combine multiple page table updates into one TLB flush
- Use per-CPU data structures to eliminate cross-core contention
- Avoid unnecessary copies: use zero-copy where possible
- Cache frequently-accessed metadata in process control blocks

**Dispatch the kernel-reviewer agent** after writing any optimization to verify:
- The optimization does not break correctness
- Unsafe code has proper safety comments
- The change is idiomatic Rust and uses existing abstractions
- Lock discipline is maintained

## Phase 6: Re-Benchmark

Run the same benchmark again on StarryOS after applying optimizations.

1. Re-run via `bash ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh bench_<category>`
2. Extract results and compare against the pre-optimization baseline
3. Compute the improvement ratio: `improvement = (old_time - new_time) / old_time * 100`
4. Verify correctness: the optimized benchmark must still produce correct output
5. Run `cargo xtask clippy --package starry-kernel` and `cargo fmt` to ensure code quality

**Iterate** the Profile-Optimize-Bench loop until diminishing returns are observed, then try a different category. There is no fixed target — keep improving as long as gains are measurable.

## Phase 7: Generate Report

Produce a structured benchmark report and log the work.

**Benchmark report**: Write to `docs/starry-reports/benchmarks/BENCH-NNN-<category>.md` using the template from `references/benchmark-suite.md`. The report must include:
- Benchmark description and methodology
- Linux baseline measurements
- StarryOS before-optimization measurements
- Optimizations applied (with kernel source file references)
- StarryOS after-optimization measurements
- Improvement summary table
- Next steps and remaining bottlenecks

**Journal entry**: Log the benchmarking session via the journal script:
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh BENCH "<category>: <summary>" "<detail>"
```

**Share results**: If the improvement is significant (>20% in any metric), note it as a candidate for the competition submission.

## Agents

- **linux-comparator** — Run benchmarks on Docker Linux for baselines; also handles behavioral correctness comparisons
- **kernel-reviewer** — Review optimization patches for code quality, safety, and architectural coherence before committing

## Reference Files

- **`references/benchmark-suite.md`** — Complete catalog of benchmark programs with C source templates, expected Linux baseline ranges, interpretation guides, and the benchmark report template
