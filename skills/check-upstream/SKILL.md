---
name: check-upstream
description: This skill should be used when the user asks to "check upstream", "check PRs", "what's been fixed upstream", "overlap check", "check rcore-os PRs", "sync with upstream", "deduplicate fixes", or wants to see which bugs from known.json have already been fixed or claimed by upstream PRs in rcore-os/tgoskits and rcore-os/linux-compatible-testsuit.
---

# Check Upstream PR Status

Compares the local bug registry (`known.json`) against open and recently merged PRs in the upstream repos to identify:
- Bugs already fixed upstream (our fix is redundant)
- Bugs claimed in open PRs (wait or coordinate)
- Bugs safe to submit (no upstream overlap)

Run this **before starting a submission** and **periodically during development sessions** to avoid wasted work.

## When to Run

- Before every `start-submission` invocation
- At the start of an `evolve` session (after loading strategy)
- When the user asks "what should I work on next" — deprioritize bugs that are claimed upstream
- Weekly as a housekeeping check

## Execution

### Step 1: Fetch Upstream PRs

Use the `gh` CLI to list open and recently merged PRs:

```bash
# Open PRs targeting fixbug-based-dev
gh pr list --repo rcore-os/tgoskits --base fixbug-based-dev --state open --json number,title,body,files,commits,author --limit 50

# Recently merged PRs (last 30 days)
gh pr list --repo rcore-os/tgoskits --base fixbug-based-dev --state merged --json number,title,body,files,mergedAt --limit 50

# Test repo PRs
gh pr list --repo rcore-os/linux-compatible-testsuit --state open --json number,title,body --limit 20
gh pr list --repo rcore-os/linux-compatible-testsuit --state merged --json number,title,body,mergedAt --limit 20
```

### Step 2: Extract Syscall Mentions

For each PR, extract which syscalls it fixes by:
1. Parsing the PR title (e.g., `fix(prlimit64): ...` → prlimit64)
2. Parsing the changed files (e.g., `os/StarryOS/kernel/src/syscall/fs/io.rs` → io-related syscalls)
3. Searching the PR body and commit messages for syscall names

### Step 3: Cross-Reference Against known.json

Read `os/StarryOS/tests/known.json`. For each buggy/broken syscall:
1. Check if any upstream PR touches the same file
2. Check if any upstream PR mentions the same syscall by name
3. Classify:
   - **MERGED**: upstream already fixed this → mark as "redundant, do not submit"
   - **OPEN**: upstream has a PR claiming this → mark as "claimed, wait or coordinate"
   - **SAFE**: no upstream activity → safe to submit our fix

### Step 4: Update Strategy

Update `docs/starry-reports/strategy.json`:
- Add an `upstream_status` section tracking overlap
- Deprioritize bugs that are MERGED or OPEN upstream
- Reprioritize SAFE bugs to the top of the queue

### Step 5: Report

Present a clear table to the user:

```
Upstream Overlap Check (as of YYYY-MM-DD)

MERGED (do not submit):
  accept4 (peer_addr) — PR #203 by yks23, merged 2026-04-15

CLAIMED (open PR, not yet merged):
  lseek negative offset — PR #204 by ZhiyuanSue (commit ffeabdc)
  fcntl_catchall — PR #204 by ZhiyuanSue (commits 56c2822, 3a3a8bd)
  ...

SAFE (no upstream overlap):
  sigaltstack off-by-one
  getgroups size=0
  clock_gettime invalid clock_id
  ftruncate negative length
  pwrite64 negative offset
  getrandom invalid flags
  ...
```

Also update the journal: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/journal-entry.sh NOTE "Upstream overlap check" "<summary>"`

## Notes

- Large omnibus PRs (like PR #204 with 15 commits) may claim many syscalls at once. Parse each commit individually.
- A PR being OPEN doesn't mean it will be merged — it may be rejected or stale. If it's been open for >2 weeks with no activity, consider it soft-claimed but not guaranteed.
- If our fix is better than the upstream PR's fix (more tests, cleaner code, catches edge cases they missed), it may still be worth submitting. Note this in the report.
- Always check `fixbug-based-dev` branch, not `main` or `dev` — that's where fix PRs target.
