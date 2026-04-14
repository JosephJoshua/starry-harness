#!/usr/bin/env python3
"""lock-order-graph.py — Static lock ordering analysis for StarryOS.

Scans Rust source files for lock acquisition patterns, builds a directed
graph of which locks are held when other locks are acquired, and detects
cycles (potential deadlocks).

Understands Rust ownership semantics:
  - `let guard = x.lock();`  → lock is HELD until end of scope or drop(guard)
  - `x.lock().do_thing();`   → temporary, dropped at semicolon, NOT held
  - `drop(guard);`           → explicit release before next acquisition

Usage:
    lock-order-graph.py [kernel_src_dir] [--json output.json]

This is fully deterministic — no LLM reasoning involved.
"""
import re
import sys
import json
import os
from collections import defaultdict
from pathlib import Path

# Try to use tree-sitter for accurate AST analysis; fall back to regex
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from rust_analyzer import analyze_file as ts_analyze_file, TREE_SITTER_AVAILABLE
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Compiled regexes
LOCK_RE = re.compile(r'(\w[\w.()]*)\.(lock|read|write)\(\)')
FN_RE = re.compile(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)')
UNSAFE_RE = re.compile(r'\bunsafe\b')
SAFETY_COMMENT_RE = re.compile(r'//\s*SAFETY:', re.IGNORECASE)

# Patterns for Rust ownership analysis
LET_BIND_RE = re.compile(r'let\s+(?:mut\s+)?(\w+)\s*=.*\.(lock|read|write)\(\)')
DROP_RE = re.compile(r'drop\(\s*(\w+)\s*\)')
SCOPE_OPEN_RE = re.compile(r'\{')
SCOPE_CLOSE_RE = re.compile(r'\}')
# Temporary: lock().method() on same line with no let binding
TEMP_LOCK_RE = re.compile(r'(?<!let\s)(?<!let\smut\s)\w[\w.()]*\.(lock|read|write)\(\)\s*\.')


def find_rust_files(root: Path):
    """Find all .rs files under root."""
    return sorted(root.rglob('*.rs'))


def extract_lock_sites(filepath: Path, lines: list[str]):
    """Extract lock acquisition sites with ownership analysis.

    Each site includes whether the lock guard is:
    - 'held': bound to a let variable (alive until scope end or drop)
    - 'temporary': used as a temporary expression (dropped at semicolon)
    """
    sites = []
    current_fn = None
    current_fn_line = 0

    for i, line in enumerate(lines, 1):
        fn_match = FN_RE.match(line)
        if fn_match:
            current_fn = fn_match.group(1)
            current_fn_line = i

        for lock_match in LOCK_RE.finditer(line):
            receiver = lock_match.group(1)
            method = lock_match.group(2)
            lock_name = f"{receiver}.{method}()"

            # Determine if the lock guard is held or temporary
            let_match = LET_BIND_RE.search(line)
            if let_match:
                binding = 'held'
                guard_var = let_match.group(1)
            elif TEMP_LOCK_RE.search(line) or (';' in line and 'let ' not in line):
                binding = 'temporary'
                guard_var = None
            else:
                # Conservative: assume held if we can't determine
                binding = 'held'
                guard_var = None

            sites.append({
                'file': str(filepath),
                'line': i,
                'function': current_fn or '<module>',
                'function_line': current_fn_line,
                'lock': lock_name,
                'method': method,
                'receiver': receiver,
                'binding': binding,
                'guard_var': guard_var,
                'raw_line': line.strip(),
            })

    return sites


def extract_unsafe_blocks(filepath: Path, lines: list[str]):
    """Extract unsafe blocks and check for SAFETY comments."""
    blocks = []
    current_fn = None
    for i, line in enumerate(lines, 1):
        fn_match = FN_RE.match(line)
        if fn_match:
            current_fn = fn_match.group(1)

        if UNSAFE_RE.search(line) and 'unsafe fn' not in line:
            has_safety = False
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


def find_drops_in_function(lines, fn_sites):
    """Find explicit drop() calls within a function's line range."""
    if not fn_sites:
        return {}
    min_line = min(s['line'] for s in fn_sites)
    max_line = max(s['line'] for s in fn_sites)
    drops = {}  # var_name -> line_number
    for i in range(min_line - 1, min(max_line + 50, len(lines))):
        for m in DROP_RE.finditer(lines[i]):
            drops[m.group(1)] = i + 1
    return drops


def build_lock_graph(sites, all_lines_by_file):
    """Build a directed graph with Rust ownership awareness.

    Edge A→B means lock A is demonstrably HELD when lock B is acquired:
    - A must be 'held' (let-bound), not 'temporary'
    - A must not have been drop()'d before B's line
    - A and B must be in the same function
    """
    graph = defaultdict(set)
    edge_evidence = defaultdict(list)  # For reporting

    by_function = defaultdict(list)
    for site in sites:
        key = (site['file'], site['function'])
        by_function[key].append(site)

    for (filepath, fn_name), fn_sites in by_function.items():
        fn_sites.sort(key=lambda s: s['line'])

        # Find drop() calls in this function
        file_lines = all_lines_by_file.get(filepath, [])
        drops = find_drops_in_function(file_lines, fn_sites)

        for i in range(len(fn_sites)):
            a = fn_sites[i]
            # Only create edges from HELD locks, not temporaries
            if a['binding'] == 'temporary':
                continue

            for j in range(i + 1, len(fn_sites)):
                b = fn_sites[j]

                # Check if A was explicitly dropped before B
                if a['guard_var'] and a['guard_var'] in drops:
                    drop_line = drops[a['guard_var']]
                    if drop_line < b['line']:
                        continue  # A was dropped before B acquired

                if a['lock'] != b['lock']:
                    graph[a['lock']].add(b['lock'])
                    edge_evidence[(a['lock'], b['lock'])].append({
                        'file': filepath,
                        'function': fn_name,
                        'a_line': a['line'],
                        'b_line': b['line'],
                        'a_binding': a['binding'],
                        'a_guard': a['guard_var'],
                    })

    return graph, edge_evidence


def find_cycles(graph):
    """Find all cycles in the directed graph using DFS."""
    cycles = []
    visited = set()
    rec_stack = set()

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, path)
            elif neighbor in rec_stack:
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
    sys.setrecursionlimit(5000)

    kernel_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('CLAUDE_PROJECT_DIR', '.')
    kernel_src = Path(kernel_dir) / 'os' / 'StarryOS' / 'kernel' / 'src'

    json_output = None
    if '--json' in sys.argv:
        idx = sys.argv.index('--json')
        json_output = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else 'lock-order.json'

    if not kernel_src.exists():
        print(f"Error: kernel source not found at {kernel_src}", file=sys.stderr)
        sys.exit(1)

    component_dirs = [
        Path(kernel_dir) / 'components' / 'kspin' / 'src',
        Path(kernel_dir) / 'os' / 'arceos' / 'modules' / 'axsync' / 'src',
        Path(kernel_dir) / 'components' / 'starry-vm' / 'src',
        Path(kernel_dir) / 'components' / 'starry-process' / 'src',
        Path(kernel_dir) / 'components' / 'starry-signal' / 'src',
    ]

    all_dirs = [kernel_src] + [d for d in component_dirs if d.exists()]

    # Single pass: read all files, extract lock sites and unsafe blocks
    all_sites = []
    all_unsafe = []
    all_lines_by_file = {}
    analysis_mode = "tree-sitter" if TREE_SITTER_AVAILABLE else "regex"

    for src_dir in all_dirs:
        for rs_file in find_rust_files(src_dir):
            if TREE_SITTER_AVAILABLE:
                # AST-based analysis — accurate ownership tracking
                result = ts_analyze_file(rs_file)
                if result is not None:
                    for lock in result['locks']:
                        all_sites.append({
                            'file': lock.file, 'line': lock.line,
                            'function': lock.function, 'function_line': 0,
                            'lock': lock.lock_name, 'method': lock.method,
                            'receiver': lock.lock_name.split('.')[0],
                            'binding': lock.binding, 'guard_var': lock.guard_var,
                            'raw_line': lock.raw_line,
                        })
                    for ub in result['unsafe_blocks']:
                        all_unsafe.append({
                            'file': ub.file, 'line': ub.line,
                            'function': ub.function,
                            'has_safety_comment': ub.has_safety_comment,
                            'raw_line': ub.raw_line,
                        })
                    all_lines_by_file[str(rs_file)] = rs_file.read_text().splitlines()
                    continue
            # Regex fallback
            try:
                lines = rs_file.read_text().splitlines()
            except Exception:
                continue
            all_lines_by_file[str(rs_file)] = lines
            all_sites.extend(extract_lock_sites(rs_file, lines))
            all_unsafe.extend(extract_unsafe_blocks(rs_file, lines))

    # Build graph with ownership awareness
    graph, edge_evidence = build_lock_graph(all_sites, all_lines_by_file)
    serializable_graph = {k: sorted(v) for k, v in graph.items()}

    cycles = find_cycles(graph)
    unsafe_missing_safety = [b for b in all_unsafe if not b['has_safety_comment']]

    # Count binding types
    held_count = sum(1 for s in all_sites if s['binding'] == 'held')
    temp_count = sum(1 for s in all_sites if s['binding'] == 'temporary')

    # Print report
    print("╔═══════════════════════════════════════════════════╗")
    print("║  Lock Order Analysis — StarryOS Kernel            ║")
    print(f"║  (analysis: {analysis_mode})" + " " * (41 - len(analysis_mode)) + "║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    print(f"Files scanned:        {len(set(s['file'] for s in all_sites))}")
    print(f"Lock acquisitions:    {len(all_sites)} ({held_count} held, {temp_count} temporary)")
    print(f"Unique locks:         {len(set(s['lock'] for s in all_sites))}")
    print(f"Lock ordering edges:  {sum(len(v) for v in graph.values())} (after filtering temporaries)")
    print(f"Cycles found:         {len(cycles)}")
    print(f"Unsafe blocks:        {len(all_unsafe)}")
    print(f"Unsafe without SAFETY: {len(unsafe_missing_safety)}")
    print()

    if cycles:
        print("⚠  POTENTIAL DEADLOCKS DETECTED:")
        for i, cycle in enumerate(cycles, 1):
            print(f"  Cycle {i}: {' → '.join(cycle)}")
            # Show evidence for each edge in the cycle
            for k in range(len(cycle) - 1):
                edge_key = (cycle[k], cycle[k + 1])
                evidence = edge_evidence.get(edge_key, [])
                for ev in evidence[:1]:  # Show first evidence only
                    rel = os.path.relpath(ev['file'], kernel_dir)
                    print(f"    {cycle[k]} → {cycle[k+1]}: {rel}:{ev['a_line']}-{ev['b_line']} "
                          f"in {ev['function']}() [guard={ev['a_guard'] or 'unknown'}]")
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
            'held_locks': held_count,
            'temporary_locks': temp_count,
            'unique_locks': len(set(s['lock'] for s in all_sites)),
            'ordering_edges': sum(len(v) for v in graph.values()),
            'cycles': len(cycles),
            'unsafe_blocks': len(all_unsafe),
            'unsafe_missing_safety': len(unsafe_missing_safety),
        },
        'cycles': cycles,
        'cycle_evidence': {
            f"{a}->{b}": evs for (a, b), evs in edge_evidence.items()
            if any(a in c and b in c for c in cycles)
        },
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
        print("--- JSON ---")
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
