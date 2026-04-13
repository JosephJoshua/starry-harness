# Report Templates and Formatting Guidelines

Complete templates for all report types produced by the starry-harness plugin. Copy the relevant template, replace all placeholder text in angle brackets, and fill in every section. Do not remove sections -- mark them "N/A" if they do not apply.

---

## Bug Report Template

**Filename**: `BUG-NNN-<syscall>.md`

Place in `docs/starry-reports/bugs/`. Replace `NNN` with the zero-padded auto-incremented number. Replace `<syscall>` with the syscall name in lowercase (e.g., `BUG-003-fcntl.md`, `BUG-007-mremap.md`).

```markdown
# BUG-NNN: <title>

Short, descriptive title stating the bug. Example: "pwritev2 reads instead of writes due to copy-paste error".

## Classification

- **Category**: <Concurrency|Memory|Safety|Semantic|Correctness>
- **Severity**: <P0|P1|P2|P3>
- **Syscall**: <syscall_name>
- **Location**: <relative/path/to/file.rs:line_number>
- **Status**: <open|fixed|wontfix>

### Category Definitions

- **Concurrency**: Race conditions, deadlocks, missing synchronization, incorrect atomics.
- **Memory**: Use-after-free, double-free, buffer overflows, incorrect page mapping, memory leaks.
- **Safety**: Unsafe code misuse, missing user pointer validation, unsound abstractions.
- **Semantic**: Wrong errno, missing flag handling, incorrect return value, deviation from POSIX semantics.
- **Correctness**: Logic errors, copy-paste bugs, off-by-one, wrong function called, dead code paths.

### Severity Definitions

- **P0**: Data corruption, kernel panic, security vulnerability. Blocks competition submission.
- **P1**: Incorrect behavior that breaks real applications. High priority fix.
- **P2**: Deviation from POSIX semantics unlikely to affect common applications.
- **P3**: Minor cosmetic issue or missing error code for unlikely edge case.

## Summary

One paragraph (3-5 sentences) describing what the bug is, how it manifests, and why it matters. State the expected behavior, the actual behavior, and the practical impact.

## Man Page Reference

Cite the specific man page section that defines the correct behavior. Include the relevant excerpt or paraphrase. Example:

> From `man 2 pwritev2`: "The pwritev2() system call is similar to pwritev(). The difference is in the addition of flags, which modifies the behavior on a per-call basis."
>
> Section "DESCRIPTION" specifies that pwritev2 **writes** data from the buffers described by `iov` to file descriptor `fd` at offset `offset`.

## Test Case

The C test case that demonstrates the bug. Provide the filename and the specific TEST blocks that fail.

```
File: os/StarryOS/tests/cases/test_<syscall>.c

Relevant test blocks:
- <test_name_1>: <what it tests>
- <test_name_2>: <what it tests>
```

If the test case was written as part of this investigation, include the full source. If it already existed, reference it by path and list only the failing test blocks.

## Linux Behavior

Exact output from running the test case on Linux (via Docker or native host). Include both stdout and the test summary line.

```
[PASS] test_name_1
[PASS] test_name_2
...
Results: N passed, 0 failed
```

State the Linux version and architecture used for the baseline.

## StarryOS Behavior

Exact output from running the test case on StarryOS (via QEMU pipeline). Include both stdout and the test summary line.

```
[PASS] test_name_1
[FAIL] test_name_2: expected <X>, got <Y>
...
Results: M passed, K failed
```

State the StarryOS commit hash and build configuration.

## Root Cause Analysis

Detailed technical explanation of why the bug occurs. Trace the code path from the syscall entry point to the point of divergence. Reference specific lines of source code. Explain the logic error.

Example structure:
1. User calls `pwritev2(fd, iov, iovcnt, offset, flags)`
2. Kernel dispatches to `sys_pwritev2()` at `kernel/src/syscall/fs/io.rs:220`
3. Handler calls `file.read_at(offset, buf)` instead of `file.write_at(offset, buf)`
4. This is a copy-paste error from the adjacent `sys_preadv2()` function
5. All data "written" via pwritev2 is silently discarded; the buffer is filled with file contents instead

## Fix

If a fix has been applied, describe it precisely. Include the diff or a summary of the change. Reference the commit hash if available.

```diff
- file.read_at(offset, buf)
+ file.write_at(offset, buf)
```

If the bug is still open, describe the proposed fix approach and any considerations (backwards compatibility, performance implications, interaction with other syscalls).

If the status is `wontfix`, explain why.

## Verification

After applying the fix, re-run the test on both StarryOS and Linux. Show that the results now match.

```
StarryOS (after fix):
[PASS] test_name_1
[PASS] test_name_2
Results: N passed, 0 failed

Linux:
[PASS] test_name_1
[PASS] test_name_2
Results: N passed, 0 failed
```

Also note any regression tests run (e.g., `cargo xtask clippy`, `cargo fmt`, full test suite).
```

---

## Benchmark Report Template

**Filename**: `BENCH-NNN-<category>.md`

Place in `docs/starry-reports/benchmarks/`. Replace `NNN` with the zero-padded auto-incremented number. Replace `<category>` with a short lowercase descriptor of the benchmark area (e.g., `BENCH-001-syscall-overhead.md`, `BENCH-003-mmap-throughput.md`).

```markdown
# BENCH-NNN: <title>

Short, descriptive title. Example: "mmap/munmap cycle throughput improved 3x via TLB flush batching".

## Category

The performance domain being measured. One of:
- **Syscall overhead**: Raw syscall entry/exit latency
- **Memory management**: mmap, munmap, mprotect, brk throughput and latency
- **File I/O**: read, write, sendfile, copy_file_range throughput
- **Process management**: fork, exec, wait latency
- **Networking**: socket, connect, send, recv throughput and latency
- **Scheduling**: Context switch latency, thread creation overhead

## Methodology

Describe the benchmark tool, parameters, and environment in sufficient detail for reproduction.

- **Tool**: <benchmark tool name and version, or custom test program>
- **Parameters**: <exact command line or configuration>
- **Iterations**: <number of iterations or duration>
- **Environment**:
  - Linux baseline: <distro, kernel version, architecture, VM/bare-metal>
  - StarryOS: <commit hash, build configuration, QEMU version and flags>
- **Metric**: <what is being measured: ops/sec, latency in us, throughput in MB/s, etc.>

If a custom benchmark program was written, include its source path:
`os/StarryOS/tests/cases/bench_<name>.c`

## Linux Baseline

Raw measurement results from the Linux reference environment.

```
<benchmark output>
```

**Summary**: <N> ops/sec | <M> us latency | <X> MB/s throughput (whichever applies)

## StarryOS Before

Raw measurement results from StarryOS before any optimization.

```
<benchmark output>
```

**Summary**: <N> ops/sec | <M> us latency | <X> MB/s throughput

**Ratio vs. Linux**: <X>x slower | <Y>% of Linux throughput

## Optimization Applied

Describe the optimization in technical detail. Reference specific source files and lines. Explain the hypothesis for why this change improves performance.

Example:
> Replaced per-page TLB flush in `munmap` with a batched flush after the entire region is unmapped. Previously, unmapping a 1MB region (256 pages) issued 256 individual `invlpg` instructions. Now a single full TLB flush is issued after all pages are released.
>
> Source: `kernel/src/mm/tlb.rs:45-67`

If no optimization was applied (baseline-only measurement), state "No optimization applied. This report establishes the baseline for future work." and leave the "StarryOS After" section as "N/A".

## StarryOS After

Raw measurement results from StarryOS after the optimization.

```
<benchmark output>
```

**Summary**: <N> ops/sec | <M> us latency | <X> MB/s throughput

**Ratio vs. Linux**: <X>x slower | <Y>% of Linux throughput

## Improvement

Compute and present the improvement clearly.

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| <metric> | <value> | <value> | <ratio>x / <percent>% |

State whether the competition target (50% or greater improvement in at least one area) has been met by this optimization.

## Analysis

Interpret the results. Address:
- Why did the optimization have this effect?
- Are there diminishing returns, or is further improvement possible?
- How does StarryOS now compare to Linux for this workload?
- Are there any regressions in other areas caused by this change?
- What would be the next optimization target for this category?
```

---

## App Compatibility Report Template

**Filename**: `APP-NNN-<name>.md`

Place in `docs/starry-reports/apps/`. Replace `NNN` with the zero-padded auto-incremented number. Replace `<name>` with the application name in lowercase (e.g., `APP-001-nginx.md`, `APP-002-python.md`, `APP-003-redis.md`).

```markdown
# APP-NNN: <application>

Short identification. Example: "Nginx 1.24 static file serving on StarryOS".

## Application

- **Name**: <application name>
- **Version**: <version tested>
- **Purpose**: <one-line description of what the application does>
- **Why this app**: <why it was chosen for the competition -- popularity, syscall coverage, etc.>
- **Upstream source**: <URL to source tarball or repository>

## Syscall Requirements

List every syscall the application uses. Obtain this via `strace` on Linux or by analyzing the application's source/binary.

| Syscall | Used for | StarryOS status |
|---------|----------|-----------------|
| `mmap` | Memory allocation | Implemented (mostly_ok) |
| `epoll_create1` | Event loop | Implemented |
| `accept4` | Connection handling | Missing |
| ... | ... | ... |

Group by subsystem (memory, file I/O, networking, process, signals) for readability.

## Gap Analysis

For each syscall that is missing, stubbed, or buggy in StarryOS, assess the impact on the application.

| Gap | Impact | Workaround | Effort |
|-----|--------|------------|--------|
| `accept4` missing | Cannot accept connections -- fatal | Implement accept4 as wrapper around accept + fcntl | Medium |
| `flock` stubbed | Log file locking silently fails -- non-fatal | None needed for basic operation | Low |
| ... | ... | ... | ... |

**Blocking gaps**: List the syscalls whose absence or brokenness prevents the application from starting or performing its core function.

**Non-blocking gaps**: List the syscalls whose absence causes degraded behavior but does not prevent basic operation.

## Build Instructions

Step-by-step instructions to cross-compile the application for StarryOS's target architecture (typically aarch64 or riscv64).

```bash
# Download source
wget <url>
tar xf <archive>
cd <directory>

# Configure for cross-compilation
./configure --host=<target-triple> --prefix=/usr \
    CC=<cross-compiler> \
    CFLAGS="<flags>" \
    LDFLAGS="-static"

# Build
make -j$(nproc)

# Verify the binary
file <binary>
```

Note any patches required for static linking or missing features.

## Test Results

Describe how the application was tested and what happened.

**Test procedure**:
1. Inject the binary into StarryOS rootfs via `os/StarryOS/tools/inject.sh`
2. Boot StarryOS with QEMU
3. <describe the test scenario: start the server, send requests, etc.>

**Results**:

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Binary starts | Process runs | <what happened> | PASS/FAIL |
| Serves static file | HTTP 200 with correct body | <what happened> | PASS/FAIL |
| Handles 10 concurrent connections | All complete | <what happened> | PASS/FAIL |
| ... | ... | ... | ... |

**Console output** (relevant excerpt):
```
<StarryOS console output showing the application's behavior>
```

## Fixes Required

List all kernel fixes that were needed to make the application work (or partially work). Cross-reference bug reports.

| Fix | Bug Report | Description |
|-----|-----------|-------------|
| Implement `accept4` | [BUG-005](../bugs/BUG-005-accept4.md) | Added accept4 syscall handler |
| Fix `fcntl F_GETFL` | [BUG-003](../bugs/BUG-003-fcntl.md) | Return open flags instead of permission bits |
| ... | ... | ... |

If no fixes were required, state "No kernel fixes were required. The application ran successfully on unmodified StarryOS."

## Status

Current status of the application on StarryOS. One of:

- **Working**: Application starts, performs its core function, and passes all tests.
- **Partial**: Application starts but some functionality is broken or degraded. State what works and what does not.
- **Blocked**: Application cannot start or immediately crashes. State the blocking issue.
- **Not tested**: Syscall gap analysis complete but the application has not been built or run yet.

**Competition target**: State whether this application satisfies the requirement of "at least one mainstream application running" and justify the claim.
```

---

## Progress Summary Template

The progress summary is not a numbered report file. Generate it on demand and present it directly, or write it to a user-specified location. This is the top-level competition deliverable.

```markdown
# StarryOS Development Progress

Generated: <YYYY-MM-DD>
Period: <start date> to <end date>
StarryOS commit: <hash>

## Bug Discovery

**Total bugs found**: <N> across <M> categories.
**Competition target** (>=10 bugs across >=2 categories): <MET / NOT MET>

### By Category

| Category | Count | P0 | P1 | P2 | P3 | Fixed | Open |
|----------|-------|----|----|----|----|-------|------|
| Concurrency | <n> | <n> | <n> | <n> | <n> | <n> | <n> |
| Memory | <n> | <n> | <n> | <n> | <n> | <n> | <n> |
| Safety | <n> | <n> | <n> | <n> | <n> | <n> | <n> |
| Semantic | <n> | <n> | <n> | <n> | <n> | <n> | <n> |
| Correctness | <n> | <n> | <n> | <n> | <n> | <n> | <n> |
| **Total** | **<N>** | | | | | **<n>** | **<n>** |

### Notable Bugs

List the 3-5 most significant bugs with one-line descriptions and links to their reports.

1. **[BUG-NNN](bugs/BUG-NNN-<name>.md)**: <one-line description>. <P-level>. <Status>.
2. ...

### Syscall Coverage

| Syscall | Status | Bugs Found | Report |
|---------|--------|------------|--------|
| pwritev2 | buggy | 1 | [BUG-001](bugs/BUG-001-pwritev2.md) |
| mremap | broken | 5 | [BUG-002](bugs/BUG-002-mremap.md) |
| ... | ... | ... | ... |

## Performance Improvements

**Competition target** (>=50% improvement in at least one area): <MET / NOT MET>

| Benchmark | Before | After | Improvement | Report |
|-----------|--------|-------|-------------|--------|
| <name> | <value> | <value> | <ratio>x | [BENCH-NNN](benchmarks/BENCH-NNN-<name>.md) |
| ... | ... | ... | ... | ... |

### Key Optimization

Describe the single most impactful optimization in 2-3 sentences, with a link to the full benchmark report.

## Application Compatibility

**Competition target** (>=1 mainstream application running): <MET / NOT MET>

| Application | Status | Syscalls Needed | Gaps Remaining | Report |
|-------------|--------|-----------------|----------------|--------|
| <name> | Working/Partial/Blocked | <N> | <M> | [APP-NNN](apps/APP-NNN-<name>.md) |
| ... | ... | ... | ... | ... |

### Key Achievement

If an application is working, describe the achievement in 2-3 sentences. If none are working yet, describe the closest candidate and what remains.

## New Features

List features or enhancements added to the StarryOS kernel during this development period, even if they were not directly related to bug fixing.

| Feature | Description | Files Changed |
|---------|-------------|---------------|
| <feature> | <description> | <files> |
| ... | ... | ... |

## Methodology

Describe the systematic approach used for this work. This section demonstrates rigor to competition judges.

### Bug Discovery Process

Summarize the hunt-bugs workflow: automated pattern scanning, man page cross-referencing, C test case generation, Docker-based Linux comparison testing, root cause analysis.

### Testing Infrastructure

Describe the test harness:
- `starry_test.h` framework for structured C test cases
- Docker-based Linux reference testing via `linux-ref-test.sh`
- QEMU-based StarryOS testing via `pipeline.sh` (compile, inject, run)
- `known.json` registry for tracking all tested syscalls

### Performance Measurement

Describe the benchmarking methodology: tools used, iteration counts, environment normalization, statistical rigor.

## Remaining Work

Honest assessment of what has not been achieved and what would be needed to reach all competition targets. List in priority order.

1. <highest priority remaining item>
2. <next priority>
3. ...
```

---

## Naming Conventions

### File Names

- Bug reports: `BUG-NNN-<syscall>.md` where `<syscall>` is the primary syscall name in lowercase. If the bug spans multiple syscalls, use the most representative one.
- Benchmark reports: `BENCH-NNN-<category>.md` where `<category>` is a short hyphenated descriptor (e.g., `mmap-throughput`, `syscall-overhead`, `context-switch`).
- App compatibility reports: `APP-NNN-<name>.md` where `<name>` is the application name in lowercase (e.g., `nginx`, `python`, `redis`, `busybox`).

### Numbers

Always zero-pad to three digits: `001`, `002`, ..., `099`, `100`. This ensures correct lexicographic sorting in directory listings.

### Syscall Names

Use the raw syscall name as it appears in the kernel source, without the `sys_` prefix. Example: `mremap`, not `sys_mremap`. Use underscores, not hyphens, within syscall names: `copy_file_range`, not `copy-file-range`.

---

## Cross-Referencing Between Reports

Reports form a connected graph. Maintain these links to build a navigable knowledge base.

### Bug reports referencing other reports

In the "Fix" or "Verification" section of a bug report, link to:
- Related benchmark reports if the fix also improved performance: "This fix also improved mmap throughput; see [BENCH-002](../benchmarks/BENCH-002-mmap-throughput.md)."
- Related app compatibility reports if the fix unblocked an application: "Fixing this bug unblocked Nginx startup; see [APP-001](../apps/APP-001-nginx.md)."
- Other bug reports for the same syscall or subsystem: "See also [BUG-004](BUG-004-mremap.md) for related mremap issues."

### App compatibility reports referencing bug reports

In the "Fixes Required" section, link every kernel fix to its bug report using the table format shown in the template. In the "Gap Analysis" section, reference known bug reports for syscalls that are buggy rather than missing.

### Benchmark reports referencing bug reports

If a performance improvement resulted from fixing a bug, link the benchmark report's "Optimization Applied" section to the bug report: "The optimization was implemented as part of the fix for [BUG-007](../bugs/BUG-007-munmap.md)."

### Journal referencing reports

Every journal entry that corresponds to a report should include the report number in its body text. Example:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh BUG "Found pwritev2 copy-paste bug" "pwritev2 calls read_at instead of write_at. See BUG-001."
```

### Progress summary referencing everything

The progress summary links to all individual reports via its tables. Use relative paths from the summary's location (`docs/starry-reports/`) to the report subdirectories.

---

## Competition Submission Formatting Guidelines

The progress summary and individual reports may be submitted to OS competition judges. Follow these guidelines to maintain a professional standard.

### Voice and Tone

- Write in third person: "StarryOS implements...", "The kernel's mremap handler...", not "we implemented" or "I found".
- Use precise technical language. Avoid hedging words ("seems", "might", "possibly") unless the analysis is genuinely uncertain -- in which case state the uncertainty explicitly.
- Lead with facts and data. Place interpretation after the evidence.

### Quantitative Claims

- Every claimed bug must have a reproducing test case and side-by-side comparison output.
- Every claimed performance improvement must have before/after numbers with the same methodology.
- State units explicitly: "3.2x improvement in mmap/munmap cycles per second (1,200 ops/s to 3,840 ops/s)" not "3.2x faster".
- Report the measurement environment (QEMU version, host CPU, memory) so results can be reproduced.

### POSIX and Man Page References

- When describing expected behavior, cite the man page section: "per `mmap(2)`, DESCRIPTION paragraph 3".
- When a StarryOS behavior deviates from POSIX, state which standard (POSIX.1-2017, Linux-specific extension, etc.) defines the expected behavior.
- Quote the relevant man page text directly when the exact wording matters.

### Code References

- Always include the source file path and line number when discussing kernel code.
- Use the repository-relative path: `kernel/src/syscall/fs/io.rs:220`, not the absolute filesystem path.
- When showing diffs, use standard unified diff format.

### Length and Structure

- Individual bug reports: 300-800 words depending on complexity.
- Individual benchmark reports: 400-1000 words.
- Individual app compatibility reports: 500-1500 words.
- Progress summary: under 3000 words. Link to individual reports for detail. Judges will skim the summary and deep-dive into individual reports selectively.

### Formatting

- Use Markdown throughout. Assume reports will be rendered by a Markdown viewer.
- Use tables for structured data (metrics, syscall lists, test results).
- Use code blocks with language hints for source code, diffs, and console output.
- Use blockquotes for man page citations.
- Use bold for key terms on first use and for status indicators (PASS/FAIL, MET/NOT MET).
