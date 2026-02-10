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

# Docker イメージのロード
if ! docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    if [ ! -f "$SCRIPT_DIR/formal-hdl-env.tar" ]; then
        echo "ERROR: formal-hdl-env.tar not found."
        exit 1
    fi
    echo "Loading Docker image..."
    docker load -q < "$SCRIPT_DIR/formal-hdl-env.tar"
fi

DOCKER_RUN="docker run --rm -v $SCRIPT_DIR:/work -w /work"

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
        echo "Running demo smoke test..."
        $DOCKER_RUN "$IMAGE_REF" make check-binaries
        echo "✓ Demo binaries OK."
        ;;

    *)
        echo "Usage: $0 [all|verify|demo]"
        exit 1
        ;;
esac
