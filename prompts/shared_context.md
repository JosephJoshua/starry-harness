# StarryOS Kernel Improvement — Shared Context

## Project
- Repository: https://github.com/rcore-os/tgoskits
- Kernel: os/StarryOS/ (Linux-compatible kernel built on ArceOS)
- ~200 syscalls implemented, Alpine Linux partially supported
- Architectures: riscv64 (primary test target), aarch64, x86_64, loongarch64
- Runs on QEMU and select physical hardware

## Engineering Principles
1. Linux behavior is the correctness oracle — always compare against Docker Linux
2. Tests before fixes — prove the bug exists before modifying kernel code
3. Minimal changes — one bug per round, no bundled modifications
4. Evidence-driven — every claim must have verifiable evidence (test output, source proof, or measurable property)
5. Regression protection — every fix must add at least one test to the permanent suite
6. Deterministic tools first — use lock-order-graph, pattern-scanner, kernel-graph before LLM reasoning

## Evidence Tiers (only report tier 1-5)
1. Executable: test produces different output on Linux vs StarryOS
2. Source proof: code is visibly wrong (file:line reference)
3. Property violation: measurable invariant broken (e.g., memory leak after N cycles)
4. Differential: behavior changes with config (SMP=1 vs SMP=4)
5. Linux source comparison: StarryOS diverges from Linux kernel source
6. Pattern match: code matches known-bad pattern (use to guide testing, not as evidence)
7. LLM reasoning: "I think this could..." (NEVER report as confirmed)

## State Files
- `os/StarryOS/tests/known.json` — Bug registry (tested syscalls, status, pass/fail counts)
- `docs/starry-reports/strategy.json` — Coverage, effectiveness, priorities, review history
- `docs/starry-reports/journal.md` — Running work log
- `docs/starry-reports/patterns.json` — Deterministic scanner rules (evolves as bugs are found)

## Test Infrastructure
- Test harness: `os/StarryOS/tests/cases/starry_test.h` (C test framework)
- Pipeline: `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh`
- Linux baseline: `scripts/linux-ref-test.sh` (Docker)
- Stress testing: `scripts/stress-test.sh` (SMP sweeping, deadlock detection)
