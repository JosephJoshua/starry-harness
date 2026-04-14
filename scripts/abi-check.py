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

# Linux kernel SYSCALL_DEFINE arities and signatures.
#
# VERIFICATION: Every entry is from the Linux kernel source SYSCALL_DEFINE macros.
# Reference: https://elixir.bootlin.com/linux/v6.12/source
# To verify any entry: search for SYSCALL_DEFINE<N>(<name>, ...) at the URL above.
#
# NOTE on 64-bit vs 32-bit ABI:
# - On 64-bit (which StarryOS targets), most syscalls pass args directly in registers.
# - EXCEPTION: preadv2/pwritev2 pass the offset as TWO 32-bit halves (pos_l, pos_h)
#   even on 64-bit, because the syscall was designed for compat. This makes them
#   6-arg syscalls even on 64-bit, unlike preadv/pwritev which are 5-arg.
#   See: https://elixir.bootlin.com/linux/v6.12/source/fs/read_write.c
#
# Format: syscall_name -> (arg_count, "SYSCALL_DEFINE signature", "source file")
LINUX_ABI = {
    # fs/read_write.c — https://elixir.bootlin.com/linux/v6.12/source/fs/read_write.c
    "read":     (3, "unsigned int fd, char __user *buf, size_t count", "fs/read_write.c"),
    "write":    (3, "unsigned int fd, const char __user *buf, size_t count", "fs/read_write.c"),
    "pread64":  (4, "unsigned int fd, char __user *buf, size_t count, loff_t pos", "fs/read_write.c"),
    "pwrite64": (4, "unsigned int fd, const char __user *buf, size_t count, loff_t pos", "fs/read_write.c"),
    "readv":    (3, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen", "fs/read_write.c"),
    "writev":   (3, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen", "fs/read_write.c"),
    "preadv":   (5, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen, unsigned long pos_l, unsigned long pos_h", "fs/read_write.c"),
    "pwritev":  (5, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen, unsigned long pos_l, unsigned long pos_h", "fs/read_write.c"),
    "preadv2":  (6, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen, unsigned long pos_l, unsigned long pos_h, rwf_t flags", "fs/read_write.c"),
    "pwritev2": (6, "unsigned long fd, const struct iovec __user *vec, unsigned long vlen, unsigned long pos_l, unsigned long pos_h, rwf_t flags", "fs/read_write.c"),
    "lseek":    (3, "unsigned int fd, off_t offset, unsigned int whence", "fs/read_write.c"),
    "sendfile": (4, "int out_fd, int in_fd, off_t __user *offset, size_t count", "fs/read_write.c"),

    # fs/open.c — https://elixir.bootlin.com/linux/v6.12/source/fs/open.c
    "openat":     (4, "int dfd, const char __user *filename, int flags, umode_t mode", "fs/open.c"),
    "close":      (1, "unsigned int fd", "fs/open.c"),
    "ftruncate":  (2, "unsigned int fd, unsigned long length", "fs/open.c"),
    "truncate":   (2, "const char __user *path, long length", "fs/open.c"),
    "faccessat":  (3, "int dfd, const char __user *filename, int mode", "fs/open.c"),
    "faccessat2": (4, "int dfd, const char __user *filename, int mode, int flags", "fs/open.c"),
    "fchmodat":   (3, "int dfd, const char __user *filename, umode_t mode", "fs/open.c"),
    "fchmod":     (2, "unsigned int fd, umode_t mode", "fs/open.c"),
    "fchownat":   (5, "int dfd, const char __user *filename, uid_t user, gid_t group, int flag", "fs/open.c"),
    "fchown":     (3, "unsigned int fd, uid_t user, gid_t group", "fs/open.c"),

    # fs/stat.c — https://elixir.bootlin.com/linux/v6.12/source/fs/stat.c
    "fstat":    (2, "unsigned int fd, struct __old_kernel_stat __user *statbuf", "fs/stat.c"),
    "fstatat":  (4, "int dfd, const char __user *filename, struct stat __user *statbuf, int flag", "fs/stat.c"),
    "statx":    (5, "int dfd, const char __user *path, unsigned flags, unsigned mask, struct statx __user *buffer", "fs/stat.c"),
    "statfs":   (2, "const char __user *path, struct statfs __user *buf", "fs/statfs.c"),
    "fstatfs":  (2, "unsigned int fd, struct statfs __user *buf", "fs/statfs.c"),

    # fs/fcntl.c — https://elixir.bootlin.com/linux/v6.12/source/fs/fcntl.c
    "fcntl": (3, "unsigned int fd, unsigned int cmd, unsigned long arg", "fs/fcntl.c"),
    "dup":   (1, "unsigned int fildes", "fs/file.c"),
    "dup2":  (2, "unsigned int oldfd, unsigned int newfd", "fs/file.c"),
    "dup3":  (3, "unsigned int oldfd, unsigned int newfd, int flags", "fs/file.c"),

    # fs/ioctl.c
    "ioctl": (3, "unsigned int fd, unsigned int cmd, unsigned long arg", "fs/ioctl.c"),

    # fs/namespace.c
    "mount":      (5, "char __user *dev_name, char __user *dir_name, char __user *type, unsigned long flags, void __user *data", "fs/namespace.c"),
    "umount2":    (2, "char __user *name, int flags", "fs/namespace.c"),
    "pivot_root": (2, "const char __user *new_root, const char __user *put_old", "fs/namespace.c"),

    # fs/namei.c
    "mkdir":      (2, "const char __user *pathname, umode_t mode", "fs/namei.c"),
    "mkdirat":    (3, "int dfd, const char __user *pathname, umode_t mode", "fs/namei.c"),
    "rmdir":      (1, "const char __user *pathname", "fs/namei.c"),
    "unlink":     (1, "const char __user *pathname", "fs/namei.c"),
    "unlinkat":   (3, "int dfd, const char __user *pathname, int flag", "fs/namei.c"),
    "rename":     (2, "const char __user *oldname, const char __user *newname", "fs/namei.c"),
    "renameat":   (4, "int olddfd, const char __user *oldname, int newdfd, const char __user *newname", "fs/namei.c"),
    "renameat2":  (5, "int olddfd, const char __user *oldname, int newdfd, const char __user *newname, unsigned int flags", "fs/namei.c"),
    "link":       (2, "const char __user *oldname, const char __user *newname", "fs/namei.c"),
    "linkat":     (5, "int olddfd, const char __user *oldname, int newdfd, const char __user *newname, int flags", "fs/namei.c"),
    "symlink":    (2, "const char __user *old, const char __user *new", "fs/namei.c"),
    "symlinkat":  (3, "const char __user *oldname, int newdfd, const char __user *newname", "fs/namei.c"),
    "readlinkat": (4, "int dfd, const char __user *path, char __user *buf, int bufsiz", "fs/stat.c"),

    # fs/readdir.c
    "getdents64": (3, "unsigned int fd, struct linux_dirent64 __user *dirent, unsigned int count", "fs/readdir.c"),

    # fs/pipe.c
    "pipe2": (2, "int __user *fildes, int flags", "fs/pipe.c"),

    # fs/eventfd.c
    "eventfd2": (2, "unsigned int count, int flags", "fs/eventfd.c"),

    # fs/select.c / fs/eventpoll.c
    "select":       (5, "int n, fd_set __user *inp, fd_set __user *outp, fd_set __user *exp, struct __kernel_old_timeval __user *tvp", "fs/select.c"),
    "pselect6":     (6, "int n, fd_set __user *inp, fd_set __user *outp, fd_set __user *exp, struct __kernel_timespec __user *tsp, void __user *sig", "fs/select.c"),
    "poll":         (3, "struct pollfd __user *ufds, unsigned int nfds, int timeout", "fs/select.c"),
    "ppoll":        (5, "struct pollfd __user *ufds, unsigned int nfds, struct __kernel_timespec __user *tsp, const sigset_t __user *sigmask, size_t sigsetsize", "fs/select.c"),
    "epoll_create1": (1, "int flags", "fs/eventpoll.c"),
    "epoll_ctl":    (4, "int epfd, int op, int fd, struct epoll_event __user *event", "fs/eventpoll.c"),
    "epoll_wait":   (4, "int epfd, struct epoll_event __user *events, int maxevents, int timeout", "fs/eventpoll.c"),
    "epoll_pwait":  (6, "int epfd, struct epoll_event __user *events, int maxevents, int timeout, const sigset_t __user *sigmask, size_t sigsetsize", "fs/eventpoll.c"),

    # fs/signalfd.c
    "signalfd4": (3, "int ufd, sigset_t __user *user_mask, size_t sizemask, int flags", "fs/signalfd.c"),

    # fs/splice.c
    "copy_file_range": (6, "int fd_in, loff_t __user *off_in, int fd_out, loff_t __user *off_out, size_t len, unsigned int flags", "fs/splice.c"),

    # mm/mmap.c
    "mmap":     (6, "unsigned long addr, unsigned long len, unsigned long prot, unsigned long flags, unsigned long fd, unsigned long off", "arch/*/kernel/sys_*.c"),
    "munmap":   (2, "unsigned long addr, size_t len", "mm/mmap.c"),
    "mremap":   (5, "unsigned long addr, unsigned long old_len, unsigned long new_len, unsigned long flags, unsigned long new_addr", "mm/mremap.c"),
    "mprotect": (3, "unsigned long start, size_t len, unsigned long prot", "mm/mprotect.c"),
    "madvise":  (3, "unsigned long start, size_t len, int behavior", "mm/madvise.c"),
    "msync":    (3, "unsigned long start, size_t len, int flags", "mm/msync.c"),
    "mincore":  (3, "unsigned long start, size_t len, unsigned char __user *vec", "mm/mincore.c"),
    "brk":      (1, "unsigned long brk", "mm/mmap.c"),

    # mm/memfd.c
    "memfd_create": (2, "const char __user *uname, unsigned int flags", "mm/memfd.c"),

    # kernel/fork.c
    "clone":  (5, "unsigned long clone_flags, unsigned long newsp, int __user *parent_tidptr, int __user *child_tidptr, unsigned long tls", "kernel/fork.c"),
    "clone3": (2, "struct clone_args __user *uargs, size_t size", "kernel/fork.c"),

    # fs/exec.c
    "execve": (3, "const char __user *filename, const char __user *const __user *argv, const char __user *const __user *envp", "fs/exec.c"),

    # kernel/exit.c
    "exit":       (1, "int error_code", "kernel/exit.c"),
    "exit_group": (1, "int error_code", "kernel/exit.c"),

    # kernel/sys.c
    "getpid": (0, "", "kernel/sys.c"), "gettid": (0, "", "kernel/sys.c"),
    "getppid": (0, "", "kernel/sys.c"),
    "getuid": (0, "", "kernel/sys.c"), "geteuid": (0, "", "kernel/sys.c"),
    "getgid": (0, "", "kernel/sys.c"), "getegid": (0, "", "kernel/sys.c"),
    "setuid": (1, "uid_t uid", "kernel/sys.c"),
    "setgid": (1, "gid_t gid", "kernel/sys.c"),
    "setsid": (0, "", "kernel/sys.c"),
    "setpgid":  (2, "pid_t pid, pid_t pgid", "kernel/sys.c"),
    "getpgrp":  (0, "", "kernel/sys.c"),
    "getpgid":  (1, "pid_t pid", "kernel/sys.c"),
    "uname":    (1, "struct old_utsname __user *name", "kernel/sys.c"),
    "sysinfo":  (1, "struct sysinfo __user *info", "kernel/sys.c"),
    "prctl":    (5, "int option, unsigned long arg2, unsigned long arg3, unsigned long arg4, unsigned long arg5", "kernel/sys.c"),
    "getgroups": (2, "int gidsetsize, gid_t __user *grouplist", "kernel/groups.c"),
    "setgroups": (2, "int gidsetsize, gid_t __user *grouplist", "kernel/groups.c"),
    "prlimit64": (4, "pid_t pid, unsigned int resource, const struct rlimit64 __user *new_rlim, struct rlimit64 __user *old_rlim", "kernel/sys.c"),
    "getrlimit": (2, "unsigned int resource, struct rlimit __user *rlim", "kernel/sys.c"),
    "setrlimit": (2, "unsigned int resource, struct rlimit __user *rlim", "kernel/sys.c"),
    "getrusage": (2, "int who, struct rusage __user *ru", "kernel/sys.c"),
    "getrandom": (3, "char __user *buf, size_t count, unsigned int flags", "drivers/char/random.c"),
    "umask":     (1, "int mask", "kernel/sys.c"),

    # kernel/signal.c
    "rt_sigaction":   (4, "int sig, const struct sigaction __user *act, struct sigaction __user *oact, size_t sigsetsize", "kernel/signal.c"),
    "rt_sigprocmask": (4, "int how, sigset_t __user *set, sigset_t __user *oset, size_t sigsetsize", "kernel/signal.c"),
    "rt_sigreturn":   (0, "", "arch/*/kernel/signal.c"),
    "kill":   (2, "pid_t pid, int sig", "kernel/signal.c"),
    "tgkill": (3, "pid_t tgid, pid_t pid, int sig", "kernel/signal.c"),
    "tkill":  (2, "pid_t pid, int sig", "kernel/signal.c"),
    "sigaltstack": (2, "const struct sigaltstack __user *uss, struct sigaltstack __user *uoss", "kernel/signal.c"),

    # kernel/sched
    "sched_yield":         (0, "", "kernel/sched/core.c"),
    "sched_getaffinity":   (3, "pid_t pid, unsigned int len, unsigned long __user *user_mask_ptr", "kernel/sched/core.c"),
    "sched_setaffinity":   (3, "pid_t pid, unsigned int len, unsigned long __user *user_mask_ptr", "kernel/sched/core.c"),
    "sched_getscheduler":  (1, "pid_t pid", "kernel/sched/core.c"),
    "sched_setscheduler":  (3, "pid_t pid, int policy, struct sched_param __user *param", "kernel/sched/core.c"),

    # kernel/futex
    "futex": (6, "u32 __user *uaddr, int op, u32 val, const struct __kernel_timespec __user *utime, u32 __user *uaddr2, u32 val3", "kernel/futex/syscalls.c"),

    # kernel/time
    "clock_gettime":   (2, "clockid_t which_clock, struct __kernel_timespec __user *tp", "kernel/time/posix-timers.c"),
    "gettimeofday":    (2, "struct __kernel_old_timeval __user *tv, struct timezone __user *tz", "kernel/time/time.c"),
    "nanosleep":       (2, "struct __kernel_timespec __user *rqtp, struct __kernel_timespec __user *rmtp", "kernel/time/hrtimer.c"),
    "clock_nanosleep": (4, "clockid_t which_clock, int flags, const struct __kernel_timespec __user *rqtp, struct __kernel_timespec __user *rmtp", "kernel/time/posix-timers.c"),
    "timer_create":    (3, "clockid_t which_clock, struct sigevent __user *timer_event_spec, timer_t __user *created_timer_id", "kernel/time/posix-timers.c"),
    "timer_settime":   (4, "timer_t timer_id, int flags, const struct __kernel_itimerspec __user *new_setting, struct __kernel_itimerspec __user *old_setting", "kernel/time/posix-timers.c"),
    "timer_gettime":   (2, "timer_t timer_id, struct __kernel_itimerspec __user *setting", "kernel/time/posix-timers.c"),
    "timer_delete":    (1, "timer_t timer_id", "kernel/time/posix-timers.c"),
    "timerfd_create":  (2, "int clockid, int flags", "fs/timerfd.c"),
    "timerfd_settime": (4, "int ufd, int flags, const struct __kernel_itimerspec __user *utmr, struct __kernel_itimerspec __user *otmr", "fs/timerfd.c"),
    "timerfd_gettime": (2, "int ufd, struct __kernel_itimerspec __user *otmr", "fs/timerfd.c"),

    # net/socket.c
    "socket":      (3, "int family, int type, int protocol", "net/socket.c"),
    "bind":        (3, "int fd, struct sockaddr __user *umyaddr, int addrlen", "net/socket.c"),
    "listen":      (2, "int fd, int backlog", "net/socket.c"),
    "accept":      (3, "int fd, struct sockaddr __user *upeer_sockaddr, int __user *upeer_addrlen", "net/socket.c"),
    "accept4":     (4, "int fd, struct sockaddr __user *upeer_sockaddr, int __user *upeer_addrlen, int flags", "net/socket.c"),
    "connect":     (3, "int fd, struct sockaddr __user *uservaddr, int addrlen", "net/socket.c"),
    "getsockname": (3, "int fd, struct sockaddr __user *usockaddr, int __user *usockaddr_len", "net/socket.c"),
    "getpeername": (3, "int fd, struct sockaddr __user *usockaddr, int __user *usockaddr_len", "net/socket.c"),
    "sendto":      (6, "int fd, void __user *buff, size_t len, unsigned int flags, struct sockaddr __user *addr, int addr_len", "net/socket.c"),
    "recvfrom":    (6, "int fd, void __user *ubuf, size_t size, unsigned int flags, struct sockaddr __user *addr, int __user *addr_len", "net/socket.c"),
    "sendmsg":     (3, "int fd, struct user_msghdr __user *msg, unsigned int flags", "net/socket.c"),
    "recvmsg":     (3, "int fd, struct user_msghdr __user *msg, unsigned int flags", "net/socket.c"),
    "setsockopt":  (5, "int fd, int level, int optname, char __user *optval, int optlen", "net/socket.c"),
    "getsockopt":  (5, "int fd, int level, int optname, char __user *optval, int __user *optlen", "net/socket.c"),
    "shutdown":    (2, "int fd, int how", "net/socket.c"),
    "socketpair":  (4, "int family, int type, int protocol, int __user *usockvec", "net/socket.c"),

    # ipc
    "shmget": (3, "key_t key, size_t size, int shmflg", "ipc/shm.c"),
    "shmat":  (3, "int shmid, char __user *shmaddr, int shmflg", "ipc/shm.c"),
    "shmdt":  (1, "char __user *shmaddr", "ipc/shm.c"),
    "shmctl": (3, "int shmid, int cmd, struct shmid_ds __user *buf", "ipc/shm.c"),
    "semget": (3, "key_t key, int nsems, int semflg", "ipc/sem.c"),
    "semop":  (3, "int semid, struct sembuf __user *tsops, unsigned nsops", "ipc/sem.c"),
    "semctl": (4, "int semid, int semnum, int cmd, unsigned long arg", "ipc/sem.c"),
    "msgget": (2, "key_t key, int msgflg", "ipc/msg.c"),
    "msgsnd": (4, "int msqid, struct msgbuf __user *msgp, size_t msgsz, int msgflg", "ipc/msg.c"),
    "msgrcv": (5, "int msqid, struct msgbuf __user *msgp, size_t msgsz, long msgtyp, int msgflg", "ipc/msg.c"),
    "msgctl": (3, "int msqid, int cmd, struct msqid_ds __user *buf", "ipc/msg.c"),

    # kernel/sys.c — misc
    "set_tid_address": (1, "int __user *tidptr", "kernel/fork.c"),
    "set_robust_list": (2, "struct robust_list_head __user *head, size_t len", "kernel/futex/syscalls.c"),
    "flock":           (2, "unsigned int fd, unsigned int cmd", "fs/locks.c"),
    "wait4":           (4, "pid_t upid, int __user *stat_addr, int options, struct rusage __user *ru", "kernel/exit.c"),
    "waitid":          (5, "int which, pid_t upid, struct siginfo __user *infop, int options, struct rusage __user *ru", "kernel/exit.c"),
    "getcwd":          (2, "char __user *buf, unsigned long size", "fs/d_path.c"),
    "chdir":           (1, "const char __user *filename", "fs/open.c"),
    "fchdir":          (1, "unsigned int fd", "fs/open.c"),
    "access":          (2, "const char __user *filename, int mode", "fs/open.c"),
}

# Extract just the arg count for backward compat
LINUX_ARITY = {name: info[0] for name, info in LINUX_ABI.items()}

# Also store the full signatures for detailed reporting
LINUX_SIGNATURES = {name: info[1] for name, info in LINUX_ABI.items()}
LINUX_SOURCES = {name: info[2] for name, info in LINUX_ABI.items()}

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
            sig = LINUX_SIGNATURES.get(m['syscall'], '?')
            src = LINUX_SOURCES.get(m['syscall'], '?')
            print(f"  {m['syscall']}: StarryOS reads {m['starry_args']} args, "
                  f"Linux expects {m['linux_args']} (used: arg{m['args_used']})")
            print(f"    Linux signature: SYSCALL_DEFINE{m['linux_args']}({m['syscall']}, {sig})")
            print(f"    Source: https://elixir.bootlin.com/linux/v6.12/source/{src}")
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
