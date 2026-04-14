# strategy.json Schema Reference

Persistent state file for the self-evolving harness. Lives at `docs/starry-reports/strategy.json` in the tgoskits repo. Read and update this file at the start and end of every evolve cycle.

## Top-Level Structure

```json
{
  "version": 1,
  "last_updated": "2026-04-14T01:00:00Z",
  "coverage": { ... },
  "effectiveness": { ... },
  "targets": { ... },
  "analysis_queue": { ... },
  "reviews": { ... },
  "review_config": { ... },
  "next_priorities": [ ... ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `version` | `integer` | Schema version. Always `1` for this revision. Increment on breaking changes. |
| `last_updated` | `string` (ISO 8601) | Timestamp of last write. Update on every save. |

---

## Section: `coverage`

Track which syscalls and subsystems have been tested or audited.

```json
{
  "coverage": {
    "tested_syscalls": ["mmap", "read", "write"],
    "untested_syscalls": ["pkey_mprotect", "io_uring_enter"],
    "tested_subsystems": ["memory", "scheduler"],
    "untested_subsystems": ["signal", "net"]
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `tested_syscalls` | `string[]` | Syscall names that have entries in `os/StarryOS/tests/known.json`. Use lowercase snake_case matching the `Sysno` variant names. |
| `untested_syscalls` | `string[]` | Syscall names present in the kernel dispatch table but absent from `known.json`. |
| `tested_subsystems` | `string[]` | Subsystem names that have been audited via the `audit-kernel` skill. Valid names: `"scheduler"`, `"memory"`, `"filesystem"`, `"net"`, `"signal"`, `"process"`, `"ipc"`, `"device"`, `"time"`. |
| `untested_subsystems` | `string[]` | Subsystem names not yet audited. |

**How to populate:**

1. Open `os/StarryOS/kernel/src/syscall/mod.rs` and extract every `Sysno::*` match arm from the dispatch function.
2. Read `os/StarryOS/tests/known.json` and collect its top-level keys.
3. Set `tested_syscalls` to the intersection of (1) and (2).
4. Set `untested_syscalls` to set(1) minus set(2).
5. On first initialization, place all subsystem names in `untested_subsystems` and leave `tested_subsystems` empty.

---

## Section: `effectiveness`

Track which techniques find bugs and at what rate. Use this data to prioritize techniques during target selection.

```json
{
  "effectiveness": {
    "techniques": {
      "man_page_comparison": { "runs": 12, "bugs_found": 4, "avg_bugs_per_run": 0.33 },
      "stub_detection": { "runs": 8, "bugs_found": 6, "avg_bugs_per_run": 0.75 }
    },
    "bug_categories": {
      "semantic": 3,
      "correctness": 5,
      "safety": 1,
      "concurrency": 0,
      "memory": 2
    }
  }
}
```

### `techniques`

Object mapping technique name to its statistics.

| Technique Name | Description |
|----------------|-------------|
| `"man_page_comparison"` | Compare kernel behavior against POSIX/Linux man page specification |
| `"stub_detection"` | Identify handlers that return hardcoded values without real logic |
| `"pattern_scan"` | Grep-based scan for known anti-patterns (TODO, unimplemented!, unwrap on user input) |
| `"smp_sweeping"` | Run tests under SMP configuration to surface concurrency bugs |
| `"yield_injection"` | Insert yield points or delays to widen race windows |
| `"memory_pressure"` | Run tests under constrained memory to surface allocation failures |
| `"linux_source_comparison"` | Diff kernel handler logic against Linux kernel source |

Each technique value:

| Field | Type | Description |
|-------|------|-------------|
| `runs` | `integer` | Number of times this technique has been applied. |
| `bugs_found` | `integer` | Total bugs discovered using this technique. |
| `avg_bugs_per_run` | `float` | Computed as `bugs_found / runs`. Recompute on every update. |

### `bug_categories`

Object mapping category name to total count of confirmed bugs in that category.

| Category | Description |
|----------|-------------|
| `"semantic"` | Behavior differs from POSIX/Linux specification but does not crash |
| `"correctness"` | Wrong return value, wrong errno, wrong side effect |
| `"safety"` | Memory safety violation, use-after-free, buffer overflow |
| `"concurrency"` | Race condition, deadlock, lock ordering violation |
| `"memory"` | Leak, double free, incorrect page table management |

**Update rule:** After every `hunt-bugs` or `audit-kernel` cycle, increment `runs` for each technique used and `bugs_found` for each confirmed bug. Recompute `avg_bugs_per_run`. Increment the appropriate `bug_categories` entry for each new confirmed bug.

---

## Section: `targets`

Track progress toward competition goals. Each sub-object represents one goal.

```json
{
  "progress": {
    "bugs": {
      "found": 47,
      "fixed": 3,
      "categories_covered": 3,
      "category_gaps": ["concurrency", "memory"]
    },
    "performance": {
      "benchmarks_run": 0,
      "best_improvement_pct": null,
      "best_improvement_area": null
    },
    "applications": {
      "tested": [],
      "working": [],
      "blocked": []
    },
    "features": {
      "items": []
    }
  }
}
```

### `bugs`

| Field | Type | Description |
|-------|------|-------------|
| `found` | `integer` | Number of confirmed bugs so far. No upper limit — keep finding more. |
| `fixed` | `integer` | Number of bugs with verified fixes. |
| `categories_covered` | `integer` | Number of categories in `effectiveness.bug_categories` with count > 0. |
| `category_gaps` | `string[]` | Categories with 0 bugs found — these are priority areas. |

### `performance`

| Field | Type | Description |
|-------|------|-------------|
| `benchmarks_run` | `integer` | Number of benchmark categories measured. |
| `best_improvement_pct` | `float \| null` | Best measured improvement percentage. `null` until a benchmark has been run. |
| `area` | `string \| null` | Subsystem or operation where improvement was measured (e.g., `"pipe_throughput"`, `"context_switch"`). `null` until set. |
| `met` | `boolean` | `true` when `improvement_pct` is not null and represents a meaningful improvement (typically >= 10%). |

### `application`

| Field | Type | Description |
|-------|------|-------------|
| `needed` | `boolean` | Whether an application compatibility result is required. Always `true`. |
| `app_name` | `string \| null` | Name of the tested application (e.g., `"nginx"`, `"redis"`, `"python3"`). `null` until set. |
| `status` | `string \| null` | Current status: `"not_started"`, `"partial"`, `"functional"`, `"passing"`. `null` until set. |
| `met` | `boolean` | `true` when `status` is `"functional"` or `"passing"`. |

### `features`

| Field | Type | Description |
|-------|------|-------------|
| `items` | `string[]` | List of feature descriptions implemented during this cycle. Free-form text. |
| `met` | `boolean` | `true` when `items` is non-empty. |

**Update rules:**
- After `hunt-bugs` or `audit-kernel`: update `bugs.found`, `bugs.categories_covered`, recompute `bugs.met`.
- After `benchmark`: update `performance.improvement_pct`, `performance.area`, recompute `performance.met`.
- After `test-app`: update `application.app_name`, `application.status`, recompute `application.met`.
- After any feature implementation: append to `features.items`, recompute `features.met`.

---

## Section: `analysis_queue`

Manage the pipeline of targets awaiting investigation. Items move between queues as analysis progresses.

```json
{
  "analysis_queue": {
    "needs_deep": [
      {
        "target": "mremap",
        "reason": "3 divergences in sweep, touches shared page tables",
        "sweep_signals": ["wrong_errno", "missing_flag_support", "race_suspect"],
        "estimated_severity": "P1",
        "category_hint": "memory"
      }
    ],
    "swept_clean": ["getpid", "getuid", "getgid"],
    "swept_suspicious": [
      {
        "target": "ioctl",
        "reason": "large match arm with many unhandled commands",
        "depth": "medium",
        "bugs_in_sweep": 1
      }
    ]
  }
}
```

### `needs_deep`

Array of targets requiring full deep-mode investigation.

| Field | Type | Description |
|-------|------|-------------|
| `target` | `string` | Syscall or subsystem name. |
| `reason` | `string` | Human-readable explanation of why deep analysis is needed. |
| `sweep_signals` | `string[]` | Specific signals detected during sweep (e.g., `"wrong_errno"`, `"stub_detected"`, `"race_suspect"`, `"missing_flag_support"`, `"ignored_parameter"`). |
| `estimated_severity` | `string` | One of `"P0"`, `"P1"`, `"P2"`, `"P3"`. Guides prioritization. |
| `category_hint` | `string` | Expected bug category if confirmed. One of the keys from `effectiveness.bug_categories`. |

### `swept_clean`

Array of `string` values. Each is a syscall or subsystem name confirmed to have 0 divergences from Linux behavior in sweep mode. Do not re-sweep these unless the kernel source changes.

### `swept_suspicious`

Array of targets with signals but not enough evidence for deep mode.

| Field | Type | Description |
|-------|------|-------------|
| `target` | `string` | Syscall or subsystem name. |
| `reason` | `string` | Human-readable description of suspicious signals. |
| `depth` | `string` | One of `"medium"` (worth another pass) or `"blocked"` (needs infrastructure not yet available). |
| `bugs_in_sweep` | `integer` | Number of confirmed bugs found during the sweep. |

**Movement rules:**
- Sweep finds 0 divergences: add to `swept_clean`.
- Sweep finds 1 divergence or suspicious pattern: add to `swept_suspicious`.
- Sweep finds >= 2 divergences, concurrency signals, or shared-state access: add to `needs_deep`.
- Deep analysis completes on a `needs_deep` item: remove from `needs_deep`. If all bugs fixed and verified, add to `swept_clean`. If unresolved issues remain, move to `swept_suspicious` with `depth: "blocked"`.

---

## Section: `reviews`

Per-bug review tracking. Maps bug ID (string, e.g., `"BUG-mremap-001"`) to its review state.

```json
{
  "reviews": {
    "BUG-mremap-001": {
      "rounds": [
        {
          "type": "self-check",
          "result": "pass",
          "findings": ["Fix handles MREMAP_MAYMOVE correctly per man page"]
        },
        {
          "type": "kernel-review",
          "result": "pass",
          "agent": "kernel-reviewer",
          "agrees": true,
          "findings": ["Lock ordering correct", "No UB in unsafe block"]
        },
        {
          "type": "re-derive",
          "result": "agree",
          "agent": "claude-agent",
          "agrees": true,
          "divergence": null,
          "findings": ["Independently derived same fix approach"]
        },
        {
          "type": "regression",
          "result": "pass",
          "findings": ["All 47 existing tests pass", "Clippy clean"]
        }
      ],
      "confidence": "high",
      "total_rounds": 4
    }
  }
}
```

### Per-bug object

| Field | Type | Description |
|-------|------|-------------|
| `rounds` | `object[]` | Ordered list of review rounds executed for this bug. |
| `confidence` | `string` | Current confidence level: `"high"`, `"medium"`, or `"low"`. |
| `total_rounds` | `integer` | Length of `rounds`. |

### Round object

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | Round type: `"self-check"`, `"kernel-review"`, `"re-derive"`, `"reconcile"`, `"regression"`. |
| `result` | `string` | Outcome: `"pass"`, `"fail"`, `"agree"`, `"disagree"`, `"partial"`. |
| `agent` | `string \| undefined` | Agent that performed this round. Present for `"kernel-review"`, `"re-derive"`, `"reconcile"`. |
| `agrees` | `boolean \| undefined` | Whether this round agrees with the proposed fix. Present for `"kernel-review"`, `"re-derive"`. |
| `divergence` | `string \| null \| undefined` | Description of how the re-derivation diverged from the proposed fix. `null` if no divergence. Present for `"re-derive"`, `"reconcile"`. |
| `findings` | `string[] \| undefined` | Specific observations from this round. Present for all types when there are notable findings. |
| `action` | `string \| undefined` | Action taken as a result (e.g., `"revised_fix"`, `"escalated"`, `"accepted"`). Present when the round triggers a state change. |

---

## Section: `review_config`

Configuration for the adaptive review pipeline. Set once during initialization; adjust per-project as needed.

```json
{
  "review_config": {
    "re_derivation_agents": ["claude-agent", "codex-if-available"],
    "min_rounds": 3,
    "max_rounds": 8,
    "confidence_threshold": "high",
    "escalate_after_rounds": 6
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `re_derivation_agents` | `string[]` | `["claude-agent", "codex-if-available"]` | Agent types available for independent re-derivation. `"codex-if-available"` means use Codex only if the plugin is installed. |
| `min_rounds` | `integer` | `3` | Minimum number of review rounds per fix before it can reach `"high"` confidence. |
| `max_rounds` | `integer` | `8` | Maximum rounds before escalating to human review regardless of outcome. |
| `confidence_threshold` | `string` | `"high"` | Minimum confidence required to auto-commit a fix in autonomous mode. One of `"high"`, `"medium"`, `"low"`. |
| `escalate_after_rounds` | `integer` | `6` | Number of rounds after which the system escalates to a human if confidence has not reached the threshold. |

---

## Section: `next_priorities`

Computed ranked array of human-readable priority strings. Recompute after every cycle.

```json
{
  "next_priorities": [
    "benchmark: establish IO throughput baseline (perf target not met)",
    "test-app: attempt nginx compatibility (app target not met)",
    "deep: mremap - 3 divergences, memory category (needs_deep queue)",
    "hunt-bugs: concurrency category has 0 bugs (category gap)",
    "sweep: 14 untested syscalls in fs group (coverage expansion)"
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `next_priorities` | `string[]` | Ranked list of recommended next actions. Each entry is a human-readable sentence prefixed with the action type. |

**Computation rules (apply in order, higher = more urgent):**

1. **Unexplored areas** come first. Check `progress.performance.benchmarks_run` (0 = no benchmarks yet), `progress.applications.tested` (empty = no apps tested), `progress.bugs.category_gaps` (non-empty = categories with no bugs found). Generate a priority for each gap.
2. **Category gaps** next. For each category in `progress.bugs.category_gaps`, generate a priority to hunt bugs in that category.
3. **Analysis queue depth**. For each item in `analysis_queue.needs_deep`, generate a priority entry sorted by `estimated_severity` (P0 first).
4. **Coverage expansion**. If `coverage.untested_syscalls` is non-empty, generate sweep priorities grouped by syscall subsystem (fs, mm, net, process, signal, time).
5. Limit the array to 10 entries. Drop lower-priority items beyond that.

---

## Initialization

When `docs/starry-reports/strategy.json` does not exist, generate it:

1. Scan `os/StarryOS/kernel/src/syscall/mod.rs` for all `Sysno::*` match arms. Collect as the full syscall set.
2. Read `os/StarryOS/tests/known.json`. Collect its top-level keys as the tested syscall set.
3. Set `coverage.tested_syscalls` to the intersection. Set `coverage.untested_syscalls` to the difference.
4. Set `coverage.tested_subsystems` to `[]`. Set `coverage.untested_subsystems` to the full subsystem list: `["scheduler", "memory", "filesystem", "net", "signal", "process", "ipc", "device", "time"]`.
5. Set all `targets` sub-objects to their not-met defaults (`found: 0`, `met: false`, null optional fields).
6. Set `effectiveness.techniques` to an object with all seven technique keys, each with `{runs: 0, bugs_found: 0, avg_bugs_per_run: 0.0}`.
7. Set `effectiveness.bug_categories` to an object with all five category keys, each with value `0`.
8. Set `analysis_queue` to `{needs_deep: [], swept_clean: [], swept_suspicious: []}`.
9. Set `reviews` to `{}`.
10. Set `review_config` to the defaults documented above.
11. Compute `next_priorities` using the computation rules.
12. Set `version` to `1` and `last_updated` to the current ISO 8601 timestamp.

---

## Update Rules

Apply these rules after each cycle completes, before saving:

- **After `hunt-bugs`**: Update `coverage.tested_syscalls` (add any newly tested syscalls). Update `effectiveness.techniques` (increment `runs` and `bugs_found` for each technique used; recompute `avg_bugs_per_run`). Update `effectiveness.bug_categories` (increment counts for each confirmed bug). Update `analysis_queue` (move items based on findings). Update `targets.bugs` (recompute `found`, `categories_covered`, `met`).
- **After `audit-kernel`**: Move the audited subsystem from `coverage.untested_subsystems` to `coverage.tested_subsystems`. Update `effectiveness` as above. Update `analysis_queue` with any new findings.
- **After `benchmark`**: Set `targets.performance.improvement_pct` and `targets.performance.area`. Recompute `targets.performance.met`.
- **After `test-app`**: Set `targets.application.app_name` and `targets.application.status`. Recompute `targets.application.met`.
- **After fix + review**: Add or update the entry in `reviews` for the bug ID. Record each round as it completes. Set `confidence` based on round outcomes.
- **Always**: Recompute `next_priorities` using the priority computation rules. Update `last_updated` to the current timestamp.
