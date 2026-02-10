#!/bin/bash
# exec.sh — Draw Engine デモ実行
#
# Usage:
#   ./exec.sh              # 対話シェルに入る
#   ./exec.sh linux        # Linux ブートデモ (telnet localhost 4321)
#   ./exec.sh imgproc IMG  # 画像処理デモ
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_REF="formal-hdl-env:latest"

# Docker イメージのロード
load_image() {
    if ! docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
        if [ ! -f "$SCRIPT_DIR/formal-hdl-env.tar" ]; then
            echo "ERROR: formal-hdl-env.tar not found."
            exit 1
        fi
        echo "Loading Docker image..."
        docker load -q < "$SCRIPT_DIR/formal-hdl-env.tar"
    fi
}

DOCKER_RUN="docker run --rm -v $SCRIPT_DIR:/work -w /work"

case "${1:-shell}" in
    shell)
        load_image
        echo "Starting interactive shell..."
        echo "  Available commands:"
        echo "    make help       — show available targets"
        echo "    make demo-info  — show package contents"
        echo ""
        $DOCKER_RUN -it "$IMAGE_REF"
        ;;

    linux)
        load_image
        echo "╔══════════════════════════════════════════════════╗"
        echo "║  Draw Engine — Linux Boot Demo                   ║"
        echo "║                                                  ║"
        echo "║  UART console:  telnet localhost 4321           ║"
        echo "║  VNC display:   vncviewer localhost:5900        ║"
        echo "║  Web viewer:    http://localhost:5800           ║"
        echo "║  Renode monitor: telnet localhost 1234           ║"
        echo "║  Stop:           Ctrl+C                         ║"
        echo "╚══════════════════════════════════════════════════╝"
        $DOCKER_RUN -it \
            -p 4321:4321 -p 1234:1234 \
            -p 5900:5900 -p 5800:5800 \
            "$IMAGE_REF" make demo-linux
        ;;

    linux-headless)
        load_image
        echo "=== Linux Boot (UART only, no VNC) ==="
        echo "  telnet localhost 4321"
        $DOCKER_RUN -it \
            -p 4321:4321 -p 1234:1234 \
            "$IMAGE_REF" make demo-linux-headless
        ;;

    imgproc)
        INPUT="${2:-}"
        load_image
        if [ -n "$INPUT" ]; then
            if [ ! -f "$INPUT" ]; then
                echo "ERROR: $INPUT not found"
                exit 1
            fi
            echo "=== Image Processing Demo ($(basename "$INPUT")) ==="
            $DOCKER_RUN -it \
                -v "$(realpath "$INPUT"):/work/input_image" \
                "$IMAGE_REF" make demo-imgproc INPUT=/work/input_image
        else
            echo "=== Image Processing Demo (sample: rabbit 🐰) ==="
            $DOCKER_RUN -it \
                "$IMAGE_REF" make demo-imgproc INPUT=/work/sample/rabbit.png
        fi
        ;;

    *)
        echo "Usage: $0 [shell|linux|linux-headless|imgproc <image>]"
        exit 1
        ;;
esac
