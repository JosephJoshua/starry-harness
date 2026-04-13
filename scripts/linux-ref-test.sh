#!/bin/bash
# linux-ref-test.sh — Run a C test case inside a Docker Linux container
# and capture output as the reference "expected" behavior.
#
# Usage:
#   linux-ref-test.sh <test_source.c> [output_file]
#
# The test source MUST be self-contained (include starry_test.h inline or
# use only standard POSIX headers).  The script compiles with musl-gcc
# (static) inside the container, runs the binary, and writes stdout to
# output_file (default: /dev/stdout).
#
# Requires: docker
set -euo pipefail

TEST_SRC="${1:?Usage: linux-ref-test.sh <test_source.c> [output_file]}"
OUTPUT="${2:-/dev/stdout}"
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
TESTS_DIR="$PROJECT_ROOT/os/StarryOS/tests"

if [ ! -f "$TEST_SRC" ]; then
  echo "Error: test source not found: $TEST_SRC" >&2
  exit 1
fi

# Resolve to absolute path
TEST_SRC="$(cd "$(dirname "$TEST_SRC")" && pwd)/$(basename "$TEST_SRC")"

# Build a minimal container image if it doesn't exist
IMAGE_NAME="starry-linux-ref:latest"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "[linux-ref] Building reference container..." >&2
  BUILD_CTX=$(mktemp -d)
  trap "rm -rf '$BUILD_CTX'" EXIT
  docker build -t "$IMAGE_NAME" -f - "$BUILD_CTX" <<'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev musl-tools && rm -rf /var/lib/apt/lists/*
WORKDIR /test
DOCKERFILE
fi

# Mount the test source and harness header, compile, and run
docker run --rm \
  -v "$TEST_SRC:/test/test_case.c:ro" \
  -v "$TESTS_DIR/cases/starry_test.h:/test/starry_test.h:ro" \
  "$IMAGE_NAME" \
  sh -c 'gcc -static -o /test/runner /test/test_case.c -I/test 2>&1 && /test/runner 2>&1 || true' \
  > "$OUTPUT"

echo "[linux-ref] Reference output captured → $OUTPUT" >&2
