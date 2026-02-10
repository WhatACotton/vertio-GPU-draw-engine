#!/usr/bin/env python3
"""
img2raw.py — Convert an image file to raw ARGB8888 binary for Renode.

Produces:
  <output>.bin   — raw pixel data (ARGB8888, little-endian)
  <output>.hdr   — 8-byte header: uint32 width, uint32 height

Usage:
  python3 img2raw.py input.png output [--max-width W] [--max-height H]

The header file is loaded into RAM so the firmware can read the image
dimensions at runtime without hardcoding.
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


FB_WIDTH  = 640
FB_HEIGHT = 480


def _to_argb(pixels: 'np.ndarray') -> 'np.ndarray':
    """Convert (h,w,4) RGBA uint8 array to (h,w) ARGB8888 uint32 array."""
    import numpy as np
    r = pixels[:, :, 0].astype(np.uint32)
    g = pixels[:, :, 1].astype(np.uint32)
    b = pixels[:, :, 2].astype(np.uint32)
    a = pixels[:, :, 3].astype(np.uint32)
    return (a << 24) | (r << 16) | (g << 8) | b


def _apply_sepia(pixels: 'np.ndarray') -> None:
    """In-place sepia tone filter on (h,w,4) RGBA uint8 array."""
    import numpy as np
    r = pixels[:, :, 0].astype(np.float32)
    g = pixels[:, :, 1].astype(np.float32)
    b = pixels[:, :, 2].astype(np.float32)
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    pixels[:, :, 0] = np.clip(gray + 40, 0, 255).astype(np.uint8)
    pixels[:, :, 1] = np.clip(gray + 16, 0, 255).astype(np.uint8)
    pixels[:, :, 2] = np.clip(gray, 0, 255).astype(np.uint8)


def convert(input_path: str, output_base: str,
            max_w: int = 640, max_h: int = 480,
            sepia: bool = False,
            compose_fb: bool = False) -> None:
    import numpy as np

    img = Image.open(input_path).convert("RGBA")
    w, h = img.size

    # Resize if necessary (keep aspect ratio)
    if w > max_w or h > max_h:
        ratio = min(max_w / w, max_h / h)
        w = int(w * ratio)
        h = int(h * ratio)
        img = img.resize((w, h), Image.LANCZOS)
        print(f"Resized to {w}×{h}")

    pixels = np.array(img, dtype=np.uint8)  # shape (h, w, 4) — R, G, B, A

    if sepia:
        print("Applying sepia tone...")
        _apply_sepia(pixels)

    # Write header: width (u32 LE) + height (u32 LE)
    hdr_path = output_base + ".hdr"
    with open(hdr_path, "wb") as f:
        f.write(struct.pack("<II", w, h))
    print(f"Header : {hdr_path}  ({w}×{h})")

    # Write raw ARGB8888 pixel data
    argb = _to_argb(pixels)
    bin_path = output_base + ".bin"
    argb.tofile(bin_path)
    size = w * h * 4
    print(f"Pixels : {bin_path}  ({size} bytes, {size/1024:.1f} KiB)")

    # Optionally compose full 640×480 framebuffer for direct loading
    if compose_fb:
        _compose_framebuffer(pixels, w, h, output_base)


def _compose_framebuffer(pixels: 'np.ndarray', w: int, h: int,
                         output_base: str) -> None:
    """Compose a full 640×480 FB: dark background + centred image + border."""
    import numpy as np
    print("Composing full framebuffer...")

    # Start with dark background
    bg = np.array([0x1A, 0x1A, 0x2E, 0xFF], dtype=np.uint8)  # R,G,B,A
    fb = np.tile(bg, (FB_HEIGHT, FB_WIDTH, 1))  # (480, 640, 4)

    # Centre the image
    dx = (FB_WIDTH  - w) // 2 if w < FB_WIDTH  else 0
    dy = (FB_HEIGHT - h) // 2 if h < FB_HEIGHT else 0
    bw = min(w, FB_WIDTH)
    bh = min(h, FB_HEIGHT)
    fb[dy:dy+bh, dx:dx+bw] = pixels[:bh, :bw]

    # Vignette: darken corners (24×24 regions, multiply by 0.38)
    cs = 24
    corners = [
        (dy, dx),
        (dy, dx + bw - cs),
        (dy + bh - cs, dx),
        (dy + bh - cs, dx + bw - cs),
    ]
    for cy, cx in corners:
        region = fb[cy:cy+cs, cx:cx+cs, :3].astype(np.float32)
        fb[cy:cy+cs, cx:cx+cs, :3] = (region * 0.38).clip(0, 255).astype(np.uint8)

    # Border: 3px warm gold (0xFFC8A87E → R=0xC8, G=0xA8, B=0x7E)
    bc = np.array([0xC8, 0xA8, 0x7E, 0xFF], dtype=np.uint8)
    bw_ = 3
    fb[:bw_, :] = bc
    fb[FB_HEIGHT-bw_:, :] = bc
    fb[bw_:FB_HEIGHT-bw_, :bw_] = bc
    fb[bw_:FB_HEIGHT-bw_, FB_WIDTH-bw_:] = bc

    # Inner accent: 1px dark bronze (0xFF806030)
    ic = np.array([0x80, 0x60, 0x30, 0xFF], dtype=np.uint8)
    fb[bw_, bw_:FB_WIDTH-bw_] = ic
    fb[FB_HEIGHT-bw_-1, bw_:FB_WIDTH-bw_] = ic
    fb[bw_+1:FB_HEIGHT-bw_-1, bw_] = ic
    fb[bw_+1:FB_HEIGHT-bw_-1, FB_WIDTH-bw_-1] = ic

    argb = _to_argb(fb)
    fb_path = output_base + "_fb.bin"
    argb.tofile(fb_path)
    size = FB_WIDTH * FB_HEIGHT * 4
    print(f"FB     : {fb_path}  ({size} bytes, {size/1024:.1f} KiB)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert image to raw ARGB8888 binary for Renode")
    parser.add_argument("input", help="Input image file (PNG/JPG/BMP/…)")
    parser.add_argument("output", help="Output base name (without extension)")
    parser.add_argument("--max-width", type=int, default=640,
                        help="Max width (default: 640)")
    parser.add_argument("--max-height", type=int, default=480,
                        help="Max height (default: 480)")
    parser.add_argument("--sepia", action="store_true",
                        help="Apply sepia tone filter")
    parser.add_argument("--compose-fb", action="store_true",
                        help="Also output a pre-composited 640×480 framebuffer")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    convert(args.input, args.output, args.max_width, args.max_height,
            sepia=args.sepia, compose_fb=args.compose_fb)
    print("Done.")


if __name__ == "__main__":
    main()
