---
name: start-submission
description: This skill should be used when the user asks to "submit a fix", "prepare a PR", "submit upstream", "start submission", "push fix", "create PR", "prepare submission", "submit to tgoskits", "submit test", or wants to prepare a kernel fix and test case for upstream submission to rcore-os/tgoskits and rcore-os/linux-compatible-testsuit.
---

# Prepare Upstream Submission

Execute the full upstream submission pipeline automatically. Do all the work — clone, port, convert, verify, draft — and present the finished PR text to the user. The ONLY thing that must NOT be automated is `gh pr create`.

**Two PRs are always produced:**
1. Kernel fix → `rcore-os/tgoskits` branch `fixbug-based-dev`
2. Test case → `rcore-os/linux-compatible-testsuit`

**Hard rules:**
- NEVER run `gh pr create` — do everything else automatically
- PR language: **Chinese** (natural technical Chinese — write like a kernel developer)
- Forbidden: em dashes (—), "不仅...而且...", filler adjectives ("巧妙地", "优雅的"), AI-style structures
- Ask the user for the branch name before starting — never auto-generate it

## Step 1: Gather Info (ask the user)

Ask these three questions, then proceed without further prompting:
1. Which bug/fix is being submitted? (BUG-NNN, syscall name, or "the prlimit64 fix")
2. What branch name? (e.g., `b1`, `b2`, `fix-prlimit64`)
3. Is `starryos-linux-compatible-testsuit/` already cloned locally? (check automatically — if it exists, skip the clone)

## Step 2: Fresh Clone (execute automatically)

Run these commands via Bash:
1. `git clone https://github.com/rcore-os/tgoskits "tgoskits-${BRANCH}"` in the project root
2. `echo "tgoskits-${BRANCH}/" >> .git/info/exclude` so the parent repo ignores it
3. `cd "tgoskits-${BRANCH}" && git checkout -b "${BRANCH}" origin/fixbug-based-dev`

## Step 3: Port Minimal Fix (execute automatically)

1. Identify the changed files by reading the bug report or diffing the working repo
2. Copy ONLY the kernel fix files into the clean clone. Typical: `os/StarryOS/kernel/src/syscall/<subsystem>/<file>.rs`. Nothing else — no test harness, no starry-harness artifacts, no docs changes.
3. Run in the clean clone:
   - `cargo fmt`
   - `cargo xtask clippy --package starry-kernel`
   - `cargo starry build --arch riscv64`
4. If any of these fail, fix the issue and retry. Do not ask the user unless stuck.

## Step 4: Convert Test (execute automatically)

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py os/StarryOS/tests/cases/test_<name>.c /tmp/test_<name>_upstream.c`
2. Read the converted output. If the automatic conversion missed anything (complex macros, multi-file tests), fix it manually in the output file.
3. The converted test must:
   - Include `test_framework.h` (not `starry_test.h`)
   - Have a single `main()` with `TEST_START("name")` and `TEST_DONE()`
   - Use `CHECK()`, `CHECK_RET()`, `CHECK_ERR()` macros
4. Verify the converted test compiles and passes on the host: `gcc -static -O2 -Wall -I. -o /tmp/test_runner /tmp/test_<name>_upstream.c && /tmp/test_runner`

## Step 5: Prepare Test Repo (execute automatically)

1. Check if `starryos-linux-compatible-testsuit/` exists locally. If not, clone it: `git clone https://github.com/rcore-os/linux-compatible-testsuit starryos-linux-compatible-testsuit`
2. Fetch upstream: `git fetch origin`
3. Create branch: `git checkout -b "test-<syscall>" origin/main`
4. Detect the test directory (it may be `test_program/` or `tests/` — check what actually exists, do not hardcode)
5. Copy the converted test file into the test directory
6. Verify it compiles within the repo: `cd <test_dir> && gcc -static -O2 -Wall -I. -o test_<name> test_<name>.c && ./test_<name>`

## Step 6: Generate PR Drafts (execute automatically, present to user)

Write both PR drafts and present them. The user reviews and submits manually.

### Kernel Fix PR

```
标题: fix(<syscall>): <简短描述>

正文:
## 问题
<描述bug: 什么行为是错的, 在哪个文件哪一行>

## 原因
<root cause: 为什么会出错>

## 修复
<做了什么改动, 为什么这样改>

## 测试
- 测试用例: test_<name>.c (<N>/<N> 通过)
- Linux对比: 行为一致
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
- StarryOS (修复后): <N>/<N> 通过
```

### Output format

Present both PRs clearly to the user with:
- The `gh pr create` command they can copy-paste (but DO NOT execute it)
- The clean clone directory path so they can `cd` into it
- The test repo directory path

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py` | Convert starry_test.h → test_framework.h format |
| `${CLAUDE_PLUGIN_ROOT}/scripts/draft-pr.sh` | Generate PR draft markdown |
| `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` | Verify fix in clean clone |
