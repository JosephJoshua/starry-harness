---
name: start-submission
description: This skill should be used when the user asks to "submit a fix", "prepare a PR", "submit upstream", "start submission", "push fix", "create PR", "prepare submission", "submit to tgoskits", "submit test", or wants to prepare a kernel fix and test case for upstream submission to rcore-os/tgoskits and rcore-os/linux-compatible-testsuit.
---

# Prepare Upstream Submission

Execute the full upstream submission pipeline using parallel subagents. Do all the work — clone, port, convert, verify, test before/after, draft — and present the finished PR text to the user.

**Two PRs are always produced:**
1. Kernel fix → `rcore-os/tgoskits` branch `fixbug-based-dev`
2. Test case → `rcore-os/linux-compatible-testsuit`

**Hard rules:**
- NEVER run `gh pr create`
- PR language: **Chinese** (natural technical Chinese — write like a kernel developer)
- Forbidden: em dashes (—), "不仅...而且...", filler adjectives, AI-style structures
- Ask the user for the branch name — never auto-generate it

## Step 1: Gather Info

Ask these questions, then proceed without further prompting:
1. Which bug/fix? (BUG-NNN, syscall name, or description)
2. Branch name? (e.g., `b1`, `b6`, `fix-prlimit64`)
3. Check automatically if `starryos-linux-compatible-testsuit/` exists locally

## Step 2: Dispatch Subagents

Run two subagents in parallel:

### Subagent A: Port & Build (model: sonnet)

This agent handles the mechanical work — cloning, copying files, building, committing:

1. **Fresh clone**: `git clone https://github.com/rcore-os/tgoskits "tgoskits-${BRANCH}"` in the project root. Add `tgoskits-${BRANCH}/` to `.git/info/exclude`.
2. **Branch**: `cd "tgoskits-${BRANCH}" && git checkout -b "${BRANCH}" origin/fixbug-based-dev`
3. **Port fix**: Copy ONLY the changed kernel files from the working repo. No test harness, no starry-harness artifacts, no docs.
4. **Verify**: `cargo fmt && cargo xtask clippy --package starry-kernel && cargo starry build --arch riscv64`
5. **Commit**: `fix(<scope>): <description>` — no body, no footer, under 70 chars
6. **Convert test**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py` to convert from `starry_test.h` to `test_framework.h` format. Fix any conversion issues. Verify it compiles and passes on the host.
7. **Prepare test repo**: Clone or fetch `linux-compatible-testsuit`. Branch off `origin/dev`: `git checkout -b "test-<syscall>" origin/dev`. Detect the test directory (check what actually exists — `test_program/` or `tests/`). Copy converted test. Verify it compiles in-repo.
8. **Commit test**: `test(<scope>): <description>`

### Subagent B: Review Changes (model: opus)

This agent reviews the fix independently with fresh context:

1. Read the bug report and man page
2. Read the diff of the ported fix (from Subagent A's clean clone)
3. Verify: does the fix address the root cause? Are there edge cases missed?
4. Check: Rust idioms, safety, code reuse, API consistency
5. Check: does the test actually cover the bug's failure mode?
6. Produce a verdict: PASS / REVISE with specific issues

If Subagent B says REVISE, address the issues before proceeding.

## Step 3: Run Before/After Tests

After Subagent A finishes and Subagent B passes, run the upstream test suite to prove the fix works:

### Before (without fix)

The test repo's `run_all_tests.sh` needs a tgoskits repo to build against. It auto-detects `../tgoskits` or accepts `--tgoskits DIR`.

1. Run `run_all_tests.sh` in the test repo, pointing it at the clean clone **before the fix commit**:
```bash
cd starryos-linux-compatible-testsuit
git stash  # stash the new test temporarily
cd ../tgoskits-${BRANCH}
git stash  # stash the fix temporarily
cd ../starryos-linux-compatible-testsuit
bash run_all_tests.sh --tgoskits ../tgoskits-${BRANCH} --arch riscv64
```
Record the results — the new test should FAIL (proving the bug exists in the baseline).

### After (with fix)

2. Re-apply the fix and the new test:
```bash
cd ../tgoskits-${BRANCH} && git stash pop
cd ../starryos-linux-compatible-testsuit && git stash pop
bash run_all_tests.sh --tgoskits ../tgoskits-${BRANCH} --arch riscv64
```
Record the results — the new test should now PASS. No other tests should regress.

### Cleanup

3. After testing, clean up any copies of the tgoskits clone that `run_all_tests.sh` may have created inside the test repo directory. The rule:
   - **Keep**: `tgoskits-${BRANCH}/` in the project root (the user needs this for the PR)
   - **Keep**: `starryos-linux-compatible-testsuit/` in the project root (the user needs this for the test PR)
   - **Remove**: any `tgoskits-*` directory that appeared INSIDE `starryos-linux-compatible-testsuit/` during the test run (these are working copies, not needed)
   - **Remove**: temporary rootfs copies, build artifacts from the test run

```bash
# Clean up inner copies only
rm -rf starryos-linux-compatible-testsuit/tgoskits-*/
# Clean up temp rootfs copies if run_all_tests.sh created them
rm -f starryos-linux-compatible-testsuit/*.img starryos-linux-compatible-testsuit/work_rootfs_*.img
```

## Step 4: Generate PR Drafts

Write both PR drafts and present them to the user.

### Kernel Fix PR

```
标题: fix(<syscall>): <简短描述>

正文:
## 问题
<什么行为是错的, 在哪个文件哪一行>

## 原因
<为什么会出错>

## 修复
<做了什么改动, 为什么这样改>

## 测试
- 测试用例: test_<name>.c (<N>/<N> 通过)
- Linux对比: 行为一致
- run_all_tests.sh: 修复前新测试FAIL, 修复后PASS, 无回归
- clippy/fmt: 通过
```

### Test Case PR

```
标题: test: 添加 <syscall> 测试用例

正文:
## 测试内容
<测试了哪些行为: 正常路径, 错误路径, 边界条件>

## 测试结果
- Linux: <N>/<N> 通过
- StarryOS (修复前): <N>/<N> (新测试FAIL)
- StarryOS (修复后): <N>/<N> 通过
```

### Output

Present to the user:
- Both PR drafts with copy-pasteable `gh pr create` commands (DO NOT execute)
- The `tgoskits-${BRANCH}/` directory path
- The `starryos-linux-compatible-testsuit/` directory path
- Before/after test results summary
- Reviewer verdict from Subagent B

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py` | Convert starry_test.h → test_framework.h format |
| `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` | Verify fix builds and boots |
