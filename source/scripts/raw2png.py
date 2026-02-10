#!/usr/bin/env python3
"""
raw2png.py — Convert a raw ARGB8888 framebuffer dump to PNG.

Usage:
  python3 raw2png.py input.raw output.png --width W --height H
"""

import argparse
import struct
import sys
import os

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required.  pip install Pillow", file=sys.stderr)
    sys.exit(1)


def convert(raw_path: str, out_path: str, width: int, height: int) -> None:
    import numpy as np

    expected_size = width * height * 4
    with open(raw_path, "rb") as f:
        raw = f.read()

    if len(raw) < expected_size:
        print(f"WARNING: file is {len(raw)} bytes, expected {expected_size}",
              file=sys.stderr)
        # Pad with zeros if too short
        raw = raw + b'\x00' * (expected_size - len(raw))

    # Parse ARGB8888 (little-endian u32: byte order in memory is B,G,R,A)
    data = np.frombuffer(raw[:expected_size], dtype=np.uint32).reshape(height, width)
    a = ((data >> 24) & 0xFF).astype(np.uint8)
    r = ((data >> 16) & 0xFF).astype(np.uint8)
    g = ((data >> 8) & 0xFF).astype(np.uint8)
    b = (data & 0xFF).astype(np.uint8)
    rgba = np.stack([r, g, b, a], axis=-1)

    img = Image.fromarray(rgba, "RGBA")
    img.save(out_path)
    print(f"Saved: {out_path}  ({width}×{height})")


def main():
    parser = argparse.ArgumentParser(
        description="Convert raw ARGB8888 framebuffer to PNG")
    parser.add_argument("input", help="Input raw ARGB8888 file")
    parser.add_argument("output", help="Output PNG file")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    convert(args.input, args.output, args.width, args.height)


if __name__ == "__main__":
    main()
