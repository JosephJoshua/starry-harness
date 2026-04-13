#!/bin/bash
# journal-entry.sh — Append a structured entry to the StarryOS work journal.
#
# Usage:
#   journal-entry.sh <type> <title> [body]
#
# Types: BUG, BENCH, APP, FIX, FEAT, NOTE
# Title: one-line summary
# Body:  multi-line detail (optional, read from stdin if not provided)
#
# Appends to docs/starry-reports/journal.md in the project root.
set -euo pipefail

TYPE="${1:?Usage: journal-entry.sh <type> <title> [body]}"
TITLE="${2:?Usage: journal-entry.sh <type> <title> [body]}"
BODY="${3:-}"

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
JOURNAL="$PROJECT_ROOT/docs/starry-reports/journal.md"

# Read body from stdin if not provided as argument
if [ -z "$BODY" ] && [ ! -t 0 ]; then
  BODY=$(cat)
fi

DATE=$(date +%Y-%m-%d)
TIME=$(date +%H:%M)

# Ensure journal exists with header
if [ ! -f "$JOURNAL" ]; then
  mkdir -p "$(dirname "$JOURNAL")"
  cat > "$JOURNAL" <<'EOF'
# StarryOS Development Journal

Work log for StarryOS kernel improvement. Entries are prepended newest-first.

---
EOF
fi

# Build entry
ENTRY=$(cat <<ENTRY_EOF

## $DATE $TIME — [$TYPE] $TITLE
ENTRY_EOF
)

if [ -n "$BODY" ]; then
  ENTRY="$ENTRY
$BODY"
fi

# Prepend entry after the header (after the first ---)
# Find line number of first "---" separator
SEP_LINE=$(grep -n '^---$' "$JOURNAL" | head -1 | cut -d: -f1)

if [ -n "$SEP_LINE" ]; then
  # Insert after the separator line
  head -n "$SEP_LINE" "$JOURNAL" > "$JOURNAL.tmp"
  echo "$ENTRY" >> "$JOURNAL.tmp"
  tail -n +"$((SEP_LINE + 1))" "$JOURNAL" >> "$JOURNAL.tmp"
  mv "$JOURNAL.tmp" "$JOURNAL"
else
  # No separator found, just append
  echo "$ENTRY" >> "$JOURNAL"
fi

echo "[journal] Added [$TYPE] entry: $TITLE" >&2
