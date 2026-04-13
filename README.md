# starry-harness

AI-driven engineering harness for [StarryOS](https://github.com/rcore-os/tgoskits) kernel improvement. A [Claude Code](https://claude.ai/code) plugin providing systematic workflows for bug hunting, performance benchmarking, application compatibility testing, code quality review, and structured reporting.

## Installation

Add the marketplace to your `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "starry-harness": {
      "source": {
        "source": "github",
        "repo": "JosephJoshua/starry-harness"
      }
    }
  },
  "enabledPlugins": {
    "starry-harness@starry-harness": true
  }
}
```

Restart Claude Code. The plugin loads automatically in any project.

## Skills

| Skill | Trigger | Purpose |
|-------|---------|---------|
| **hunt-bugs** | "find bugs", "test syscalls", "compare with Linux" | Discover → Test → Compare → Fix → Report cycle with Docker Linux comparison |
| **audit-kernel** | "audit kernel", "find deadlocks", "check for races" | Deep kernel internal audit: scheduler, memory, concurrency, signals, filesystem |
| **benchmark** | "benchmark performance", "optimize IO" | Performance testing against Linux baselines, ≥50% improvement target |
| **test-app** | "run Nginx on StarryOS", "test app" | Linux application compatibility (Nginx, PostgreSQL, Python, rustc, etc.) |
| **review-quality** | "review my changes", "check code quality" | Code quality gate: Rust idioms, API design, safety, framework reuse |
| **report** | "generate report", "status report" | Structured reporting and journal system |

## Agents

| Agent | Role |
|-------|------|
| **linux-comparator** | Runs tests in Docker Linux, diffs behavior against StarryOS |
| **kernel-reviewer** | Deep code quality review for kernel changes (read-only) |
| **bug-triager** | Classifies bugs into competition categories, prioritizes fixes |

## Infrastructure

| Script | Purpose |
|--------|---------|
| `scripts/linux-ref-test.sh` | Compile and run a C test inside a Docker Linux container |
| `scripts/man-lookup.sh` | Fetch syscall man pages (local → Docker → man7.org) |
| `scripts/journal-entry.sh` | Append structured entries to the work journal |
| `scripts/stress-test.sh` | Multi-run test runner with SMP sweeping and deadlock detection |

## Workflow

```
hunt-bugs ──► review-quality ──► report ──► (repeat)
                                              │
              benchmark ◄─────────────────────┤
              test-app  ◄─────────────────────┘
```

1. **Hunt bugs** — scan syscall handlers, generate tests, compare against Linux, fix divergences
2. **Audit kernel** — go deeper: scheduler fairness, memory leaks, concurrency races, signal delivery, lock ordering
3. **Review quality** — gate every fix for Rust idioms, safety, API design, code reuse
4. **Report** — write bug report + journal entry
5. **Benchmark** — establish Linux baselines, profile, optimize, measure improvement
6. **Test apps** — pick a Linux application, audit its syscall requirements, fill gaps, verify it runs

The `audit-kernel` skill enforces a verification tier system and controlled amplification techniques for reproducing concurrency bugs. See `skills/audit-kernel/references/` for the full protocol.

Reports are written to `docs/starry-reports/` in the target project:
- `journal.md` — running work log
- `bugs/BUG-NNN-*.md` — individual bug reports
- `benchmarks/BENCH-NNN-*.md` — performance reports
- `apps/APP-NNN-*.md` — application compatibility reports

## Requirements

- [Claude Code](https://claude.ai/code)
- Docker (for Linux comparison testing and man page lookup)
- `riscv64-linux-musl-gcc` (for cross-compiling test cases)
- `qemu-system-riscv64` (for running StarryOS)

## License

Apache-2.0
