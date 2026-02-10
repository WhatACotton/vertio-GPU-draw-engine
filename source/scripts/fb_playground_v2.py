#!/usr/bin/env python3
"""
Framebuffer Playground v2 - /dev/tty0 にリダイレクトして描画
console=tty0 console=ttyS0 の場合、シェル出力は ttyS0 のみ。
tty0 (フレームバッファ) に表示するには > /dev/tty0 が必要。
"""

import telnetlib
import time
import sys
import argparse

UART_PORT = 4321
WIDTH = 640
HEIGHT = 480

class UartSession:
    """Persistent UART telnet session"""
    def __init__(self, port=UART_PORT):
        self.tn = telnetlib.Telnet('localhost', port, timeout=10)
        time.sleep(0.2)
        # Flush any pending output
        self.tn.read_very_eager()
    
    def send(self, cmd, delay=0.5):
        """Send command and return output"""
        self.tn.write((cmd + '\n').encode())
        time.sleep(delay)
        return self.tn.read_very_eager().decode('utf-8', errors='replace')
    
    def close(self):
        self.tn.close()


def tty0_cmd(session, cmd, delay=0.3):
    """Execute a command with output redirected to /dev/tty0 (framebuffer)"""
    session.send(f'{cmd} > /dev/tty0 2>&1', delay=delay)


def tty0_echo(session, text, delay=0.2):
    """Echo text to /dev/tty0 (framebuffer)"""
    session.send(f'echo "{text}" > /dev/tty0', delay=delay)


def tty0_echo_e(session, text, delay=0.2):
    """Echo with escape sequences to /dev/tty0"""
    session.send(f'printf "{text}\\n" > /dev/tty0', delay=delay)


def tty0_clear(session):
    """Clear the framebuffer console"""
    # Send clear escape sequence to tty0
    session.send(r'printf "\033c" > /dev/tty0', delay=0.5)


def demo_hello(session):
    """最もシンプル: Hello World を表示"""
    print("🎨 Demo: Hello World")
    tty0_clear(session)
    time.sleep(0.3)
    
    tty0_echo(session, "")
    tty0_echo(session, "  Hello, World!")
    tty0_echo(session, "  This is Draw Engine SoC!")
    tty0_echo(session, "  Running on RISC-V Linux")
    tty0_echo(session, "  with VirtIO-GPU + HW BITBLT")
    tty0_echo(session, "")
    
    print("   → VNC (port 5900) で確認！")


def demo_custom(session, message):
    """任意のメッセージを表示"""
    print(f"🎨 メッセージ: {message}")
    tty0_clear(session)
    time.sleep(0.3)
    
    bar = '=' * (len(message) + 6)
    tty0_echo(session, "")
    tty0_echo(session, f"  +{bar}+")
    tty0_echo(session, f"  |   {message}   |")
    tty0_echo(session, f"  +{bar}+")
    tty0_echo(session, "")
    
    print("   → VNC (port 5900) で確認！")


def demo_big_text(session):
    """大きなテキストでメッセージ表示"""
    print("🎨 Demo: ビッグテキスト")
    tty0_clear(session)
    time.sleep(0.3)
    
    # "COJT" in block letters
    lines = [
        "",
        " ####   ####     ## ######",
        "#    # #    #    ##   ##  ",
        "#      #    #    ##   ##  ",
        "#      #    #    ##   ##  ",
        "#      #    # #  ##   ##  ",
        "#    # #    # #  ##   ##  ",
        " ####   ####   ##    ##  ",
        "",
        "   Draw Engine Project",
        "   RISC-V + VirtIO-GPU",
        "   Hardware BITBLT Engine",
        "",
    ]
    
    for line in lines:
        tty0_echo(session, line, delay=0.15)
    
    print("   → VNC (port 5900) で確認！")


def demo_sysinfo(session):
    """システム情報を表示"""
    print("🎨 Demo: システム情報")
    tty0_clear(session)
    time.sleep(0.3)
    
    tty0_echo(session, "")
    tty0_echo(session, "+----------------------------------+")
    tty0_echo(session, "|   Draw Engine SoC - System Info  |")
    tty0_echo(session, "+----------------------------------+")
    
    # Each info line: run command, capture, display
    info_cmds = [
        ("Kernel ", "uname -r"),
        ("Arch   ", "uname -m"),
        ("CPU    ", "head -3 /proc/cpuinfo | tail -1 | cut -d: -f2"),
        ("Memory ", "head -1 /proc/meminfo | awk '{print $2, $3}'"),
        ("Uptime ", "cat /proc/uptime"),
        ("FB     ", "cat /sys/class/graphics/fb0/virtual_size 2>/dev/null || echo N/A"),
        ("DRM    ", "ls /dev/dri/ 2>/dev/null | tr '\\n' ' '"),
    ]
    
    for label, cmd in info_cmds:
        full_cmd = f'printf "|  {label}: " > /dev/tty0 && {cmd} | tr -d "\\n" > /dev/tty0 && echo "" > /dev/tty0'
        session.send(full_cmd, delay=0.4)
    
    tty0_echo(session, "+----------------------------------+")
    tty0_echo(session, "|  VirtIO-GPU + HW BITBLT Active   |")
    tty0_echo(session, "+----------------------------------+")
    
    print("   → VNC (port 5900) で確認！")


def demo_ascii_art(session):
    """アスキーアートロケット"""
    print("🎨 Demo: アスキーアート")
    tty0_clear(session)
    time.sleep(0.3)
    
    art = [
        "",
        "            /\\",
        "           /  \\",
        "          / () \\",
        "         /      \\",
        "        /________\\",
        "        |  COJT  |",
        "        |  DRAW  |",
        "        | ENGINE |",
        "        |________|",
        "       /|   ||   |\\",
        "      / |   ||   | \\",
        "     /__|___||___|__\\",
        "          ||||",
        "          ||||",
        "         //  \\\\",
        "        //    \\\\",
        "",
        "     Draw Engine Launch!",
        "",
    ]
    
    for line in art:
        # Escape backslashes for echo
        safe = line.replace('\\', '\\\\')
        tty0_echo(session, safe, delay=0.1)
    
    print("   → VNC (port 5900) で確認！")


def demo_color_bars(session):
    """ANSIカラーバー (tty0 が対応していれば)"""
    print("🎨 Demo: カラーバー")
    tty0_clear(session)
    time.sleep(0.3)
    
    tty0_echo(session, "")
    tty0_echo(session, "  === Linux Console Color Test ===")
    tty0_echo(session, "")
    
    # Use printf with ANSI codes to tty0
    colors = [
        ("30", "Black  "), ("31", "Red    "), ("32", "Green  "), ("33", "Yellow "),
        ("34", "Blue   "), ("35", "Magenta"), ("36", "Cyan   "), ("37", "White  "),
    ]
    
    for code, name in colors:
        session.send(
            f'printf "\\033[1;{code}m  {name} \\033[4{code[1]}m      \\033[0m\\n" > /dev/tty0',
            delay=0.15
        )
    
    tty0_echo(session, "")
    
    # Bright colors
    for code, name in colors:
        bright = str(int(code) + 60)
        session.send(
            f'printf "\\033[{bright}m  {name} \\033[10{code[1]}m      \\033[0m\\n" > /dev/tty0',
            delay=0.15
        )
    
    print("   → VNC (port 5900) で確認！")


def demo_fb_direct(session):
    """Linux 側から /dev/fb0 に直接描画"""
    print("🎨 Demo: /dev/fb0 直接描画")
    
    tty0_echo(session, "")
    tty0_echo(session, "  Drawing to /dev/fb0...")
    
    # Generate color stripes directly to /dev/fb0 using shell
    # FB format: BGRX, 640 pixels/line = 2560 bytes/line
    
    stripe_cmds = [
        # Red stripe (10 lines at line 350)
        r"""{ for i in $(seq 1 6400); do printf '\x00\x00\xff\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=350 count=10 2>/dev/null""",
        # Green stripe
        r"""{ for i in $(seq 1 6400); do printf '\x00\xff\x00\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=365 count=10 2>/dev/null""",
        # Blue stripe
        r"""{ for i in $(seq 1 6400); do printf '\xff\x00\x00\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=380 count=10 2>/dev/null""",
        # Yellow stripe
        r"""{ for i in $(seq 1 6400); do printf '\x00\xff\xff\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=395 count=10 2>/dev/null""",
        # Cyan stripe
        r"""{ for i in $(seq 1 6400); do printf '\xff\xff\x00\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=410 count=10 2>/dev/null""",
        # Magenta stripe
        r"""{ for i in $(seq 1 6400); do printf '\xff\x00\xff\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=425 count=10 2>/dev/null""",
        # White stripe
        r"""{ for i in $(seq 1 6400); do printf '\xff\xff\xff\x00'; done; } | dd of=/dev/fb0 bs=2560 seek=440 count=10 2>/dev/null""",
    ]
    
    for i, cmd in enumerate(stripe_cmds):
        session.send(cmd, delay=2.0)
        print(f"   ストライプ {i+1}/7 完了")
    
    tty0_echo(session, "  Done! Check VNC!")
    print("   → VNC (port 5900) で確認！")
    print("   ※ ただし次の RESOURCE_FLUSH で上書きされる可能性あり")


def demo_boxes(session):
    """ボックスアート"""
    print("🎨 Demo: ボックスアート")
    tty0_clear(session)
    time.sleep(0.3)
    
    lines = [
        "",
        "  +----------+  +----------+  +----------+",
        "  |  RISC-V  |  |  VirtIO  |  |  BITBLT  |",
        "  |  rv32ima |  |   GPU    |  |  Engine  |",
        "  +----+-----+  +-----+----+  +-----+----+",
        "       |              |             |",
        "       +--------------+-------------+",
        "                      |",
        "                +-----+-----+",
        "                |   Linux   |",
        "                |  fbcon    |",
        "                +-----------+",
        "                      |",
        "                +-----+-----+",
        "                |    VNC    |",
        "                |  Display  |",
        "                +-----------+",
        "",
        "     Draw Engine SoC Architecture",
        "",
    ]
    
    for line in lines:
        tty0_echo(session, line, delay=0.12)
    
    print("   → VNC (port 5900) で確認！")


def demo_matrix(session):
    """マトリックス風エフェクト"""
    print("🎨 Demo: Matrix 風")
    tty0_clear(session)
    time.sleep(0.3)
    
    # Green text on black
    session.send(r'printf "\033[32m" > /dev/tty0', delay=0.1)
    
    import random
    chars = "01ABCDEFabcdef@#$%"
    for row in range(25):
        line = ''.join(random.choice(chars) for _ in range(78))
        tty0_echo(session, f" {line}", delay=0.08)
    
    # Reset color
    session.send(r'printf "\033[0m" > /dev/tty0', delay=0.1)
    time.sleep(0.3)
    
    # Overlay message
    session.send(r'printf "\033[12;20H\033[1;32m>>> DRAW ENGINE ONLINE <<<\033[0m\n" > /dev/tty0', delay=0.3)
    
    print("   → VNC (port 5900) で確認！")


def main():
    parser = argparse.ArgumentParser(description='Framebuffer Playground v2 🎮')
    parser.add_argument('demo', nargs='?', default='menu',
                       help='Demo name')
    parser.add_argument('--message', '-m', type=str, default='Hello Draw Engine!',
                       help='Custom message for "custom" demo')
    args = parser.parse_args()
    
    demos = {
        'hello':    ('Hello World',              demo_hello),
        'custom':   ('カスタムメッセージ',        None),
        'big':      ('ビッグテキスト COJT',      demo_big_text),
        'sysinfo':  ('システム情報',              demo_sysinfo),
        'ascii':    ('ロケット ASCII アート',     demo_ascii_art),
        'colors':   ('ANSI カラーバー',           demo_color_bars),
        'boxes':    ('アーキテクチャ図',          demo_boxes),
        'matrix':   ('Matrix 風エフェクト',       demo_matrix),
        'fb':       ('/dev/fb0 直接描画',         demo_fb_direct),
    }
    
    if args.demo == 'menu':
        print("╔════════════════════════════════════════╗")
        print("║   🎮 Framebuffer Playground v2 🎮     ║")
        print("╠════════════════════════════════════════╣")
        for key, (desc, _) in demos.items():
            print(f"║  {key:10s} - {desc:24s}  ║")
        print("║  all        - 全デモ実行 (fb除く)     ║")
        print("╚════════════════════════════════════════╝")
        print("")
        print("使い方: python3 fb_playground_v2.py <demo名>")
        print("例:     python3 fb_playground_v2.py hello")
        print("        python3 fb_playground_v2.py custom -m 'テスト!'")
        print("        python3 fb_playground_v2.py all")
        return
    
    # Connect
    print("📡 UART (localhost:4321) に接続中...")
    session = UartSession()
    print("   接続OK!")
    
    try:
        if args.demo == 'all':
            for key, (desc, func) in demos.items():
                if key in ('fb', 'custom'):
                    continue
                print(f"\n--- {desc} ---")
                func(session)
                time.sleep(2)
        elif args.demo == 'custom':
            demo_custom(session, args.message)
        elif args.demo in demos:
            _, func = demos[args.demo]
            if func:
                func(session)
            else:
                demo_custom(session, args.message)
        else:
            print(f"Unknown: {args.demo}")
            print(f"Available: {', '.join(demos.keys())}")
    finally:
        session.close()


if __name__ == '__main__':
    main()
