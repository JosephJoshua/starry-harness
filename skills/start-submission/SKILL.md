---
name: start-submission
description: This skill should be used when the user asks to "submit a fix", "prepare a PR", "submit upstream", "start submission", "push fix", "create PR", "prepare submission", "submit to tgoskits", "submit test", or wants to prepare a kernel fix and test case for upstream submission to rcore-os/tgoskits.
---

# Prepare Upstream Submission

Execute the full upstream submission pipeline using parallel subagents. Tests and fixes now live in the **same repo** (`rcore-os/tgoskits`). There is no separate test repository — test cases go in `test-suit/starryos/normal/<case-name>/` and are auto-run by CI via `cargo starry test qemu`.

**One PR to `rcore-os/tgoskits` branch `fixbug-based-dev`** containing both the kernel fix and the test case.

**Hard rules:**
- NEVER run `gh pr create`
- PR language: **Chinese** (natural technical Chinese — write like a kernel developer)
- Forbidden: em dashes (—), "不仅...而且...", filler adjectives, AI-style structures
- Ask the user for the branch name — never auto-generate it

## Step 1: Gather Info and Check Upstream

Ask these questions, then proceed without further prompting:
1. Which bug/fix? (BUG-NNN, syscall name, or description)
2. Branch name? (e.g., `b1`, `b6`, `fix-prlimit64`)

**Before proceeding**, run the `check-upstream` skill to verify this bug hasn't been fixed or claimed upstream. If MERGED → abort. If OPEN → warn and ask whether to proceed.

## Step 2: Dispatch Subagents

### Subagent A: Port, Build, and Create Test Case (model: sonnet)

1. **Fresh clone**: `git clone https://github.com/rcore-os/tgoskits "tgoskits-${BRANCH}"` in the project root. Add `tgoskits-${BRANCH}/` to `.git/info/exclude`.
2. **Branch**: `cd "tgoskits-${BRANCH}" && git checkout -b "${BRANCH}" origin/fixbug-based-dev`
3. **Port fix**: Copy ONLY the changed kernel files from the working repo. No harness artifacts, no docs.
4. **Verify build**: `cargo fmt && cargo xtask clippy --package starry-kernel && cargo starry build --arch riscv64`
5. **Create test case** following the upstream format (see `test-suit/starryos/GUIDE.md`):

   Create the test directory:
   ```
   test-suit/starryos/normal/test-<syscall>/
     c/
       CMakeLists.txt
       src/
         main.c
     qemu-riscv64.toml
   ```

   **CMakeLists.txt**:
   ```cmake
   cmake_minimum_required(VERSION 3.20)
   project(test_<syscall> C)
   set(CMAKE_C_STANDARD 11)
   set(CMAKE_C_STANDARD_REQUIRED ON)
   add_executable(test_<syscall> src/main.c)
   target_compile_options(test_<syscall> PRIVATE -Wall -Wextra)
   install(TARGETS test_<syscall> RUNTIME DESTINATION usr/bin)
   ```

   **src/main.c**: Convert the local test from `starry_test.h` format to a standalone C program that:
   - Prints `PASS` / `FAIL` lines to stdout
   - Returns 0 on all-pass, 1 on any failure
   - Ends with a clear success line like `All tests passed!` (for `success_regex`)
   - Does NOT depend on `starry_test.h` or `test_framework.h` — use plain printf

   **qemu-riscv64.toml**: Copy from an existing test case (e.g., `smoke/qemu-riscv64.toml`) and change:
   - `shell_init_cmd = "/usr/bin/test_<syscall>"`
   - `success_regex` to match the test's success output
   - `fail_regex` to catch panics and test failures
   - Only create TOML files for architectures actually validated

   If the test needs `stdio.h` or other libc headers, add a `prebuild.sh`:
   ```sh
   #!/bin/sh
   set -eu
   apk add gcc musl-dev
   ```

6. **Run the test before fix** (verify the bug exists in baseline):
   ```bash
   cd tgoskits-${BRANCH}
   git stash  # stash the fix temporarily
   cargo starry test qemu -t riscv64 -c test-<syscall>
   # Should FAIL (proving the bug exists)
   git stash pop
   ```

7. **Run the test after fix**:
   ```bash
   cargo starry test qemu -t riscv64 -c test-<syscall>
   # Should PASS
   ```

8. **Run full regression** to check nothing else broke:
   ```bash
   cargo starry test qemu -t riscv64
   ```

9. **Commit** with conventional commit — no body:
   - `fix(<scope>): <description>`
   - Include both the kernel fix and the test case in one commit, or split into two:
     - `fix(<scope>): <description>` for the kernel change
     - `test(<scope>): add <syscall> test case` for the test

### Subagent B: Review Changes (model: opus)

Reviews the fix independently with fresh context:

1. Read the bug report and man page
2. Read the diff of the ported fix
3. Verify: does the fix address the root cause? Edge cases?
4. Check the test: does it actually exercise the bug's failure mode?
5. Check the TOML config: are `success_regex` and `fail_regex` specific enough?
6. Produce verdict: PASS / REVISE with specific issues

If Subagent B says REVISE, address the issues before proceeding.

## Step 3: Cleanup

After testing:
- **Keep**: `tgoskits-${BRANCH}/` in the project root (user needs it for the PR)
- **Remove**: any temporary build artifacts, rootfs copies from test runs

## Step 4: Generate PR Draft

Write the PR draft and present it to the user. The user reviews and submits manually.

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
- 新增测试: test-suit/starryos/normal/test-<syscall>/
- cargo starry test qemu -t riscv64 -c test-<syscall>: 修复前FAIL, 修复后PASS
- 全量回归: cargo starry test qemu -t riscv64: 无回归
- clippy/fmt: 通过
```

### Output

Present to the user:
- PR draft with copy-pasteable `gh pr create` command (DO NOT execute)
- The `tgoskits-${BRANCH}/` directory path
- Before/after test results
- Reviewer verdict from Subagent B

## Key Scripts

| Script | Purpose |
|--------|---------|
| `${CLAUDE_PLUGIN_ROOT}/scripts/convert-test.py` | Convert starry_test.h format to standalone C (starting point — may need manual adjustment for the new format) |
| `${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.sh` | Verify fix builds and boots |
