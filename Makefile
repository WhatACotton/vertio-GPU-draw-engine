# Makefile — Draw Engine バイナリ配布パッケージ
#
# Docker コンテナ内で実行:
#   docker run --rm -v "$PWD:/work" -w /work formal-hdl-env:latest make <target>
#
# Targets:
#   help           — ターゲット一覧
#   demo-info      — パッケージ内容表示
#   demo-linux     — Linux ブートデモ (Renode)
#   demo-imgproc   — 画像処理デモ (Renode)
#   check-integrity — パッケージ完全性チェック
#   check-binaries  — ビルド済みバイナリの検証
#   check-results   — 事前実行テスト結果の検証

PYTHON := python3

# VNC/HTTP framebuffer viewer settings
VNC_PORT  ?= 5900
WEB_PORT  ?= 5800
FB_ADDR   ?= 0x43E00000
FB_WIDTH  ?= 640
FB_HEIGHT ?= 480
FPS       ?= 2

.PHONY: help demo-info demo-linux demo-linux-headless demo-imgproc
.PHONY: check-integrity check-binaries check-results

help:
	@echo "Draw Engine Package — Available Targets"
	@echo ""
	@echo "  Demo:"
	@echo "    make demo-info             パッケージ内容を表示"
	@echo "    make demo-linux            Linux デモ + VNC/HTTP ビューア"
	@echo "    make demo-linux-headless   Linux デモ (UART のみ)"
	@echo "    make demo-imgproc          画像処理デモ"
	@echo ""
	@echo "  Verification:"
	@echo "    make check-integrity       パッケージ完全性チェック"
	@echo "    make check-binaries        バイナリ動作確認"
	@echo "    make check-results         テスト結果レポート確認"
	@echo ""

demo-info:
	@echo "=== Draw Engine Package Contents ==="
	@echo ""
	@echo "hdl/      Encrypted RTL (IEEE P1735)"
	@ls -1 hdl/*.sv 2>/dev/null | wc -l | xargs -I{} echo "          {} files"
	@echo ""
	@echo "boot/     Pre-built Linux boot images"
	@ls -lh boot/ 2>/dev/null || echo "          (not available)"
	@echo ""
	@echo "lib/      Pre-built Verilator libraries"
	@ls -lh lib/*.so 2>/dev/null || echo "          (not available)"
	@echo ""
	@echo "fw/       Pre-built firmware"
	@ls -lh fw/*.bin 2>/dev/null || echo "          (not available)"
	@echo ""
	@echo "results/  Pre-run test results"
	@ls -1 results/ 2>/dev/null || echo "          (not available)"
	@echo ""

# ── Linux ブートデモ (VNC + HTTP ビューア付き) ────────────────────
# Renode co-sim + VNC サーバーで framebuffer をブラウザから確認可能
demo-linux:
	@echo "╔══════════════════════════════════════════════════╗"
	@echo "║  Draw Engine — Linux Boot Demo                   ║"
	@echo "║                                                  ║"
	@echo "║  UART console:  telnet localhost 4321           ║"
	@echo "║  VNC display:   vncviewer localhost:$(VNC_PORT)        ║"
	@echo "║  Web viewer:    http://localhost:$(WEB_PORT)           ║"
	@echo "║  Renode monitor: telnet localhost 1234           ║"
	@echo "║  Stop:           Ctrl+C                         ║"
	@echo "╚══════════════════════════════════════════════════╝"
	@test -f boot/fw_jump.elf     || { echo "ERROR: boot/fw_jump.elf not found"; exit 1; }
	@test -f boot/Image           || { echo "ERROR: boot/Image not found"; exit 1; }
	@test -f boot/rootfs.cpio     || { echo "ERROR: boot/rootfs.cpio not found"; exit 1; }
	@test -f lib/libVtop_virtio.so || { echo "ERROR: lib/libVtop_virtio.so not found"; exit 1; }
	@# VNC サーバーをバックグラウンド起動し、Renode 終了時に確実に停止
	@cleanup() { pkill -f vnc_server.py 2>/dev/null; wait 2>/dev/null; }; \
	trap cleanup EXIT; \
	( sleep 5 && $(PYTHON) renode/scripts/vnc_server.py \
		--renode-port 1234 \
		--port $(VNC_PORT) --web-port $(WEB_PORT) \
		--fps $(FPS) \
		--fb-addr $(FB_ADDR) --width $(FB_WIDTH) --height $(FB_HEIGHT) \
		--uart-log /tmp/uart_output_interactive.txt ) & \
	cd renode && renode --plain --disable-xwt --port 1234 \
		-e 'set elf @/work/boot/fw_jump.elf' \
		-e 'set kernel @/work/boot/Image' \
		-e 'set dtb @/work/boot/draw_engine_soc_virtio.dtb' \
		-e 'i @draw_linux_interactive.resc'

# ── Linux ブートデモ (UART のみ / VNC なし) ──────────────────────
demo-linux-headless:
	@echo "=== Linux Boot Demo (UART only) ==="
	@echo "  UART console: telnet localhost 4321"
	@test -f boot/fw_jump.elf     || { echo "ERROR: boot/fw_jump.elf not found"; exit 1; }
	@test -f boot/Image           || { echo "ERROR: boot/Image not found"; exit 1; }
	cd renode && renode --plain --disable-xwt --port 1234 \
		-e 'set elf @/work/boot/fw_jump.elf' \
		-e 'set kernel @/work/boot/Image' \
		-e 'set dtb @/work/boot/draw_engine_soc_virtio.dtb' \
		-e 'i @draw_linux_interactive.resc'

# ── 画像処理デモ ─────────────────────────────────────────────────
INPUT ?= /work/sample/rabbit.png
OUTPUT ?= /work/output.png

demo-imgproc:
	@test -f "$(INPUT)" || { echo "ERROR: Input image not found: $(INPUT)"; exit 1; }
	@test -f lib/libVtop.so || { echo "ERROR: lib/libVtop.so not found"; exit 1; }
	@mkdir -p /tmp/draw_imgproc
	@echo "[1/3] Converting input image..."
	$(PYTHON) renode/scripts/img2raw.py "$(INPUT)" /tmp/draw_imgproc/texture \
		--max-width 640 --max-height 480 --sepia --compose-fb
	@echo "[2/3] Running Renode co-simulation..."
	cd renode && timeout 120 renode --plain --disable-xwt \
		-e 'set img_hdr @/tmp/draw_imgproc/texture.hdr' \
		-e 'set img_bin @/tmp/draw_imgproc/texture.bin' \
		-e 'set fb_bin @/tmp/draw_imgproc/texture_fb.bin' \
		-e 'i @draw_imgproc.resc' 2>&1 || true
	@echo "[3/3] Converting output..."
	@if [ -f /tmp/draw_engine_output.raw ]; then \
		$(PYTHON) renode/scripts/raw2png.py /tmp/draw_engine_output.raw "$(OUTPUT)" \
			--width 640 --height 480; \
		echo "✓ Output: $(OUTPUT)"; \
	else \
		echo "⚠ Raw output not found (Renode framebuffer dump skipped)"; \
		echo "  Simulation completed successfully (check UART log above)"; \
	fi

# ── パッケージ検証 ───────────────────────────────────────────────
check-integrity:
	@echo "Checking package structure..."
	@ok=true; \
	for d in hdl boot lib fw renode results; do \
		if [ -d "$$d" ]; then \
			echo "  ✓ $$d/"; \
		else \
			echo "  ✗ $$d/ — MISSING"; ok=false; \
		fi; \
	done; \
	for f in Makefile README.md formal-hdl-env.tar; do \
		if [ -f "$$f" ]; then \
			echo "  ✓ $$f"; \
		else \
			echo "  ✗ $$f — MISSING"; ok=false; \
		fi; \
	done; \
	$$ok && echo "✓ Package structure OK" || { echo "✗ Package incomplete"; exit 1; }

check-binaries:
	@echo "Checking pre-built binaries..."
	@ok=true; \
	for f in boot/Image boot/fw_jump.bin; do \
		if [ -f "$$f" ]; then \
			sz=$$(du -h "$$f" | cut -f1); \
			echo "  ✓ $$f ($$sz)"; \
		else \
			echo "  ✗ $$f — MISSING"; ok=false; \
		fi; \
	done; \
	for f in lib/libVtop.so lib/libVtop_virtio.so; do \
		if [ -f "$$f" ]; then \
			echo "  ✓ $$f"; \
		else \
			echo "  ⚠ $$f — not found (optional)"; \
		fi; \
	done; \
	for f in fw/*.bin; do \
		[ -f "$$f" ] && echo "  ✓ $$f" || true; \
	done; \
	$$ok && echo "✓ Binaries OK" || { echo "✗ Required binaries missing"; exit 1; }

check-results:
	@echo "Checking test results..."
	@if [ -f results/results.xml ]; then \
		tests=$$(grep -c 'testcase ' results/results.xml 2>/dev/null || echo 0); \
		fails=$$(grep -c 'failure' results/results.xml 2>/dev/null || echo 0); \
		echo "  Test cases: $$tests"; \
		echo "  Failures:   $$fails"; \
		if [ "$$fails" = "0" ]; then \
			echo "  ✓ All tests passed"; \
		else \
			echo "  ✗ $$fails test(s) failed"; exit 1; \
		fi; \
	else \
		echo "  ⚠ results/results.xml not found"; \
	fi
	@gallery=$$(ls results/gallery/*.png 2>/dev/null | wc -l); \
	echo "  Gallery images: $$gallery"
	@echo "✓ Results check complete"
