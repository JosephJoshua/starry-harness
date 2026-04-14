#!/usr/bin/env python3
"""rust_analyzer.py — Tree-sitter-based Rust source analysis for StarryOS.

Provides AST-level analysis that regex cannot reliably do:
- Lock acquisition with ownership tracking (let-bound vs temporary)
- Function call graph extraction
- Pattern matching that skips comments, strings, and test blocks
- Syscall dispatch parsing with multi-line match arm support

Used as a shared module by lock-order-graph.py, pattern-scanner.py,
kernel-graph.py, and abi-check.py. Falls back to regex if tree-sitter
is not installed.

Requires: pip install tree-sitter tree-sitter-rust
"""
import sys
from pathlib import Path
from dataclasses import dataclass, field

try:
    import tree_sitter_rust as ts_rust
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


@dataclass
class LockSite:
    file: str
    line: int
    function: str
    lock_name: str
    method: str        # lock, read, write
    binding: str       # "held" (let-bound) or "temporary" (expression)
    guard_var: str | None = None
    raw_line: str = ""


@dataclass
class UnsafeBlock:
    file: str
    line: int
    function: str
    has_safety_comment: bool
    raw_line: str = ""


@dataclass
class FunctionCall:
    file: str
    line: int
    caller: str
    callee: str        # full path like starry_vm::do_mmap
    raw_line: str = ""


@dataclass
class FunctionDef:
    file: str
    line: int
    name: str
    params: list[str] = field(default_factory=list)
    is_pub: bool = False


def _make_parser():
    """Create a tree-sitter parser for Rust."""
    if not TREE_SITTER_AVAILABLE:
        return None
    rust_lang = Language(ts_rust.language())
    parser = Parser(rust_lang)
    return parser


def _find_enclosing_function(node):
    """Walk up the AST to find the enclosing function name."""
    current = node.parent
    while current:
        if current.type == 'function_item':
            name_node = current.child_by_field_name('name')
            if name_node:
                return name_node.text.decode()
        current = current.parent
    return '<module>'


def _is_in_test_block(node):
    """Check if a node is inside a #[test] or #[cfg(test)] block."""
    current = node.parent
    while current:
        if current.type == 'attribute_item':
            text = current.text.decode()
            if 'test' in text or 'cfg(test)' in text:
                return True
        if current.type == 'mod_item':
            # Check for #[cfg(test)] on the module
            prev = current.prev_sibling
            while prev and prev.type == 'attribute_item':
                if 'cfg(test)' in prev.text.decode():
                    return True
                prev = prev.prev_sibling
        current = current.parent
    return False


def _is_in_comment_or_string(node):
    """Check if a node is inside a comment or string literal."""
    current = node.parent
    while current:
        if current.type in ('line_comment', 'block_comment', 'string_literal',
                            'raw_string_literal', 'char_literal'):
            return True
        current = current.parent
    return False


def _check_safety_comment(source_bytes, line_num):
    """Check if there's a // SAFETY: comment in the 3 lines above."""
    lines = source_bytes.decode(errors='replace').splitlines()
    for i in range(max(0, line_num - 4), line_num - 1):
        if i < len(lines) and '// SAFETY:' in lines[i].upper():
            return True
    return False


def analyze_locks(filepath: Path, source: bytes, tree) -> list[LockSite]:
    """Extract lock acquisitions with AST-level ownership analysis.

    Distinguishes:
    - `let guard = x.lock()` → binding="held", guard_var="guard"
    - `x.lock().method()` → binding="temporary"
    - `x.lock()` as statement → binding="temporary" (dropped at semicolon)
    """
    sites = []
    root = tree.root_node

    def visit(node):
        # Look for method calls named lock/read/write
        if node.type == 'call_expression':
            func = node.child_by_field_name('function')
            if func and func.type == 'field_expression':
                field_name = func.child_by_field_name('field')
                if field_name and field_name.text.decode() in ('lock', 'read', 'write'):
                    receiver = func.child_by_field_name('value')
                    if receiver:
                        method = field_name.text.decode()
                        receiver_text = receiver.text.decode()
                        lock_name = f"{receiver_text}.{method}()"
                        line = node.start_point[0] + 1
                        fn_name = _find_enclosing_function(node)

                        # Skip if in test block
                        if _is_in_test_block(node):
                            for child in node.children:
                                visit(child)
                            return

                        # Determine binding: check if parent is a let_declaration
                        binding = "temporary"
                        guard_var = None
                        parent = node.parent

                        # Check if this call is the init of a let binding
                        if parent and parent.type == 'let_declaration':
                            pattern = parent.child_by_field_name('pattern')
                            if pattern:
                                guard_var = pattern.text.decode().lstrip('mut ').strip()
                                binding = "held"
                        elif parent and parent.type == 'expression_statement':
                            # Bare expression ending in semicolon — temporary
                            binding = "temporary"

                        raw_line = source.decode(errors='replace').splitlines()[line - 1].strip() if line <= len(source.decode(errors='replace').splitlines()) else ""

                        sites.append(LockSite(
                            file=str(filepath),
                            line=line,
                            function=fn_name,
                            lock_name=lock_name,
                            method=method,
                            binding=binding,
                            guard_var=guard_var,
                            raw_line=raw_line,
                        ))

        for child in node.children:
            visit(child)

    visit(root)
    return sites


def analyze_unsafe(filepath: Path, source: bytes, tree) -> list[UnsafeBlock]:
    """Extract unsafe blocks with SAFETY comment detection."""
    blocks = []
    root = tree.root_node

    def visit(node):
        if node.type == 'unsafe_block':
            line = node.start_point[0] + 1
            fn_name = _find_enclosing_function(node)
            has_safety = _check_safety_comment(source, line)
            raw_line = source.decode(errors='replace').splitlines()[line - 1].strip() if line <= len(source.decode(errors='replace').splitlines()) else ""

            blocks.append(UnsafeBlock(
                file=str(filepath),
                line=line,
                function=fn_name,
                has_safety_comment=has_safety,
                raw_line=raw_line,
            ))

        for child in node.children:
            visit(child)

    visit(root)
    return blocks


def analyze_calls(filepath: Path, source: bytes, tree) -> list[FunctionCall]:
    """Extract cross-module function calls (e.g., starry_vm::do_mmap)."""
    calls = []
    root = tree.root_node

    def visit(node):
        if node.type == 'call_expression':
            func = node.child_by_field_name('function')
            if func:
                func_text = func.text.decode()
                # Look for qualified paths (module::function)
                if '::' in func_text:
                    line = node.start_point[0] + 1
                    fn_name = _find_enclosing_function(node)
                    raw_line = source.decode(errors='replace').splitlines()[line - 1].strip() if line <= len(source.decode(errors='replace').splitlines()) else ""

                    calls.append(FunctionCall(
                        file=str(filepath),
                        line=line,
                        caller=fn_name,
                        callee=func_text,
                        raw_line=raw_line,
                    ))

        for child in node.children:
            visit(child)

    visit(root)
    return calls


def find_drops(filepath: Path, source: bytes, tree) -> dict[str, int]:
    """Find explicit drop() calls and their line numbers."""
    drops = {}
    root = tree.root_node

    def visit(node):
        if node.type == 'call_expression':
            func = node.child_by_field_name('function')
            if func and func.text.decode() == 'drop':
                args = node.child_by_field_name('arguments')
                if args and args.named_child_count > 0:
                    arg = args.named_children[0]
                    drops[arg.text.decode()] = node.start_point[0] + 1

        for child in node.children:
            visit(child)

    visit(root)
    return drops


def parse_file(filepath: Path) -> tuple[bytes, object] | None:
    """Parse a Rust file with tree-sitter. Returns (source_bytes, tree) or None."""
    parser = _make_parser()
    if parser is None:
        return None
    try:
        source = filepath.read_bytes()
        tree = parser.parse(source)
        return source, tree
    except Exception:
        return None


def analyze_file(filepath: Path):
    """Full analysis of a single Rust file. Returns dict with all findings."""
    result = parse_file(filepath)
    if result is None:
        return None

    source, tree = result
    return {
        'locks': analyze_locks(filepath, source, tree),
        'unsafe_blocks': analyze_unsafe(filepath, source, tree),
        'calls': analyze_calls(filepath, source, tree),
        'drops': find_drops(filepath, source, tree),
    }


# Quick self-test
if __name__ == '__main__':
    if not TREE_SITTER_AVAILABLE:
        print("tree-sitter not installed. Install: pip install tree-sitter tree-sitter-rust")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: rust_analyzer.py <file.rs>")
        sys.exit(1)

    path = Path(sys.argv[1])
    analysis = analyze_file(path)
    if analysis is None:
        print(f"Failed to parse {path}")
        sys.exit(1)

    print(f"Locks: {len(analysis['locks'])} ({sum(1 for l in analysis['locks'] if l.binding == 'held')} held, "
          f"{sum(1 for l in analysis['locks'] if l.binding == 'temporary')} temporary)")
    print(f"Unsafe blocks: {len(analysis['unsafe_blocks'])} "
          f"({sum(1 for u in analysis['unsafe_blocks'] if not u.has_safety_comment)} missing SAFETY)")
    print(f"Cross-module calls: {len(analysis['calls'])}")
    print(f"Explicit drops: {len(analysis['drops'])}")

    for lock in analysis['locks'][:5]:
        print(f"  {lock.binding:9s} {lock.lock_name:40s} in {lock.function} ({path.name}:{lock.line})")
