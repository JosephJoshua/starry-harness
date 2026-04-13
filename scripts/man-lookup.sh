#!/bin/bash
# man-lookup.sh — Fetch a Linux man page for a syscall.
#
# Usage:
#   man-lookup.sh <syscall_name> [section]
#
# Tries three sources in order:
#   1. Local `man` command (if man-pages are installed)
#   2. Docker container with man-pages
#   3. man7.org web fetch (requires curl)
#
# Output: plain-text man page on stdout.
set -euo pipefail

SYSCALL="${1:?Usage: man-lookup.sh <syscall_name> [section]}"
SECTION="${2:-2}"

# Attempt 1: local man
if command -v man >/dev/null 2>&1; then
  result=$(man "$SECTION" "$SYSCALL" 2>/dev/null | col -bx 2>/dev/null || true)
  if [ -n "$result" ]; then
    echo "$result"
    exit 0
  fi
fi

# Attempt 2: Docker man-pages
IMAGE="starry-manpages:latest"
if command -v docker >/dev/null 2>&1; then
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[man-lookup] Building man-pages container..." >&2
    MAN_BUILD_CTX=$(mktemp -d)
    docker build -t "$IMAGE" -f - "$MAN_BUILD_CTX" <<'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    man-db manpages-dev manpages-posix-dev && rm -rf /var/lib/apt/lists/*
DOCKERFILE
    rm -rf "$MAN_BUILD_CTX"
  fi
  result=$(docker run --rm "$IMAGE" man "$SECTION" "$SYSCALL" 2>/dev/null | col -bx 2>/dev/null || true)
  if [ -n "$result" ]; then
    echo "$result"
    exit 0
  fi
fi

# Attempt 3: Fetch from man7.org
if command -v curl >/dev/null 2>&1; then
  url="https://man7.org/linux/man-pages/man${SECTION}/${SYSCALL}.${SECTION}.html"
  html=$(curl -sL "$url" 2>/dev/null || true)
  if [ -n "$html" ] && echo "$html" | grep -q "<pre>"; then
    # Strip HTML tags for a rough plaintext rendering
    echo "$html" | sed -e 's/<[^>]*>//g' -e 's/&lt;/</g' -e 's/&gt;/>/g' -e 's/&amp;/\&/g'
    exit 0
  fi
fi

echo "Error: could not fetch man page for $SYSCALL($SECTION)" >&2
exit 1
