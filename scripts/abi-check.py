#!/usr/bin/env python3
"""abi-check.py — Compare StarryOS syscall arg counts against Linux kernel SYSCALL_DEFINE arity.

Parses os/StarryOS/kernel/src/syscall/mod.rs to count uctx.argN() calls per syscall,
then compares against a known-good table of Linux kernel SYSCALL_DEFINE arities.

This catches ABI mismatches where StarryOS reads the wrong number of arguments
(e.g., pwritev2 reading 5 args when Linux passes 6 via pos_l/pos_h split).

Fully deterministic — no LLM reasoning.

Usage:
    abi-check.py [project_root] [--json output.json]
"""
import re
import sys
import json
import os
from pathlib import Path
from collections import defaultdict

# Linux kernel SYSCALL_DEFINE arities (from kernel source SYSCALL_DEFINE macros)
# Format: syscall_name -> expected_arg_count
# Source: https://elixir.bootlin.com/linux/latest/source
LINUX_ARITY = {
    # fs/read_write.c
    "read": 3, "write": 3, "pread64": 4, "pwrite64": 4,
    "readv": 3, "writev": 3,
    "preadv": 5, "pwritev": 5,    # Note: 5 on 64-bit (no pos_l/pos_h split)
    "preadv2": 6, "pwritev2": 6,  # 6 on 64-bit: fd, iov, cnt, pos_l, pos_h, flags
    "lseek": 3, "sendfile": 4,
    # fs/open.c
    "openat": 4, "close": 1, "ftruncate": 2, "truncate": 1,
    "faccessat": 3, "faccessat2": 4, "fchmodat": 3, "fchmod": 2,
    "fchownat": 5, "fchown": 3,
    # fs/stat.c
    "fstat": 2, "fstatat": 4, "statx": 5, "statfs": 2, "fstatfs": 2,
    # fs/fcntl.c
    "fcntl": 3, "dup": 1, "dup2": 2, "dup3": 3,
    # fs/ioctl.c
    "ioctl": 3,
    # fs/namespace.c
    "mount": 5, "umount2": 2, "pivot_root": 2,
    # fs/dcache.c / fs/namei.c
    "mkdir": 2, "mkdirat": 3, "rmdir": 1,
    "unlink": 1, "unlinkat": 3,
    "rename": 2, "renameat": 4, "renameat2": 5,
    "link": 2, "linkat": 5, "symlink": 2, "symlinkat": 3,
    "readlinkat": 4,
    # fs/readdir.c
    "getdents64": 3,
    # fs/pipe.c
    "pipe2": 2,
    # fs/eventfd.c
    "eventfd2": 2,
    # fs/select.c / fs/eventpoll.c
    "select": 5, "pselect6": 6, "poll": 3, "ppoll": 5,
    "epoll_create1": 1, "epoll_ctl": 4, "epoll_wait": 4, "epoll_pwait": 6,
    # fs/signalfd.c
    "signalfd4": 3,
    # fs/splice.c
    "copy_file_range": 6,
    # mm/mmap.c
    "mmap": 6, "munmap": 2, "mremap": 5, "mprotect": 3,
    "madvise": 3, "msync": 3, "mincore": 3, "brk": 1,
    # mm/memfd.c
    "memfd_create": 2,
    # kernel/fork.c
    "clone": 5, "clone3": 2,
    # fs/exec.c
    "execve": 3,
    # kernel/exit.c
    "exit": 1, "exit_group": 1,
    # kernel/sys.c
    "getpid": 0, "gettid": 0, "getppid": 0,
    "getuid": 0, "geteuid": 0, "getgid": 0, "getegid": 0,
    "setuid": 1, "setgid": 1, "setsid": 0,
    "setpgid": 2, "getpgrp": 0, "getpgid": 1,
    "uname": 1, "sysinfo": 1, "prctl": 5,
    "getgroups": 2, "setgroups": 2,
    "prlimit64": 4, "getrlimit": 2, "setrlimit": 2,
    "getrusage": 2,
    "getrandom": 3,
    # kernel/signal.c
    "rt_sigaction": 4, "rt_sigprocmask": 4, "rt_sigreturn": 0,
    "kill": 2, "tgkill": 3, "tkill": 2,
    "sigaltstack": 2,
    # kernel/sched
    "sched_yield": 0, "sched_getaffinity": 3, "sched_setaffinity": 3,
    "sched_getscheduler": 1, "sched_setscheduler": 3,
    # kernel/futex
    "futex": 6,
    # kernel/time
    "clock_gettime": 2, "gettimeofday": 2, "nanosleep": 2,
    "clock_nanosleep": 4,
    "timer_create": 3, "timer_settime": 4, "timer_gettime": 2, "timer_delete": 1,
    "timerfd_create": 2, "timerfd_settime": 4, "timerfd_gettime": 2,
    # net/socket.c
    "socket": 3, "bind": 3, "listen": 2, "accept": 3, "accept4": 4,
    "connect": 3, "getsockname": 3, "getpeername": 3,
    "sendto": 6, "recvfrom": 6, "sendmsg": 3, "recvmsg": 3,
    "setsockopt": 5, "getsockopt": 5, "shutdown": 2,
    "socketpair": 4,
    # ipc
    "shmget": 3, "shmat": 3, "shmdt": 1, "shmctl": 3,
    "semget": 3, "semop": 3, "semctl": 4,
    "msgget": 2, "msgsnd": 4, "msgrcv": 5, "msgctl": 3,
    # kernel/sys.c
    "set_tid_address": 1, "set_robust_list": 2,
    # fs/flock.c
    "flock": 2,
    # kernel/wait
    "wait4": 4, "waitid": 5,
    # misc
    "getcwd": 2, "chdir": 1, "fchdir": 1,
    "umask": 1, "access": 2,
}

# Regex to find syscall dispatch entries and their arg usage
SYSNO_RE = re.compile(r'Sysno::(\w+)\s*(?:\|[^=]*)?\s*=>')
ARG_RE = re.compile(r'uctx\.arg(\d+)\(\)')


def parse_dispatch(mod_rs_path: Path):
    """Parse mod.rs to extract syscall name -> max arg index used."""
    text = mod_rs_path.read_text()
    results = {}

    # Split by match arms
    # Find each Sysno::XXX => handler block
    for match in SYSNO_RE.finditer(text):
        syscall_name = match.group(1).lower()
        # Get the text from this match arm to the next Sysno:: or end of match
        start = match.end()
        next_match = SYSNO_RE.search(text, start)
        end = next_match.start() if next_match else len(text)
        block = text[start:end]

        # Find all uctx.argN() calls in this block
        args_used = set()
        for arg_match in ARG_RE.finditer(block):
            args_used.add(int(arg_match.group(1)))

        if args_used:
            max_arg = max(args_used)
            arg_count = max_arg + 1  # arg0 = first arg, so count = max + 1
            results[syscall_name] = {
                'args_used': sorted(args_used),
                'arg_count': arg_count,
            }

    return results


def main():
    project_root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('CLAUDE_PROJECT_DIR', '.')
    json_output = None
    if '--json' in sys.argv:
        idx = sys.argv.index('--json')
        json_output = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else 'abi-check.json'

    mod_rs = Path(project_root) / 'os' / 'StarryOS' / 'kernel' / 'src' / 'syscall' / 'mod.rs'
    if not mod_rs.exists():
        print(f"Error: {mod_rs} not found", file=sys.stderr)
        sys.exit(1)

    starry_args = parse_dispatch(mod_rs)

    mismatches = []
    matches = []
    unknown = []

    for syscall, info in sorted(starry_args.items()):
        linux_arity = LINUX_ARITY.get(syscall)
        if linux_arity is None:
            unknown.append({'syscall': syscall, 'starry_args': info['arg_count']})
        elif info['arg_count'] != linux_arity:
            mismatches.append({
                'syscall': syscall,
                'starry_args': info['arg_count'],
                'linux_args': linux_arity,
                'args_used': info['args_used'],
            })
        else:
            matches.append({'syscall': syscall, 'args': info['arg_count']})

    # Print report
    print("╔═══════════════════════════════════════════════════╗")
    print("║  ABI Arg Count Check — StarryOS vs Linux          ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    print(f"Syscalls parsed:    {len(starry_args)}")
    print(f"Matched Linux:      {len(matches)}")
    print(f"MISMATCHED:         {len(mismatches)}")
    print(f"Unknown (no ref):   {len(unknown)}")
    print()

    if mismatches:
        print("⚠  ABI MISMATCHES:")
        for m in mismatches:
            print(f"  {m['syscall']}: StarryOS reads {m['starry_args']} args, "
                  f"Linux expects {m['linux_args']} (used: arg{m['args_used']})")
        print()

    if unknown:
        print(f"Unknown syscalls (not in Linux reference table): {len(unknown)}")
        for u in unknown[:10]:
            print(f"  {u['syscall']}: reads {u['starry_args']} args")
        if len(unknown) > 10:
            print(f"  ... and {len(unknown) - 10} more")
        print()

    result = {
        'total_parsed': len(starry_args),
        'matched': len(matches),
        'mismatched': len(mismatches),
        'unknown': len(unknown),
        'mismatches': mismatches,
    }

    if json_output:
        with open(json_output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"[abi-check] JSON output: {json_output}")

    sys.exit(1 if mismatches else 0)


if __name__ == '__main__':
    main()
