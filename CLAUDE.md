# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`starry-harness` is a Claude Code plugin for AI-driven StarryOS kernel improvement. It provides skills, agents, hooks, and shell scripts for systematic bug hunting, performance benchmarking, application compatibility testing, code quality review, and structured reporting.

**Target project**: [tgoskits](https://github.com/rcore-os/tgoskits) — the StarryOS kernel lives at `os/StarryOS/` in that repo.

## Plugin Structure

```
.claude-plugin/plugin.json    ← Manifest (name: "starry-harness")
skills/                       ← 7 skills (auto-discovered SKILL.md files)
  hunt-bugs/                  ← Core bug hunting cycle
  audit-kernel/               ← Deep kernel internal audit (scheduler, memory, concurrency)
  benchmark/                  ← Performance benchmarking vs Linux
  test-app/                   ← Linux application compatibility
  review-quality/             ← Code quality gate
  report/                     ← Structured reporting + journal
  evolve/                     ← Self-evolving loop: target selection, sweep/deep, adaptive review
agents/                       ← 3 agent definitions (.md files)
  linux-comparator.md         ← Docker Linux test runner + diff
  kernel-reviewer.md          ← Code quality reviewer (read-only)
  bug-triager.md              ← Bug classification + prioritization
hooks/
  hooks.json                  ← SessionStart hook config
  scripts/session-load.sh     ← Loads journal + bug registry + strategy at session start
scripts/                      ← Shell infrastructure
  linux-ref-test.sh           ← Compile + run C test in Docker Linux
  man-lookup.sh               ← Fetch syscall man pages
  journal-entry.sh            ← Append to work journal
```

## Developing This Plugin

### Skill files
- `skills/<name>/SKILL.md` — YAML frontmatter (`name`, `description`) + markdown body
- Description must be third-person with trigger phrases
- Body in imperative/instructional style, target ~1500-2000 words
- Detailed content goes in `references/*.md`, not the SKILL.md body
- All internal paths use `${CLAUDE_PLUGIN_ROOT}` — resolved at runtime by Claude Code

### Agent files
- `agents/<name>.md` — YAML frontmatter (`name`, `description`, `model`, `color`, `tools`) + system prompt
- Description needs `<example>` blocks showing when to trigger
- System prompt in second person ("You are...")

### Hook scripts
- Must use `${CLAUDE_PLUGIN_ROOT}` for all intra-plugin paths
- `${CLAUDE_PROJECT_DIR}` for project-root paths at runtime
- Exit 0 = success (stdout shown to Claude), exit 2 = blocking error

### Shell scripts
- All scripts must be executable (`chmod +x`)
- Use `set -euo pipefail`
- Reference `${CLAUDE_PROJECT_DIR}` for project files (e.g., `os/StarryOS/tests/`)

## Testing Locally

```bash
# Test from the tgoskits project directory:
claude --plugin-dir /path/to/this/repo

# Then trigger skills with natural language:
# "find bugs in StarryOS"
# "benchmark syscall latency"
# "run Nginx on StarryOS"
```

## Publishing

This repo is registered as a custom Claude Code marketplace. Users install with:
1. Add to `~/.claude/settings.json` under `extraKnownMarketplaces`
2. Enable with `"starry-harness@starry-harness": true` in `enabledPlugins`

## Key Conventions

- `${CLAUDE_PLUGIN_ROOT}` — always use this for paths to plugin files (scripts, references, etc.)
- `${CLAUDE_PROJECT_DIR}` — always use this for paths to the target project (os/StarryOS/, docs/, etc.)
- Skills dispatch agents by name — agent file names must match what skills reference
- The `report` skill and `journal-entry.sh` script write to `docs/starry-reports/` in the target project
- `os/StarryOS/tests/known.json` is the bug registry — skills read/update it
