#!/usr/bin/env bash
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
# Requires: bash 4+ (for associative arrays), CLAUDE_PROJECT_DIR set (or run from project root)
set -euo pipefail

if command -v timeout &>/dev/null; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout &>/dev/null; then
  TIMEOUT_CMD="gtimeout"
else
  echo "[stress] Error: timeout (GNU coreutils) required. Install with: brew install coreutils" >&2
  exit 1
fi

# ── Defaults ────────────────────────────────────────────────────
TEST_NAME=""
RUNS=50
SMP_LIST="1,2,4"
TIMEOUT=60
MEMORY="1G"
ARCH="riscv64"

# ── Parse args ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)    RUNS="$2"; shift 2 ;;
    --smp)     SMP_LIST="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --memory)  MEMORY="$2"; shift 2 ;;
    --arch)    ARCH="$2"; shift 2 ;;
    -*)        echo "Unknown flag: $1" >&2; exit 1 ;;
    *)         TEST_NAME="$1"; shift ;;
  esac
done

if [ -z "$TEST_NAME" ]; then
  echo "Usage: stress-test.sh <test_name> [--runs N] [--smp LIST] [--timeout SEC] [--memory SIZE] [--arch ARCH]" >&2
  exit 1
fi

# ── Arch → QEMU mapping ────────────────────────────────────────
case "$ARCH" in
  riscv64)
    QEMU_BIN="qemu-system-riscv64"
    QEMU_MACHINE="-machine virt -bios default"
    ;;
  aarch64)
    QEMU_BIN="qemu-system-aarch64"
    QEMU_MACHINE="-cpu cortex-a72 -machine virt"
    ;;
  x86_64)
    QEMU_BIN="qemu-system-x86_64"
    QEMU_MACHINE="-machine q35"
    ;;
  loongarch64)
    QEMU_BIN="qemu-system-loongarch64"
    QEMU_MACHINE="-machine virt"
    ;;
  *)
    echo "[stress] Error: unknown arch '$ARCH'. Supported: riscv64, aarch64, x86_64, loongarch64" >&2
    exit 1
    ;;
esac

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
STARRY_DIR="$PROJECT_ROOT/os/StarryOS"
RESULTS_DIR="$STARRY_DIR/tests/results"
mkdir -p "$RESULTS_DIR"

JSON_OUT="$RESULTS_DIR/stress_${TEST_NAME}_${ARCH}.json"

echo "╔═══════════════════════════════════════════════════╗"
echo "║  stress-test: $TEST_NAME ($ARCH)"
echo "║  runs=$RUNS  smp=[$SMP_LIST]  timeout=${TIMEOUT}s  mem=$MEMORY"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── Build kernel once ───────────────────────────────────────────
echo "[stress] Building StarryOS kernel..."
if ! (cd "$STARRY_DIR" && bash tools/compile.sh "$TEST_NAME"); then
  echo "[stress] Build failed for $TEST_NAME — aborting." >&2
  exit 1
fi

KERNEL_BIN="$STARRY_DIR/tests/bin/starryos.bin"
if [ ! -f "$KERNEL_BIN" ]; then
  echo "[stress] Kernel binary not found at $KERNEL_BIN — aborting." >&2
  exit 1
fi

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

    # Capture output in variable — no temp file needed
    EXIT_CODE=0
    QEMU_OUTPUT=$("$TIMEOUT_CMD" "${TIMEOUT}s" $QEMU_BIN \
      $QEMU_MACHINE -nographic -m "$MEMORY" \
      -smp "$SMP" \
      -kernel "$KERNEL_BIN" \
      -device virtio-blk-pci,drive=disk0 \
      -drive "id=disk0,if=none,format=raw,file=$STARRY_DIR/make/disk.img" \
      2>&1) || EXIT_CODE=$?

    # Classify result
    if [ "$EXIT_CODE" -eq 124 ]; then
      TIMEOUT_COUNT[$SMP]=$((${TIMEOUT_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: TIMEOUT (likely deadlock)\n" "$i" "$RUNS"
    elif echo "$QEMU_OUTPUT" | grep -q "FAIL:"; then
      FAIL_COUNT[$SMP]=$((${FAIL_COUNT[$SMP]} + 1))
      FAIL_LINE=$(echo "$QEMU_OUTPUT" | grep "FAIL:" | head -1)
      printf "  run %3d/%d: FAIL — %s\n" "$i" "$RUNS" "$FAIL_LINE"
    elif echo "$QEMU_OUTPUT" | grep -qE "panic|trap"; then
      CRASH_COUNT[$SMP]=$((${CRASH_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: CRASH (kernel panic/trap)\n" "$i" "$RUNS"
    elif echo "$QEMU_OUTPUT" | grep -q "PASS:"; then
      PASS_COUNT[$SMP]=$((${PASS_COUNT[$SMP]} + 1))
      if (( i % 10 == 0 )); then
        printf "  run %3d/%d: PASS\n" "$i" "$RUNS"
      fi
    else
      FAIL_COUNT[$SMP]=$((${FAIL_COUNT[$SMP]} + 1))
      printf "  run %3d/%d: UNKNOWN (no PASS/FAIL markers)\n" "$i" "$RUNS"
    fi
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

  # Detect concurrency pattern: first config clean, last config fails
  SMP_CONTROL="${SMP_CONFIGS[0]}"
  SMP_TREATMENT="${SMP_CONFIGS[-1]}"
  CONTROL_FAILS=$(( ${FAIL_COUNT[$SMP_CONTROL]:-0} + ${TIMEOUT_COUNT[$SMP_CONTROL]:-0} + ${CRASH_COUNT[$SMP_CONTROL]:-0} ))
  TREATMENT_FAILS=$(( ${FAIL_COUNT[$SMP_TREATMENT]:-0} + ${TIMEOUT_COUNT[$SMP_TREATMENT]:-0} + ${CRASH_COUNT[$SMP_TREATMENT]:-0} ))
  if [ "$CONTROL_FAILS" -eq 0 ] && [ "$TREATMENT_FAILS" -gt 0 ]; then
    echo "║  PATTERN: SMP=$SMP_CONTROL clean, SMP=$SMP_TREATMENT fails → CONCURRENCY BUG"
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
  printf '{\n'
  printf '  "test": "%s",\n' "$TEST_NAME"
  printf '  "runs_per_config": %d,\n' "$RUNS"
  printf '  "timeout_seconds": %d,\n' "$TIMEOUT"
  printf '  "memory": "%s",\n' "$MEMORY"
  printf '  "total_runs": %d,\n' "$TOTAL_RUNS"
  printf '  "any_failure": %s,\n' "$ANY_FAILURE"
  printf '  "configs": {\n'
  FIRST=true
  for SMP in "${SMP_CONFIGS[@]}"; do
    $FIRST || printf ',\n'
    FIRST=false
    printf '    "smp_%s": {"pass": %d, "fail": %d, "timeout": %d, "crash": %d}' \
      "$SMP" "${PASS_COUNT[$SMP]}" "${FAIL_COUNT[$SMP]}" "${TIMEOUT_COUNT[$SMP]}" "${CRASH_COUNT[$SMP]}"
  done
  printf '\n  },\n'
  printf '  "timestamp": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '}\n'
} > "$JSON_OUT"

echo ""
echo "[stress] JSON results: $JSON_OUT"
