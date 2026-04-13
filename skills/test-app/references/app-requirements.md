# Application Requirements Reference

Detailed syscall requirements, build instructions, and feasibility notes for target Linux applications. Use this reference during the gap analysis phase (Phase 3) and the build phase (Phase 4) of the test-app workflow.

There is no required ordering — pick whichever application is most interesting or valuable. The table below groups apps by syscall breadth for quick reference, but more ambitious picks are encouraged.

| Breadth | Applications | Key Syscall Areas |
|---------|-------------|-------------------|
| Narrow | BusyBox, Lua, SQLite | fork/exec, basic I/O, signals |
| Medium | Nginx, Redis, curl | sockets, epoll, sendfile, fork |
| Wide | Python, PostgreSQL | mmap, futex, shmget, semaphores, threading |
| Very Wide | Rust Compiler, Syzkaller | clone3, getrandom, flock, everything |

If the user picks an application NOT in this catalog, proceed with the strace-based audit (Phase 2) to discover its requirements from scratch.

---

## 1. BusyBox

**Purpose**: Good stepping stone. A single static binary providing sh, ls, cat, grep, and dozens of other core utilities. Proving BusyBox works validates the fundamental POSIX process model.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| fork / clone | Process | Critical | Process creation for shell pipelines |
| execve | Process | Critical | Running subcommands |
| wait4 / waitpid | Process | Critical | Reaping child processes |
| pipe / pipe2 | IPC | Critical | Shell pipelines |
| dup / dup2 / dup3 | FD | Critical | File descriptor redirection |
| openat / open | FS | Critical | File access |
| read / write | IO | Critical | Standard I/O |
| close | FD | Critical | Cleanup |
| fstat / fstatat / stat | FS | Critical | File metadata |
| lstat | FS | Important | Symlink detection |
| getdents64 | FS | Important | Directory listing (ls) |
| chdir / fchdir | FS | Important | Shell cd |
| getcwd | FS | Important | Shell prompt |
| ioctl | Device | Important | Terminal control (TIOCGWINSZ, TCGETS) |
| rt_sigaction | Signal | Critical | Signal handlers for job control |
| rt_sigprocmask | Signal | Critical | Signal masking |
| rt_sigreturn | Signal | Critical | Return from signal handler |
| brk / sbrk | Memory | Critical | Heap allocation |
| mmap / munmap | Memory | Important | Memory allocation fallback |
| exit_group | Process | Critical | Clean termination |
| getpid / getppid | Process | Important | Process identification |
| uname | System | Important | System info (`uname -a`) |

### Build Instructions

```bash
# Download BusyBox source
wget https://busybox.net/downloads/busybox-1.36.1.tar.bz2
tar xf busybox-1.36.1.tar.bz2
cd busybox-1.36.1

# Configure for static build with musl cross-compiler
make CROSS_COMPILE=riscv64-linux-musl- defconfig
# Enable static linking
sed -i 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config
# Disable features that need kernel headers StarryOS may lack
sed -i 's/CONFIG_FEATURE_HAVE_RPC=y/# CONFIG_FEATURE_HAVE_RPC is not set/' .config

# Build
make CROSS_COMPILE=riscv64-linux-musl- -j$(nproc)

# Output: busybox (single static binary)
riscv64-linux-musl-strip busybox
```

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace busybox-static &&
  # Test basic operations
  strace -f -o /work/strace-busybox.log busybox sh -c "
    echo hello &&
    ls / &&
    cat /etc/hostname &&
    busybox grep root /etc/passwd
  " &&
  awk -F"(" "{print \$1}" /work/strace-busybox.log | sort -u > /work/syscalls-busybox.txt
'
```

---

## 2. Nginx

**Purpose**: High competition value. Demonstrates working network stack (sockets, epoll), file serving, and basic concurrency. Judges value a working web server highly.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| socket | Network | Critical | Create listening/connection sockets |
| bind | Network | Critical | Bind to address:port |
| listen | Network | Critical | Mark socket as passive |
| accept4 | Network | Critical | Accept incoming connections (prefer over accept) |
| setsockopt | Network | Critical | SO_REUSEADDR, SO_REUSEPORT, TCP_NODELAY |
| getsockopt | Network | Important | Query socket state |
| epoll_create1 | IO Mux | Critical | Create epoll instance |
| epoll_ctl | IO Mux | Critical | Register/modify/remove FDs |
| epoll_wait | IO Mux | Critical | Wait for events |
| sendfile | IO | Important | Zero-copy file-to-socket transfer (core of static file serving) |
| writev | IO | Important | Scatter-gather writes for HTTP headers + body |
| read / write | IO | Critical | Standard I/O |
| recv / send | Network | Important | Socket I/O alternatives |
| openat | FS | Critical | Open config files, served files |
| fstat | FS | Critical | File metadata for Content-Length |
| close | FD | Critical | Cleanup |
| mmap / munmap | Memory | Critical | Memory-mapped file I/O, shared memory |
| mprotect | Memory | Important | Memory permission changes |
| brk | Memory | Critical | Heap |
| clone / clone3 | Process | Important | Worker process creation (Nginx forks workers) |
| wait4 | Process | Important | Master process reaping workers |
| getpid / getppid | Process | Important | PID file management |
| fcntl | FD | Critical | F_SETFL (O_NONBLOCK), F_DUPFD, F_GETFD/F_SETFD |
| ioctl | Device | Important | FIONBIO for non-blocking I/O |
| rt_sigaction | Signal | Critical | SIGHUP (reload), SIGTERM (shutdown), SIGCHLD |
| rt_sigprocmask | Signal | Critical | Signal masking during critical sections |
| prlimit64 | Resource | Important | RLIMIT_NOFILE (max open FDs) |
| socketpair | Network | Important | Inter-process communication channel |
| gettimeofday | Time | Important | HTTP Date header, access logging |
| clock_gettime | Time | Important | High-resolution timing |
| getuid / geteuid | Process | Important | Permission checks, privilege dropping |
| setuid / setgid | Process | Important | Worker privilege dropping |
| umask | FS | Important | File creation mask |
| mkdir | FS | Important | Log directories |
| unlink | FS | Important | PID file cleanup |
| rename | FS | Important | Log rotation |

### Build Instructions

```bash
# Download Nginx
wget https://nginx.org/download/nginx-1.26.2.tar.gz
tar xf nginx-1.26.2.tar.gz
cd nginx-1.26.2

# Configure for static build on riscv64 with musl
export CC=riscv64-linux-musl-gcc
export AR=riscv64-linux-musl-ar
export RANLIB=riscv64-linux-musl-ranlib

./configure \
  --prefix=/usr/local/nginx \
  --without-http_rewrite_module \
  --without-http_gzip_module \
  --without-pcre \
  --with-cc-opt="-static -Os" \
  --with-ld-opt="-static" \
  --crossbuild=Linux:5.15.0:riscv64

make -j$(nproc)
riscv64-linux-musl-strip objs/nginx
# Output: objs/nginx (static binary)
```

**Minimal nginx.conf for testing**:
```nginx
worker_processes 1;
daemon off;
error_log /dev/stderr;

events {
    worker_connections 64;
}

http {
    access_log off;
    server {
        listen 8080;
        location / {
            return 200 "Hello from StarryOS Nginx!\n";
        }
    }
}
```

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace nginx curl &&
  # Start nginx, send a request, stop
  strace -f -o /work/strace-nginx.log timeout 5 sh -c "
    nginx -g \"daemon off;\" &
    sleep 1 &&
    curl -s http://localhost:80/ &&
    kill %1
  " 2>&1 || true &&
  awk -F"(" "{print \$1}" /work/strace-nginx.log | sort -u > /work/syscalls-nginx.txt
'
```

---

## 3. Redis

**Purpose**: High competition value. In-memory key-value store demonstrating sockets, epoll event loop, fork-based persistence (RDB snapshots, AOF rewrite), and signal handling.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| socket | Network | Critical | Server socket |
| bind | Network | Critical | Bind to port |
| listen | Network | Critical | Accept connections |
| accept / accept4 | Network | Critical | Client connections |
| setsockopt | Network | Critical | SO_REUSEADDR, TCP_NODELAY |
| epoll_create1 | IO Mux | Critical | Redis event loop (ae_epoll) |
| epoll_ctl | IO Mux | Critical | Register client FDs |
| epoll_wait | IO Mux | Critical | Wait for client commands |
| read / write | IO | Critical | RESP protocol I/O |
| openat | FS | Critical | RDB/AOF file access |
| close | FD | Critical | Cleanup |
| fstat | FS | Important | File size checks |
| mmap / munmap | Memory | Critical | Large allocation and RDB loading |
| brk | Memory | Critical | Heap management |
| clone / fork | Process | Critical | BGSAVE (RDB snapshot), BGREWRITEAOF |
| wait4 | Process | Critical | Reap background child processes |
| pipe / pipe2 | IPC | Important | Parent-child communication during BGSAVE |
| fcntl | FD | Critical | O_NONBLOCK on client sockets |
| rt_sigaction | Signal | Critical | SIGCHLD, SIGTERM, SIGUSR1/2 |
| rt_sigprocmask | Signal | Important | Signal masking |
| getpid | Process | Important | PID file |
| gettimeofday | Time | Critical | Redis timers, expiration, slow log |
| clock_gettime | Time | Important | Monotonic clock for latency measurement |
| fsync / fdatasync | FS | Important | AOF persistence durability |
| rename | FS | Important | Atomic RDB file swap |
| unlink | FS | Important | Temp file cleanup |
| getrlimit / prlimit64 | Resource | Important | Max open FD check at startup |
| getrandom | System | Important | Random seed (falls back to /dev/urandom) |

### Build Instructions

```bash
# Download Redis
wget https://download.redis.io/releases/redis-7.2.7.tar.gz
tar xf redis-7.2.7.tar.gz
cd redis-7.2.7

# Build with musl cross-compiler
make CC=riscv64-linux-musl-gcc \
     AR="riscv64-linux-musl-ar rcs" \
     MALLOC=libc \
     CFLAGS="-static -Os" \
     LDFLAGS="-static" \
     -j$(nproc)

riscv64-linux-musl-strip src/redis-server
# Output: src/redis-server (static binary)
```

**Minimal redis.conf for testing**:
```
port 6379
bind 0.0.0.0
save ""
appendonly no
loglevel notice
daemonize no
```

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace redis-server redis-tools &&
  strace -f -o /work/strace-redis.log timeout 5 sh -c "
    redis-server --save \"\" --appendonly no --daemonize no &
    sleep 1 &&
    redis-cli SET hello world &&
    redis-cli GET hello &&
    redis-cli BGSAVE &&
    sleep 1 &&
    redis-cli SHUTDOWN NOSAVE
  " 2>&1 || true &&
  awk -F"(" "{print \$1}" /work/strace-redis.log | sort -u > /work/syscalls-redis.txt
'
```

---

## 4. Python (CPython)

**Purpose**: High competition value. Demonstrates wide syscall coverage: memory management, threading, signal handling, file I/O, and pipes. A working Python interpreter shows the kernel can support complex runtimes.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| mmap / munmap | Memory | Critical | Python's memory allocator uses mmap extensively |
| mprotect | Memory | Critical | Guard pages, memory permission changes |
| brk | Memory | Critical | Small allocations |
| madvise | Memory | Important | MADV_DONTNEED for memory release |
| futex | Sync | Critical | GIL (Global Interpreter Lock), threading |
| clone / clone3 | Process | Critical | threading module, multiprocessing |
| pipe2 | IPC | Critical | subprocess module, internal signaling |
| eventfd2 | IPC | Important | Internal event notification (newer Python) |
| memfd_create | Memory | Important | Zipimport optimization (Python 3.12+) |
| execve | Process | Critical | subprocess.run(), os.exec*() |
| wait4 | Process | Critical | subprocess, multiprocessing child reaping |
| getdents64 | FS | Critical | os.listdir(), os.scandir() |
| fstat / fstatat | FS | Critical | File metadata, import machinery |
| openat | FS | Critical | Open .py files, .pyc files, shared objects |
| close | FD | Critical | Cleanup |
| read / write | IO | Critical | Standard I/O, file I/O |
| lseek | IO | Important | Random file access |
| ioctl | Device | Important | Terminal queries (TIOCGWINSZ) |
| fcntl | FD | Important | F_GETFD, F_SETFD (CLOEXEC) |
| rt_sigaction | Signal | Critical | signal module, SIGINT handler |
| rt_sigprocmask | Signal | Critical | Signal masking in threading code |
| rt_sigreturn | Signal | Critical | Signal handler return |
| getpid / gettid | Process | Important | os.getpid(), threading |
| getuid / getgid / geteuid / getegid | Process | Important | os.getuid() and friends |
| clock_gettime | Time | Critical | time.time(), time.monotonic() |
| gettimeofday | Time | Important | Fallback timing |
| getrandom | System | Critical | random module, secrets module |
| prlimit64 | Resource | Important | resource module |
| sched_getaffinity | Process | Important | os.cpu_count() |
| set_tid_address | Process | Critical | Thread setup (called by musl/glibc at thread start) |
| set_robust_list | Process | Important | Robust futex list for threading |
| readlinkat | FS | Important | Symlink resolution |
| access / faccessat | FS | Important | os.access(), import path checking |
| dup / dup3 | FD | Important | File descriptor manipulation |

### Build Instructions

```bash
# Download CPython
wget https://www.python.org/ftp/python/3.12.8/Python-3.12.8.tar.xz
tar xf Python-3.12.8.tar.xz
cd Python-3.12.8

# Configure for cross-compilation with musl
# First build a native Python for the build host
mkdir build-host && cd build-host
../configure && make -j$(nproc) python
cd ..

# Now cross-compile
mkdir build-riscv64 && cd build-riscv64
CONFIG_SITE=../Tools/cross/config.site-riscv64 \
../configure \
  --host=riscv64-linux-musl \
  --build=x86_64-linux-gnu \
  CC=riscv64-linux-musl-gcc \
  CXX=riscv64-linux-musl-g++ \
  AR=riscv64-linux-musl-ar \
  READELF=riscv64-linux-musl-readelf \
  --disable-shared \
  --disable-ipv6 \
  --disable-test-modules \
  --with-ensurepip=no \
  LDFLAGS="-static" \
  LINKFORSHARED=" "

make -j$(nproc)
riscv64-linux-musl-strip python
# Output: python (static binary, ~10-15 MB)
```

**Note**: Python cross-compilation is notoriously tricky. The `config.site` file must define values for checks that cannot run on the build host. Create it if it does not exist:
```
ac_cv_file__dev_ptmx=no
ac_cv_file__dev_ptc=no
```

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace python3 &&
  strace -f -o /work/strace-python.log python3 -c "
import sys, os, time
print(\"Python\", sys.version)
print(\"PID:\", os.getpid())
print(\"CWD:\", os.getcwd())
print(\"Time:\", time.time())
# Test threading
import threading
def worker():
    print(\"Thread\", threading.current_thread().name)
t = threading.Thread(target=worker)
t.start()
t.join()
print(\"Done\")
  " &&
  awk -F"(" "{print \$1}" /work/strace-python.log | sort -u > /work/syscalls-python.txt
'
```

---

## 5. Rust Compiler (rustc)

**Purpose**: Very high competition value but extremely complex. Demonstrates that the kernel can support the full Rust toolchain. Recommend starting with simpler Rust programs (compile and run a hello-world) before attempting a full rustc invocation.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| clone3 | Process | Critical | Thread pool for parallel compilation |
| futex | Sync | Critical | Thread synchronization, parking |
| mmap / munmap | Memory | Critical | Extremely heavy use — codegen, linking |
| mprotect | Memory | Critical | JIT-like patterns in LLVM backend |
| mremap | Memory | Important | Realloc large allocations |
| madvise | Memory | Important | Memory management hints |
| getrandom | System | Critical | Hash randomization (DoS protection) |
| pipe2 | IPC | Critical | Subprocess communication (invoking linker) |
| dup / dup2 / dup3 | FD | Critical | FD redirection for subprocesses |
| execve | Process | Critical | Invoking linker (ld, lld) |
| wait4 | Process | Critical | Reaping linker process |
| flock | FS | Important | Cargo lock files, build directory locks |
| openat | FS | Critical | Reading source files, writing object files |
| read / write | IO | Critical | Standard I/O, file I/O |
| close | FD | Critical | Cleanup |
| fstat / fstatat | FS | Critical | File metadata |
| getdents64 | FS | Important | Scanning directories for crate sources |
| lseek | IO | Important | Object file writing |
| fcntl | FD | Critical | CLOEXEC, nonblocking |
| unlink / unlinkat | FS | Important | Temp file cleanup |
| rename / renameat | FS | Important | Atomic file placement |
| mkdir / mkdirat | FS | Important | Build output directories |
| rt_sigaction | Signal | Critical | Signal handlers |
| rt_sigprocmask | Signal | Critical | Signal masking in threads |
| clock_gettime | Time | Critical | Build timing |
| sched_getaffinity | Process | Important | Thread pool sizing |
| prlimit64 | Resource | Important | Stack size limits |
| set_tid_address | Process | Critical | Thread setup |
| brk | Memory | Critical | Heap |
| statfs | FS | Important | Filesystem type checks |
| readlinkat | FS | Important | Resolving sysroot paths |

### Build Approach

**Do NOT attempt to cross-compile rustc from source for StarryOS**. Instead:

1. Use a pre-built `rustc` for riscv64 from the official Rust releases
2. Download the riscv64gc-unknown-linux-musl target
3. Extract the `rustc` binary and required libraries

```bash
# Download pre-built rustc for riscv64
RUST_VERSION="1.82.0"
wget "https://static.rust-lang.org/dist/rust-${RUST_VERSION}-riscv64gc-unknown-linux-gnu.tar.xz"
tar xf "rust-${RUST_VERSION}-riscv64gc-unknown-linux-gnu.tar.xz"
# Extract rustc binary and its runtime libraries
```

**Staged testing approach** (recommended):
1. First: Run a pre-compiled static Rust hello-world binary on StarryOS
2. Second: Run a pre-compiled static Rust binary that uses threads, files, and networking
3. Third: Attempt running `rustc` to compile a trivial `fn main() {}` program
4. Fourth: Compile and run a more complex program

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace curl build-essential &&
  curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y &&
  . $HOME/.cargo/env &&
  mkdir /tmp/hello && cd /tmp/hello &&
  echo "fn main() { println!(\"Hello from rustc\"); }" > main.rs &&
  strace -f -o /work/strace-rustc.log rustc main.rs -o hello &&
  awk -F"(" "{print \$1}" /work/strace-rustc.log | sort -u > /work/syscalls-rustc.txt
'
```

---

## 6. Syzkaller

**Purpose**: Not a user-facing application but a kernel fuzzer. Useful for finding bugs automatically. Exercises an extremely wide range of syscalls by design — its entire purpose is to generate random syscall sequences and detect kernel crashes.

### Required Syscalls

Syzkaller itself needs a working Go runtime on the target, which requires:
- clone3 / clone (goroutine scheduling)
- futex (goroutine synchronization)
- mmap / mprotect / brk (memory management)
- epoll_create1, epoll_ctl, epoll_wait (Go netpoller)
- pipe2, eventfd2 (internal signaling)
- openat, read, write, close (file I/O)
- socket, connect, bind, listen, accept4 (if testing network syscalls)
- All signal-related syscalls
- prctl, arch_prctl (thread setup)
- sched_yield (goroutine scheduling)
- getrandom (crypto/rand)

Beyond the runtime, Syzkaller will exercise whatever syscalls are described in its syscall descriptions (syzlang). The descriptions can be restricted to only test syscalls StarryOS implements.

### Build Approach

```bash
# Syzkaller is written in Go — cross-compile for riscv64
git clone https://github.com/google/syzkaller.git
cd syzkaller
make TARGETOS=linux TARGETARCH=riscv64
# Output: bin/linux_riscv64/syz-executor, syz-fuzzer, syz-manager
```

**Note**: Syzkaller has a manager/executor architecture. The manager runs on the host, the executor runs inside the guest (StarryOS). Only `syz-executor` needs to run on StarryOS.

---

## 7. PostgreSQL

**Purpose**: Very high competition value. A full relational database demonstrating System V IPC (shared memory, semaphores), complex process model (postmaster forks backends), socket-based client connections, and WAL persistence.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| socket / bind / listen / accept | Network | Critical | Client connections |
| setsockopt / getsockopt | Network | Critical | SO_REUSEADDR, keepalive |
| epoll_create1 / epoll_ctl / epoll_wait | IO Mux | Critical | Connection multiplexing (if using epoll, otherwise select/poll) |
| shmget / shmat / shmdt / shmctl | IPC | Critical | Shared buffer pool (System V shared memory) |
| semget / semop / semctl | IPC | Critical | Lightweight locks (System V semaphores) |
| mmap / munmap / mprotect | Memory | Critical | Anonymous shared memory fallback, WAL |
| fork / clone | Process | Critical | Postmaster forks per-connection backend processes |
| execve | Process | Important | Executing utility processes |
| wait4 / waitpid | Process | Critical | Reaping backend processes |
| kill | Signal | Critical | Backend signaling (SIGTERM, SIGHUP, SIGUSR1/2) |
| rt_sigaction / rt_sigprocmask | Signal | Critical | Extensive signal handling for lifecycle management |
| openat / read / write / close | FS/IO | Critical | WAL files, data files, config files |
| lseek / pread64 / pwrite64 | IO | Critical | Random I/O on data files |
| fsync / fdatasync | FS | Critical | Durability guarantees |
| ftruncate | FS | Important | WAL file management |
| flock / fcntl | FS | Critical | Advisory locking, PID file |
| mkdir / unlink / rename | FS | Important | Tablespace management, WAL archiving |
| getdents64 / fstat / fstatat | FS | Important | Directory scanning, file metadata |
| clock_gettime / gettimeofday | Time | Critical | Timestamps everywhere |
| getpid / getppid / getuid / getgid | Process | Critical | Authentication and access control |
| pipe2 | IPC | Important | Postmaster-backend communication |
| brk | Memory | Critical | Heap allocation |
| getrandom | System | Important | Authentication nonces |
| prlimit64 | Resource | Important | Resource limit checks |

### Build Instructions

```bash
# Download PostgreSQL
wget https://ftp.postgresql.org/pub/source/v16.4/postgresql-16.4.tar.bz2
tar xf postgresql-16.4.tar.bz2
cd postgresql-16.4

# Configure for cross-compilation with musl
export CC=riscv64-linux-musl-gcc
export AR=riscv64-linux-musl-ar

./configure \
  --host=riscv64-linux-musl \
  --build=x86_64-linux-gnu \
  --without-readline \
  --without-zlib \
  --without-openssl \
  --without-icu \
  --disable-thread-safety \
  CFLAGS="-static -Os" \
  LDFLAGS="-static"

make -j$(nproc) -C src/backend
make -j$(nproc) -C src/bin/initdb
make -j$(nproc) -C src/bin/psql

riscv64-linux-musl-strip src/backend/postgres
riscv64-linux-musl-strip src/bin/initdb/initdb
riscv64-linux-musl-strip src/bin/psql/psql
```

**Note**: PostgreSQL's System V IPC requirement (shmget/semget) is the biggest kernel challenge. StarryOS currently has stubs for these. An alternative is to build with `--with-segsize=...` and `--disable-spinlocks` and use POSIX shared memory (mmap with MAP_SHARED) as a fallback, but the default SysV path is what real deployments use.

### strace Profile Template

```bash
docker run --rm -v $(pwd):/work ubuntu:24.04 sh -c '
  apt-get update && apt-get install -y strace postgresql postgresql-client &&
  pg_ctlcluster 16 main start &&
  strace -f -o /work/strace-postgres.log timeout 10 sh -c "
    sudo -u postgres psql -c \"CREATE TABLE test(id int); INSERT INTO test VALUES (1); SELECT * FROM test;\" &&
    sudo -u postgres pg_ctl -D /var/lib/postgresql/16/main stop
  " 2>&1 || true &&
  awk -F"(" "{print \$1}" /work/strace-postgres.log | sort -u > /work/syscalls-postgres.txt
'
```

---

## 8. SQLite (via sqlite3 CLI)

**Purpose**: Lightweight embedded database. Narrower syscall surface than PostgreSQL but still exercises file locking, mmap, and fsync. Good stepping stone toward full database support.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| openat / close | FS | Critical | Database file and journal |
| read / write / pread64 / pwrite64 | IO | Critical | Database page I/O |
| fstat | FS | Critical | File size detection |
| fcntl | FS | Critical | POSIX advisory locks (F_SETLK, F_GETLK) — core of SQLite locking |
| flock | FS | Important | Fallback locking mechanism |
| mmap / munmap | Memory | Important | Memory-mapped I/O mode (WAL mode) |
| fsync / fdatasync | FS | Critical | Durability (PRAGMA synchronous) |
| ftruncate | FS | Important | WAL checkpoint, journal truncation |
| unlink | FS | Important | Journal file cleanup |
| brk / mmap | Memory | Critical | Heap allocation |
| getcwd | FS | Important | Resolving relative DB paths |
| access / faccessat | FS | Important | File existence checks |
| rt_sigaction | Signal | Important | SIGINT handler in CLI |

### Build Instructions

```bash
# Download SQLite amalgamation
wget https://www.sqlite.org/2024/sqlite-autoconf-3470200.tar.gz
tar xf sqlite-autoconf-3470200.tar.gz
cd sqlite-autoconf-3470200

CC=riscv64-linux-musl-gcc \
./configure --host=riscv64-linux-musl \
  CFLAGS="-static -Os -DSQLITE_THREADSAFE=0" \
  LDFLAGS="-static"

make -j$(nproc)
riscv64-linux-musl-strip sqlite3
# Output: sqlite3 (single static binary, ~1.5 MB)
```

---

## 9. curl

**Purpose**: HTTP/HTTPS client. Tests the network stack from the client side (connect, send, recv) and DNS resolution. Useful for validating that socket syscalls work end-to-end.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| socket | Network | Critical | TCP socket creation |
| connect | Network | Critical | Connect to server |
| send / recv / sendto / recvfrom | Network | Critical | HTTP request/response |
| getaddrinfo (via socket/connect) | Network | Critical | DNS resolution |
| poll / select | IO Mux | Critical | Wait for socket readiness |
| read / write | IO | Critical | Standard I/O |
| openat / close / fstat | FS | Important | Config files, output files |
| mmap / brk | Memory | Critical | Allocation |
| clock_gettime | Time | Important | Timeout management |
| rt_sigaction | Signal | Important | SIGPIPE handling |
| fcntl | FD | Important | O_NONBLOCK |

### Build Instructions

```bash
# Download curl
wget https://curl.se/download/curl-8.11.1.tar.xz
tar xf curl-8.11.1.tar.xz
cd curl-8.11.1

CC=riscv64-linux-musl-gcc \
./configure --host=riscv64-linux-musl \
  --disable-shared --enable-static \
  --without-ssl --without-libpss \
  --disable-ldap --disable-dict --disable-telnet \
  CFLAGS="-static -Os" LDFLAGS="-static -all-static"

make -j$(nproc)
riscv64-linux-musl-strip src/curl
```

---

## 10. Lua

**Purpose**: Lightweight embeddable scripting language. Very narrow syscall surface — a good quick win that proves the runtime basics work. If Lua runs, it validates enough of the POSIX interface to build confidence.

### Required Syscalls

| Syscall | Category | Criticality | Notes |
|---------|----------|-------------|-------|
| read / write | IO | Critical | Standard I/O |
| openat / close | FS | Critical | File I/O (dofile, loadfile) |
| fstat | FS | Important | File metadata |
| brk / mmap | Memory | Critical | Allocation |
| clock_gettime | Time | Important | os.clock() |
| rt_sigaction | Signal | Important | Signal handlers |
| exit_group | Process | Critical | Clean exit |
| getpid | Process | Important | Internal seeding |

### Build Instructions

```bash
wget https://www.lua.org/ftp/lua-5.4.7.tar.gz
tar xf lua-5.4.7.tar.gz
cd lua-5.4.7

make CC=riscv64-linux-musl-gcc \
  AR="riscv64-linux-musl-ar rcu" \
  RANLIB=riscv64-linux-musl-ranlib \
  MYCFLAGS="-static -Os" \
  MYLDFLAGS="-static" \
  posix

riscv64-linux-musl-strip src/lua
# Output: src/lua (~250 KB static binary)
```

---

## Custom Applications

For any application NOT listed above, proceed with this generic approach:

1. **strace in Docker**: Run the application in Docker with strace to discover all required syscalls
2. **Cross-compile**: Build with `riscv64-linux-musl-gcc` and `-static` flags
3. **Gap analysis**: Compare required syscalls against StarryOS's dispatch table
4. **Test and fix**: Use the hunt-bugs skill for each missing/broken syscall

---

## Cross-Compilation General Notes

### Musl Cross-Compiler Setup

```bash
# Option 1: Install musl-cross-make toolchain
git clone https://github.com/richfelker/musl-cross-make.git
cd musl-cross-make
echo "TARGET = riscv64-linux-musl" > config.mak
make -j$(nproc) && make install
export PATH="$(pwd)/output/bin:$PATH"

# Option 2: Use a Docker image with the toolchain pre-installed
docker run --rm -v $(pwd):/work muslcc/riscv64:riscv64-linux-musl \
  riscv64-linux-musl-gcc -static -o /work/output /work/source.c
```

### Verifying Static Linkage

```bash
file <binary>
# Should show: ELF 64-bit LSB executable, UCB RISC-V, statically linked

riscv64-linux-musl-readelf -d <binary>
# Should show: no NEEDED entries (no dynamic libraries)
```

### Common Cross-Compilation Pitfalls

- **Missing sysroot headers**: Some apps need kernel headers. Use `make headers_install ARCH=riscv INSTALL_HDR_PATH=<sysroot>` from a Linux kernel source tree.
- **Host-vs-target confusion**: Autotools configure scripts may run test programs — these fail when cross-compiling. Use `--host=riscv64-linux-musl` and set appropriate `ac_cv_*` variables.
- **Floating point**: Ensure the toolchain is built for riscv64gc (with hardware float). Soft-float binaries will work but perform poorly.
- **Thread-local storage**: musl's TLS implementation differs from glibc. Some apps may need patches.

---

## strace Command Reference

### Basic Application Profiling

```bash
# Capture all syscalls with timestamps and follow forks
strace -f -T -o output.log <command>

# Extract unique syscall list
awk -F'(' '{print $1}' output.log | sed 's/^[0-9]* *//' | sort -u > syscalls.txt

# Count syscall frequency (most-used first)
awk -F'(' '{print $1}' output.log | sed 's/^[0-9]* *//' | sort | uniq -c | sort -rn > syscall-freq.txt
```

### Focused Profiling

```bash
# Only trace specific syscall categories
strace -f -e trace=network <command>      # Socket-related
strace -f -e trace=memory <command>       # mmap, brk, etc.
strace -f -e trace=process <command>      # fork, exec, wait, etc.
strace -f -e trace=signal <command>       # Signal-related
strace -f -e trace=file <command>         # File operations
strace -f -e trace=desc <command>         # File descriptor operations

# Trace specific syscalls only
strace -f -e trace=epoll_wait,epoll_ctl,accept4,sendfile <command>
```

### Comparing Profiles

After capturing strace profiles for both the application in Docker and any failing behavior on StarryOS, compare:

```bash
# Diff the syscall lists
comm -23 syscalls-app.txt syscalls-starry-supported.txt > missing-syscalls.txt
# missing-syscalls.txt now contains syscalls the app needs but StarryOS doesn't support
```

---

## App Compatibility Report Template

Use this template when writing reports to `docs/starry-reports/apps/APP-NNN-<name>.md`.

```markdown
# APP-NNN: <Application Name> v<Version>

## Summary
- **Application**: <name> <version>
- **Status**: PASS / PARTIAL / FAIL
- **Date**: <YYYY-MM-DD>
- **Build**: Static riscv64 via <toolchain>
- **Binary Size**: <size after strip>
- **Syscalls Exercised**: <total unique syscalls during test>
- **Syscalls Fixed During Porting**: <list>

## Build Notes
- Toolchain: `riscv64-linux-musl-gcc` / `rustup target add riscv64gc-unknown-linux-musl` / etc.
- Configure flags: <flags>
- Patches applied: <list or "none">
- Build warnings: <notable warnings or "clean build">

## Test Configuration
- QEMU: `qemu-system-riscv64` with <memory> RAM
- StarryOS commit: <hash>
- Test command: <exact command run inside StarryOS>
- Expected behavior: <what correct output looks like>

## Results

### Linux Baseline
<captured from Docker using linux-ref-test.sh or strace>

### StarryOS Output
<captured from QEMU pipeline>

### Divergences
| # | Description | Syscall | Severity | Fixed? |
|---|-------------|---------|----------|--------|
| 1 | <desc> | <syscall> | Critical/High/Medium/Low | Yes/No |

## Syscall Coverage Matrix
| Syscall | Required | Implemented | Tested | Status |
|---------|----------|-------------|--------|--------|
| socket | Yes | Yes | Yes | PASS |
| accept4 | Yes | No | N/A | MISSING |
| ... | | | | |

## Remaining Issues
- [ ] <issue 1>
- [ ] <issue 2>

## Recommendations
- <next steps for full compatibility>
```

---

## Priority Syscall Groups

Certain syscalls appear across nearly all applications. Fixing these first maximizes coverage:

### Universal (needed by everything)
`openat`, `close`, `read`, `write`, `fstat`, `mmap`, `munmap`, `brk`, `rt_sigaction`, `rt_sigprocmask`, `exit_group`, `getpid`, `clock_gettime`

### Process Model (BusyBox, Python, Redis, rustc)
`clone`/`clone3`, `execve`, `wait4`, `pipe2`, `dup2`/`dup3`, `fork`

### Networking (Nginx, Redis)
`socket`, `bind`, `listen`, `accept4`, `setsockopt`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `sendfile`

### Threading (Python, rustc, Syzkaller)
`futex`, `clone3`, `set_tid_address`, `set_robust_list`, `sched_getaffinity`

### Filesystem (all)
`getdents64`, `fstatat`, `openat`, `readlinkat`, `mkdir`, `unlink`, `rename`, `fcntl`

Fix the **Universal** group first, then **Process Model**, then branch into **Networking** or **Threading** depending on the target application.
