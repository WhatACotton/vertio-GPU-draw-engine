#!/bin/bash
# test.sh — パッケージ検証 (事前ビルド済みバイナリの動作確認)
#
# Usage:
#   ./test.sh           # 全テスト実行
#   ./test.sh verify    # 事前テスト結果の検証のみ
#   ./test.sh demo      # デモ動作確認
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_REF="formal-hdl-env:latest"

# パッケージディレクトリを決定
# 開発環境: dist/draw_engine/ があればそちらを使う
# 配布環境: SCRIPT_DIR 自体がパッケージ
if [ -d "$SCRIPT_DIR/dist/draw_engine" ]; then
    PKG_DIR="$SCRIPT_DIR/dist/draw_engine"
else
    PKG_DIR="$SCRIPT_DIR"
fi

# Docker イメージのロード
if ! docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    if [ ! -f "$PKG_DIR/formal-hdl-env.tar" ]; then
        echo "ERROR: formal-hdl-env.tar not found."
        echo "  Run 'make package' first."
        exit 1
    fi
    echo "Loading Docker image..."
    docker load -q < "$PKG_DIR/formal-hdl-env.tar"
fi

DOCKER_RUN="docker run --rm -v $PKG_DIR:/work -w /work"
DOCKER_RUN_PORTS="docker run --rm -v $PKG_DIR:/work -w /work -p 4321:4321 -p 1234:1234 -p 5900:5900 -p 5800:5800"

echo ""
echo "=========================================="
echo "  Draw Engine — Package Verification"
echo "=========================================="
echo ""

case "${1:-all}" in
    all)
        echo "[1/3] Checking package integrity..."
        $DOCKER_RUN "$IMAGE_REF" make check-integrity

        echo ""
        echo "[2/3] Verifying pre-built binaries..."
        $DOCKER_RUN "$IMAGE_REF" make check-binaries

        echo ""
        echo "[3/3] Checking test results..."
        $DOCKER_RUN "$IMAGE_REF" make check-results

        echo ""
        echo "✓ All verification checks passed."
        ;;

    verify)
        echo "Checking pre-run test results..."
        $DOCKER_RUN "$IMAGE_REF" make check-results
        echo "✓ Test results verified."
        ;;

    demo)
        echo "Running demo verification..."
        $DOCKER_RUN "$IMAGE_REF" make check-binaries

        echo ""
        echo "Running image processing demo..."
        $DOCKER_RUN "$IMAGE_REF" make demo-imgproc
        echo "✓ Demo verification passed."
        ;;

    linux)
        echo "Running Linux boot demo (Ctrl+C to stop)..."
        echo "  UART:   telnet localhost 4321"
        echo "  VNC:    vncviewer localhost:5900"
        echo "  Web:    http://localhost:5800"
        $DOCKER_RUN_PORTS -it "$IMAGE_REF" make demo-linux
        ;;

    *)
        echo "Usage: $0 [all|verify|demo|linux]"
        exit 1
        ;;
esac
