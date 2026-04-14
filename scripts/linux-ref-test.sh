#!/bin/bash
# linux-ref-test.sh — Run a C test case inside a Docker Linux container
# and capture output as the reference "expected" behavior.
#
# Usage:
#   linux-ref-test.sh <test_source.c> [output_file] [--arch ARCH]
#
# Supported architectures:
#   native  — compile and run natively (default, fastest)
#   riscv64 — cross-compile with riscv64 musl, run via qemu-user
#   aarch64 — cross-compile with aarch64 musl, run via qemu-user
#
# The test source MUST be self-contained (include starry_test.h inline or
# use only standard POSIX headers).
#
# Requires: docker
set -euo pipefail

TEST_SRC=""
OUTPUT="/dev/stdout"
ARCH="native"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    -*)     echo "Unknown flag: $1" >&2; exit 1 ;;
    *)
      if [ -z "$TEST_SRC" ]; then TEST_SRC="$1"
      else OUTPUT="$1"; fi
      shift ;;
  esac
done

if [ -z "$TEST_SRC" ]; then
  echo "Usage: linux-ref-test.sh <test_source.c> [output_file] [--arch native|riscv64|aarch64]" >&2
  exit 1
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
TESTS_DIR="$PROJECT_ROOT/os/StarryOS/tests"

if [ ! -f "$TEST_SRC" ]; then
  echo "Error: test source not found: $TEST_SRC" >&2
  exit 1
fi

# Resolve to absolute path
TEST_SRC="$(cd "$(dirname "$TEST_SRC")" && pwd)/$(basename "$TEST_SRC")"

# Architecture-specific config
case "$ARCH" in
  native)
    IMAGE_NAME="starry-linux-ref:native"
    PACKAGES="gcc libc6-dev musl-tools"
    COMPILE_CMD="gcc -static -o /test/runner /test/test_case.c -I/test 2>&1"
    RUN_CMD="/test/runner 2>&1"
    ;;
  riscv64)
    IMAGE_NAME="starry-linux-ref:riscv64"
    PACKAGES="gcc-riscv64-linux-gnu libc6-dev-riscv64-cross qemu-user-static"
    COMPILE_CMD="riscv64-linux-gnu-gcc -static -o /test/runner /test/test_case.c -I/test 2>&1"
    RUN_CMD="qemu-riscv64-static /test/runner 2>&1"
    ;;
  aarch64)
    IMAGE_NAME="starry-linux-ref:aarch64"
    PACKAGES="gcc-aarch64-linux-gnu libc6-dev-arm64-cross qemu-user-static"
    COMPILE_CMD="aarch64-linux-gnu-gcc -static -o /test/runner /test/test_case.c -I/test 2>&1"
    RUN_CMD="qemu-aarch64-static /test/runner 2>&1"
    ;;
  *)
    echo "Error: unsupported arch '$ARCH'. Use: native, riscv64, aarch64" >&2
    exit 1
    ;;
esac

# Build container image if it doesn't exist
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "[linux-ref] Building reference container ($ARCH)..." >&2
  BUILD_CTX=$(mktemp -d)
  trap 'rm -rf "$BUILD_CTX"' EXIT
  docker build -t "$IMAGE_NAME" -f - "$BUILD_CTX" <<DOCKERFILE
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    $PACKAGES && rm -rf /var/lib/apt/lists/*
WORKDIR /test
DOCKERFILE
fi

# Mount the test source and harness header, compile, and run
docker run --rm \
  -v "$TEST_SRC:/test/test_case.c:ro" \
  -v "$TESTS_DIR/cases/starry_test.h:/test/starry_test.h:ro" \
  "$IMAGE_NAME" \
  sh -c "$COMPILE_CMD && $RUN_CMD || true" \
  > "$OUTPUT"

echo "[linux-ref] Reference output captured ($ARCH) → $OUTPUT" >&2
