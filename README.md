# starry-harness

A [Claude Code](https://claude.ai/code) plugin for systematic [StarryOS](https://github.com/rcore-os/tgoskits) kernel development. Provides bug hunting with Linux comparison testing, kernel internal auditing, performance benchmarking, application compatibility testing, code quality enforcement, upstream submission preparation, and structured reporting — backed by deterministic static analysis tools that eliminate guesswork.

## Installation

Add to `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "starry-harness": {
      "source": { "source": "github", "repo": "JosephJoshua/starry-harness" }
    }
  },
  "enabledPlugins": {
    "starry-harness@starry-harness": true
  }
}
```

Restart Claude Code.

## Skills

| Skill | What it does |
|-------|-------------|
| `hunt-bugs` | Syscall bug discovery: generate test, run on Docker Linux, run on StarryOS, diff, fix. Linux must pass first. |
| `audit-kernel` | Deep kernel internal audit beyond syscalls: scheduler, memory, concurrency, signals, filesystem. Uses lockdep, stress testing, property-based tests. |
| `benchmark` | Performance measurement against Linux baselines. Profile, optimize, re-measure. |
| `test-app` | Run real Linux applications (Nginx, PostgreSQL, Python, etc.) on StarryOS. Strace profiling, gap analysis, blocker fixes. |
| `review-quality` | Code quality gate for kernel changes. Rust idioms, safety, API design, framework reuse. |
| `report` | Structured bug reports, benchmark reports, app compatibility reports, and a running work journal. |
| `evolve` | Autonomous target selection with sweep/deep modes. Picks what to work on based on coverage gaps and past effectiveness. Enforces the review pipeline. |
| `start-submission` | Prepares upstream PRs: fresh clone, minimal fix port, test format conversion, verification, Chinese PR draft. Does everything except `gh pr create`. |

## Agents

| Agent | Role |
|-------|------|
| `linux-comparator` | Runs tests on Docker Linux and StarryOS, produces structured diff |
| `kernel-reviewer` | Read-only code quality review with fresh context |
| `bug-triager` | Classifies bugs by category and severity, recommends fix priority |

## Deterministic Tools

Static analysis scripts that produce ground-truth output. The agent interprets results — the tools themselves cannot hallucinate.

| Script | What it does |
|--------|-------------|
| `abi-check.py` | Compares StarryOS syscall arg counts against Linux `SYSCALL_DEFINE` signatures. Each entry sourced from kernel v6.12 with verifiable URLs. |
| `lock-order-graph.py` | Builds a directed graph of lock acquisitions, detects cycles (deadlocks). Rust ownership-aware: distinguishes `let guard = x.lock()` from `x.lock().method()`. |
| `pattern-scanner.py` | Scans kernel source against regex rules (9 default patterns). Rules evolve as new bug classes are found. |
| `kernel-graph.py` | Maps all 204 syscalls to subsystems, files, locks, and unsafe blocks. |
| `change-tracker.py` | Identifies which tests need re-running based on `git diff` since last run. |

## Test Pipeline

| Script | What it does |
|--------|-------------|
| `pipeline.sh` | Full compile → inject → build → QEMU boot → result capture. Supports `--arch riscv64\|aarch64\|x86_64\|loongarch64`. |
| `linux-ref-test.sh` | Compile and run a C test inside Docker Linux. Supports `--arch` for cross-arch comparison via QEMU user-mode. |
| `stress-test.sh` | Multi-run test execution with SMP sweeping (`--smp 1,2,4`) and timeout-based deadlock detection. |
| `regression-check.sh` | Runs all tests in `known.json`, compares against expected pass/fail counts, flags regressions. |
| `strace-profiler.sh` | Runs an application under strace in Docker, produces a structured syscall profile with gap analysis. |
| `convert-test.py` | Converts `starry_test.h` test format to upstream `test_framework.h` format. |
| `update-known.sh` | Updates `known.json` with results from a pipeline run. |

## Utility Scripts

| Script | What it does |
|--------|-------------|
| `man-lookup.sh` | Fetches syscall man pages (local, Docker, or man7.org). |
| `journal-entry.sh` | Appends structured entries to the work journal. |
| `draft-pr.sh` | Generates a PR draft markdown file with a ready-to-paste `gh pr create` command. |

## How it works

```
evolve (pick target)
  │
  ├── sweep: scan 5-10 syscalls quickly, flag suspicious ones
  └── deep: full cycle on one target
        │
        ├── abi-check.py (verify arg counts before writing tests)
        ├── man-lookup.sh (fetch Linux specification)
        ├── write test (starry_test.h format)
        ├── linux-ref-test.sh (MUST pass on Linux first)
        ├── pipeline.sh (run on StarryOS)
        ├── diff (find divergences)
        ├── fix kernel code
        ├── review pipeline:
        │     ├── self-check
        │     ├── kernel-reviewer agent
        │     ├── regression-check.sh
        │     ├── Codex independent review (P0/P1)
        │     └── convergence assessment
        ├── report (bug report + journal + strategy update)
        └── start-submission (when ready for upstream)
```

Every 3-5 runs, the system pauses to reflect: runs the pattern scanner and lock-order graph, identifies cross-cutting patterns, generates new detection rules, and updates priorities.

## State

The harness maintains persistent state in the target project:

- `docs/starry-reports/strategy.json` — coverage, effectiveness metrics, analysis queue, review history
- `docs/starry-reports/journal.md` — running work log
- `docs/starry-reports/patterns.json` — evolving regex rules for the pattern scanner
- `os/StarryOS/tests/known.json` — bug registry with per-syscall status and test results
- `docs/starry-reports/bugs/` — individual bug reports
- `docs/starry-reports/benchmarks/` — performance reports
- `docs/starry-reports/apps/` — application compatibility reports

## Hooks

- **SessionStart** — loads journal, bug registry, and strategy priorities so every session starts with full context
- **Stop** — blocks if a fix was proposed without completing the review pipeline; approves investigation-only sessions immediately

## Requirements

- [Claude Code](https://claude.ai/code) with the starry-harness plugin installed
- Docker (Linux comparison testing, rootfs mounting, man page lookup)
- QEMU (`qemu-system-riscv64` minimum; `aarch64`/`x86_64`/`loongarch64` for multi-arch)
- musl cross-compiler (`riscv64-linux-musl-gcc` minimum; others for multi-arch)
- `rust-objcopy` (from `cargo install cargo-binutils`)
- Python 3 (for deterministic analysis scripts)

## License

Apache-2.0
