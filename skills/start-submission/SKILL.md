---
name: start-submission
description: This skill should be used when the user asks to "submit a fix", "prepare a PR", "submit upstream", "start submission", "push fix", "create PR", "prepare submission", "submit to tgoskits", "submit test", or wants to prepare a kernel fix and test case for upstream submission to rcore-os/tgoskits and rcore-os/linux-compatible-testsuit.
---

# Prepare Upstream Submission

Prepares a kernel fix and its test case for submission as two upstream PRs. This skill handles the entire workflow: fresh clone, minimal fix port, test format conversion, verification, and PR draft generation.

**Two PRs are always needed:**
1. Kernel fix → `rcore-os/tgoskits` branch `fixbug-based-dev`
2. Test case → `rcore-os/linux-compatible-testsuit`

**Hard rules:**
- NEVER run `gh pr create` — only output the PR title and body for manual review
- PR language is **Chinese** — natural technical Chinese, not marketing
- Forbidden patterns: no em dashes (—), no "不仅...而且...", no filler adjectives ("巧妙地", "优雅的"), no AI-style structures
- Ask the user for the branch name — never auto-generate it

## Step 1: Ask the User

Before doing anything, ask:
1. Which bug is being submitted? (BUG-NNN or syscall name)
2. What branch name to use? (e.g., `b1`, `b2`, `fix-prlimit64`)
3. Is the linux-compatible-testsuit repo already cloned locally?

## Step 2: Fresh Clone for Kernel Fix

The working repo has too many local artifacts. Create a clean tree:

```bash
BRANCH="<user-provided-name>"
cd "$CLAUDE_PROJECT_DIR"
git clone https://github.com/rcore-os/tgoskits "tgoskits-${BRANCH}"
cd "tgoskits-${BRANCH}"
git checkout -b "$BRANCH" origin/fixbug-based-dev
```

Add `tgoskits-${BRANCH}/` to `.git/info/exclude` in the parent repo so it's ignored.

## Step 3: Port Minimal Fix

Copy ONLY the changed kernel files from the working repo to the clean clone. No test harness code, no starry-harness artifacts, no unrelated changes.

Typical files to port:
- `os/StarryOS/kernel/src/syscall/<subsystem>/<file>.rs` — the actual fix
- Nothing else

Verify in the clean clone:
```bash
cd "tgoskits-${BRANCH}"
cargo fmt
cargo xtask clippy --package starry-kernel
cargo starry build --arch riscv64
```

## Step 4: Convert Test to Upstream Format

The upstream test repo uses `test_framework.h` (CHECK/CHECK_RET/CHECK_ERR macros, single main(), TEST_START/TEST_DONE). Our local tests use `starry_test.h` (TEST/TEND blocks, EXPECT_* macros).

**Automatic conversion:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py \
  os/StarryOS/tests/cases/test_<name>.c \
  /tmp/test_<name>_upstream.c
```

**Always manually verify the converted test:**
1. Check that the conversion is correct — complex tests may need adjustment
2. The upstream format uses `CHECK(condition, "message")` not `EXPECT_EQ(a, b)`
3. Upstream tests have a single `main()` with `TEST_START("name")` and `TEST_DONE()`
4. Each test file tests one syscall or syscall family

**Upstream test style conventions:**
- Include `test_framework.h` (not `starry_test.h`)
- Use `_GNU_SOURCE` for Linux-specific syscalls
- Test both positive cases AND error paths with specific errno checks
- Use independent observation methods (e.g., `stat()` after `write()` to confirm persistence)
- Comments in Chinese are fine

## Step 5: Prepare Test Repo

```bash
# Clone or use existing local clone
TESTSUIT_DIR="$CLAUDE_PROJECT_DIR/starryos-linux-compatible-testsuit"
if [ ! -d "$TESTSUIT_DIR" ]; then
  git clone https://github.com/rcore-os/linux-compatible-testsuit "$TESTSUIT_DIR"
fi

# Sync from upstream
cd "$TESTSUIT_DIR"
git fetch origin
git checkout -b "test-<syscall>" origin/main

# Copy converted test — detect the actual test directory name
# (it may be test_program/ or tests/ — check what exists)
TEST_DIR=$(ls -d test_program tests 2>/dev/null | head -1)
cp /tmp/test_<name>_upstream.c "$TEST_DIR/"
```

**Note:** `run_all_tests.sh` auto-discovers `.c` files in the test directory. No manual registration needed — just drop the file in.

**Verify the test compiles and passes:**
```bash
cd "$TEST_DIR"
gcc -static -O2 -Wall -I. -o test_<name> test_<name>.c
./test_<name>  # Must pass on Linux
```

## Step 6: Generate PR Drafts

Generate two PR drafts. Output the title and body text — NEVER call `gh pr create`.

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

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py` | Convert starry_test.h → test_framework.h format |
| `${CLAUDE_PLUGIN_ROOT}/scripts/draft-pr.sh` | Generate PR draft markdown (never auto-submits) |
| `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` | Verify fix in clean clone |
