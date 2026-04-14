#!/usr/bin/env bash
# draft-pr.sh — Generate a PR draft markdown file with a ready-to-run gh command.
#
# Usage:
#   draft-pr.sh <bug_id> [--branch branch_name]
#
# Example:
#   draft-pr.sh BUG-001-prlimit64
#   # Creates: docs/starry-reports/prs/PR-BUG-001-prlimit64.md
#
# NEVER executes gh pr create — only writes the command into the output file.
set -euo pipefail

BUG_ID="${1:?Usage: draft-pr.sh <bug_id> [--branch branch_name]}"
shift

BRANCH=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="${2:?--branch requires a value}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
BUG_REPORT="$PROJECT_ROOT/docs/starry-reports/bugs/${BUG_ID}.md"
KNOWN_JSON="$PROJECT_ROOT/os/StarryOS/tests/known.json"
STRATEGY_JSON="$PROJECT_ROOT/docs/starry-reports/strategy.json"
PR_DIR="$PROJECT_ROOT/docs/starry-reports/prs"
PR_FILE="$PR_DIR/PR-${BUG_ID}.md"

mkdir -p "$PR_DIR"

# --- Extract syscall name from bug_id (e.g., BUG-001-prlimit64 -> prlimit64) ---
SYSCALL=$(echo "$BUG_ID" | sed 's/^BUG-[0-9]*-//')

# --- Read bug report summary (first paragraph after ## Summary) ---
BUG_SUMMARY="(no bug report found at $BUG_REPORT)"
if [[ -f "$BUG_REPORT" ]]; then
  BUG_SUMMARY=$(awk '/^## Summary/{found=1; next} found && /^## /{exit} found && NF{print}' "$BUG_REPORT")
  [[ -z "$BUG_SUMMARY" ]] && BUG_SUMMARY="(could not extract summary from bug report)"
fi

# --- Read test results from known.json ---
TEST_RESULTS="(no test data found)"
PASS_COUNT="0"
FAIL_COUNT="0"
STATUS="unknown"
if [[ -f "$KNOWN_JSON" ]] && command -v python3 &>/dev/null; then
  read -r PASS_COUNT FAIL_COUNT STATUS <<< "$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sc = d.get('syscalls', {}).get(sys.argv[2], {})
r = sc.get('results', {})
print(r.get('pass', 0), r.get('fail', 0), sc.get('status', 'unknown'))
" "$KNOWN_JSON" "$SYSCALL" 2>/dev/null || echo "0 0 unknown")"
  TEST_RESULTS="Status: **${STATUS}** | Pass: ${PASS_COUNT} | Fail: ${FAIL_COUNT}"
fi

# --- Read review confidence from strategy.json ---
REVIEW_CONFIDENCE="(no review data found)"
if [[ -f "$STRATEGY_JSON" ]] && command -v python3 &>/dev/null; then
  REVIEW_CONFIDENCE=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
r = d.get('reviews', {}).get(sys.argv[2], {})
if r:
    conf = r.get('confidence', 'unknown')
    rounds = r.get('total_rounds', '?')
    detail_parts = []
    for x in r.get('rounds', []):
        detail_parts.append(str(x.get('type', '')) + ': ' + str(x.get('result', '')))
    details = ', '.join(detail_parts)
    print(f'Confidence: **{conf}** ({rounds} rounds: {details})')
else:
    print('(no review data for this bug)')
" "$STRATEGY_JSON" "$BUG_ID" 2>/dev/null || echo "(could not read strategy.json)")
fi

# --- Git diff stat and log ---
DIFF_STAT=$(cd "$PROJECT_ROOT" && git diff main...HEAD --stat 2>/dev/null || echo "(could not compute diff)")
[[ -z "$DIFF_STAT" ]] && DIFF_STAT="(no changes relative to main)"

COMMIT_LOG=$(cd "$PROJECT_ROOT" && git log main...HEAD --oneline 2>/dev/null || echo "(could not compute log)")
[[ -z "$COMMIT_LOG" ]] && COMMIT_LOG="(no commits relative to main)"

BRANCH="${BRANCH:-$(cd "$PROJECT_ROOT" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")}"

# --- Derive a PR title ---
TITLE_VERB="fix"
[[ "$STATUS" == "stub" ]] && TITLE_VERB="implement"
BUG_TITLE=$(head -1 "$BUG_REPORT" 2>/dev/null | sed 's/^# BUG-[0-9]*: //' || echo "$SYSCALL improvements")
PR_TITLE="${TITLE_VERB}(${SYSCALL}): ${BUG_TITLE}"

TOTAL_TESTS=$((${PASS_COUNT:-0} + ${FAIL_COUNT:-0}))

# --- Write PR draft ---
cat > "$PR_FILE" <<PREOF
# PR Draft: ${BUG_ID}

**Status**: DRAFT — review before submitting
**Branch**: \`${BRANCH}\`
**Generated**: $(date +%Y-%m-%d\ %H:%M)

## Summary

${BUG_SUMMARY}

## Changes

\`\`\`
${DIFF_STAT}
\`\`\`

## Commits

\`\`\`
${COMMIT_LOG}
\`\`\`

## Test Results

${TEST_RESULTS}

## Review Confidence

${REVIEW_CONFIDENCE}

---

## Ready to submit?

When ready, run this command from the tgoskits project root:

\`\`\`bash
gh pr create --title "${PR_TITLE}" --body "\$(cat <<'EOF'
## Summary
${BUG_SUMMARY}

## Test plan
- [ ] All existing tests pass (regression-check.sh)
- [ ] New test: test_${SYSCALL}.c — ${PASS_COUNT:-?}/${TOTAL_TESTS:-?} passing
- [ ] Linux comparison: behavior matches Docker Linux baseline
- [ ] Code review: kernel-reviewer agent — PASS
EOF
)"
\`\`\`

**DO NOT run this command automatically. Review the PR content first.**
PREOF

echo "[draft-pr] Created PR draft: $PR_FILE" >&2
