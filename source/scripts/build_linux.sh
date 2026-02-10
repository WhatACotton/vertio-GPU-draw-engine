#!/usr/bin/env bash
# build_linux.sh — Build Linux boot artifacts for VirtIO-GPU co-simulation
#
# Produces:
#   boot/fw_jump.bin                    OpenSBI firmware (fw_jump, rv32)
#   boot/Image                          Linux kernel Image (rv32ima, minimal)
#   boot/rootfs.cpio                    Minimal initramfs with busybox + UIO test
#   boot/draw_engine_soc_virtio.dtb     Compiled device tree
#
# Requirements (via nix develop .#cosim):
#   - riscv64-unknown-elf-gcc      (bare-metal, for kernel + OpenSBI)
#   - riscv32-unknown-linux-gnu-gcc  (Linux cross, for BusyBox + userspace)
#   - dtc, git, make, cpio, findutils
#
# Usage:
#   nix develop .#cosim
#   ./renode/scripts/build_linux.sh [--clean]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BOOT_DIR="$PROJECT_ROOT/renode/boot"
BUILD_DIR="$PROJECT_ROOT/renode/build_linux"
DTS_FILE="$PROJECT_ROOT/renode/dts/draw_engine_soc_virtio.dts"

# Two cross-compilers:
#   - Bare-metal for kernel/OpenSBI (doesn't need libc)
#   - Linux for BusyBox/userspace (needs Linux headers + glibc)
CROSS_BARE="${CROSS_BARE:-riscv64-none-elf-}"
CROSS_LINUX="${CROSS_LINUX:-riscv32-unknown-linux-gnu-}"

NPROC=$(nproc 2>/dev/null || echo 4)

# ── Color output ─────────────────────────────────────────────
info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$*"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$*"; }
warn()  { printf "\033[1;33m[WARN]\033[0m  %s\n" "$*"; }
err()   { printf "\033[1;31m[ERR]\033[0m   %s\n" "$*" >&2; }

# ── Clean ────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    info "Cleaning build artifacts..."
    rm -rf "$BUILD_DIR" "$BOOT_DIR"
    ok "Clean done."
    exit 0
fi

# ── Pre-flight checks ───────────────────────────────────────
check_tool() {
    if ! command -v "$1" &>/dev/null; then
        err "$1 not found. Enter 'nix develop .#cosim' first."
        exit 1
    fi
}

check_tool dtc
check_tool "${CROSS_BARE}gcc"
check_tool make
check_tool cpio

# Check for Linux cross-compiler (optional — fall back to minimal init)
HAS_LINUX_CC=false
if command -v "${CROSS_LINUX}gcc" &>/dev/null; then
    HAS_LINUX_CC=true
    info "Linux cross-compiler: $(${CROSS_LINUX}gcc --version | head -1)"
else
    warn "Linux cross-compiler (${CROSS_LINUX}gcc) not found."
    warn "Will build minimal init without BusyBox (limited shell)."
fi

mkdir -p "$BOOT_DIR" "$BUILD_DIR"

# ══════════════════════════════════════════════════════════════
# 1. Compile Device Tree
# ══════════════════════════════════════════════════════════════
info "Compiling device tree..."

dtc -I dts -O dtb -o "$BOOT_DIR/draw_engine_soc_virtio.dtb" "$DTS_FILE"
ok "DTB: $BOOT_DIR/draw_engine_soc_virtio.dtb"

# ══════════════════════════════════════════════════════════════
# 2. Build OpenSBI (fw_jump for rv32)
# ══════════════════════════════════════════════════════════════
OPENSBI_DIR="$BUILD_DIR/opensbi"
OPENSBI_VERSION="v1.4"

if [[ ! -f "$BOOT_DIR/fw_jump.bin" ]]; then
    info "Building OpenSBI $OPENSBI_VERSION..."

    if [[ ! -d "$OPENSBI_DIR" ]]; then
        git clone --depth 1 --branch "$OPENSBI_VERSION" \
            https://github.com/riscv-software-src/opensbi.git "$OPENSBI_DIR"
    fi

    cd "$OPENSBI_DIR"
    # Use the Linux cross-compiler for OpenSBI — its linker supports
    # --exclude-libs and .dynsym, which the bare-metal ld lacks.
    # OpenSBI builds with -nostdlib -ffreestanding, so no libc is needed.
    # Force C17 because GCC 15+ defaults to C23 (bool is a keyword).
    make -j"$NPROC" \
        CROSS_COMPILE="$CROSS_LINUX" \
        CC="${CROSS_LINUX}gcc -std=gnu17" \
        PLATFORM=generic \
        PLATFORM_RISCV_XLEN=32 \
        PLATFORM_RISCV_ISA=rv32ima_zicsr_zifencei \
        PLATFORM_RISCV_ABI=ilp32 \
        FW_TEXT_START=0x0 \
        FW_JUMP_ADDR=0x40400000 \
        FW_JUMP_FDT_ADDR=0x40200000
    cp build/platform/generic/firmware/fw_jump.bin "$BOOT_DIR/fw_jump.bin"
    cp build/platform/generic/firmware/fw_jump.elf "$BOOT_DIR/fw_jump.elf"
    ok "OpenSBI: $BOOT_DIR/fw_jump.bin + fw_jump.elf"
else
    ok "OpenSBI: $BOOT_DIR/fw_jump.bin (cached)"
fi

# ══════════════════════════════════════════════════════════════
# 3. Build Linux Kernel (rv32ima + virtio-gpu)
# ══════════════════════════════════════════════════════════════
LINUX_SRC_DIR="$BUILD_DIR/linux"
LINUX_VERSION="v6.6"

if [[ ! -f "$BOOT_DIR/Image" ]]; then
    info "Building Linux kernel $LINUX_VERSION..."

    if [[ ! -d "$LINUX_SRC_DIR" ]]; then
        git clone --depth 1 --branch "$LINUX_VERSION" \
            https://github.com/torvalds/linux.git "$LINUX_SRC_DIR"
    fi

    cd "$LINUX_SRC_DIR"

    # Create seed config — KCONFIG_ALLCONFIG forces these during allnoconfig
    # so arch-level settings like 32-bit are applied from the start
    cat > "$BUILD_DIR/virtio_gpu.config" <<'EOF'
# ── RISC-V 32-bit base (MUST be set via KCONFIG_ALLCONFIG) ──
CONFIG_ARCH_RV32I=y
CONFIG_NONPORTABLE=y
CONFIG_MMU=y

# ── RISC-V ISA extensions (match rv32imafdc cross-compiler ABI) ──
CONFIG_RISCV_ISA_C=y
CONFIG_FPU=y

# ── Console / TTY ──
CONFIG_TTY=y
CONFIG_VT=y
CONFIG_VT_CONSOLE=y
CONFIG_PRINTK=y
CONFIG_BUG=y

# ── 8250/16550 UART (ns16550a in Renode) ──
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_CONSOLE=y
CONFIG_SERIAL_OF_PLATFORM=y

# ── Device tree ──
CONFIG_OF=y
CONFIG_OF_EARLY_FLATTREE=y

# ── Timer / IRQ ──
CONFIG_RISCV_TIMER=y
CONFIG_TIMER_OF=y
CONFIG_IRQ_DOMAIN=y

# ── VirtIO core ──
CONFIG_VIRTIO_MENU=y
CONFIG_VIRTIO=y
CONFIG_VIRTIO_MMIO=y

# ── DRM + VirtIO GPU ──
CONFIG_DRM=y
CONFIG_DRM_VIRTIO_GPU=y
CONFIG_DRM_VIRTIO_GPU_KMS=y
CONFIG_DRM_FBDEV_EMULATION=y
CONFIG_FB=y
CONFIG_FB_DEVICE=y
CONFIG_FRAMEBUFFER_CONSOLE=y

# ── UIO for legacy draw_engine path ──
CONFIG_UIO=y

# ── /dev/mem for direct physical memory access (FB flush) ──
CONFIG_DEVMEM=y

# ── Initramfs (CPIO from RAM) ──
CONFIG_BLK_DEV_INITRD=y
CONFIG_BLOCK=y

# ── Minimal filesystem support ──
CONFIG_PROC_FS=y
CONFIG_SYSFS=y
CONFIG_DEVTMPFS=y
CONFIG_DEVTMPFS_MOUNT=y
CONFIG_TMPFS=y

# ── Size optimization ──
CONFIG_EMBEDDED=y
CONFIG_CC_OPTIMIZE_FOR_SIZE=y

# ── ELF binary execution (REQUIRED to run any userspace) ──
CONFIG_BINFMT_ELF=y
CONFIG_BINFMT_SCRIPT=y

# ── Needed for /dev nodes ──
CONFIG_UNIX98_PTYS=y

# ── SBI console (earlycon) ──
CONFIG_RISCV_SBI=y
CONFIG_RISCV_SBI_V01=y
CONFIG_SERIAL_EARLYCON=y
CONFIG_HVC_RISCV_SBI=y

# ── Disable EFI (saves ~1MB, not needed) ──
# CONFIG_EFI is not set
EOF

    # allnoconfig + KCONFIG_ALLCONFIG: start minimal, with our options forced
    make ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" \
        KCONFIG_ALLCONFIG="$BUILD_DIR/virtio_gpu.config" allnoconfig

    # Resolve remaining dependencies automatically
    make ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" olddefconfig

    # If rootfs directory already exists (from a previous build), embed it
    # as initramfs now. Otherwise, step 5 will handle this after rootfs build.
    ROOTFS_DIR="$BUILD_DIR/rootfs"
    DEVLIST="$BUILD_DIR/initramfs_devices.txt"

    # Create device list file for initramfs (device nodes that must exist
    # BEFORE devtmpfs is mounted — /dev/console is needed for init's console)
    cat > "$DEVLIST" <<'DEVNODES'
# Device nodes for early boot console
nod /dev/console 0622 0 0 c 5 1
nod /dev/null    0666 0 0 c 1 3
nod /dev/ttyS0   0666 0 0 c 4 64
DEVNODES

    if [[ -d "$ROOTFS_DIR" ]]; then
        info "Embedding existing rootfs into kernel as initramfs..."
        INITRAMFS_SOURCES="$ROOTFS_DIR $DEVLIST"
        scripts/config --file .config \
            --set-str INITRAMFS_SOURCE "$INITRAMFS_SOURCES"
        make ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" olddefconfig
    fi

    # Build
    make -j"$NPROC" ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" Image
    cp arch/riscv/boot/Image "$BOOT_DIR/Image"
    ok "Kernel: $BOOT_DIR/Image ($(du -h "$BOOT_DIR/Image" | cut -f1))"
else
    ok "Kernel: $BOOT_DIR/Image (cached)"
fi

# ══════════════════════════════════════════════════════════════
# 4. Build rootfs (BusyBox initramfs or minimal init)
# ══════════════════════════════════════════════════════════════
ROOTFS_DIR="$BUILD_DIR/rootfs"

# Invalidate rootfs cache if build script changed (e.g. inittab edits)
if [[ -f "$BOOT_DIR/rootfs.cpio" && "$0" -nt "$BOOT_DIR/rootfs.cpio" ]]; then
    info "build_linux.sh is newer than rootfs.cpio — rebuilding rootfs..."
    rm -f "$BOOT_DIR/rootfs.cpio"
fi

if [[ ! -f "$BOOT_DIR/rootfs.cpio" ]]; then
    if $HAS_LINUX_CC; then
        # ── Full BusyBox rootfs ──────────────────────────────
        BUSYBOX_DIR="$BUILD_DIR/busybox"
        BUSYBOX_VERSION="1_36_1"

        info "Building BusyBox rootfs (with Linux cross-compiler)..."

        if [[ ! -d "$BUSYBOX_DIR" ]]; then
            git clone --depth 1 --branch "$BUSYBOX_VERSION" \
                https://git.busybox.net/busybox "$BUSYBOX_DIR"
        fi

        cd "$BUSYBOX_DIR"
        make CROSS_COMPILE="$CROSS_LINUX" ARCH=riscv defconfig
        sed -i 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config
        sed -i 's/CONFIG_FEATURE_HAVE_RPC=y/# CONFIG_FEATURE_HAVE_RPC is not set/' .config 2>/dev/null || true
        sed -i 's/CONFIG_FEATURE_INETD_RPC=y/# CONFIG_FEATURE_INETD_RPC is not set/' .config 2>/dev/null || true
        # Disable tc (traffic control) — CBQ constants removed from newer kernel headers
        sed -i 's/CONFIG_TC=y/# CONFIG_TC is not set/' .config 2>/dev/null || true
        # Disable features that pull in -lresolv (nslookup, etc.)
        sed -i 's/CONFIG_NSLOOKUP=y/# CONFIG_NSLOOKUP is not set/' .config 2>/dev/null || true
        # Tell linker where to find static glibc from nix
        GLIBC_STATIC=$(find /nix/store -maxdepth 1 -name "*glibc*riscv32*static*" -type d 2>/dev/null | head -1)
        if [[ -n "$GLIBC_STATIC" ]]; then
            info "Using static glibc: $GLIBC_STATIC"
            EXTRA_LDFLAGS="-L$GLIBC_STATIC/lib"
        else
            EXTRA_LDFLAGS=""
        fi
        make -j"$NPROC" CROSS_COMPILE="$CROSS_LINUX" ARCH=riscv \
            EXTRA_LDFLAGS="$EXTRA_LDFLAGS" \
            LDFLAGS="$EXTRA_LDFLAGS"

        rm -rf "$ROOTFS_DIR"
        mkdir -p "$ROOTFS_DIR"
        make CROSS_COMPILE="$CROSS_LINUX" ARCH=riscv CONFIG_PREFIX="$ROOTFS_DIR" install

    else
        # ── Minimal init (bare-metal, no BusyBox) ────────────
        info "Building minimal rootfs (no Linux cross-compiler)..."
        rm -rf "$ROOTFS_DIR"
        mkdir -p "$ROOTFS_DIR"/{bin,sbin,usr/bin,usr/sbin}

        # Minimal /init using raw syscalls (no libc needed)
        cat > "$BUILD_DIR/init.c" <<'INITC'
#define __NR_write  64
static long sys_write(int fd, const void *buf, unsigned long len) {
    register long a0 __asm__("a0") = fd;
    register long a1 __asm__("a1") = (long)buf;
    register long a2 __asm__("a2") = len;
    register long a7 __asm__("a7") = __NR_write;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a1), "r"(a2), "r"(a7) : "memory");
    return a0;
}
static void puts(const char *s) {
    unsigned long len = 0; while (s[len]) len++;
    sys_write(1, s, len);
}
static void print_hex(unsigned long v) {
    char buf[11] = "0x00000000";
    const char hex[] = "0123456789abcdef";
    for (int i = 9; i >= 2; i--) { buf[i] = hex[v & 0xf]; v >>= 4; }
    sys_write(1, buf, 10);
}
static unsigned long mmio_read(unsigned long addr) {
    return *(volatile unsigned long *)addr;
}
void _start(void) {
    puts("\n===== draw_engine Linux (VirtIO-GPU) =====\n");
    puts("Minimal init — probing hardware...\n\n");
    puts("VirtIO MMIO @ 0x82000000:\n");
    puts("  MagicValue = "); print_hex(mmio_read(0x82000000)); puts("\n");
    puts("  Version    = "); print_hex(mmio_read(0x82000004)); puts("\n");
    puts("  DeviceID   = "); print_hex(mmio_read(0x82000008)); puts("\n");
    puts("  VendorID   = "); print_hex(mmio_read(0x8200000C)); puts("\n\n");
    puts("draw_engine @ 0x82002000:\n");
    puts("  VERSION    = "); print_hex(mmio_read(0x82002000)); puts("\n\n");
    puts("Hardware probe complete. Halting.\n");
    for (;;) __asm__ volatile("wfi");
}
INITC
        ${CROSS_BARE}gcc -march=rv32ima -mabi=ilp32 \
            -nostdlib -nostartfiles -static -O2 \
            -o "$ROOTFS_DIR/init" "$BUILD_DIR/init.c"
    fi

    # ── Create rootfs directory structure ────────────────────
    cd "$ROOTFS_DIR"
    mkdir -p proc sys dev etc/init.d tmp mnt

    if $HAS_LINUX_CC; then
        cat > etc/init.d/rcS <<'INITEOF'
#!/bin/sh
mount -t proc    proc    /proc  2>/dev/null
mount -t sysfs   sysfs   /sys   2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null
echo ""
echo "===== draw_engine Linux (VirtIO-GPU) ====="
echo "Kernel: $(uname -r)"
echo ""
if [ -d /sys/bus/virtio/devices ]; then
    echo "VirtIO devices:"
    for d in /sys/bus/virtio/devices/*; do
        if [ -d "$d" ]; then
            device_id=$(cat "$d/device" 2>/dev/null || echo "?")
            vendor_id=$(cat "$d/vendor" 2>/dev/null || echo "?")
            echo "  $(basename $d): device=$device_id vendor=$vendor_id"
        fi
    done
else
    echo "No VirtIO devices found"
fi
echo ""
if [ -d /sys/class/drm ]; then
    echo "DRM devices:"
    ls -la /sys/class/drm/ 2>/dev/null
    if [ -d /dev/dri ]; then
        echo "/dev/dri:"
        ls -la /dev/dri/ 2>/dev/null
    fi
else
    echo "No DRM devices found"
fi
echo ""
if [ -d /sys/class/uio ]; then
    echo "UIO devices:"
    for u in /sys/class/uio/uio*; do
        if [ -d "$u" ]; then
            name=$(cat "$u/name" 2>/dev/null || echo "?")
            echo "  $(basename $u): name=$name"
        fi
    done
fi
echo ""
if [ -x /usr/bin/draw_uio_test ]; then
    echo "Running draw_uio_test..."
    /usr/bin/draw_uio_test
fi
echo "Ready. Type 'poweroff' to exit."
INITEOF
        chmod +x etc/init.d/rcS

        # BusyBox inittab: spawn shell on both serial and framebuffer console
        cat > etc/inittab <<'INITTAB'
::sysinit:/etc/init.d/rcS
::respawn:-/bin/sh
tty0::respawn:/bin/sh -l </dev/tty0 >/dev/tty0 2>&1
::ctrlaltdel:/sbin/reboot
::shutdown:/bin/umount -a -r
INITTAB

        cat > init <<'INITEOF'
#!/bin/sh
# Mount essential filesystems (skip if already mounted by kernel)
mount -t proc    proc    /proc  2>/dev/null
mount -t sysfs   sysfs   /sys   2>/dev/null
mount -t devtmpfs devtmpfs /dev 2>/dev/null

# Run BusyBox init (reads /etc/inittab)
exec /sbin/init
INITEOF
        chmod +x init
    fi

    # ── Cross-compile draw_uio_test if we have Linux CC ─────
    if $HAS_LINUX_CC && [[ -f "$PROJECT_ROOT/linux/draw_uio_test.c" ]]; then
        info "Cross-compiling draw_uio_test..."
        mkdir -p "$ROOTFS_DIR/usr/bin"
        ${CROSS_LINUX}gcc -march=rv32ima -mabi=ilp32 \
            -O2 -static \
            -o "$ROOTFS_DIR/usr/bin/draw_uio_test" \
            "$PROJECT_ROOT/linux/draw_uio_test.c" 2>/dev/null \
            && ok "draw_uio_test included in rootfs" \
            || warn "draw_uio_test compilation failed (non-fatal)"
    fi

    # ── Cross-compile fb_tux (framebuffer graphics demo) ────
    if $HAS_LINUX_CC && [[ -f "$PROJECT_ROOT/linux/fb_tux.c" ]]; then
        info "Cross-compiling fb_tux..."
        mkdir -p "$ROOTFS_DIR/usr/bin"
        ${CROSS_LINUX}gcc -march=rv32imafdc -mabi=ilp32d \
            -O2 -static \
            -I"$PROJECT_ROOT/linux" \
            -o "$ROOTFS_DIR/usr/bin/fb_tux" \
            "$PROJECT_ROOT/linux/fb_tux.c" 2>&1 \
            && ok "fb_tux included in rootfs" \
            || warn "fb_tux compilation failed (non-fatal)"
    fi

    # ── Create CPIO archive ──────────────────────────────────
    cd "$ROOTFS_DIR"
    find . | cpio -o -H newc > "$BOOT_DIR/rootfs.cpio" 2>/dev/null
    ok "Rootfs: $BOOT_DIR/rootfs.cpio ($(wc -c < "$BOOT_DIR/rootfs.cpio") bytes)"
else
    ok "Rootfs: $BOOT_DIR/rootfs.cpio (cached)"
fi

# ══════════════════════════════════════════════════════════════
# 5. Rebuild kernel with embedded initramfs (if rootfs was just built)
# ══════════════════════════════════════════════════════════════
ROOTFS_DIR="$BUILD_DIR/rootfs"
DEVLIST="$BUILD_DIR/initramfs_devices.txt"

if [[ -d "$ROOTFS_DIR" && -d "$LINUX_SRC_DIR" ]]; then
    cd "$LINUX_SRC_DIR"
    CURRENT_SRC=$(grep '^CONFIG_INITRAMFS_SOURCE=' .config 2>/dev/null | sed 's/.*="\(.*\)"/\1/')
    EXPECTED_SRC="$ROOTFS_DIR"
    [[ -f "$DEVLIST" ]] && EXPECTED_SRC="$ROOTFS_DIR $DEVLIST"

    if [[ "$CURRENT_SRC" != "$EXPECTED_SRC" ]]; then
        info "Embedding rootfs into kernel as initramfs..."
        scripts/config --file .config \
            --set-str INITRAMFS_SOURCE "$EXPECTED_SRC"
        make ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" olddefconfig
        make -j"$NPROC" ARCH=riscv CROSS_COMPILE="$CROSS_LINUX" Image
        cp arch/riscv/boot/Image "$BOOT_DIR/Image"
        ok "Kernel rebuilt with embedded initramfs: $BOOT_DIR/Image ($(du -h "$BOOT_DIR/Image" | cut -f1))"
    fi
fi

# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
echo ""
info "Boot artifacts ready in $BOOT_DIR:"
ls -lh "$BOOT_DIR/"
echo ""
info "Run Linux co-simulation:"
echo "  make cosim-linux \\"
echo "    SBI=$BOOT_DIR/fw_jump.bin \\"
echo "    KERNEL=$BOOT_DIR/Image \\"
echo "    DTB=$BOOT_DIR/draw_engine_soc_virtio.dtb \\"
echo "    ROOTFS=$BOOT_DIR/rootfs.cpio"
