---
name: report
description: This skill should be used when the user asks to "generate report", "write bug report", "update journal", "create benchmark report", "summarize progress", "competition report", "status report", "write up findings", "log this", "journal entry", or wants to produce structured documentation of StarryOS development work for the OS competition submission.
---

# Structured Reporting and Journal System

Produce, maintain, and aggregate all structured reports tracking StarryOS development work. Every discovery, benchmark, compatibility test, and fix flows through this system, building the evidence base required for the OS competition submission.

## Report Types

Four distinct report categories feed into the competition deliverables. Each has a fixed directory, naming convention, and template.

### 1. Bug Reports

**Directory**: `docs/starry-reports/bugs/BUG-NNN-<syscall>.md`

Bug reports document behavioral divergences between StarryOS and Linux. They are the primary output of the hunt-bugs skill and form the backbone of the competition submission's "bug discovery" section.

Generate a bug report whenever:
- A syscall test reveals a behavioral difference from Linux
- Root cause analysis has been completed for a divergence
- A fix has been verified or a workaround documented

Each report must include classification metadata (category, severity, syscall, source location, status), a summary of the divergence, the relevant man page reference, a minimal reproducing test case, side-by-side Linux vs. StarryOS behavior, root cause analysis, the fix (if applied), and verification results.

Use the template from `references/templates.md` section "Bug Report Template".

### 2. Benchmark Reports

**Directory**: `docs/starry-reports/benchmarks/BENCH-NNN-<category>.md`

Benchmark reports capture performance measurements before and after optimizations. They are the primary output of the benchmark skill and feed the "performance improvements" section of the competition submission.

Generate a benchmark report whenever:
- A baseline performance measurement has been taken on both Linux and StarryOS
- An optimization has been applied and re-measured
- A significant performance characteristic has been documented (even without optimization)

Each report must include the benchmark category, methodology (tool, parameters, environment), Linux baseline numbers, StarryOS before-optimization numbers, description of the optimization applied, StarryOS after-optimization numbers, the computed improvement ratio, and analysis of what changed and why.

Use the template from `references/templates.md` section "Benchmark Report Template".

### 3. App Compatibility Reports

**Directory**: `docs/starry-reports/apps/APP-NNN-<name>.md`

App compatibility reports track attempts to run mainstream applications on StarryOS. They are the primary output of the test-app skill and feed the "application compatibility" section of the competition submission.

Generate an app compatibility report whenever:
- An application has been tested against StarryOS (whether it succeeds or fails)
- A syscall gap analysis has been performed for a target application
- Fixes have been applied to make an application work (or partially work)

Each report must include the application identity and version, the full set of syscalls the application requires, a gap analysis mapping required syscalls to their StarryOS implementation status, build instructions for cross-compilation, test results with pass/fail detail, fixes that were required (with cross-references to bug reports), and the current status.

Use the template from `references/templates.md` section "App Compatibility Report Template".

### 4. Progress Summaries

Progress summaries are aggregated, competition-ready documents that pull together data from all other report types plus the journal. They are not stored in a numbered file -- produce them on demand when requested.

Generate a progress summary whenever:
- The user asks for a competition report or status overview
- A milestone has been reached (e.g., 10 bugs found, first app running)
- Preparation for a competition checkpoint or submission is underway

## Journal System

The journal is the activity log binding all work together. Every skill invocation across the entire starry-harness plugin must end with a journal entry.

### Location

`docs/starry-reports/journal.md`

### Appending Entries

Use the journal-entry script:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh <TYPE> "<title>" "<body>"
```

The script prepends entries (newest first) after the header separator. It creates the journal file and parent directories if they do not exist.

### Entry Types

| Type   | When to use |
|--------|-------------|
| `BUG`  | A bug has been discovered or a bug report written |
| `BENCH`| A benchmark has been run or a benchmark report written |
| `APP`  | An application compatibility test has been performed |
| `FIX`  | A bug fix has been implemented and verified |
| `FEAT` | A new feature or enhancement has been added to StarryOS |
| `NOTE` | General observations, decisions, or context worth preserving |

### Entry Format

The script produces entries in this format:

```
## YYYY-MM-DD HH:MM -- [TYPE] Title

Body text with details.
```

### Journal Discipline

- End every skill invocation with a journal entry summarizing what was done.
- Use `BUG` when discovering, not when fixing -- use `FIX` for the fix.
- Keep titles concise (under 80 characters). Put detail in the body.
- Reference report numbers in journal entries (e.g., "See BUG-003" or "See BENCH-001").
- For multi-step sessions, write one entry per major milestone rather than one per micro-step.

## Report Numbering

Report numbers are three-digit, zero-padded, and auto-incremented from existing reports in the target directory.

**Procedure to determine the next number**:

1. List all files in the target directory matching the pattern (`BUG-*.md`, `BENCH-*.md`, or `APP-*.md`).
2. Extract the numeric portion from each filename.
3. Find the maximum number.
4. Increment by one and zero-pad to three digits.
5. If the directory is empty, start at `001`.

Example: if `docs/starry-reports/bugs/` contains `BUG-001-pwritev2.md` and `BUG-002-mremap.md`, the next bug report is `BUG-003-<syscall>.md`.

## Progress Summary Generation

When generating a competition-ready progress summary, follow this aggregation procedure:

### Step 1: Gather Data

- Read all bug reports from `docs/starry-reports/bugs/`
- Read all benchmark reports from `docs/starry-reports/benchmarks/`
- Read all app compatibility reports from `docs/starry-reports/apps/`
- Read `os/StarryOS/tests/known.json` for the full bug registry (includes bugs not yet written up as reports)
- Read `docs/starry-reports/journal.md` for the activity log

### Step 2: Compute Metrics

**Bug discovery metrics**:
- Total bugs found (count distinct bugs across known.json and bug reports)
- Bugs by category (Concurrency, Memory, Safety, Semantic, Correctness)
- Bugs by severity (P0, P1, P2, P3)
- Bugs fixed vs. open
- Competition target: need 10 or more bugs across 2 or more categories

**Performance improvement metrics**:
- List each benchmark with before/after numbers and improvement ratio
- Highlight the largest improvement
- Competition target: need 50% or greater improvement in at least one area

**Application compatibility metrics**:
- Applications tested and their current status (working, partial, blocked)
- Syscalls unblocked by fixes
- Competition target: need 1 or more mainstream application running

**Feature metrics**:
- New features or enhancements implemented
- Lines of code changed (from git stats)

### Step 3: Produce the Summary

Use the template from `references/templates.md` section "Progress Summary Template". Include concrete numbers, cross-references to individual reports, and an honest assessment of what remains to be done. Flag any competition targets that have not yet been met.

## Cross-Referencing

Reports should link to each other to build a navigable knowledge base:
- Bug reports referenced from app compatibility reports when a bug blocks an application
- Benchmark reports referenced from bug reports when a fix also improves performance
- Journal entries reference report numbers for traceability
- The progress summary references all individual reports

Use relative Markdown links: `[BUG-003](../bugs/BUG-003-fcntl.md)`.

## Competition Submission Formatting

The progress summary is the top-level competition deliverable. Keep the following guidelines:
- Write in third person ("the implementation", "StarryOS", not "we" or "I")
- Lead with quantitative results before qualitative discussion
- Include reproduction steps for every claimed bug
- Include exact benchmark numbers with units, not just percentages
- Cite man page sections when describing expected Linux behavior
- Keep the summary under 3000 words; link to individual reports for details

## Reference

See `references/templates.md` for complete templates for all report types, naming conventions, and formatting guidelines.
