#!/usr/bin/env python3
"""change-tracker.py — Deterministic change-impact tracker for StarryOS.

Checks what kernel files changed since the last harness run and flags
affected tests/findings for re-verification.

Usage:
    change-tracker.py [project_root] [--json output.json] [--since TIMESTAMP]

Reads docs/starry-reports/strategy.json for last_updated timestamp,
queries git for changed files, and cross-references against known.json
to produce a structured re-verification report.

This is fully deterministic — same git state always produces same output.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths we consider kernel-relevant (relative to project root)
# ---------------------------------------------------------------------------
KERNEL_PREFIXES = [
    "os/StarryOS/kernel/src/",
    "components/starry-vm/",
    "components/starry-process/",
    "components/starry-signal/",
    "components/kspin/",
    "os/arceos/modules/axsync/",
    "os/arceos/modules/axtask/",
    "os/arceos/modules/axmm/",
    "os/arceos/modules/axfs/",
    "os/arceos/modules/axnet/",
]

# ---------------------------------------------------------------------------
# File-path → affected-syscall mapping (directory-based heuristics)
# ---------------------------------------------------------------------------
# Each entry: regex on the relative path → list of affected syscalls.
# Order matters: first match wins unless the path matches multiple entries.
FILE_SYSCALL_MAP = [
    # Memory management
    (r"syscall/mm/mmap\.rs", ["mmap", "mremap", "munmap", "mprotect", "madvise"]),
    (r"syscall/mm/brk\.rs", ["brk"]),
    (r"syscall/mm/mincore\.rs", ["mincore"]),
    (r"syscall/mm/", ["mmap", "mremap", "munmap", "mprotect", "brk", "mincore", "madvise", "msync"]),
    # Filesystem I/O
    (r"syscall/fs/io\.rs", ["read", "write", "pread64", "pwrite64", "preadv2", "pwritev2",
                            "copy_file_range", "sendfile", "lseek", "ftruncate"]),
    (r"syscall/fs/fd_ops\.rs", ["fcntl_getfl", "fcntl_catchall", "flock", "dup3"]),
    (r"syscall/fs/stat\.rs", ["faccessat", "statx", "fstatat"]),
    (r"syscall/fs/memfd\.rs", ["memfd_create"]),
    (r"syscall/fs/pipe\.rs", ["pipe2"]),
    (r"syscall/fs/mount\.rs", ["mount", "umount2"]),
    (r"syscall/fs/ctl\.rs", ["ioctl"]),
    (r"syscall/fs/event\.rs", ["eventfd2"]),
    (r"syscall/fs/signalfd\.rs", ["signalfd4"]),
    (r"syscall/fs/pidfd\.rs", ["pidfd_open"]),
    (r"syscall/fs/", ["read", "write", "open", "close", "fcntl_getfl", "fcntl_catchall",
                       "flock", "faccessat", "copy_file_range", "lseek", "ftruncate",
                       "memfd_create", "dup3", "pipe2"]),
    # Networking
    (r"syscall/net/socket\.rs", ["socket", "bind", "listen", "connect", "accept4",
                                  "shutdown", "setsockopt", "getsockopt"]),
    (r"syscall/net/io\.rs", ["sendto", "recvfrom", "sendmsg", "recvmsg"]),
    (r"syscall/net/", ["socket", "bind", "listen", "connect", "accept4", "sendto",
                        "recvfrom", "sendmsg", "recvmsg", "shutdown",
                        "setsockopt", "getsockopt"]),
    # I/O multiplexing
    (r"syscall/io_mpx/", ["epoll_create1", "epoll_ctl", "epoll_wait", "select",
                           "pselect6", "ppoll", "poll"]),
    # Task management
    (r"syscall/task/clone\.rs", ["clone"]),
    (r"syscall/task/clone3\.rs", ["clone3"]),
    (r"syscall/task/execve\.rs", ["execve"]),
    (r"syscall/task/exit\.rs", ["exit", "exit_group"]),
    (r"syscall/task/wait\.rs", ["wait4", "waitid"]),
    (r"syscall/task/ctl\.rs", ["prctl"]),
    (r"syscall/task/schedule\.rs", ["sched_getaffinity", "sched_setaffinity"]),
    (r"syscall/task/", ["clone", "clone3", "execve", "exit", "wait4", "prctl",
                         "sched_getaffinity", "sched_setaffinity"]),
    # Synchronization
    (r"syscall/sync/", ["futex"]),
    # IPC
    (r"syscall/ipc/", ["shmget", "shmat", "shmdt", "shmctl",
                        "semget", "semop", "semctl",
                        "msgget", "msgsnd", "msgrcv", "msgctl"]),
    # Top-level syscall files
    (r"syscall/signal\.rs", ["rt_sigaction", "rt_sigprocmask", "rt_sigreturn",
                              "kill", "tgkill", "sigaltstack"]),
    (r"syscall/time\.rs", ["clock_gettime", "clock_getres", "timerfd_create",
                            "timerfd_settime"]),
    (r"syscall/resources\.rs", ["prlimit64", "getrusage_children", "getrlimit"]),
    (r"syscall/sys\.rs", ["getrandom", "getgroups", "uname"]),
    # Component-level broad mappings
    (r"components/starry-vm/", ["mmap", "mremap", "munmap", "mprotect", "madvise",
                                 "msync", "mincore", "brk"]),
    (r"components/starry-process/", ["clone", "clone3", "execve", "exit", "wait4",
                                      "prctl", "getrusage_children"]),
    (r"components/starry-signal/", ["rt_sigaction", "rt_sigprocmask", "rt_sigreturn",
                                     "kill", "tgkill", "sigaltstack"]),
    (r"components/kspin/", []),  # Sentinel: handled specially for concurrency
    # ArceOS module-level broad mappings
    (r"os/arceos/modules/axsync/", []),
    (r"os/arceos/modules/axtask/", ["clone", "clone3", "exit", "wait4",
                                     "sched_getaffinity", "sched_setaffinity", "futex"]),
    (r"os/arceos/modules/axmm/", ["mmap", "mremap", "munmap", "mprotect", "brk"]),
    (r"os/arceos/modules/axfs/", ["read", "write", "open", "close", "lseek",
                                   "ftruncate", "faccessat", "statx", "mount"]),
    (r"os/arceos/modules/axnet/", ["socket", "bind", "listen", "connect", "accept4",
                                    "sendto", "recvfrom"]),
]

# Concurrency-related components: any change here decays confidence on
# all concurrency findings.
CONCURRENCY_PATHS = [
    "components/kspin/",
    "os/arceos/modules/axsync/",
]


def is_kernel_relevant(path):
    """Return True if path matches any kernel-relevant prefix."""
    return any(path.startswith(p) for p in KERNEL_PREFIXES)


def get_changed_files(project_root, since):
    """Run git log --since to get files changed since timestamp."""
    cmd = [
        "git", "-C", project_root,
        "log", f"--since={since}", "--name-only", "--pretty=format:",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[change-tracker] git log failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    # Deduplicate and filter empty lines, sort for determinism
    files = sorted(set(line.strip() for line in result.stdout.splitlines() if line.strip()))
    return files


def map_files_to_syscalls(changed_files, kernel_graph):
    """Map changed files to affected syscalls using kernel-graph or heuristics."""
    affected = set()
    concurrency_affected = False

    for fpath in changed_files:
        # Check concurrency paths first
        if any(fpath.startswith(cp) for cp in CONCURRENCY_PATHS):
            concurrency_affected = True

        # Try kernel-graph.json for precise mapping
        if kernel_graph:
            entry = kernel_graph.get(fpath)
            if entry and "syscalls" in entry:
                affected.update(entry["syscalls"])
                continue

        # Fall back to directory-based heuristics
        for pattern, syscalls in FILE_SYSCALL_MAP:
            if re.search(pattern, fpath):
                affected.update(syscalls)
                break

    return sorted(affected), concurrency_affected


def cross_reference_known(known, affected_syscalls, changed_files, concurrency_affected):
    """Cross-reference affected syscalls against known.json."""
    tests_to_rerun = []
    findings_to_reverify = []
    confidence_decayed = []

    syscalls = known.get("syscalls", {})
    for name, info in sorted(syscalls.items()):
        # Direct syscall match
        if name in affected_syscalls:
            test_file = info.get("test", "")
            if test_file and info.get("tested"):
                test_name = Path(test_file).stem
                if test_name not in tests_to_rerun:
                    tests_to_rerun.append(test_name)

            # Decay confidence on findings associated with this syscall
            for bug in info.get("bugs", []):
                if not bug.startswith("FIXED:"):
                    confidence_decayed.append({
                        "bug": name,
                        "reason": f"source file modified since last verification",
                    })
                    break  # One entry per syscall

        # Check if the test source file itself changed
        test_file = info.get("test", "")
        if test_file:
            # test paths in known.json are relative to os/StarryOS/
            full_test_path = f"os/StarryOS/{test_file}"
            if full_test_path in changed_files:
                test_name = Path(test_file).stem
                if test_name not in tests_to_rerun:
                    tests_to_rerun.append(test_name)

    # Concurrency: flag all concurrency-related findings
    if concurrency_affected:
        for name, info in sorted(syscalls.items()):
            if name in ("futex",) or any("concurren" in b.lower() or "lock" in b.lower()
                                         or "race" in b.lower() for b in info.get("bugs", [])):
                findings_to_reverify.append(f"concurrency-{name}")

    # Build findings_to_reverify from reviews in strategy.json (handled by caller)
    return sorted(tests_to_rerun), sorted(findings_to_reverify), confidence_decayed


def load_json_file(path):
    """Load a JSON file, return None if missing."""
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return None


def main():
    project_root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    json_output = None
    since_override = None

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--json":
            json_output = args[i + 1]
            i += 2
        elif args[i] == "--since":
            since_override = args[i + 1]
            i += 2
        elif args[i] == "--help" or args[i] == "-h":
            print(__doc__.strip())
            sys.exit(0)
        else:
            project_root = args[i]
            i += 1

    project_root = os.path.abspath(project_root)

    # 1. Determine timestamp
    strategy_path = os.path.join(project_root, "docs", "starry-reports", "strategy.json")
    strategy = load_json_file(strategy_path)

    if since_override:
        since = since_override
    elif strategy and "last_updated" in strategy:
        since = strategy["last_updated"]
    else:
        print("[change-tracker] No strategy.json or --since provided; defaulting to 7 days.",
              file=sys.stderr)
        since = "7.days.ago"

    # 2. Get changed files from git
    all_changed = get_changed_files(project_root, since)
    kernel_changed = sorted(f for f in all_changed if is_kernel_relevant(f))

    # 3. Load optional kernel-graph.json for precise mapping
    graph_path = os.path.join(project_root, "docs", "starry-reports", "kernel-graph.json")
    kernel_graph = load_json_file(graph_path)

    # 4. Map to affected syscalls
    affected_syscalls, concurrency_affected = map_files_to_syscalls(kernel_changed, kernel_graph)

    # 5. Cross-reference with known.json
    known_path = os.path.join(project_root, "os", "StarryOS", "tests", "known.json")
    known = load_json_file(known_path) or {}

    tests_to_rerun, findings_to_reverify, confidence_decayed = cross_reference_known(
        known, affected_syscalls, all_changed, concurrency_affected,
    )

    # Flag review entries from strategy.json whose syscalls are affected
    if strategy and "reviews" in strategy:
        for review_id, review in sorted(strategy["reviews"].items()):
            # Extract syscall name from review ID (e.g., "BUG-001-prlimit64" → "prlimit64")
            parts = review_id.split("-")
            syscall_name = parts[-1] if len(parts) >= 3 else review_id
            if syscall_name in affected_syscalls:
                if review_id not in findings_to_reverify:
                    findings_to_reverify.append(review_id)

    findings_to_reverify.sort()

    # 6. Build result
    result = {
        "since": since,
        "files_changed": kernel_changed,
        "affected_syscalls": affected_syscalls,
        "tests_to_rerun": tests_to_rerun,
        "findings_to_reverify": findings_to_reverify,
        "confidence_decayed": confidence_decayed,
    }

    # Print human-readable report
    print("+" + "=" * 55 + "+")
    print("|  Change Tracker -- StarryOS Kernel                    |")
    print("+" + "=" * 55 + "+")
    print()
    print(f"Since:              {since}")
    print(f"Files changed:      {len(kernel_changed)} kernel-relevant ({len(all_changed)} total)")
    print(f"Affected syscalls:  {len(affected_syscalls)}")
    print(f"Tests to re-run:    {len(tests_to_rerun)}")
    print(f"Findings to verify: {len(findings_to_reverify)}")
    print()

    if kernel_changed:
        print("Changed kernel files:")
        for f in kernel_changed:
            print(f"  {f}")
        print()

    if affected_syscalls:
        print("Affected syscalls:")
        print(f"  {', '.join(affected_syscalls)}")
        print()

    if tests_to_rerun:
        print("Tests to re-run:")
        for t in tests_to_rerun:
            print(f"  {t}")
        print()

    if findings_to_reverify:
        print("Findings to re-verify:")
        for f in findings_to_reverify:
            print(f"  {f}")
        print()

    if confidence_decayed:
        print("Confidence decayed:")
        for d in confidence_decayed:
            print(f"  {d['bug']}: {d['reason']}")
        print()

    if not kernel_changed:
        print("No kernel-relevant changes detected since last run.")
        print()

    # Write JSON output
    if json_output:
        os.makedirs(os.path.dirname(json_output) or ".", exist_ok=True)
        with open(json_output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"[change-tracker] JSON written to {json_output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
