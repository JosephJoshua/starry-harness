#!/usr/bin/env bash
# update-known.sh — Update known.json with results from a test run.
#
# Usage:
#   update-known.sh <test_name> [--syscall NAME]
#
# Reads tests/results/<test_name>.json and tests/results/<test_name>.txt,
# merges pass/fail counts into tests/known.json.
#
# If --syscall is not given, derives it from the test name:
#   test_mmap_edge → mmap
#   test_fcntl_getfl → fcntl_getfl
#   test_prlimit64 → prlimit64
set -euo pipefail

TEST_NAME=""
SYSCALL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --syscall) SYSCALL="$2"; shift 2 ;;
    -*)        echo "Unknown flag: $1" >&2; exit 1 ;;
    *)         TEST_NAME="$1"; shift ;;
  esac
done

if [ -z "$TEST_NAME" ]; then
  echo "Usage: update-known.sh <test_name> [--syscall NAME]" >&2
  exit 1
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
STARRY_DIR="$PROJECT_ROOT/os/StarryOS"
RESULT_JSON="$STARRY_DIR/tests/results/${TEST_NAME}.json"
RESULT_TXT="$STARRY_DIR/tests/results/${TEST_NAME}.txt"
KNOWN_JSON="$STARRY_DIR/tests/known.json"

if [ ! -f "$RESULT_JSON" ]; then
  echo "Error: $RESULT_JSON not found. Run the pipeline first." >&2
  exit 1
fi

if [ ! -f "$KNOWN_JSON" ]; then
  echo "Error: $KNOWN_JSON not found." >&2
  exit 1
fi

python3 -c "
import json, sys, re

test_name = sys.argv[1]
syscall_override = sys.argv[2]
result_json_path = sys.argv[3]
result_txt_path = sys.argv[4]
known_json_path = sys.argv[5]

# Derive syscall name from test name if not overridden
if syscall_override:
    syscall = syscall_override
else:
    # test_mmap_edge -> mmap, test_fcntl_getfl -> fcntl_getfl, test_prlimit64 -> prlimit64
    syscall = re.sub(r'^test_', '', test_name)

# Read result JSON
with open(result_json_path) as f:
    result = json.load(f)

pass_count = result.get('pass', 0)
fail_count = result.get('fail', 0)

# Read known.json
with open(known_json_path) as f:
    known = json.load(f)

syscalls = known.setdefault('syscalls', {})
entry = syscalls.setdefault(syscall, {
    'tested': True,
    'status': 'unknown',
    'bugs': [],
    'source': '',
    'test': f'tests/cases/{test_name}.c',
    'results': {'pass': 0, 'fail': 0}
})

# Update results
entry['tested'] = True
entry['results'] = {'pass': pass_count, 'fail': fail_count}
entry['test'] = f'tests/cases/{test_name}.c'

# Auto-classify status
if fail_count == 0 and pass_count > 0:
    if entry.get('status') in ('buggy', 'broken', 'stub'):
        entry['status'] = 'fixed'
    elif entry.get('status') != 'fixed':
        entry['status'] = 'mostly_ok'
elif fail_count > 0 and pass_count == 0:
    entry['status'] = 'broken'
elif fail_count > 0:
    entry['status'] = 'buggy'

# Write back
with open(known_json_path, 'w') as f:
    json.dump(known, f, indent=2)
    f.write('\n')

print(f'[update-known] {syscall}: pass={pass_count} fail={fail_count} status={entry[\"status\"]}')
" "$TEST_NAME" "$SYSCALL" "$RESULT_JSON" "$RESULT_TXT" "$KNOWN_JSON"
