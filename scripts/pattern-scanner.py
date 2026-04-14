#!/usr/bin/env python3
"""pattern-scanner.py — Deterministic bug pattern scanner for StarryOS.

Reads pattern rules from a JSON file and scans kernel source for matches.
Each pattern is a concrete grep/regex rule — no LLM reasoning.
The scanner produces structured hits for the LLM to triage.

Usage:
    pattern-scanner.py [--patterns patterns.json] [--json output.json]

The patterns file lives at docs/starry-reports/patterns.json in the target
project. If it doesn't exist, a default set is created from known bug patterns.

This is fully deterministic — same input always produces same output.
"""
import re
import sys
import json
import os
from pathlib import Path
from collections import defaultdict

DEFAULT_PATTERNS = [
    {
        "id": "negative-to-unsigned-cast",
        "description": "i64/isize argument cast to u64/usize without negativity check — silent wraparound",
        "grep_pattern": r"as u64|as usize",
        "file_glob": "os/StarryOS/kernel/src/syscall/**/*.rs",
        "exclude_pattern": r"if\s.*[<>]=?\s*0|\.is_negative|checked_|\.try_into",
        "context_lines": 2,
        "severity": "P1",
        "category": "correctness",
        "discovered_from": ["ftruncate", "pwrite64", "lseek"]
    },
    {
        "id": "ok-zero-stub",
        "description": "Syscall handler returns Ok(0) without doing any real work — silent stub",
        "grep_pattern": r"Ok\(0\)",
        "file_glob": "os/StarryOS/kernel/src/syscall/**/*.rs",
        "exclude_pattern": r"fn sys_getpid|fn sys_gettid|fn sys_getuid|fn sys_getgid|fn sys_geteuid|fn sys_getegid",
        "context_lines": 5,
        "severity": "P2",
        "category": "semantic",
        "discovered_from": ["flock", "prctl"]
    },
    {
        "id": "catch-all-match-arm",
        "description": "Match arm with _ => Ok(0) or _ => {} that silently ignores unknown values",
        "grep_pattern": r"_\s*=>\s*(Ok\(0\)|0|\{\s*\})",
        "file_glob": "os/StarryOS/kernel/src/syscall/**/*.rs",
        "exclude_pattern": r"// handled|// intentional|exhaustive",
        "context_lines": 3,
        "severity": "P2",
        "category": "semantic",
        "discovered_from": ["fcntl_catchall", "clock_gettime"]
    },
    {
        "id": "todo-fixme-hack",
        "description": "TODO/FIXME/HACK comment indicating incomplete implementation",
        "grep_pattern": r"TODO|FIXME|HACK|XXX|STUB",
        "file_glob": "os/StarryOS/kernel/src/**/*.rs",
        "exclude_pattern": None,
        "context_lines": 1,
        "severity": "P3",
        "category": "correctness",
        "discovered_from": []
    },
    {
        "id": "ignored-flags-parameter",
        "description": "Function parameter named 'flags' or '_flags' that is never used in the body",
        "grep_pattern": r"_flags\s*:|flags\s*:.*\bu32\b|flags\s*:.*\bi32\b",
        "file_glob": "os/StarryOS/kernel/src/syscall/**/*.rs",
        "exclude_pattern": r"flags\.(contains|bits|is_empty|intersects)|from_bits|flags\s*[!=<>]|if flags",
        "context_lines": 10,
        "severity": "P2",
        "category": "semantic",
        "discovered_from": ["copy_file_range"]
    },
    {
        "id": "unsafe-without-safety-comment",
        "description": "unsafe block without a preceding // SAFETY: comment",
        "grep_pattern": r"\bunsafe\s*\{",
        "file_glob": "os/StarryOS/kernel/src/**/*.rs",
        "exclude_pattern": r"unsafe fn |// SAFETY:",
        "context_lines": 3,
        "severity": "P2",
        "category": "safety",
        "discovered_from": ["prlimit64"]
    },
    {
        "id": "read-at-write-at-mismatch",
        "description": "Copy-paste error: read_at called in a write path or write_at in a read path",
        "grep_pattern": r"read_at|write_at",
        "file_glob": "os/StarryOS/kernel/src/syscall/fs/io.rs",
        "exclude_pattern": None,
        "context_lines": 5,
        "severity": "P0",
        "category": "correctness",
        "discovered_from": ["pwritev2"]
    },
]


def glob_to_regex(glob_pattern):
    """Convert a simple glob pattern to a regex for path matching."""
    pattern = glob_pattern.replace('.', r'\.')
    pattern = pattern.replace('**/', '(.*/)?')
    pattern = pattern.replace('*', '[^/]*')
    return re.compile(pattern)


def scan_file(filepath, pattern, project_root):
    """Scan a single file for a pattern, return hits."""
    hits = []
    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return hits

    grep_re = re.compile(pattern['grep_pattern'])
    exclude_re = re.compile(pattern['exclude_pattern']) if pattern.get('exclude_pattern') else None
    ctx = pattern.get('context_lines', 2)

    for i, line in enumerate(lines):
        if grep_re.search(line):
            # Check exclusion
            # Look at surrounding context for exclusion patterns
            ctx_start = max(0, i - ctx)
            ctx_end = min(len(lines), i + ctx + 1)
            context_block = '\n'.join(lines[ctx_start:ctx_end])

            if exclude_re and exclude_re.search(context_block):
                continue

            rel_path = os.path.relpath(str(filepath), project_root)
            hits.append({
                'file': rel_path,
                'line': i + 1,
                'match': line.strip(),
                'context': [
                    f"{j+1}: {lines[j]}"
                    for j in range(ctx_start, ctx_end)
                ],
            })

    return hits


def main():
    project_root = os.environ.get('CLAUDE_PROJECT_DIR', '.')
    patterns_file = os.path.join(project_root, 'docs', 'starry-reports', 'patterns.json')
    json_output = None

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--patterns':
            patterns_file = args[i + 1]
            i += 2
        elif args[i] == '--json':
            json_output = args[i + 1]
            i += 2
        else:
            project_root = args[i]
            i += 1

    # Load or create patterns
    if os.path.exists(patterns_file):
        with open(patterns_file) as f:
            patterns = json.load(f)
    else:
        patterns = DEFAULT_PATTERNS
        # Save default patterns
        os.makedirs(os.path.dirname(patterns_file), exist_ok=True)
        with open(patterns_file, 'w') as f:
            json.dump(patterns, f, indent=2)
        print(f"[pattern-scanner] Created default patterns at {patterns_file}", file=sys.stderr)

    # Scan
    all_hits = {}
    total_hits = 0

    for pattern in patterns:
        glob_re = glob_to_regex(pattern['file_glob'])
        pattern_hits = []

        for rs_file in Path(project_root).rglob('*.rs'):
            rel = os.path.relpath(str(rs_file), project_root)
            if glob_re.search(rel):
                pattern_hits.extend(scan_file(rs_file, pattern, project_root))

        all_hits[pattern['id']] = {
            'pattern': pattern,
            'hits': pattern_hits,
            'count': len(pattern_hits),
        }
        total_hits += len(pattern_hits)

    # Print report
    print("╔═══════════════════════════════════════════════════╗")
    print("║  Pattern Scanner — StarryOS Kernel                ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    print(f"Patterns loaded: {len(patterns)}")
    print(f"Total hits:      {total_hits}")
    print()

    for pid, data in all_hits.items():
        p = data['pattern']
        count = data['count']
        marker = "⚠ " if count > 0 else "✓ "
        print(f"{marker}{pid}: {count} hits [{p['severity']}] — {p['description']}")
        if count > 0 and count <= 10:
            for hit in data['hits']:
                print(f"    {hit['file']}:{hit['line']}: {hit['match']}")
        elif count > 10:
            for hit in data['hits'][:5]:
                print(f"    {hit['file']}:{hit['line']}: {hit['match']}")
            print(f"    ... and {count - 5} more")
        print()

    # JSON output
    result = {
        'total_patterns': len(patterns),
        'total_hits': total_hits,
        'patterns': all_hits,
        'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
    }

    if json_output:
        with open(json_output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"[pattern-scanner] JSON output: {json_output}")

    # Exit code: 1 if any P0 hits found
    p0_hits = sum(d['count'] for d in all_hits.values()
                  if d['pattern']['severity'] == 'P0')
    sys.exit(1 if p0_hits > 0 else 0)


if __name__ == '__main__':
    main()
