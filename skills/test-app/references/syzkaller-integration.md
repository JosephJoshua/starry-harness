# Syzkaller Integration Guide

Syzkaller is Google's kernel fuzzer — it generates random syscall sequences, detects crashes, and automatically produces reproducers. Rather than building a lightweight custom fuzzer, integrate the real Syzkaller since it is purpose-built for exactly this task.

## Architecture

Syzkaller has a manager/executor split:
- **syz-manager**: Runs on the host machine. Manages fuzzing campaigns, tracks coverage, stores crashes and reproducers. Written in Go.
- **syz-executor**: Runs inside the guest (StarryOS on QEMU). Executes syscall sequences received from the manager. Small static binary.
- **syz-fuzzer**: Also runs inside the guest. Mutates and generates new syscall sequences. Communicates with syz-manager over a serial/network link.

For StarryOS, only **syz-executor** needs to run inside the kernel. syz-manager runs on the host macOS/Linux machine.

## Setup Steps

### 1. Build Syzkaller for riscv64

```bash
# Clone Syzkaller
git clone https://github.com/google/syzkaller.git
cd syzkaller

# Build for riscv64 target (host tools + guest executor)
make TARGETOS=linux TARGETARCH=riscv64

# Output:
#   bin/syz-manager          (host, native arch)
#   bin/syz-fuzzer           (host, native arch)
#   bin/linux_riscv64/syz-executor  (guest, riscv64 static binary)
#   bin/linux_riscv64/syz-fuzzer    (guest, riscv64)
```

### 2. Create Syzkaller Config

Create `syzkaller.cfg` for StarryOS:

```json
{
    "name": "starryos-riscv64",
    "target": "linux/riscv64",
    "http": ":56741",
    "workdir": "./workdir",
    "syzkaller": ".",
    "type": "qemu",
    "vm": {
        "count": 1,
        "qemu": "qemu-system-riscv64",
        "qemu_args": "-machine virt -bios default",
        "kernel": "<path-to-starryos-binary>",
        "image": "<path-to-rootfs-disk.img>",
        "mem": 1024,
        "smp": 2,
        "cmdline": ""
    },
    "enable_syscalls": [
        "mmap", "munmap", "mremap", "mprotect", "brk",
        "read", "write", "openat", "close", "fstat",
        "clone", "fork", "execve", "wait4", "exit_group",
        "pipe2", "dup3", "fcntl",
        "rt_sigaction", "rt_sigprocmask", "kill",
        "futex", "clock_gettime", "gettimeofday",
        "prlimit64", "getrusage"
    ]
}
```

**Key configuration choices:**
- `enable_syscalls`: Start with syscalls StarryOS actually implements. Expand as more are added. Syzkaller with disabled syscalls won't waste time on ENOSYS.
- `smp: 2`: Start with 2 cores to catch basic concurrency bugs without overwhelming the kernel.
- `vm.count: 1`: Single VM instance initially. Scale up once stable.

### 3. Restrict Syscall Descriptions

Syzkaller uses `.txt` description files (syzlang) to define syscall argument types and constraints. The default Linux descriptions are in `sys/linux/`. For StarryOS, create a restricted subset:

```bash
# Copy only the syscall descriptions for implemented syscalls
mkdir -p sys/starryos
# Start with basic file ops, memory, process
cp sys/linux/sys.txt sys/starryos/
cp sys/linux/mmap.txt sys/starryos/
cp sys/linux/open.txt sys/starryos/
# Edit to remove syscalls StarryOS doesn't support
```

Alternatively, use `enable_syscalls` in the config (simpler, recommended for initial setup).

### 4. Inject Executor into StarryOS rootfs

```bash
# Copy syz-executor and syz-fuzzer into the StarryOS rootfs
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-.}"
DISK_IMG="$PROJECT_ROOT/os/StarryOS/make/disk.img"

# Use Docker to mount ext4 and inject
docker run --rm --privileged \
  -v "$(pwd)/bin/linux_riscv64:/syzkaller:ro" \
  -v "$DISK_IMG:/disk.img" \
  ubuntu:24.04 sh -c '
    mkdir -p /mnt &&
    mount -o loop /disk.img /mnt &&
    cp /syzkaller/syz-executor /mnt/syz-executor &&
    cp /syzkaller/syz-fuzzer /mnt/syz-fuzzer &&
    chmod +x /mnt/syz-executor /mnt/syz-fuzzer &&
    sync && umount /mnt
  '
```

### 5. Run Syzkaller

```bash
# Start the fuzzing campaign
./bin/syz-manager -config syzkaller.cfg

# Monitor via web UI at http://localhost:56741
# Syzkaller reports:
#   - Crashes (kernel panics, hangs)
#   - Reproducers (minimal syscall sequences that trigger the crash)
#   - Coverage data (which kernel code paths were exercised)
```

## Interpreting Results

### Crashes

Syzkaller categorizes crashes by:
- **Type**: KASAN (memory), WARNING, BUG, panic, hang (deadlock)
- **Reproducer**: C program that triggers the crash deterministically
- **Bisection**: Which commit introduced the bug (if git history available)

Each crash maps to the harness's bug categories:
| Syzkaller Type | Harness Category |
|---------------|-----------------|
| KASAN: use-after-free | Memory |
| KASAN: out-of-bounds | Memory |
| WARNING: lock ordering | Concurrency |
| Hang (deadlock) | Concurrency |
| Panic | Correctness |
| SIGBUS/SIGSEGV in kernel | Safety |

### Coverage

Syzkaller tracks which kernel code lines are exercised. Export coverage data to identify untested code paths:

```bash
# After a fuzzing run, coverage is in workdir/
# Convert to human-readable format
./bin/syz-cover -workdir ./workdir -kernel <vmlinux>
```

This coverage data feeds back into the harness — code paths that Syzkaller never reached are candidates for targeted testing via hunt-bugs or audit-kernel.

### Reproducers

Syzkaller produces C reproducers for each crash. These can be directly converted to `starry_test.h` format:

```c
// Syzkaller reproducer (auto-generated):
#include <sys/mman.h>
void main() {
    mmap(0x20000000, 0x1000, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    mremap(0x20000000, 0x1000, 0x2000, MREMAP_MAYMOVE);
}

// Convert to starry_test.h format:
#include "starry_test.h"
TEST_BEGIN("syzkaller_repro_001")
TEST("mremap_crash") {
    void *p = mmap(0x20000000, 0x1000, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    EXPECT_NE(p, MAP_FAILED);
    void *q = mremap(p, 0x1000, 0x2000, MREMAP_MAYMOVE);
    EXPECT_NE(q, MAP_FAILED);  // Should not crash
} TEND
TEST_END
```

## StarryOS-Specific Considerations

### Missing Infrastructure

StarryOS may lack some infrastructure Syzkaller expects:
- **kcov**: Kernel coverage instrumentation. Syzkaller uses this for coverage-guided fuzzing. Without it, fuzzing is random (still useful but less efficient). Implementing kcov support in StarryOS would be a valuable feature.
- **/dev/null, /dev/zero**: Syzkaller's executor uses these. Ensure they exist in the pseudofs.
- **Networking between host and guest**: syz-manager communicates with syz-executor via serial or network. Serial is simpler — configure QEMU with `-serial mon:stdio` and use the serial console for communication.

### Incremental Approach

1. **Phase 1**: Run syz-executor manually with a single handwritten syscall sequence. Verify it doesn't crash.
2. **Phase 2**: Run syz-manager with `enable_syscalls` restricted to 5-10 well-tested syscalls. Verify no crashes in the "known-good" set.
3. **Phase 3**: Expand to all implemented syscalls. Let it fuzz for hours. Any crash is a real bug.
4. **Phase 4**: Add SMP (increase `smp` in config). This dramatically increases the chance of finding concurrency bugs.

### Integration with starry-harness

After a Syzkaller run:
1. Convert reproducers to `starry_test.h` format
2. Run through the Linux comparison pipeline (linux-ref-test.sh) to confirm it's a StarryOS bug, not a test bug
3. Classify via bug-triager agent
4. Add to known.json
5. Generate bug report via report skill
6. Run through the adaptive review pipeline if a fix is proposed

The `evolve` skill should check for new Syzkaller crashes in `workdir/crashes/` during the reflect phase and automatically queue them for investigation.
