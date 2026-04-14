#!/usr/bin/env bash
# pipeline.sh — Full multi-arch test pipeline: compile → inject → build → QEMU → report
#
# Usage:
#   pipeline.sh <test_name> [--arch ARCH]
#
# Supported architectures: riscv64 (default), aarch64, x86_64, loongarch64
#
# This replaces the single-arch scripts in os/StarryOS/tools/ with a unified
# multi-arch pipeline. It handles: cross-compilation, rootfs injection,
# kernel build, QEMU boot, and result parsing.
#
# Requires: Docker, QEMU, musl cross-compilers, CLAUDE_PROJECT_DIR
set -euo pipefail

TEST_NAME=""
ARCH="riscv64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    -*)     echo "Unknown flag: $1" >&2; exit 1 ;;
    *)      TEST_NAME="$1"; shift ;;
  esac
done

if [ -z "$TEST_NAME" ]; then
  echo "Usage: pipeline.sh <test_name> [--arch ARCH]" >&2
  exit 1
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
STARRY_DIR="$PROJECT_ROOT/os/StarryOS"
TESTS_DIR="$STARRY_DIR/tests"
RESULTS_DIR="$TESTS_DIR/results"
mkdir -p "$RESULTS_DIR" "$TESTS_DIR/bin"

# ── Arch mappings ───────────────────────────────────────────────
case "$ARCH" in
  riscv64)
    TARGET_TRIPLE="riscv64gc-unknown-none-elf"
    MUSL_PREFIX="riscv64-linux-musl"
    OBJCOPY_ARCH="riscv64"
    QEMU_BIN="qemu-system-riscv64"
    QEMU_ARGS="-machine virt -bios default"
    KERNEL_FMT="binary"   # needs objcopy to raw binary
    ;;
  aarch64)
    TARGET_TRIPLE="aarch64-unknown-none-softfloat"
    MUSL_PREFIX="aarch64-linux-musl"
    OBJCOPY_ARCH="aarch64"
    QEMU_BIN="qemu-system-aarch64"
    QEMU_ARGS="-cpu cortex-a72 -machine virt"
    KERNEL_FMT="binary"
    ;;
  x86_64)
    TARGET_TRIPLE="x86_64-unknown-none"
    MUSL_PREFIX="x86_64-linux-musl"
    OBJCOPY_ARCH="x86_64"
    QEMU_BIN="qemu-system-x86_64"
    QEMU_ARGS="-machine q35"
    KERNEL_FMT="elf"      # x86_64 uses ELF directly
    ;;
  loongarch64)
    TARGET_TRIPLE="loongarch64-unknown-none-softfloat"
    MUSL_PREFIX="loongarch64-linux-musl"
    OBJCOPY_ARCH="loongarch64"
    QEMU_BIN="qemu-system-loongarch64"
    QEMU_ARGS="-machine virt"
    KERNEL_FMT="binary"
    ;;
  *)
    echo "Error: unsupported arch '$ARCH'. Supported: riscv64, aarch64, x86_64, loongarch64" >&2
    exit 1
    ;;
esac

COMPILER="${MUSL_PREFIX}-gcc"
KERNEL_ELF="$PROJECT_ROOT/target/${TARGET_TRIPLE}/release/starryos"
KERNEL_BIN="$TESTS_DIR/bin/starryos-${ARCH}.bin"
DISK_IMG="$STARRY_DIR/make/disk.img"
RESULT_FILE="$RESULTS_DIR/${TEST_NAME}.txt"
TIMEOUT_SECS=60

echo "╔═══════════════════════════════════════════════════╗"
echo "║  Pipeline: $TEST_NAME ($ARCH)"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Compile ─────────────────────────────────────────────
SRC="$TESTS_DIR/cases/${TEST_NAME}.c"
if [ ! -f "$SRC" ]; then
  echo "Error: test source not found: $SRC" >&2
  exit 1
fi

echo ">>> Step 1/4: Compile ($COMPILER)"
if command -v "$COMPILER" &>/dev/null; then
  "$COMPILER" -static -O2 -Wall \
    -I"$TESTS_DIR/cases" \
    -o "$TESTS_DIR/bin/$TEST_NAME" "$SRC"
else
  echo "[compile] $COMPILER not found — trying Docker..." >&2
  docker run --rm \
    --platform linux/amd64 \
    -v "$TESTS_DIR/cases:/src:ro" \
    -v "$TESTS_DIR/bin:/out" \
    ubuntu:24.04 sh -c "
      apt-get update -qq && apt-get install -y -qq gcc-${MUSL_PREFIX/-/_} >/dev/null 2>&1 || true
      ${COMPILER} -static -O2 -Wall -I/src -o /out/$TEST_NAME /src/${TEST_NAME}.c 2>&1
    "
fi
echo "[compile] Output: tests/bin/$TEST_NAME"
echo ""

# ── Step 2: Inject ──────────────────────────────────────────────
echo ">>> Step 2/4: Inject into rootfs"
if [ ! -f "$DISK_IMG" ]; then
  echo "Error: $DISK_IMG not found. Run 'cargo starry rootfs --arch $ARCH' first." >&2
  exit 1
fi

docker run --rm --privileged \
  -v "$DISK_IMG:/disk.img" \
  -v "$TESTS_DIR/bin/$TEST_NAME:/test_bin:ro" \
  alpine:3.20 sh -c "
    mkdir -p /mnt &&
    mount -o loop /disk.img /mnt &&
    cp /test_bin /mnt/starry_test &&
    chmod 755 /mnt/starry_test &&
    printf '#!/bin/sh\necho \"=== StarryOS Test Runner ===\"\n/starry_test\nEXIT_CODE=\$?\necho \"=== Test Runner Exit: \$EXIT_CODE ===\"\npoweroff -f\n' > /mnt/test_runner.sh &&
    chmod 755 /mnt/test_runner.sh &&
    sync && umount /mnt
  "
echo "[inject] Done."
echo ""

# ── Step 3: Build kernel ────────────────────────────────────────
echo ">>> Step 3/4: Build StarryOS ($ARCH)"
(cd "$PROJECT_ROOT" && cargo starry build --arch "$ARCH") 2>&1 | tail -5

if [ "$KERNEL_FMT" = "binary" ]; then
  echo "[build] Converting ELF to raw binary..."
  rust-objcopy --binary-architecture="$OBJCOPY_ARCH" "$KERNEL_ELF" --strip-all -O binary "$KERNEL_BIN"
  KERNEL_ARG="$KERNEL_BIN"
else
  # x86_64 uses ELF directly
  KERNEL_ARG="$KERNEL_ELF"
fi
echo ""

# ── Step 4: QEMU boot ──────────────────────────────────────────
echo ">>> Step 4/4: Boot QEMU ($ARCH, timeout ${TIMEOUT_SECS}s)"

TIMEOUT_CMD="timeout"
command -v timeout &>/dev/null || TIMEOUT_CMD="gtimeout"
command -v "$TIMEOUT_CMD" &>/dev/null || { echo "Error: timeout/gtimeout required" >&2; exit 1; }

"$TIMEOUT_CMD" "$TIMEOUT_SECS" $QEMU_BIN \
  $QEMU_ARGS \
  -nographic -m 1G \
  -kernel "$KERNEL_ARG" \
  -device virtio-blk-pci,drive=disk0 \
  -drive "id=disk0,if=none,format=raw,file=$DISK_IMG" \
  2>&1 | tee "$RESULT_FILE" || true

# ── Parse results ───────────────────────────────────────────────
PASSES=$(grep -c '^PASS:' "$RESULT_FILE" 2>/dev/null || echo "0")
FAILS=$(grep -c '^FAIL:' "$RESULT_FILE" 2>/dev/null || echo "0")

echo ""
echo "============================="
echo "  Results: $TEST_NAME ($ARCH)"
echo "============================="
grep '^PASS:\|^FAIL:' "$RESULT_FILE" 2>/dev/null || echo "(no test output captured)"
echo "-----------------------------"
echo "  PASS: $PASSES  FAIL: $FAILS"
echo "============================="

# JSON result
JSON_FILE="$RESULTS_DIR/${TEST_NAME}.json"
printf '{\n  "test": "%s",\n  "arch": "%s",\n  "timestamp": "%s",\n  "pass": %s,\n  "fail": %s\n}\n' \
  "$TEST_NAME" "$ARCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PASSES" "$FAILS" > "$JSON_FILE"

echo "[pipeline] JSON results: $JSON_FILE"
