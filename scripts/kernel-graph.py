#!/usr/bin/env python3
"""
kernel-graph.py -- Deterministic source-level knowledge graph of the StarryOS kernel.

Scans the StarryOS syscall dispatch table and handler sources to produce a
structured JSON mapping of syscalls, subsystems, lock hotspots, and test coverage.

Usage:
    kernel-graph.py [project_root] [--json output.json]

Defaults project_root to $CLAUDE_PROJECT_DIR if set.
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KERNEL_REL = Path("os/StarryOS/kernel/src/syscall")
KNOWN_JSON_REL = Path("os/StarryOS/tests/known.json")

# Map directory/file stems to human-readable subsystem names.
SUBSYSTEM_MAP = {
    "fs":     "filesystem",
    "mm":     "memory",
    "net":    "networking",
    "task":   "process",
    "signal": "signals",
    "sync":   "sync",
    "time":   "time",
    "io_mpx": "io_multiplex",
    "ipc":    "ipc",
    "resources": "resources",
    "sys":    "system",
}

# Regex: extract  Sysno::<name> => <handler>(  from the dispatch match block.
# Handles `|` alternatives like `Sysno::faccessat | Sysno::faccessat2 => sys_faccessat2(`.
RE_DISPATCH = re.compile(
    r"Sysno::(\w+)(?:\s*\|\s*Sysno::(\w+))*\s*=>\s*(sys_\w+)\s*\(",
)

# Regex: `pub fn sys_<name>` at start of line.
RE_HANDLER_DEF = re.compile(r"^pub fn (sys_\w+)\s*\(", re.MULTILINE)

# Regex: lock / read-guard / write-guard acquisitions on named objects.
# We look for `.lock()`, `.read()`, `.write()` but filter obvious I/O false positives.
RE_LOCK = re.compile(r"(\w[\w.]*(?:\(\))?)\s*\.\s*(lock|read|write)\s*\(")
# Receiver names that are almost certainly I/O, not lock operations.
LOCK_FALSE_POSITIVES = {
    "f", "file", "dst", "src", "buf", "new()", "reader", "writer",
    "in_file", "out_file", "self",
}

# Regex: unsafe blocks or expressions.
RE_UNSAFE = re.compile(r"\bunsafe\b")

# Regex: cross-module calls (starry_vm::, starry_process::, starry_signal::, etc.)
# Matches both direct qualified paths and `use` imports with braces.
RE_CROSS_CALL = re.compile(r"(starry_\w+)::([\w:]+)")
RE_USE_IMPORT = re.compile(r"use\s+(starry_\w+)::\{([^}]+)\}")

# Regex: common kernel data-structure types.
KERNEL_TYPES = [
    "VmAreaStruct", "PageTable", "FdTable", "ProcessData",
    "SignalActions", "SignalSet", "SignalStack", "AddrSpace",
    "SharedPages", "IoVec", "MappingFlags", "FileBackend",
    "FS_CONTEXT", "FD_TABLE", "Pipe", "File",
]
RE_KERNEL_TYPES = re.compile(r"\b(" + "|".join(KERNEL_TYPES) + r")\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def classify_subsystem(rel_path: str) -> str:
    """Derive a subsystem tag from the relative path of a handler file."""
    parts = Path(rel_path).parts  # e.g. ('fs', 'io.rs') or ('signal.rs',)
    for part in parts:
        stem = part.removesuffix(".rs")
        if stem in SUBSYSTEM_MAP:
            return SUBSYSTEM_MAP[stem]
    return "other"


def sysno_to_name(sysno: str) -> str:
    """Normalise a Sysno variant to a syscall name (identity for now)."""
    return sysno


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def parse_dispatch(mod_rs: Path) -> list[tuple[list[str], str]]:
    """Return [(list_of_sysno_names, handler_fn_name), ...] from mod.rs."""
    text = mod_rs.read_text()
    results = []
    for m in RE_DISPATCH.finditer(text):
        names = [m.group(1)]
        # Capture any `| Sysno::xxx` alternatives from the full match text.
        full = m.group(0)
        for alt in re.finditer(r"Sysno::(\w+)", full):
            if alt.group(1) not in names:
                names.append(alt.group(1))
        handler = m.group(3)
        results.append((names, handler))
    return results


def build_handler_index(syscall_dir: Path) -> dict[str, str]:
    """Map handler function name -> relative file path (under syscall/)."""
    index: dict[str, str] = {}
    for rs_file in sorted(syscall_dir.rglob("*.rs")):
        if rs_file.name == "mod.rs" and rs_file.parent == syscall_dir:
            continue  # skip the dispatch file itself
        rel = str(rs_file.relative_to(syscall_dir))
        text = rs_file.read_text()
        for m in RE_HANDLER_DEF.finditer(text):
            index[m.group(1)] = rel
    return index


def analyse_file(filepath: Path) -> dict:
    """Extract locks, unsafe count, cross-module calls, and kernel types from a file."""
    text = filepath.read_text()

    locks = sorted({
        f"{m.group(1)}.{m.group(2)}()"
        for m in RE_LOCK.finditer(text)
        if m.group(1) not in LOCK_FALSE_POSITIVES
    })
    unsafe_count = len(RE_UNSAFE.findall(text))

    # Collect cross-module references: both qualified paths and `use` imports.
    cross_set: set[str] = set()
    for m in RE_CROSS_CALL.finditer(text):
        cross_set.add(f"{m.group(1)}::{m.group(2)}")
    for m in RE_USE_IMPORT.finditer(text):
        crate_name = m.group(1)
        for item in m.group(2).split(","):
            item = item.strip()
            if item:
                cross_set.add(f"{crate_name}::{item}")
    # Also catch simple `use starry_foo::bar;` (no braces).
    for m in RE_CROSS_CALL.finditer(text):
        cross_set.add(f"{m.group(1)}::{m.group(2)}")
    cross_calls = sorted(cross_set)

    types_used = sorted({m.group(1) for m in RE_KERNEL_TYPES.finditer(text)})

    return {
        "locks": locks,
        "unsafe_count": unsafe_count,
        "calls_to": cross_calls,
        "types_used": types_used,
    }


def load_known(project_root: Path) -> dict:
    """Load known.json if it exists; return the syscalls dict."""
    p = project_root / KNOWN_JSON_REL
    if not p.exists():
        return {}
    with open(p) as f:
        data = json.load(f)
    return data.get("syscalls", {})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # --- Argument parsing (minimal, no argparse dep needed) ---
    args = sys.argv[1:]
    json_out: str | None = None
    project_root: str | None = None

    i = 0
    while i < len(args):
        if args[i] == "--json" and i + 1 < len(args):
            json_out = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            die(f"Unknown flag: {args[i]}")
        elif project_root is None:
            project_root = args[i]
            i += 1
        else:
            die(f"Unexpected argument: {args[i]}")

    if project_root is None:
        project_root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_root:
        die("No project root given and CLAUDE_PROJECT_DIR not set.")

    root = Path(project_root).resolve()
    syscall_dir = root / KERNEL_REL
    mod_rs = syscall_dir / "mod.rs"

    if not mod_rs.exists():
        die(f"Cannot find dispatch file: {mod_rs}")

    # --- Parse ---
    dispatch = parse_dispatch(mod_rs)
    handler_index = build_handler_index(syscall_dir)
    known = load_known(root)

    # Cache per-file analysis so we don't re-read.
    file_cache: dict[str, dict] = {}

    def get_analysis(rel: str) -> dict:
        if rel not in file_cache:
            file_cache[rel] = analyse_file(syscall_dir / rel)
        return file_cache[rel]

    # --- Build syscall entries ---
    syscalls: dict[str, dict] = {}
    subsystem_index: dict[str, list[str]] = defaultdict(list)  # subsystem -> [syscall_name]
    lock_usage: dict[str, list[str]] = defaultdict(list)        # lock_expr -> [syscall_name]

    # Track dummy/noop syscalls handled by the catch-all arms.
    DUMMY_SYSNO = {
        "timerfd_create", "fanotify_init", "inotify_init1", "userfaultfd",
        "perf_event_open", "io_uring_setup", "bpf", "fsopen", "fspick",
        "open_tree", "memfd_secret",
    }
    NOOP_SYSNO = {"timer_create", "timer_gettime", "timer_settime"}

    for sysno_names, handler in dispatch:
        for sysno in sysno_names:
            name = sysno_to_name(sysno)
            rel_file = handler_index.get(handler, "")
            subsystem = classify_subsystem(rel_file) if rel_file else "unknown"

            entry: dict = {
                "handler": handler,
                "file": f"os/StarryOS/kernel/src/syscall/{rel_file}" if rel_file else "",
                "subsystem": subsystem,
            }

            if rel_file:
                analysis = get_analysis(rel_file)
                entry["locks"] = analysis["locks"]
                entry["unsafe_count"] = analysis["unsafe_count"]
                entry["calls_to"] = analysis["calls_to"]
            else:
                entry["locks"] = []
                entry["unsafe_count"] = 0
                entry["calls_to"] = []

            # known.json annotations
            kn = known.get(name, {})
            entry["in_known_json"] = name in known
            if kn:
                entry["status"] = kn.get("status", "unknown")
                entry["tested"] = kn.get("tested", False)
            else:
                entry["tested"] = False
                entry["status"] = "untested"

            syscalls[name] = entry
            subsystem_index[subsystem].append(name)

            for lk in entry["locks"]:
                lock_usage[lk].append(name)

    # Add dummy/noop syscalls not handled individually.
    for sysno in sorted(DUMMY_SYSNO):
        if sysno not in syscalls:
            syscalls[sysno] = {
                "handler": "sys_dummy_fd",
                "file": "os/StarryOS/kernel/src/syscall/fs/io.rs",
                "subsystem": "filesystem",
                "locks": [],
                "unsafe_count": 0,
                "calls_to": [],
                "in_known_json": sysno in known,
                "tested": False,
                "status": "stub",
            }
            subsystem_index["filesystem"].append(sysno)

    for sysno in sorted(NOOP_SYSNO):
        if sysno not in syscalls:
            syscalls[sysno] = {
                "handler": "<noop>",
                "file": "os/StarryOS/kernel/src/syscall/mod.rs",
                "subsystem": "time",
                "locks": [],
                "unsafe_count": 0,
                "calls_to": [],
                "in_known_json": sysno in known,
                "tested": False,
                "status": "noop",
            }
            subsystem_index["time"].append(sysno)

    # --- Compute related syscalls (same file) ---
    file_to_syscalls: dict[str, list[str]] = defaultdict(list)
    for name, entry in syscalls.items():
        if entry["file"]:
            file_to_syscalls[entry["file"]].append(name)

    for name, entry in syscalls.items():
        same_file = sorted(
            s for s in file_to_syscalls.get(entry["file"], []) if s != name
        )
        same_sub = sorted(
            s for s in subsystem_index.get(entry["subsystem"], [])
            if s != name and s not in same_file
        )
        entry["related_syscalls"] = same_file + same_sub

    # --- Build subsystem summaries ---
    subsystems: dict[str, dict] = {}
    for sub, names in sorted(subsystem_index.items()):
        files_set: set[str] = set()
        total_locks = 0
        total_unsafe = 0
        for n in names:
            e = syscalls[n]
            if e["file"]:
                files_set.add(e["file"])
            total_locks += len(e["locks"])
            total_unsafe += e["unsafe_count"]
        subsystems[sub] = {
            "files": sorted(files_set),
            "syscalls": sorted(set(names)),
            "total_locks": total_locks,
            "total_unsafe": total_unsafe,
        }

    # --- Lock hotspots ---
    lock_hotspots = sorted(
        [{"lock": lk, "used_by_syscalls": sorted(set(names))}
         for lk, names in lock_usage.items()],
        key=lambda x: -len(x["used_by_syscalls"]),
    )

    # --- Stats ---
    total = len(syscalls)
    tested = sum(1 for e in syscalls.values() if e.get("tested"))
    buggy = sum(1 for e in syscalls.values() if e.get("status") == "buggy")
    fixed = sum(1 for e in syscalls.values() if e.get("status") == "fixed")
    stubs = sum(1 for e in syscalls.values() if e.get("status") in ("stub", "noop"))
    coverage = round(tested / total * 100, 1) if total else 0

    stats = {
        "total_syscalls": total,
        "tested": tested,
        "untested": total - tested,
        "buggy": buggy,
        "fixed": fixed,
        "stubs": stubs,
        "coverage_pct": coverage,
    }

    # --- Assemble output ---
    graph = {
        "syscalls": dict(sorted(syscalls.items())),
        "subsystems": subsystems,
        "lock_hotspots": lock_hotspots,
        "stats": stats,
    }

    # --- Write JSON ---
    if json_out:
        outpath = Path(json_out)
        outpath.parent.mkdir(parents=True, exist_ok=True)
        with open(outpath, "w") as f:
            json.dump(graph, f, indent=2)
        print(f"JSON written to {outpath}")

    # --- Human-readable summary ---
    print()
    print("=" * 60)
    print("  StarryOS Kernel Architecture Graph")
    print("=" * 60)
    print()
    print(f"  Total syscalls handled:  {total}")
    print(f"  Tested (in known.json):  {tested}")
    print(f"  Untested:                {total - tested}")
    print(f"  Buggy:                   {buggy}")
    print(f"  Fixed:                   {fixed}")
    print(f"  Stubs / noops:           {stubs}")
    print(f"  Test coverage:           {coverage}%")
    print()

    print("  Subsystems:")
    for sub, info in sorted(subsystems.items()):
        n = len(info["syscalls"])
        print(f"    {sub:16s}  {n:3d} syscalls, {len(info['files']):2d} files, "
              f"{info['total_locks']:3d} lock sites, {info['total_unsafe']:3d} unsafe sites")
    print()

    if lock_hotspots:
        print("  Lock hotspots (top 10):")
        for h in lock_hotspots[:10]:
            count = len(h["used_by_syscalls"])
            preview = ", ".join(h["used_by_syscalls"][:5])
            if count > 5:
                preview += f", ... (+{count - 5})"
            print(f"    {h['lock']:40s}  {count:3d} syscalls  [{preview}]")
        print()

    untested_subs = defaultdict(int)
    for name, e in syscalls.items():
        if not e.get("tested"):
            untested_subs[e["subsystem"]] += 1
    if untested_subs:
        print("  Untested by subsystem:")
        for sub, count in sorted(untested_subs.items(), key=lambda x: -x[1]):
            print(f"    {sub:16s}  {count}")
        print()

    print("=" * 60)


if __name__ == "__main__":
    main()
