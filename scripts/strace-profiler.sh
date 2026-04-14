#!/usr/bin/env bash
# strace-profiler.sh — Profile an application's syscall usage inside Docker
# and compare against StarryOS's known.json implementation status.
#
# Usage:
#   strace-profiler.sh <app_command> [--json output.json] [--package apt_package]
#
# Examples:
#   strace-profiler.sh "nginx -g 'daemon off;'" --package nginx --json nginx-profile.json
#   strace-profiler.sh "/usr/bin/python3 -c 'print(1)'" --package python3
#   strace-profiler.sh "busybox sh -c 'echo hello && ls /'" --package busybox-static
#
# Requires: docker, python3
set -euo pipefail

APP_CMD="${1:?Usage: strace-profiler.sh <app_command> [--json output.json] [--package apt_package]}"
shift

JSON_OUT=""
PACKAGE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)    JSON_OUT="$2"; shift 2 ;;
    --package) PACKAGE="$2";  shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$PACKAGE" && ! "$PACKAGE" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "Error: invalid package name: $PACKAGE" >&2; exit 1
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
KNOWN_JSON="$PROJECT_ROOT/os/StarryOS/tests/known.json"

if [ ! -f "$KNOWN_JSON" ]; then
  echo "Error: known.json not found at $KNOWN_JSON" >&2
  exit 1
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# Build the install command
INSTALL_CMD="apt-get update -qq && apt-get install -y -qq strace"
if [ -n "$PACKAGE" ]; then
  INSTALL_CMD="$INSTALL_CMD $PACKAGE"
fi

echo "[strace-profiler] Running '$APP_CMD' under strace in Docker..." >&2

# Run strace in summary mode (-c) and full trace (-o) with a 10s timeout
# NOTE: APP_CMD is interpolated into the docker shell command. This is intentional —
# users pass compound shell commands (e.g., "nginx -g 'daemon off;'"). This only
# executes inside a disposable Docker container, not on the host.
docker run --rm \
  ubuntu:24.04 \
  bash -c "
    $INSTALL_CMD >/dev/null 2>&1
    # Summary trace (sorted by call count)
    strace -f -c -S calls -o /tmp/strace-summary.txt -- timeout 10 $APP_CMD >/dev/null 2>&1 || true
    echo '---SUMMARY---'
    cat /tmp/strace-summary.txt
    echo '---ENDSUMMARY---'
    # Full trace for unique syscall extraction
    strace -f -o /tmp/strace-full.txt -- timeout 10 $APP_CMD >/dev/null 2>&1 || true
    echo '---FULL---'
    cat /tmp/strace-full.txt
    echo '---ENDFULL---'
  " > "$WORKDIR/raw_output.txt" 2>&1

# Extract summary and full trace sections
sed -n '/^---SUMMARY---$/,/^---ENDSUMMARY---$/p' "$WORKDIR/raw_output.txt" \
  | sed '1d;$d' > "$WORKDIR/summary.txt"
sed -n '/^---FULL---$/,/^---ENDFULL---$/p' "$WORKDIR/raw_output.txt" \
  | sed '1d;$d' > "$WORKDIR/full.txt"

# Parse summary + full trace + known.json into structured JSON
RESULT=$(python3 - "$WORKDIR/summary.txt" "$WORKDIR/full.txt" "$KNOWN_JSON" "$APP_CMD" <<'PYEOF'
import json, re, sys

summary_path, full_path, known_path, app_cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

# Parse strace -c summary table
# Format: % time  seconds  usecs/call  calls  errors  syscall
freq = []
with open(summary_path) as f:
    for line in f:
        line = line.strip()
        # Match data rows: fields ending with a syscall name
        m = re.match(
            r'[\d.]+\s+[\d.]+\s+\d+\s+(\d+)\s+(\d+)?\s+(\w+)$', line
        )
        if m:
            calls = int(m.group(1))
            errors = int(m.group(2)) if m.group(2) else 0
            name = m.group(3)
            freq.append({"name": name, "calls": calls, "errors": errors})

# Sort by call count descending
freq.sort(key=lambda x: x["calls"], reverse=True)
summary_syscalls = {e["name"] for e in freq}

# Extract unique syscalls from full trace (catches any the summary missed)
full_syscalls = set()
with open(full_path) as f:
    for line in f:
        m = re.match(r'\d+\s+(\w+)\(', line)
        if m:
            full_syscalls.add(m.group(1))

all_syscalls = sorted(summary_syscalls | full_syscalls)

# Load known.json
with open(known_path) as f:
    known = json.load(f)
known_sc = known.get("syscalls", {})

# Classify: build a set of all known syscall names (normalize underscores)
implemented_tested = []
implemented_untested = []
known_buggy = []
missing = []

# Build lookup: known.json keys may be like "fcntl_getfl" — extract base syscall name
known_lookup = {}  # base_name -> list of entries
for key, info in known_sc.items():
    known_lookup[key] = info

for sc in all_syscalls:
    # Try exact match first, then prefix match (e.g., "fcntl" matches "fcntl_getfl")
    matched_entries = []
    for key, info in known_lookup.items():
        if key == sc or key.startswith(sc + "_") or sc.startswith(key + "_"):
            matched_entries.append((key, info))

    if not matched_entries:
        missing.append(sc)
        continue

    # Use the "worst" status among matches
    statuses = [info.get("status", "") for _, info in matched_entries]
    tested_any = any(info.get("tested", False) for _, info in matched_entries)

    if "buggy" in statuses or "stub" in statuses:
        known_buggy.append(sc)
    elif tested_any:
        implemented_tested.append(sc)
    else:
        implemented_untested.append(sc)

total = len(all_syscalls)
gap = len(missing)
coverage = round((total - gap) / total * 100, 1) if total > 0 else 0.0

# Derive app name from command
app_name = app_cmd.strip().split()[0].split("/")[-1]

result = {
    "application": app_name,
    "command": app_cmd,
    "total_unique_syscalls": total,
    "syscall_frequency": freq,
    "unique_syscalls": all_syscalls,
    "starry_status": {
        "implemented_and_tested": sorted(implemented_tested),
        "implemented_untested": sorted(implemented_untested),
        "missing": sorted(missing),
        "known_buggy": sorted(known_buggy),
    },
    "gap_count": gap,
    "coverage_pct": coverage,
}

print(json.dumps(result, indent=2))
PYEOF
)

if [ -n "$JSON_OUT" ]; then
  echo "$RESULT" > "$JSON_OUT"
  echo "[strace-profiler] Profile written to $JSON_OUT" >&2
else
  echo "$RESULT"
fi
