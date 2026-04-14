#!/usr/bin/env python3
"""lock-order-graph.py — Static lock ordering analysis for StarryOS.

Scans Rust source files for lock acquisition patterns, builds a directed
graph of which locks are held when other locks are acquired, and detects
cycles (potential deadlocks).

Usage:
    lock-order-graph.py [kernel_src_dir] [--json output.json]

Output:
    - Lock acquisition sites with enclosing function
    - Directed graph of lock orderings
    - Any cycles found (potential deadlocks)
    - JSON output for downstream consumption

This is fully deterministic — no LLM reasoning involved.
"""
import re
import sys
import json
import os
from collections import defaultdict
from pathlib import Path

# Lock acquisition patterns in Rust
LOCK_PATTERNS = [
    # pattern, lock_type
    (r'\.lock\(\)', 'Mutex'),
    (r'\.read\(\)', 'RwLock-read'),
    (r'\.write\(\)', 'RwLock-write'),
    (r'SpinNoIrq::new\(', 'SpinNoIrq-init'),
]

# Compiled regex for lock calls
LOCK_RE = re.compile(r'(\w[\w.()]*)\.(lock|read|write)\(\)')

# Function definition pattern
FN_RE = re.compile(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)')

# Unsafe block pattern
UNSAFE_RE = re.compile(r'\bunsafe\b')
SAFETY_COMMENT_RE = re.compile(r'//\s*SAFETY:', re.IGNORECASE)


def find_rust_files(root: Path):
    """Find all .rs files under root."""
    return sorted(root.rglob('*.rs'))


def extract_lock_sites(filepath: Path):
    """Extract lock acquisition sites with their enclosing function."""
    sites = []
    current_fn = None
    current_fn_line = 0

    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return sites

    for i, line in enumerate(lines, 1):
        # Track current function
        fn_match = FN_RE.match(line)
        if fn_match:
            current_fn = fn_match.group(1)
            current_fn_line = i

        # Find lock acquisitions
        for lock_match in LOCK_RE.finditer(line):
            receiver = lock_match.group(1)
            method = lock_match.group(2)
            lock_name = f"{receiver}.{method}()"
            sites.append({
                'file': str(filepath),
                'line': i,
                'function': current_fn or '<module>',
                'function_line': current_fn_line,
                'lock': lock_name,
                'method': method,
                'receiver': receiver,
                'raw_line': line.strip(),
            })

    return sites


def extract_unsafe_blocks(filepath: Path):
    """Extract unsafe blocks and check for SAFETY comments."""
    blocks = []
    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return blocks

    current_fn = None
    for i, line in enumerate(lines, 1):
        fn_match = FN_RE.match(line)
        if fn_match:
            current_fn = fn_match.group(1)

        if UNSAFE_RE.search(line) and 'unsafe fn' not in line:
            has_safety = False
            # Check previous 3 lines for SAFETY comment
            for j in range(max(0, i - 4), i - 1):
                if SAFETY_COMMENT_RE.search(lines[j]):
                    has_safety = True
                    break
            blocks.append({
                'file': str(filepath),
                'line': i,
                'function': current_fn or '<module>',
                'has_safety_comment': has_safety,
                'raw_line': line.strip(),
            })

    return blocks


def build_lock_graph(sites):
    """Build a directed graph: edge A→B means lock A is held when lock B is acquired.

    Approximation: within the same function, if lock A is acquired before lock B,
    we assume A is still held when B is acquired (conservative).
    """
    graph = defaultdict(set)
    # Group sites by function
    by_function = defaultdict(list)
    for site in sites:
        key = (site['file'], site['function'])
        by_function[key].append(site)

    for key, fn_sites in by_function.items():
        # Sort by line number within function
        fn_sites.sort(key=lambda s: s['line'])
        # Each lock acquired after a previous lock creates an edge
        for i in range(len(fn_sites)):
            for j in range(i + 1, len(fn_sites)):
                a = fn_sites[i]['lock']
                b = fn_sites[j]['lock']
                if a != b:
                    graph[a].add(b)

    return graph


def find_cycles(graph):
    """Find all cycles in the directed graph using DFS."""
    cycles = []
    visited = set()
    rec_stack = set()
    parent = {}

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                parent[neighbor] = node
                dfs(neighbor, path)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)

        path.pop()
        rec_stack.discard(node)

    for node in graph:
        if node not in visited:
            dfs(node, [])

    return cycles


def main():
    # Parse arguments
    kernel_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('CLAUDE_PROJECT_DIR', '.')
    kernel_src = Path(kernel_dir) / 'os' / 'StarryOS' / 'kernel' / 'src'

    json_output = None
    if '--json' in sys.argv:
        idx = sys.argv.index('--json')
        json_output = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else 'lock-order.json'

    if not kernel_src.exists():
        print(f"Error: kernel source not found at {kernel_src}", file=sys.stderr)
        sys.exit(1)

    # Also scan key component sources
    component_dirs = [
        Path(kernel_dir) / 'components' / 'kspin' / 'src',
        Path(kernel_dir) / 'os' / 'arceos' / 'modules' / 'axsync' / 'src',
        Path(kernel_dir) / 'components' / 'starry-vm' / 'src',
        Path(kernel_dir) / 'components' / 'starry-process' / 'src',
        Path(kernel_dir) / 'components' / 'starry-signal' / 'src',
    ]

    all_dirs = [kernel_src] + [d for d in component_dirs if d.exists()]

    # Extract lock sites
    all_sites = []
    all_unsafe = []
    for src_dir in all_dirs:
        for rs_file in find_rust_files(src_dir):
            all_sites.extend(extract_lock_sites(rs_file))
            all_unsafe.extend(extract_unsafe_blocks(rs_file))

    # Build graph
    graph = build_lock_graph(all_sites)
    serializable_graph = {k: sorted(v) for k, v in graph.items()}

    # Find cycles
    cycles = find_cycles(graph)

    # Count unsafe without SAFETY
    unsafe_missing_safety = [b for b in all_unsafe if not b['has_safety_comment']]

    # Print report
    print("╔═══════════════════════════════════════════════════╗")
    print("║  Lock Order Analysis — StarryOS Kernel            ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    print(f"Files scanned:        {len(set(s['file'] for s in all_sites))}")
    print(f"Lock acquisitions:    {len(all_sites)}")
    print(f"Unique locks:         {len(set(s['lock'] for s in all_sites))}")
    print(f"Lock ordering edges:  {sum(len(v) for v in graph.values())}")
    print(f"Cycles found:         {len(cycles)}")
    print(f"Unsafe blocks:        {len(all_unsafe)}")
    print(f"Unsafe without SAFETY: {len(unsafe_missing_safety)}")
    print()

    if cycles:
        print("⚠  POTENTIAL DEADLOCKS DETECTED:")
        for i, cycle in enumerate(cycles, 1):
            print(f"  Cycle {i}: {' → '.join(cycle)}")
        print()

    if unsafe_missing_safety:
        print("⚠  UNSAFE BLOCKS WITHOUT SAFETY COMMENT:")
        for b in unsafe_missing_safety[:20]:
            rel = os.path.relpath(b['file'], kernel_dir)
            print(f"  {rel}:{b['line']} in {b['function']}: {b['raw_line']}")
        if len(unsafe_missing_safety) > 20:
            print(f"  ... and {len(unsafe_missing_safety) - 20} more")
        print()

    # Top functions by lock density
    fn_lock_count = defaultdict(int)
    for s in all_sites:
        fn_lock_count[f"{os.path.relpath(s['file'], kernel_dir)}:{s['function']}"] += 1
    top_fns = sorted(fn_lock_count.items(), key=lambda x: -x[1])[:10]
    if top_fns:
        print("Lock-dense functions (most acquisitions):")
        for fn, count in top_fns:
            print(f"  {fn}: {count} lock acquisitions")
        print()

    # JSON output
    result = {
        'summary': {
            'files_scanned': len(set(s['file'] for s in all_sites)),
            'lock_acquisitions': len(all_sites),
            'unique_locks': len(set(s['lock'] for s in all_sites)),
            'ordering_edges': sum(len(v) for v in graph.values()),
            'cycles': len(cycles),
            'unsafe_blocks': len(all_unsafe),
            'unsafe_missing_safety': len(unsafe_missing_safety),
        },
        'cycles': cycles,
        'graph': serializable_graph,
        'unsafe_missing_safety': [
            {'file': os.path.relpath(b['file'], kernel_dir), 'line': b['line'],
             'function': b['function'], 'raw_line': b['raw_line']}
            for b in unsafe_missing_safety
        ],
        'lock_dense_functions': [{'function': fn, 'count': c} for fn, c in top_fns],
    }

    if json_output:
        with open(json_output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"[lock-order] JSON output: {json_output}")
    else:
        # Print to stdout as JSON for piping
        print("--- JSON ---")
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
