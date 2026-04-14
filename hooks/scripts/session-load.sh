#!/bin/bash
# session-load.sh — Load StarryOS harness status at session start.
#
# Reads the journal and known-bugs registry, emits a concise status
# summary so the agent has context about prior work.
# (MOTD is handled by companyAnnouncements in .claude/settings.json)
set -euo pipefail

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
JOURNAL="$PROJECT_ROOT/docs/starry-reports/journal.md"
KNOWN="$PROJECT_ROOT/os/StarryOS/tests/known.json"

STATUS=""

# Journal summary — last 5 entries
if [ -f "$JOURNAL" ]; then
  RECENT=$(grep -E '^\#\# [0-9]{4}' "$JOURNAL" | head -5 || true)
  if [ -n "$RECENT" ]; then
    STATUS="$STATUS
[starry-harness] Recent journal entries:
$RECENT"
  fi
fi

# Known bugs summary
if [ -f "$KNOWN" ]; then
  BUG_COUNT=$(python3 -c "
import json, sys
try:
  d = json.load(open('$KNOWN'))
  syscalls = d.get('syscalls', d) if isinstance(d, dict) else d
  total = len(syscalls) if isinstance(syscalls, (list, dict)) else 0
  buggy = sum(1 for v in (syscalls.values() if isinstance(syscalls, dict) else syscalls) if isinstance(v, dict) and v.get('status', '') in ('buggy', 'broken', 'stub'))
  print(f'{total} syscalls tested, {buggy} with known bugs')
except Exception as e:
  print(f'known.json parse error: {e}', file=sys.stderr)
  print('unknown')
" 2>/dev/null || echo "unknown")
  STATUS="$STATUS
[starry-harness] Bug registry: $BUG_COUNT"
fi

# Report directory summary
REPORT_DIR="$PROJECT_ROOT/docs/starry-reports"
if [ -d "$REPORT_DIR" ]; then
  BUG_REPORTS=$(find "$REPORT_DIR/bugs" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  BENCH_REPORTS=$(find "$REPORT_DIR/benchmarks" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  APP_REPORTS=$(find "$REPORT_DIR/apps" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  STATUS="$STATUS
[starry-harness] Reports: ${BUG_REPORTS} bug, ${BENCH_REPORTS} benchmark, ${APP_REPORTS} app-compat"
fi

# Strategy priorities
STRATEGY="$PROJECT_ROOT/docs/starry-reports/strategy.json"
if [ -f "$STRATEGY" ]; then
  PRIORITIES=$(python3 -c "
import json
try:
  d = json.load(open('$STRATEGY'))
  t = d.get('targets', {})
  perf = 'MET' if t.get('performance', {}).get('met') else 'NOT MET'
  app = 'MET' if t.get('application', {}).get('met') else 'NOT MET'
  bugs = 'MET' if t.get('bugs', {}).get('met') else 'NOT MET'
  cats = d.get('effectiveness', {}).get('bug_categories', {})
  gaps = [k for k, v in cats.items() if v == 0]
  deep = len(d.get('analysis_queue', {}).get('needs_deep', []))
  plist = d.get('next_priorities', [])[:3]
  print(f'Targets: bugs={bugs} perf={perf} app={app}')
  if gaps:
    print(f'Category gaps: {', '.join(gaps)}')
  if deep:
    print(f'Deep queue: {deep} targets waiting')
  if plist:
    print('Top priorities:')
    for p in plist:
      print(f'  → {p}')
except Exception:
  pass
" 2>/dev/null || true)
  if [ -n "$PRIORITIES" ]; then
    STATUS="$STATUS
[starry-harness] Strategy:
$PRIORITIES"
  fi
fi

if [ -n "$STATUS" ]; then
  echo "$STATUS"
fi
