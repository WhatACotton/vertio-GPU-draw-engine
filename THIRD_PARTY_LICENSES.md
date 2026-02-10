# Third-Party Software Licenses

This package includes the following GPL-licensed open-source components:

## Components

### Linux Kernel
- **Version**: 6.x (rv32ima)
- **License**: GPL v2
- **Copyright**: Linux kernel developers and contributors
- **Source**: Included in `source/` directory

### BusyBox
- **Version**: 1.36.x
- **License**: GPL v2
- **Copyright**: Erik Andersen and BusyBox contributors
- **Source**: Included in `source/` directory

### OpenSBI (RISC-V Supervisor Binary Interface)
- **Version**: Latest
- **License**: BSD 2-Clause
- **Copyright**: RISC-V International
- **Source**: Included in `source/` directory

### Renode
- **Version**: 1.14+
- **License**: MIT
- **Copyright**: Antmicro Ltd.
- **Source**: https://github.com/renode/renode

### Verilator-generated Libraries
- **License**: GPL v3
- **Copyright**: Verilator contributors
- **Note**: Generated from encrypted RTL. Decryption keys available on request.
- **Source**: Verilator upstream at https://github.com/verilator/verilator

### Draw Engine RTL
- **License**: Apache License 2.0
- **Copyright**: 2024 draw_COJT Project
- **Format**: IEEE P1735 encrypted SystemVerilog

## GPL Compliance

Source code for all GPL components is included in the `source/` directory of this package.

### Building from Source

```bash
cd source/scripts
./build_linux.sh
```

See `source/BUILD.md` for detailed build instructions.

### Upstream Repositories

- Linux Kernel: https://git.kernel.org/
- BusyBox: https://busybox.net/
- OpenSBI: https://github.com/riscv-software-src/opensbi
- Verilator: https://github.com/verilator/verilator

## Draw Engine RTL

The Draw Engine hardware design is distributed as encrypted RTL. Decryption keys are available under separate agreement. Contact via GitHub Issues for details.

## Questions

For license inquiries or source code requests, please open an issue at the project's GitHub repository.

