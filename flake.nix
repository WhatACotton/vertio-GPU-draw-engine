{
  description = "Unified Formal-Driven Agile Hardware Design Environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    
    # LiteX ecosystem inputs (optional, for litex shell)
    migen-src = { url = "github:m-labs/migen"; flake = false; };
    litex-src = { url = "github:enjoy-digital/litex"; flake = false; };
    litedram-src = { url = "github:enjoy-digital/litedram"; flake = false; };
    liteeth-src = { url = "github:enjoy-digital/liteeth"; flake = false; };
    litescope-src = { url = "github:enjoy-digital/litescope"; flake = false; };
    liteiclink-src = { url = "github:enjoy-digital/liteiclink"; flake = false; };
    litespi-src = { url = "github:litex-hub/litespi"; flake = false; };
    litex-boards-src = { url = "github:litex-hub/litex-boards"; flake = false; };
    pythondata-cpu-vexriscv-src = { url = "github:litex-hub/pythondata-cpu-vexriscv"; flake = false; };
    pythondata-cpu-ibex-src = { url = "github:litex-hub/pythondata-cpu-ibex"; flake = false; };
    pythondata-software-picolibc-src = { 
      url = "git+https://github.com/litex-hub/pythondata-software-picolibc?submodules=1"; 
      flake = false; 
    };
    pythondata-software-compiler_rt-src = { 
      url = "git+https://github.com/litex-hub/pythondata-software-compiler_rt?submodules=1"; 
      flake = false; 
    };
    pythondata-misc-tapcfg-src = { url = "github:litex-hub/pythondata-misc-tapcfg"; flake = false; };
  };

  outputs = { self, nixpkgs, flake-utils, ... }@inputs:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Use Python 3.11 for cocotb compatibility (3.13 has issues with cocotb 2.0.1)
        python = pkgs.python311;

        # ===== Common Python Packages =====
        # Base Python environment for Formal Verification workflow
        formalPythonEnv = python.withPackages (ps: with ps; [
          z3-solver   # For Phase 1: Spec Logic Checking
          cocotb      # For Phase 3: Simulation
          pytest      # Test runner
          pandas      # Data analysis for logs/coverage
          pillow      # Image processing for test frame buffer rendering
        ]);

        # Shared toolchain for shells and Docker image
        toolchain = with pkgs; [
          # --- Shell ---
          zsh
          starship
          which
          findutils

          # --- Synthesis & Verification ---
          yosys
          sby
          boolector
          yices
          z3

          # --- Simulation & Coverage ---
          verilator
          lcov
          gtkwave

          # --- Build Tools ---
          gcc
          gnumake
          formalPythonEnv
        ];

        # Cocotb-focused Python environment
        cocotbPythonEnv = python.withPackages (ps: [ ps.cocotb ps.pytest ps.pillow ]);

        # Python for co-sim image pipeline (img2raw / raw2png)
        cosimPythonEnv = python.withPackages (ps: [ ps.pillow ps.numpy ]);

        # ===== LiteX Package Builder =====
        mkLitePackage = pname: src: deps: python.pkgs.buildPythonPackage {
          inherit pname src;
          version = "latest";
          pyproject = true;
          build-system = [ python.pkgs.setuptools ];
          propagatedBuildInputs = deps;
          postInstall = ''
            PKG_NAME=$(echo ${pname} | tr '-' '_')
            if [ -d "$src/$PKG_NAME" ]; then
              mkdir -p $out/${python.sitePackages}/$PKG_NAME
              cp -r $src/$PKG_NAME/* $out/${python.sitePackages}/$PKG_NAME/
            fi
          '';
          doCheck = false;
        };

        # ===== LiteX Packages =====
        migen = mkLitePackage "migen" inputs.migen-src [ python.pkgs.colorama ];
        pythondata-cpu-vexriscv = mkLitePackage "pythondata-cpu-vexriscv" inputs.pythondata-cpu-vexriscv-src [ ];
        pythondata-cpu-ibex = mkLitePackage "pythondata-cpu-ibex" inputs.pythondata-cpu-ibex-src [ ];
        pythondata-software-picolibc = mkLitePackage "pythondata-software-picolibc" inputs.pythondata-software-picolibc-src [ ];
        pythondata-software-compiler_rt = mkLitePackage "pythondata-software-compiler_rt" inputs.pythondata-software-compiler_rt-src [ ];
        pythondata-misc-tapcfg = mkLitePackage "pythondata-misc-tapcfg" inputs.pythondata-misc-tapcfg-src [ ];

        litex = mkLitePackage "litex" inputs.litex-src [
          migen
          python.pkgs.pyserial
          python.pkgs.pyyaml
          python.pkgs.requests
          python.pkgs.packaging
        ];

        liteiclink = mkLitePackage "liteiclink" inputs.liteiclink-src [ migen litex ];
        litedram = mkLitePackage "litedram" inputs.litedram-src [ migen litex python.pkgs.pyyaml python.pkgs.packaging ];
        liteeth = mkLitePackage "liteeth" inputs.liteeth-src [ migen litex liteiclink ];
        litescope = mkLitePackage "litescope" inputs.litescope-src [ migen litex python.pkgs.pyyaml python.pkgs.pyvcd ];
        litespi = mkLitePackage "litespi" inputs.litespi-src [ migen litex ];
        litex-boards = mkLitePackage "litex-boards" inputs.litex-boards-src [ litex ];

        litexPythonEnv = python.withPackages (ps: [ 
          litex migen litedram liteeth litescope liteiclink litespi litex-boards
          pythondata-cpu-vexriscv
          pythondata-cpu-ibex
          pythondata-software-picolibc
          pythondata-software-compiler_rt
          pythondata-misc-tapcfg
          ps.setuptools ps.colorama ps.pyyaml ps.requests ps.packaging ps.pyvcd
        ]);

        # ===== Common Shell Hook Components =====
        commonShellHook = ''
          if [ -z "$FORMAL_SKIP_SHELL_HOOK" ]; then
            # Add scripts to path for easier execution
            export PATH=$PWD/scripts:$PATH
            export PYTHONPATH=$PWD/scripts:$PYTHONPATH
            export PYTHONNOUSERSITE=1
            
            # Prefer project starship config if present
            if [ -f "$PWD/dotfiles/starship.toml" ]; then
              export STARSHIP_CONFIG="$PWD/dotfiles/starship.toml"
            fi
            
            # Source project zshrc if using zsh
            if [ -n "$ZSH_VERSION" ] && [ -f "$PWD/dotfiles/.zshrc" ]; then
              source "$PWD/dotfiles/.zshrc"
            fi
          fi
        '';

      in
      {
        # ===== Default Shell: Formal Verification Environment =====
        devShells.default = pkgs.mkShell {
          name = "formal-hdl-env";

          buildInputs = toolchain;

          shellHook = commonShellHook + ''
            if [ -z "$FORMAL_SKIP_SHELL_HOOK" ]; then
              # Add Python environment to PYTHONPATH for cocotb
              export PYTHONPATH="${formalPythonEnv}/${python.sitePackages}:$PYTHONPATH"
              
              # Add Python library to LD_LIBRARY_PATH for cocotb VPI
              export LD_LIBRARY_PATH="${python}/lib:$LD_LIBRARY_PATH"
              
              echo "======================================================="
              echo "🚀 Formal-Driven Agile Hardware Environment Activated"
              echo "   - Shell:     $(basename $SHELL)"
              echo "   - Verilator: $(verilator --version | head -n1)"
              echo "   - Yosys:     $(yosys -V 2>&1 | head -n1)"
              echo "   - SBY:       $(sby --version 2>&1 | head -n1 || echo 'installed')"
              echo "   - Python:    $(python3 --version) (w/ z3-solver, cocotb)"
              echo "======================================================="
              echo ""
              echo "Available shells:"
              echo "  • nix develop                  (this shell - formal verification)"
              echo "  • nix develop .#cocotb         (cocotb-focused environment)"
              echo "  • nix develop .#litex          (LiteX development)"
              echo ""
              echo "💡 Tip: Using zsh? Starship prompt is auto-configured!"
              echo ""
              
              # Launch zsh if available and not already in zsh
              if [ -z "$ZSH_VERSION" ] && command -v zsh >/dev/null 2>&1; then
                exec zsh
              fi
            fi
          '';
        };

        # ===== Cocotb Shell: Focused Simulation Environment =====
        devShells.cocotb = pkgs.mkShell {
          name = "cocotb-env";
          
          buildInputs = [ 
            pkgs.zsh
            pkgs.starship
            cocotbPythonEnv 
            pkgs.verilator 
            pkgs.gtkwave
            pkgs.gnumake
          ];
          
          shellHook = commonShellHook + ''
            if [ -z "$FORMAL_SKIP_SHELL_HOOK" ]; then
              # Cocotb environment – only set paths, NOT design-specific variables.
              # SIM / TOPLEVEL / MODULE / VERILOG_SOURCES are intentionally left
              # to each Makefile so that per-target Makefiles (Makefile.render,
              # Makefile.gallery, etc.) are not polluted by stale defaults.
              export PYTHONPATH="${cocotbPythonEnv}/${python.sitePackages}:$PWD/tests:''${PYTHONPATH:-}"
              export LD_LIBRARY_PATH="${python}/lib:$LD_LIBRARY_PATH"
              
              echo "======================================================="
              echo "🧪 Cocotb Simulation Environment Activated"
              echo "   - Verilator: $(verilator --version | head -n1)"
              echo "   - Python:    $(python --version 2>&1)"
              python -c 'import importlib.util; m=importlib.util.find_spec("cocotb"); print("   - Cocotb:    " + (importlib.import_module("cocotb").__version__ if m else "(not installed)"))'
              echo "======================================================="
              echo ""
              echo "Run: cd tests && make -f Makefile.<target>"
              echo ""
            fi
          '';
        };

        # ===== LiteX Shell: Full SoC Development =====
        devShells.litex = pkgs.mkShell {
          name = "litex-env";
          
          buildInputs = [
            pkgs.zsh
            pkgs.starship
            litexPythonEnv
            pkgs.verilator
            pkgs.gtkwave
            pkgs.dtc
            pkgs.libevent
            pkgs.json_c
            pkgs.zlib
            pkgs.meson
            pkgs.ninja
            pkgs.cmake
            pkgs.pkg-config
            pkgs.renode-bin
            pkgs.pkgsCross.riscv64-embedded.buildPackages.gcc
            pkgs.gnumake
          ];

          shellHook = commonShellHook + ''
            if [ -z "$FORMAL_SKIP_SHELL_HOOK" ]; then
              # Populate refs/ from vendor/ via helper script (if refs/ missing)
              if [ -f ./scripts/make_refs.sh ]; then
                sh ./scripts/make_refs.sh "${litexPythonEnv}/${python.sitePackages}" || true
              fi

              # Prepend refs sources to PYTHONPATH for local development
              for r in refs/litex refs/migen refs/litedram refs/liteeth refs/litescope refs/liteiclink refs/litespi refs/litex-boards refs/pythondata-cpu-vexriscv refs/pythondata-cpu-ibex refs/pythondata-software-picolibc refs/pythondata-software-compiler_rt refs/pythondata-misc-tapcfg; do
                if [ -d "$PWD/$r" ]; then
                  export PYTHONPATH="$PWD/$r:$PYTHONPATH"
                fi
              done

              export PYTHONPATH="${litexPythonEnv}/${python.sitePackages}:$PYTHONPATH"

              echo "======================================================="
              echo "🔧 LiteX SoC Development Environment Activated"
              echo "   - Verilator:   $(verilator --version | head -n1)"
              echo "   - Python:      $(python --version 2>&1)"
              python -c 'import litex; print("   - LiteX:       " + litex.__version__)'
              echo "   - RISC-V GCC:  $(riscv64-none-elf-gcc --version 2>&1 | head -n1 || echo 'not in PATH')"
              echo "======================================================="
              echo ""
              echo "LiteX ecosystem ready for SoC development"
              echo ""
            fi
          '';
        };

        # ===== Co-Sim Shell: Renode + Verilator image processing pipeline =====
        devShells.cosim = pkgs.mkShell {
          name = "cosim-env";

          buildInputs = [
            pkgs.zsh
            pkgs.starship
            pkgs.gnumake
            pkgs.gcc           # host C/C++ (for Verilator build)
            pkgs.cmake
            pkgs.pkg-config
            pkgs.verilator
            pkgs.renode-bin
            pkgs.dtc           # device tree compiler (DTS→DTB for Linux boot)
            cosimPythonEnv
            pkgs.pkgsCross.riscv64-embedded.buildPackages.gcc  # bare-metal (kernel, OpenSBI, firmware)
            pkgs.pkgsCross.riscv32.buildPackages.gcc           # Linux userspace (BusyBox, UIO test)
            pkgs.pkgsCross.riscv32.stdenv.cc.libc.static       # static libc/libm for BusyBox
            pkgs.cpio          # rootfs initramfs packing
            pkgs.findutils     # find (used by rootfs CPIO creation)
            pkgs.gnutar        # for rootfs creation
          ];

          shellHook = commonShellHook + ''
            if [ -z "$FORMAL_SKIP_SHELL_HOOK" ]; then
              export PYTHONPATH="${cosimPythonEnv}/${python.sitePackages}:''${PYTHONPATH:-}"

              echo "======================================================="
              echo "🖥️  Co-Sim Environment (Renode + Verilator)"
              echo "   - Renode:      $(renode --version 2>&1 | head -n1 || echo unknown)"
              echo "   - Verilator:   $(verilator --version | head -n1)"
              echo "   - RV bare-metal: $(riscv64-none-elf-gcc --version 2>&1 | head -n1)"
              echo "   - RV Linux:    $(riscv32-unknown-linux-gnu-gcc --version 2>&1 | head -n1 || echo 'not available')"
              echo "   - Python:      $(python3 --version) (Pillow + NumPy)"
              echo "======================================================="
              echo ""
              echo "Targets:  make cosim            — full pipeline (build + run + png)"
              echo "          make cosim-build       — build firmware + verilator lib"
              echo "          make cosim-run         — run Renode co-simulation"
              echo "          make cosim-linux-build — build Linux boot artifacts"
              echo "          make cosim-linux       — boot Linux in Renode"
              echo ""
            fi
          '';
        };

        # ===== Exported Packages =====
        packages = {
          formalPythonEnv = formalPythonEnv;
          cocotbPythonEnv = cocotbPythonEnv;
          cosimPythonEnv = cosimPythonEnv;
          litexPythonEnv = litexPythonEnv;
          dockerImage = pkgs.dockerTools.buildLayeredImage {
            name = "formal-hdl-env";
            tag = "latest";

            # Base utilities plus the full toolchain used by the devShell
            # + Renode co-sim + image processing dependencies for product package
            contents = [
              pkgs.bashInteractive
              pkgs.coreutils
              pkgs.git
              pkgs.cacert
              # --- Co-simulation (Renode) ---
              pkgs.renode-bin
              # --- Image processing (img2raw / raw2png / vnc_server) ---
              cosimPythonEnv
              # --- Networking utilities (telnet for VNC bridge to Renode) ---
              pkgs.inetutils
              # --- /usr/bin/env symlink (required by renode launcher) ---
              (pkgs.runCommand "usr-bin-env" {} ''
                mkdir -p $out/usr/bin
                ln -s /bin/env $out/usr/bin/env
              '')
            ] ++ toolchain;

            config = {
              Cmd = [ "/bin/bash" ];
              WorkingDir = "/work";
              Env = [
                "PYTHONPATH=/work/scripts"
                "PATH=/work/scripts:/bin:/usr/bin:/usr/local/bin"
              ];
            };
          };
        };
      }
    );
}
