#!/bin/bash
# stress-test.sh — Multi-run test runner with SMP variation and deadlock detection.
#
# Runs a StarryOS test multiple times across different SMP configurations
# to expose concurrency bugs, deadlocks, and non-deterministic failures.
#
# Usage:
#   stress-test.sh <test_name> [--runs N] [--smp LIST] [--timeout SEC] [--memory SIZE]
#
# Examples:
#   stress-test.sh test_futex_race                     # 50 runs, SMP=1,2,4, 60s timeout
#   stress-test.sh test_fork_exit --runs 200 --smp 1,4,8
#   stress-test.sh test_mmap_concurrent --timeout 120 --memory 128M
#
# Output: structured report on stdout + JSON results to tests/results/stress_<name>.json
#
# Requires: CLAUDE_PROJECT_DIR set (or run from project root)
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────
TEST_NAME=""
RUNS=50
SMP_LIST="1,2,4"
TIMEOUT=60
MEMORY="1G"

# ── Parse args ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)   RUNS="$2"; shift 2 ;;
    --smp)    SMP_LIST="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    -*)       echo "Unknown flag: $1" >&2; exit 1 ;;
    *)        TEST_NAME="$1"; shift ;;
  esac
done

if [ -z "$TEST_NAME" ]; then
  echo "Usage: stress-test.sh <test_name> [--runs N] [--smp LIST] [--timeout SEC] [--memory SIZE]" >&2
  exit 1
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
STARRY_DIR="$PROJECT_ROOT/os/StarryOS"
RESULTS_DIR="$STARRY_DIR/tests/results"
mkdir -p "$RESULTS_DIR"

JSON_OUT="$RESULTS_DIR/stress_${TEST_NAME}.json"

echo "╔═══════════════════════════════════════════════════╗"
echo "║  stress-test: $TEST_NAME"
echo "║  runs=$RUNS  smp=[$SMP_LIST]  timeout=${TIMEOUT}s  mem=$MEMORY"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── Build kernel once ───────────────────────────────────────────
echo "[stress] Building StarryOS kernel..."
(cd "$STARRY_DIR" && bash tools/compile.sh "$TEST_NAME" 2>/dev/null) || true

# ── Run matrix ──────────────────────────────────────────────────
IFS=',' read -ra SMP_CONFIGS <<< "$SMP_LIST"

declare -A PASS_COUNT
declare -A FAIL_COUNT
declare -A TIMEOUT_COUNT
declare -A CRASH_COUNT
TOTAL_RUNS=0

for SMP in "${SMP_CONFIGS[@]}"; do
  PASS_COUNT[$SMP]=0
  FAIL_COUNT[$SMP]=0
  TIMEOUT_COUNT[$SMP]=0
  CRASH_COUNT[$SMP]=0

  echo "[stress] ── SMP=$SMP ($RUNS runs) ──"

  for ((i=1; i<=RUNS; i++)); do
    TOTAL_RUNS=$((TOTAL_RUNS + 1))
    RUN_OUTPUT=$(mktemp)

    # Run with timeout; capture exit code
    EXIT_CODE=0
    timeout "${TIMEOUT}s" qemu-system-riscv64 \
      -machine virt -nographic -m "$MEMORY" -bios default \
      -smp "$SMP" \
      -kernel "$STARRY_DIR/tests/bin/starryos.bin" \
      -device virtio-blk-pci,drive=disk0 \
      -drive "id=disk0,if=none,format=raw,file=$STARRY_DIR/make/disk.img" \
      > "$RUN_OUTPUT" 2>&1 || EXIT_CODE=$?

    # Classify result
    if [ "$EXIT_CODE" -eq 124 ]; then
      # timeout(1) returns 124 on timeout
      TIMEOUT_COUNT[$SMP]=$((${TIMEOUT_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: TIMEOUT (likely deadlock)\n" "$i" "$RUNS"
    elif grep -q "FAIL:" "$RUN_OUTPUT"; then
      FAIL_COUNT[$SMP]=$((${FAIL_COUNT[$SMP]} + 1))
      FAIL_LINE=$(grep "FAIL:" "$RUN_OUTPUT" | head -1)
      printf "  run %3d/%d: FAIL — %s\n" "$i" "$RUNS" "$FAIL_LINE"
    elif grep -q "panic" "$RUN_OUTPUT" || grep -q "trap" "$RUN_OUTPUT"; then
      CRASH_COUNT[$SMP]=$((${CRASH_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: CRASH (kernel panic/trap)\n" "$i" "$RUNS"
    elif grep -q "PASS:" "$RUN_OUTPUT"; then
      PASS_COUNT[$SMP]=$((${PASS_COUNT[$SMP]} + 1))
      # Only print every 10th pass to reduce noise
      if (( i % 10 == 0 )); then
        printf "  run %3d/%d: PASS\n" "$i" "$RUNS"
      fi
    else
      FAIL_COUNT[$SMP]=$((${FAIL_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: UNKNOWN (no PASS/FAIL markers)\n" "$i" "$RUNS"
    fi

    rm -f "$RUN_OUTPUT"
  done

  P=${PASS_COUNT[$SMP]}
  F=${FAIL_COUNT[$SMP]}
  T=${TIMEOUT_COUNT[$SMP]}
  C=${CRASH_COUNT[$SMP]}
  echo "  ── SMP=$SMP summary: $P pass, $F fail, $T timeout, $C crash"
  echo ""
done

# ── Summary ─────────────────────────────────────────────────────
echo "╔═══════════════════════════════════════════════════╗"
echo "║  STRESS TEST SUMMARY: $TEST_NAME"
echo "╠═══════════════════════════════════════════════════╣"

ANY_FAILURE=false
for SMP in "${SMP_CONFIGS[@]}"; do
  P=${PASS_COUNT[$SMP]}
  F=${FAIL_COUNT[$SMP]}
  T=${TIMEOUT_COUNT[$SMP]}
  C=${CRASH_COUNT[$SMP]}
  TOTAL=$((P + F + T + C))
  FAIL_RATE=0
  if [ "$TOTAL" -gt 0 ]; then
    FAIL_RATE=$(( (F + T + C) * 100 / TOTAL ))
  fi
  printf "║  SMP=%-2s: %3d pass, %3d fail, %3d timeout, %3d crash (%d%% failure)\n" \
    "$SMP" "$P" "$F" "$T" "$C" "$FAIL_RATE"
  if [ "$F" -gt 0 ] || [ "$T" -gt 0 ] || [ "$C" -gt 0 ]; then
    ANY_FAILURE=true
  fi
done

echo "╠═══════════════════════════════════════════════════╣"
if [ "$ANY_FAILURE" = true ]; then
  echo "║  RESULT: BUG CONFIRMED (non-deterministic failure)"

  # Detect concurrency-specific patterns
  SMP1_FAILS=$(( ${FAIL_COUNT[1]:-0} + ${TIMEOUT_COUNT[1]:-0} + ${CRASH_COUNT[1]:-0} ))
  SMP4_FAILS=$(( ${FAIL_COUNT[4]:-0} + ${TIMEOUT_COUNT[4]:-0} + ${CRASH_COUNT[4]:-0} ))
  if [ "$SMP1_FAILS" -eq 0 ] && [ "$SMP4_FAILS" -gt 0 ]; then
    echo "║  PATTERN: SMP=1 clean, SMP=4 fails → CONCURRENCY BUG"
  fi

  TOTAL_TIMEOUTS=0
  for SMP in "${SMP_CONFIGS[@]}"; do
    TOTAL_TIMEOUTS=$((TOTAL_TIMEOUTS + ${TIMEOUT_COUNT[$SMP]}))
  done
  if [ "$TOTAL_TIMEOUTS" -gt 0 ]; then
    echo "║  PATTERN: $TOTAL_TIMEOUTS timeouts detected → LIKELY DEADLOCK"
  fi
else
  echo "║  RESULT: ALL PASSED ($TOTAL_RUNS runs across ${#SMP_CONFIGS[@]} SMP configs)"
fi
echo "╚═══════════════════════════════════════════════════╝"

# ── JSON output ─────────────────────────────────────────────────
{
  echo "{"
  echo "  \"test\": \"$TEST_NAME\","
  echo "  \"runs_per_config\": $RUNS,"
  echo "  \"timeout_seconds\": $TIMEOUT,"
  echo "  \"memory\": \"$MEMORY\","
  echo "  \"total_runs\": $TOTAL_RUNS,"
  echo "  \"any_failure\": $ANY_FAILURE,"
  echo "  \"configs\": {"
  FIRST=true
  for SMP in "${SMP_CONFIGS[@]}"; do
    $FIRST || echo ","
    FIRST=false
    printf "    \"smp_%s\": {\"pass\": %d, \"fail\": %d, \"timeout\": %d, \"crash\": %d}" \
      "$SMP" "${PASS_COUNT[$SMP]}" "${FAIL_COUNT[$SMP]}" "${TIMEOUT_COUNT[$SMP]}" "${CRASH_COUNT[$SMP]}"
  done
  echo ""
  echo "  },"
  echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
  echo "}"
} > "$JSON_OUT"

echo ""
echo "[stress] JSON results: $JSON_OUT"
