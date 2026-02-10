# Draw Engine — 2D Graphics Accelerator

Demo and evaluation package for the "Draw Engine" hardware 2D graphics accelerator.  
With just a Docker environment, you can immediately experience **HW-accelerated rendering on RISC-V Linux**.

---

## System Overview

Draw Engine is a 2D graphics accelerator IP that executes rectangle fill, texture transfer (BitBlt), alpha blending, and stencil transparency processing in a hardware pipeline.

By simply submitting a **display list (command sequence)** from the CPU,
it performs high-speed 2D rendering operations on the framebuffer in VRAM.

### Key Features

| Feature | Description |
|---------|-------------|
| **Rectangle Fill** | Fill rectangular area with specified color |
| **Texture Transfer** | Copy texture image from VRAM to arbitrary position |
| **Alpha Blending** | Alpha compositing between source and destination |
| **Stencil (Transparency)** | Color key processing treating specified color as transparent |
| **Supported Resolutions** | VGA (640×480) / SVGA (800×600) / XGA (1024×768) / SXGA (1280×1024) |
| **Pixel Format** | ARGB8888 / RGB888 |

---

## Architecture

### Bus Interface

Draw Engine has two bus interfaces:

- **APB3** — Register control from CPU (command submission, status readout)
- **AXI4 Master** — Direct DMA access to VRAM (pixel read/write)

```
              ┌──────────────┐
    APB3      │              │  AXI4 Master
  ─────────▶  │  Draw Engine │ ◀──────────▶  VRAM (Main Memory)
Register Ctrl │              │  Pixel DMA
              └──────────────┘
                     │
                 DRW_IRQ (Rendering Complete Interrupt)
```

### 5-Stage Pipeline

Rendering commands are processed through the following 5-stage pipeline.
Stages are connected by FIFOs, absorbing AXI latency variations.

```
  ┌─────────────┐    ┌─────────────┐    ┌───────────┐    ┌────────────┐    ┌────────────┐
  │ ① Command   │    │ ② Address   │    │ ③ VRAM    │    │ ④ Pixel    │    │ ⑤ VRAM    │
  │    Parse    │──▶│  Generation  │──▶│    Read    │──▶│ Generation  │──▶│    Write   │
  │             │    │             │    │ (AXI Read)│    │ α Blend     │    │(AXI Write)│
  │             │    │             │    │           │    │ Stencil     │    │           │
  └─────────────┘    └─────────────┘    └───────────┘    └────────────┘    └────────────┘
```

### Command Architecture

The CPU writes command words to the command FIFO.
The rendering engine reads commands from the FIFO and processes them sequentially in the pipeline.

Commands are broadly classified into three types:

- **State Setting Commands** — Set rendering parameters such as framebuffer address, drawing area, texture, fill color, stencil, alpha blending, etc.
- **Drawing Execution Commands** — Execute rectangle fill, texture transfer (BitBlt)
- **Control Commands** — NOP, display list termination, etc.

---

## VirtIO-GPU Integration

In this package, the Draw Engine can be used from the Linux kernel standard `virtio-gpu.ko` driver
via a **VirtIO-GPU frontend**.

### VirtIO-GPU Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                     SoC (Renode Co-Simulation)                        │
│                                                                       │
│  ┌─────────────┐                  ┌─────────────────────────────────┐ │
│  │  VexRiscv    │    APB3          │       VirtIO-GPU Engine         │ │
│  │  rv32ima     │ ───────────────▶ │                                 │ │
│  │  Linux       │  Register Ctrl   │  ┌───────────────────────────┐ │ │
│  │              │                  │  │  VirtIO MMIO Transport    │ │ │
│  │ virtio-gpu   │                  │  │  Device Detection/        │ │ │
│  │ .ko driver   │                  │  │  Feature Negotiation      │ │ │
│  │              │                  │  └─────────┬─────────────────┘ │ │
│  │              │                  │            │                    │ │
│  │              │                  │  ┌─────────▼─────────────────┐ │ │
│  │              │     AXI4         │  │  Virtqueue Processing     │ │ │
│  │              │ ◀─────────────── │  │  Engine                   │ │ │
│  │              │ Desc/Ring DMA    │  │  DMA Ring Processing      │ │ │
│  │              │                  │  └─────────┬─────────────────┘ │ │
│  │              │                  │            │                    │ │
│  │              │                  │  ┌─────────▼─────────────────┐ │ │
│  │              │                  │  │  GPU Command Translation  │ │ │
│  │              │                  │  │  VirtIO GPU → Draw Engine │ │ │
│  │              │                  │  └─────────┬─────────────────┘ │ │
│  │              │     AXI4         │  ┌─────────▼─────────────────┐ │ │
│  │              │ ◀─────────────── │  │  Draw Engine              │ │ │
│  │              │  Pixel DMA       │  │  5-Stage Pipeline         │ │ │
│  │              │                  │  │  Rendering Process        │ │ │
│  └──────┬───────┘                  │  └───────────────────────────┘ │ │
│         │                          └─────────────────────────────────┘ │
│  ┌──────▼──────────────────────┐                                      │
│  │        Main RAM             │                                      │
│  │  Kernel + Root FS           │                                      │
│  │  Framebuffer                │                                      │
│  └─────────────────────────────┘                                      │
└───────────────────────────────────────────────────────────────────────┘
```

### Operation Flow

1. **Linux kernel recognizes device** — Detects `virtio,mmio` node in Device Tree and loads `virtio-gpu.ko`
2. **GPU command issuance** — User space rendering requests reach HW via VirtIO queue
3. **Command translation** — VirtIO GPU commands are converted to Draw Engine native command sequences
4. **HW rendering execution** — Draw Engine executes pixel DMA in pipeline and updates framebuffer
5. **Interrupt notification** — After completion, updates VirtIO Used Ring and notifies Linux via IRQ

### Dual Access Path

VirtIO-GPU Engine provides two access paths:

- **VirtIO MMIO Path** — Access from Linux `virtio-gpu.ko` driver via standard VirtIO protocol
- **Legacy Register Path** — Direct access to Draw Engine registers from bare-metal environment or UIO driver

---

## Simulation Environment

The demos in this package run on **Renode + Verilator Co-Simulation**.

| Component | Description |
|-----------|-------------|
| **CPU** | VexRiscv rv32ima (with MMU) |
| **OS** | Linux (OpenSBI + Linux Kernel + BusyBox rootfs) |
| **Graphics IP** | Draw Engine RTL compiled to shared library with Verilator |
| **Simulator** | Renode (multi-peripheral SoC emulator) |
| **Display** | VNC / HTTP framebuffer viewer (Python) |

The RTL is compiled to C++ with Verilator, and the entire SoC is simulated with cycle accuracy
using Renode's Co-Simulation framework.
Rendering is performed by accessing actual HW registers from the Linux kernel running on the CPU.

### SoC Configuration

| Component | Description |
|-----------|-------------|
| ROM | OpenSBI firmware |
| SRAM | High-speed work memory |
| Main RAM (64 MiB) | Linux kernel + root FS + framebuffer |
| VirtIO-GPU Engine | Draw Engine + VirtIO frontend |
| UART | Serial console |
| CLINT / PLIC | Timer / interrupt controller |

---

## How to Run Demos

### Requirements

- **Docker** only
- **Git LFS** (if cloning from repository)

### Clone Repository (with Git LFS)

This repository uses **Git LFS** to store large files (Docker image: 922 MB).

```bash
# Install Git LFS (if not already installed)
# Ubuntu/Debian:
sudo apt-get install git-lfs

# macOS:
brew install git-lfs

# Initialize Git LFS
git lfs install

# Clone repository (automatically downloads LFS files)
git clone https://github.com/WhatACotton/vertio-GPU-draw-engine.git
cd vertio-GPU-draw-engine
```

**Note:** If you cloned without Git LFS, the Docker tar will be just a pointer file.
To download the actual file:

```bash
git lfs pull
```

### 1. Extract (from tar.gz package)

If you downloaded the tar.gz archive instead of cloning:

```bash
tar xzf draw_engine.tar.gz
cd draw_engine
```

### 2. Load Docker Image

```bash
docker load < formal-hdl-env.tar
```

### 3. Linux Boot Demo 🐧

Boot Linux on a RISC-V SoC equipped with Draw Engine and perform
HW-accelerated rendering to the framebuffer via VirtIO-GPU.

```bash
./exec.sh linux
```

After startup, you can connect to the following endpoints:

| Endpoint | Connection Method |
|----------|-------------------|
| **Framebuffer** | Open http://localhost:5800 in browser |
| **UART Console** | `telnet localhost 4321` |
| **VNC Connection** | `vncviewer localhost:5900` |
| **Renode Monitor** | `telnet localhost 1234` |

To boot in headless mode (UART only):

```bash
./exec.sh linux-headless
```

#### fb_tux — Framebuffer Drawing Tool 🐧

After Linux boots, you can draw directly to the framebuffer from the UART console (`telnet localhost 4321`)
using the `fb_tux` command. Drawing results can be viewed in real-time via VNC / HTTP viewer.

```bash
fb_tux tux              # Draw Tux (Linux penguin)
fb_tux logo             # Draw kernel boot logo style
fb_tux color red        # Fill entire screen with color
fb_tux gradient         # RGB gradient
fb_tux fill 00FF00FF    # Fill with specified color (AARRGGBB)
fb_tux clear            # Clear screen
fb_tux text             # Return to fbcon text mode
```

`fb_tux` is a binary included in the rootfs that directly mmaps `/dev/fb0` for rendering.
Since Draw Engine writes pixel data to VRAM via VirtIO-GPU,
it can be used to verify HW acceleration operation even though it's software rendering.

### 4. Image Processing Demo 🐰

Bare-metal firmware uses Draw Engine to perform image processing (texture transfer + alpha blending).
The included rabbit image is used by default.

```bash
# Run with default image
./exec.sh imgproc

# Specify your own image
./exec.sh imgproc photo.png
```

### 5. Interactive Shell

You can freely operate inside the Docker container.

```bash
./exec.sh

# Inside container:
make help           # List available targets
make demo-info      # Package details
make check-results  # Check test results
```

---

## Package Structure

```
draw_engine/
├── formal-hdl-env.tar          # Docker execution environment (all tools included)
├── hdl/                        # Encrypted RTL (IEEE P1735)
├── boot/                       # Pre-built Linux boot images
│   ├── fw_jump.bin            #   OpenSBI firmware
│   ├── Image                  #   Linux kernel (rv32)
│   ├── rootfs.cpio            #   Root filesystem
│   └── *.dtb                  #   Device tree blob
├── lib/                        # Verilator shared libraries (pre-built binaries)
│   ├── libVtop.so             #   Draw Engine (for bare-metal)
│   └── libVtop_virtio.so     #   VirtIO-GPU Engine (for Linux)
├── fw/                         # Pre-built firmware (binaries only)
│   ├── draw_imgproc.bin       #   Image processing demo
│   └── draw_fb.bin            #   Framebuffer demo
├── renode/                     # Renode platform definitions and scripts
├── sample/                     # Sample images
│   └── rabbit.png             #   Default demo image 🐰
├── results/                    # Pre-executed test results
│   ├── results.xml            #   cocotb test results (JUnit XML)
│   └── gallery/               #   Gallery of rendering output images
├── exec.sh                     # Demo launch script
├── test.sh                     # Package verification script
└── README.md                   # This file
```

> **Note**: All components included in this package (RTL, firmware, shared libraries,
> Linux boot images) consist only of pre-built binaries.
> No source code or driver sources are included.

## Package Verification

```bash
# Full check (structural integrity + binaries + test results)
./test.sh

# Check test results only
./test.sh verify
```

## About RTL

HDL sources are encrypted in **IEEE P1735** format.
They can be decrypted and synthesized with FPGA vendor tools (Vivado, Quartus, etc.), but plaintext sources are not included.

This is because the project is designed as an assignment for the COJT Hardware Course.
To prevent future students from referencing this implementation,
the RTL source is distributed in encrypted form.

## License

See the included LICENSE file for usage terms.

### Open Source Components

This package includes GPL-licensed components (Linux kernel, BusyBox, Verilator libraries).
Source code is included in `source/` directory. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for details.

