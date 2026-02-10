#!/usr/bin/env python3
"""Write a test pattern to Renode framebuffer via Renode's Python API.

Creates a raw file with the test pattern, then uses Renode's Python
command to load it via SystemBus.WriteBytes.
"""

import struct
import sys
import os

FB_ADDR = 0x43E00000
WIDTH = 640
HEIGHT = 480
BPP = 4  # bytes per pixel (XRGB8888)

def create_test_pattern():
    """Create a simple color-bar test pattern."""
    data = bytearray(WIDTH * HEIGHT * BPP)
    
    # Color bars: Red, Green, Blue, White, Cyan, Magenta, Yellow, Gray
    colors = [
        0x00FF0000,  # Red
        0x0000FF00,  # Green
        0x000000FF,  # Blue
        0x00FFFFFF,  # White
        0x0000FFFF,  # Cyan
        0x00FF00FF,  # Magenta
        0x00FFFF00,  # Yellow
        0x00808080,  # Gray
    ]
    
    bar_width = WIDTH // len(colors)
    
    for y in range(HEIGHT):
        for x in range(WIDTH):
            bar_idx = min(x // bar_width, len(colors) - 1)
            color = colors[bar_idx]
            offset = (y * WIDTH + x) * BPP
            struct.pack_into('<I', data, offset, color)
    
    return bytes(data)

def main():
    pattern = create_test_pattern()
    
    # Write raw pattern to file
    raw_path = "/tmp/fb_test_pattern.raw"
    with open(raw_path, 'wb') as f:
        f.write(pattern)
    
    print(f"Test pattern written to {raw_path} ({len(pattern)} bytes)")
    print(f"Use in Renode monitor:")
    print(f'  python "from System.IO import File; data = File.ReadAllBytes(\\"{raw_path}\\"); self.Machine.SystemBus.WriteBytes(data, long(0x{FB_ADDR:08X}))"')

if __name__ == "__main__":
    main()
