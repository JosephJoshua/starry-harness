#!/usr/bin/env bash
# regression-check.sh — Run ALL StarryOS tests and compare against known-good baseline.
# Exit 0 if no regressions, 1 if any regression found.
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:?CLAUDE_PROJECT_DIR must be set}"
STARRY_DIR="$PROJECT_DIR/os/StarryOS"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPELINE="$SCRIPT_DIR/pipeline.sh"
KNOWN_JSON="$STARRY_DIR/tests/known.json"
CASES_DIR="$STARRY_DIR/tests/cases"
RESULTS_DIR="$STARRY_DIR/tests/results"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
REPORT_FILE="$RESULTS_DIR/regression-${TIMESTAMP}.json"

if [ ! -f "$KNOWN_JSON" ]; then
  echo "ERROR: $KNOWN_JSON not found" >&2
  exit 1
fi

# ── Header ──────────────────────────────────────────────
printf '\n╔═══════════════════════════════════════════════════╗\n'
printf '║  Regression Check — StarryOS                      ║\n'
printf '╚═══════════════════════════════════════════════════╝\n\n'

# ── Collect test list from known.json ───────────────────
# Each line: syscall_name  tested  expected_pass  expected_fail  test_file
SYSCALLS=()
while IFS= read -r line; do
  SYSCALLS+=("$line")
done < <(
  python3 -c "
import json, sys, os
with open('$KNOWN_JSON') as f:
    data = json.load(f)
for name, info in data.get('syscalls', {}).items():
    tested = info.get('tested', False)
    test_rel = info.get('test', '')
    test_file = os.path.join('$STARRY_DIR', test_rel) if test_rel else ''
    test_name = os.path.splitext(os.path.basename(test_file))[0] if test_file else ''
    exp_pass = info.get('results', {}).get('pass', 0)
    exp_fail = info.get('results', {}).get('fail', 0)
    print(f'{name}\t{tested}\t{exp_pass}\t{exp_fail}\t{test_name}\t{test_file}')
" | sort
)

regressions=0
improvements=0
unchanged=0
skipped=0
json_entries=()

for entry in "${SYSCALLS[@]}"; do
  IFS=$'\t' read -r syscall tested exp_pass exp_fail test_name test_file <<< "$entry"

  # Skip untested syscalls
  if [ "$tested" != "True" ]; then
    skipped=$((skipped + 1))
    continue
  fi

  # Skip if .c source doesn't exist
  src_file="$CASES_DIR/${test_name}.c"
  if [ ! -f "$src_file" ]; then
    printf '  %-24s SKIP (no source file)\n' "${test_name}:"
    skipped=$((skipped + 1))
    continue
  fi

  # ── Run pipeline: compile -> inject -> run ──
  printf '  %-24s running...' "${test_name}:"
  pipeline_log="$RESULTS_DIR/.pipeline-${test_name}.log"
  if ! "$PIPELINE" "$test_name" > "$pipeline_log" 2>&1; then
    printf '\r  %-24s ERROR (pipeline failed)\n' "${test_name}:"
    json_entries+=("$(printf '{"syscall":"%s","test":"%s","status":"error","expected_pass":%d,"expected_fail":%d,"actual_pass":0,"actual_fail":0}' \
      "$syscall" "$test_name" "$exp_pass" "$exp_fail")")
    regressions=$((regressions + 1))
    continue
  fi

  # ── Parse PASS/FAIL counts from result .txt ──
  result_txt="$RESULTS_DIR/${test_name}.txt"
  actual_pass=0
  actual_fail=0
  if [ -f "$result_txt" ]; then
    actual_pass="$(grep -c '^PASS:' "$result_txt" 2>/dev/null || echo 0)"
    actual_fail="$(grep -c '^FAIL:' "$result_txt" 2>/dev/null || echo 0)"
  fi

  total=$((actual_pass + actual_fail))
  exp_total=$((exp_pass + exp_fail))

  # ── Compare against baseline ──
  pass_diff=$((actual_pass - exp_pass))
  if [ "$pass_diff" -lt 0 ]; then
    status="regression"
    marker="REGRESSION (${pass_diff} pass)"
    symbol="⚠"
    regressions=$((regressions + 1))
  elif [ "$pass_diff" -gt 0 ]; then
    status="improved"
    marker="IMPROVED (+${pass_diff} pass)"
    symbol="★"
    improvements=$((improvements + 1))
  else
    status="unchanged"
    if [ "$actual_fail" -eq 0 ]; then
      marker=""
      symbol="✓"
    else
      marker="(known failures)"
      symbol="✓"
    fi
    unchanged=$((unchanged + 1))
  fi

  # ── Print result line ──
  printf '\r  %-24s %d/%d PASS (expected %d/%d) %s %s\n' \
    "${test_name}:" "$actual_pass" "$total" "$exp_pass" "$exp_total" "$symbol" "$marker"

  json_entries+=("$(printf '{"syscall":"%s","test":"%s","status":"%s","expected_pass":%d,"expected_fail":%d,"actual_pass":%d,"actual_fail":%d,"pass_diff":%d}' \
    "$syscall" "$test_name" "$status" "$exp_pass" "$exp_fail" "$actual_pass" "$actual_fail" "$pass_diff")")
done

# ── Summary ─────────────────────────────────────────────
tested_count=$((regressions + improvements + unchanged))
printf '\n  RESULT: %d regression(s), %d improvement(s), %d unchanged, %d skipped\n\n' \
  "$regressions" "$improvements" "$unchanged" "$skipped"

# ── Write JSON report ──────────────────────────────────
mkdir -p "$RESULTS_DIR"
{
  printf '{\n'
  printf '  "timestamp": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "summary": {"regressions": %d, "improvements": %d, "unchanged": %d, "skipped": %d},\n' \
    "$regressions" "$improvements" "$unchanged" "$skipped"
  printf '  "tests": [\n'
  for i in "${!json_entries[@]}"; do
    if [ "$i" -lt $((${#json_entries[@]} - 1)) ]; then
      printf '    %s,\n' "${json_entries[$i]}"
    else
      printf '    %s\n' "${json_entries[$i]}"
    fi
  done
  printf '  ]\n'
  printf '}\n'
} > "$REPORT_FILE"

echo "Report saved to: $REPORT_FILE"

# ── Exit code ───────────────────────────────────────────
if [ "$regressions" -gt 0 ]; then
  exit 1
fi
exit 0
