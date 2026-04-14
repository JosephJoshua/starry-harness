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

# Tree-sitter integration for AST-aware scanning (eliminates false positives
# from comments, strings, and #[cfg(test)] blocks).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from rust_analyzer import parse_file, TREE_SITTER_AVAILABLE, \
        _is_in_test_block, _is_in_comment_or_string, _find_enclosing_function
except ImportError:
    TREE_SITTER_AVAILABLE = False

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
    {
        "id": "assert-unsigned-on-u32",
        "description": "assert_unsigned or similar check rejects valid u32 values >= 2^31 by treating them as signed",
        "grep_pattern": r"assert_unsigned|as i32.*< 0|\.is_negative",
        "file_glob": "os/StarryOS/kernel/src/**/*.rs",
        "exclude_pattern": r"i64|isize|signed",
        "context_lines": 3,
        "severity": "P1",
        "category": "correctness",
        "discovered_from": ["futex"]
    },
    {
        "id": "ab-ba-lock-pattern",
        "description": "Two let-bound lock guards in same function — potential AB/BA deadlock if another function reverses the order",
        "grep_pattern": r"let\s+(?:mut\s+)?\w+\s*=.*\.lock\(\)",
        "file_glob": "os/StarryOS/kernel/src/**/*.rs",
        "exclude_pattern": r"drop\(|#\[should_panic\]|test",
        "context_lines": 8,
        "severity": "P1",
        "category": "concurrency",
        "discovered_from": ["shm_deadlock"]
    },
]


def glob_to_regex(glob_pattern):
    """Convert a simple glob pattern to a regex for path matching."""
    pattern = glob_pattern.replace('.', r'\.')
    pattern = pattern.replace('**/', '(.*/)?')
    pattern = pattern.replace('*', '[^/]*')
    return re.compile('^' + pattern)


def _deepest_node_at_line(root, line_0indexed):
    """Find the deepest (most specific) AST node whose start is on the given line."""
    best = None

    def visit(node):
        nonlocal best
        if node.start_point[0] == line_0indexed:
            if best is None or node.start_point[1] >= best.start_point[1]:
                best = node
        for child in node.children:
            # Only descend if the child's range can cover our target line
            if child.start_point[0] <= line_0indexed <= child.end_point[0]:
                visit(child)

    visit(root)
    return best


def _is_ok_zero_structural(node):
    """Check if an Ok(0) node is a stub: last expr in a match arm or fn body.

    Returns True if Ok(0) appears as the tail expression of a match arm body
    or a function body (i.e., a silent stub), and not inside an assertion
    macro or test helper.
    """
    current = node.parent
    while current:
        if current.type == 'match_arm':
            # Ok(0) in a match arm — check it's the body (value) of the arm
            body = current.child_by_field_name('value')
            if body is not None and _node_spans_overlap(body, node):
                return True
            return False
        if current.type == 'block':
            # Check if this block is a function body and Ok(0) is the last expression
            parent_of_block = current.parent
            if parent_of_block and parent_of_block.type == 'function_item':
                # Is node the last named child (tail expression) of the block?
                named = [c for c in current.named_children if c.type != 'line_comment' and c.type != 'block_comment']
                if named and _node_spans_overlap(named[-1], node):
                    return True
            return False
        # Skip macro invocations (assert_eq!, debug_assert!, etc.)
        if current.type == 'macro_invocation':
            macro_name = current.child_by_field_name('macro')
            if macro_name:
                name_text = macro_name.text.decode()
                if 'assert' in name_text or 'debug' in name_text:
                    return False
            return False
        current = current.parent
    return False


def _node_spans_overlap(a, b):
    """Check if node b is contained within node a's span."""
    return (a.start_byte <= b.start_byte and b.end_byte <= a.end_byte)


def scan_file_treesitter(filepath, pattern, project_root, tree_cache):
    """Scan a file using tree-sitter AST to eliminate false positives.

    Skips matches inside comments, string literals, and #[cfg(test)] blocks.
    For the 'ok-zero-stub' pattern, only matches Ok(0) that is a structural
    stub (last expression in match arm or function body).
    """
    hits = []
    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return hits

    # Parse file (with caching)
    fpath = Path(filepath) if not isinstance(filepath, Path) else filepath
    cache_key = str(fpath)
    if cache_key not in tree_cache:
        result = parse_file(fpath)
        tree_cache[cache_key] = result
    result = tree_cache[cache_key]

    if result is None:
        # tree-sitter parse failed, fall back to regex for this file
        return scan_file_regex(filepath, pattern, project_root, tag_may_be_fp=True)

    source, tree = result
    root = tree.root_node
    grep_re = re.compile(pattern['grep_pattern'])
    exclude_re = re.compile(pattern['exclude_pattern']) if pattern.get('exclude_pattern') else None
    ctx = pattern.get('context_lines', 2)
    is_ok_zero = pattern['id'] == 'ok-zero-stub'

    for i, line in enumerate(lines):
        if not grep_re.search(line):
            continue

        # Check exclusion via surrounding context (same as regex path)
        ctx_start = max(0, i - ctx)
        ctx_end = min(len(lines), i + ctx + 1)
        context_block = '\n'.join(lines[ctx_start:ctx_end])
        if exclude_re and exclude_re.search(context_block):
            continue

        # Find the AST node at this line
        node = _deepest_node_at_line(root, i)
        if node is None:
            # No AST node found; still report the hit
            pass
        else:
            # Skip matches inside comments or string literals
            if node.type in ('line_comment', 'block_comment', 'string_literal',
                             'raw_string_literal', 'char_literal'):
                continue
            if _is_in_comment_or_string(node):
                continue

            # Skip matches inside #[cfg(test)] blocks
            if _is_in_test_block(node):
                continue

            # For ok-zero-stub: only match if it's a structural stub
            if is_ok_zero and not _is_ok_zero_structural(node):
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


def scan_file_regex(filepath, pattern, project_root, tag_may_be_fp=False):
    """Scan a single file for a pattern using regex. May produce false positives
    from matches inside comments, strings, or #[cfg(test)] blocks."""
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

            match_text = line.strip()
            if tag_may_be_fp:
                match_text += '  [regex-may-be-false-positive]'

            rel_path = os.path.relpath(str(filepath), project_root)
            hits.append({
                'file': rel_path,
                'line': i + 1,
                'match': match_text,
                'context': [
                    f"{j+1}: {lines[j]}"
                    for j in range(ctx_start, ctx_end)
                ],
            })

    return hits


def scan_file(filepath, pattern, project_root, tree_cache=None):
    """Scan a single file for a pattern, return hits.

    When tree-sitter is available, uses AST analysis to skip false positives
    from comments, strings, and test blocks. Falls back to regex otherwise.
    """
    if TREE_SITTER_AVAILABLE and tree_cache is not None:
        return scan_file_treesitter(filepath, pattern, project_root, tree_cache)
    else:
        # Regex fallback — may produce false positives from comments/strings/test blocks
        return scan_file_regex(filepath, pattern, project_root,
                               tag_may_be_fp=not TREE_SITTER_AVAILABLE)


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
    all_rs_files = list(Path(project_root).rglob('*.rs'))
    analysis_mode = "tree-sitter" if TREE_SITTER_AVAILABLE else "regex"
    tree_cache = {} if TREE_SITTER_AVAILABLE else None

    for pattern in patterns:
        glob_re = glob_to_regex(pattern['file_glob'])
        pattern_hits = []

        for rs_file in all_rs_files:
            rel = os.path.relpath(str(rs_file), project_root)
            if glob_re.match(rel):
                pattern_hits.extend(scan_file(rs_file, pattern, project_root, tree_cache))

        all_hits[pattern['id']] = {
            'pattern': pattern,
            'hits': pattern_hits,
            'count': len(pattern_hits),
        }
        total_hits += len(pattern_hits)

    # Print report
    print("╔═══════════════════════════════════════════════════╗")
    print("║  Pattern Scanner — StarryOS Kernel                ║")
    print(f"║  (analysis: {analysis_mode})" + " " * (37 - len(analysis_mode)) + "║")
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
        'analysis_mode': analysis_mode,
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
