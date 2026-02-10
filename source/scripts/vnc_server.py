#!/usr/bin/env python3
"""
VNC + HTTP framebuffer server for Renode.

Connects to Renode's telnet monitor, periodically reads the framebuffer
memory region, and serves it via:
  1. RFB/VNC protocol on port 5900 (native VNC clients)
  2. HTTP on port 5800 (browser-based viewer — works everywhere)

Usage:
    python3 vnc_server.py [--port 5900] [--web-port 5800]
                          [--renode-port 1234] [--fps 2]

Connect from Mac:
    open http://<host>:5800             # Browser (recommended)
    open vnc://<host>:5900              # Screen Sharing.app
"""

import argparse
import hashlib
import http.server
import logging
import os
import re
import signal
import socket
import socketserver
import struct
import sys
import telnetlib
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vnc-bridge")

# ─── RFB Protocol Constants ───────────────────────────────────────────
SEC_NONE = 1
MSG_SET_PIXEL_FORMAT = 0
MSG_SET_ENCODINGS = 2
MSG_FB_UPDATE_REQUEST = 3
MSG_KEY_EVENT = 4
MSG_POINTER_EVENT = 5
MSG_CLIENT_CUT_TEXT = 6
MSG_FB_UPDATE = 0
ENC_RAW = 0


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket, raising on short read."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf.extend(chunk)
    return bytes(buf)


# ─── Renode Framebuffer Reader ────────────────────────────────────────

class RenodeFramebufferReader:
    """Reads framebuffer data from Renode via telnet monitor."""

    def __init__(self, renode_host: str, renode_port: int,
                 fb_addr: int, width: int, height: int,
                 uart_log_path: str = "/tmp/uart_output_interactive.txt"):
        self.host = renode_host
        self.port = renode_port
        self.fb_addr = fb_addr
        self.width = width
        self.height = height
        self.fb_size = width * height * 4  # ARGB8888
        self.uart_log_path = uart_log_path
        self._tn = None
        self._lock = threading.Lock()
        self._framebuffer = bytearray(self.fb_size)
        self._fb_hash = b"\x00"
        self._connected = False
        self._read_count = 0

    def connect(self) -> bool:
        """Connect to Renode telnet monitor."""
        for attempt in range(30):
            try:
                self._tn = telnetlib.Telnet(self.host, self.port, timeout=5)
                time.sleep(3)
                self._tn.read_very_eager()
                self._tn.write(b"\n")
                self._tn.read_until(b")", timeout=5)
                self._connected = True
                log.info("Connected to Renode monitor at %s:%d",
                         self.host, self.port)
                return True
            except (ConnectionRefusedError, OSError) as e:
                if attempt < 29:
                    time.sleep(2)
                else:
                    log.error("Cannot connect to Renode at %s:%d",
                              self.host, self.port)
                    return False
        return False

    def _send_command(self, cmd: str) -> str:
        """Send command to Renode monitor."""
        if not self._connected or self._tn is None:
            return ""
        try:
            self._tn.read_very_eager()
            self._tn.write(cmd.encode("ascii") + b"\n")
            resp = self._tn.read_until(b")", timeout=15)
            return resp.decode("ascii", errors="replace")
        except (EOFError, OSError) as e:
            log.warning("Renode connection lost: %s", e)
            self._connected = False
            return ""

    def read_framebuffer(self) -> bool:
        """Read framebuffer from Renode. Returns True if changed."""
        with self._lock:
            if not self._connected:
                return False

            # --- Read debug registers every 50 reads ---
            if self._read_count > 0 and self._read_count % 5 == 0:
                try:
                    results = []
                    for addr, name in [
                        (0x82001000, "state"), (0x82001004, "last_cmd"),
                        (0x82001010, "cmd_cnt"), (0x8200100C, "backing1"),
                        (0x82001014, "fb_addr"), (0x82001018, "wxh"),
                    ]:
                        resp = self._send_command(
                            f"sysbus ReadDoubleWord 0x{addr:08X}"
                        )
                        # Renode response: "<echo>\r\n0x00000000\r\n(monitor)"
                        # Find all hex values; the last one is the result
                        # (the first is the address in the echoed command)
                        matches = re.findall(r'(0x[0-9A-Fa-f]+)', resp)
                        val = matches[-1] if matches else "???"
                        results.append(f"{name}={val}")
                    log.info("DBG: %s", " | ".join(results))
                except Exception as e:
                    log.warning("Debug read error: %s", e)

            tmp_path = "/tmp/renode_vnc_fb.raw"
            cmd = (
                'python "from System.IO import File; '
                f'data = self.Machine.SystemBus.ReadBytes('
                f'long({self.fb_addr}), int({self.fb_size})); '
                f'File.WriteAllBytes(\\"{tmp_path}\\", data)"'
            )
            resp = self._send_command(cmd)
            if "error executing" in resp.lower():
                self._read_count += 1
                if self._read_count <= 3:
                    log.warning("FB read error: %s", resp.strip()[:100])
                return False

            try:
                time.sleep(0.05)
                with open(tmp_path, "rb") as f:
                    data = f.read()
                if len(data) != self.fb_size:
                    return False

                new_hash = hashlib.md5(data).digest()
                if new_hash == self._fb_hash:
                    return False

                self._fb_hash = new_hash
                self._framebuffer = bytearray(data)
                self._read_count += 1
                if self._read_count <= 3 or self._read_count % 5 == 0:
                    log.info("Framebuffer updated (#%d)", self._read_count)
                return True

            except FileNotFoundError:
                return False
            except Exception as e:
                log.warning("FB read error: %s", e)
                return False

    @property
    def framebuffer(self) -> bytes:
        """Current framebuffer (BGRA in memory, little-endian ARGB)."""
        with self._lock:
            return bytes(self._framebuffer)

    def _make_bmp(self, pixel_data: bytearray) -> bytes:
        """Convert BGRX pixel data to 24-bit BMP. Shared helper."""
        w, h = self.width, self.height
        # 640 * 3 = 1920, which is divisible by 4, so no padding needed
        row_size = w * 3
        pixel_size = row_size * h
        file_size = 54 + pixel_size

        bmp = bytearray(file_size)
        bmp[0:2] = b"BM"
        struct.pack_into("<I", bmp, 2, file_size)
        struct.pack_into("<I", bmp, 10, 54)
        struct.pack_into("<I", bmp, 14, 40)
        struct.pack_into("<i", bmp, 18, w)
        struct.pack_into("<i", bmp, 22, -h)  # top-down
        struct.pack_into("<H", bmp, 26, 1)
        struct.pack_into("<H", bmp, 28, 24)
        struct.pack_into("<I", bmp, 34, pixel_size)

        # Fast conversion: skip every 4th byte (alpha) from BGRX
        src = pixel_data
        dst = 54
        for i in range(0, w * h * 4, 4):
            bmp[dst]     = src[i]
            bmp[dst + 1] = src[i + 1]
            bmp[dst + 2] = src[i + 2]
            dst += 3

        return bytes(bmp)

    def framebuffer_as_bmp(self) -> bytes:
        """Return framebuffer as a BMP image."""
        with self._lock:
            fb = bytearray(self._framebuffer)
        return self._make_bmp(fb)

    def uart_log_tail(self, max_lines: int = 80) -> str:
        """Read last N lines from UART log file."""
        try:
            with open(self.uart_log_path, "r", errors="replace") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:])
        except FileNotFoundError:
            return f"(UART log not found: {self.uart_log_path})\n"
        except Exception as e:
            return f"(UART log read error: {e})\n"

    @staticmethod
    def ansi_to_html(text: str) -> str:
        """Convert ANSI escape sequences to HTML spans with colors."""
        import html as html_mod
        # HTML-escape first to prevent XSS
        text = html_mod.escape(text)
        # ANSI color map: code -> CSS color
        colors = {
            '30': '#555', '31': '#ff5572', '32': '#c3e88d',
            '33': '#ffcb6b', '34': '#82aaff', '35': '#c792ea',
            '36': '#89ddff', '37': '#e0e0e0',
            '90': '#888', '91': '#ff8a98', '92': '#ddffa7',
            '93': '#ffe083', '94': '#a0c4ff', '95': '#ddb0ff',
            '96': '#a8f0ff', '97': '#ffffff',
        }
        result = []
        open_spans = 0
        i = 0
        # Work with the escaped text; ESC char is not affected by html.escape
        # but \x1b is a control char so it passes through
        while i < len(text):
            if text[i] == '\x1b' and i + 1 < len(text) and text[i + 1] == '[':
                # Find the 'm' terminator
                j = i + 2
                while j < len(text) and j < i + 20 and text[j] != 'm':
                    j += 1
                if j < len(text) and text[j] == 'm':
                    codes = text[i+2:j].split(';')
                    # Close any previous span
                    if open_spans > 0:
                        result.append('</span>')
                        open_spans -= 1
                    # Reset or empty
                    if codes == [''] or codes == ['0']:
                        pass  # just closed
                    else:
                        style_parts = []
                        for c in codes:
                            if c == '1':
                                style_parts.append('font-weight:bold')
                            elif c in colors:
                                style_parts.append(f'color:{colors[c]}')
                        if style_parts:
                            result.append(f'<span style="{";".join(style_parts)}">')
                            open_spans += 1
                    i = j + 1
                    continue
            result.append(text[i])
            i += 1
        # Close any remaining spans
        result.append('</span>' * open_spans)
        return ''.join(result)

    def send_uart(self, text: str) -> bool:
        """Send text to UART via Renode monitor 'uart WriteChar' command."""
        with self._lock:
            if not self._connected:
                return False
            try:
                for ch in text:
                    byte_val = ord(ch)
                    self._send_command(f"uart WriteChar {byte_val}")
                # Send newline (Enter)
                self._send_command("uart WriteChar 13")
                log.info("UART TX: %s", text.strip())
                return True
            except Exception as e:
                log.warning("UART send error: %s", e)
                return False


# ─── HTTP Web Viewer ──────────────────────────────────────────────────

WEB_VIEWER_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Draw Engine — Framebuffer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e; color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; padding: 16px;
  }
  h1 { font-size: 1.3em; margin-bottom: 6px; color: #7fdbca; }
  .info { font-size: 0.85em; color: #888; margin-bottom: 12px; }
  .info span { color: #c3e88d; }
  .main-row {
    display: flex; gap: 12px; align-items: flex-start;
  }
  .fb-container {
    display: flex; flex-direction: column; align-items: center;
  }
  canvas {
    border: 2px solid #333; border-radius: 4px;
    image-rendering: pixelated; image-rendering: crisp-edges;
    background: #000;
  }
  .uart-input-bar {
    display: flex; gap: 6px; margin-top: 8px; width: 640px;
  }
  .uart-input-bar input {
    flex: 1; background: #0c0c1a; color: #7fdbca; border: 1px solid #444;
    padding: 6px 10px; border-radius: 4px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 13px;
    outline: none;
  }
  .uart-input-bar input:focus { border-color: #7fdbca; }
  .uart-input-bar input::placeholder { color: #555; }
  .uart-input-bar button {
    background: #2a2a4a; color: #7fdbca; border: 1px solid #444;
    padding: 6px 14px; border-radius: 4px; cursor: pointer;
    font-size: 13px;
  }
  .uart-input-bar button:hover { background: #3a3a6a; border-color: #7fdbca; }
  .uart-send-status { font-size: 0.75em; color: #666; margin-top: 2px; height: 1em; }
  .uart-send-status.ok { color: #7fdbca; }
  .uart-send-status.err { color: #ff5572; }
  .uart-log-panel {
    display: flex; flex-direction: column;
    width: 480px; height: 514px;
  }
  .uart-log-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 4px 8px; background: #2a2a4a; border: 2px solid #333;
    border-bottom: none; border-radius: 4px 4px 0 0;
    font-size: 0.85em; color: #c3e88d;
  }
  .uart-log-header button {
    background: #1a1a2e; color: #888; border: 1px solid #444;
    padding: 2px 8px; border-radius: 3px; cursor: pointer; font-size: 0.85em;
  }
  .uart-log-header button:hover { color: #e0e0e0; border-color: #7fdbca; }
  .uart-log {
    flex: 1; background: #0c0c1a; color: #b0b0b0; border: 2px solid #333;
    border-radius: 0 0 4px 4px; padding: 8px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px;
    line-height: 1.4; overflow-y: auto; white-space: pre-wrap;
    word-break: break-all;
  }
  .controls {
    margin-top: 12px; display: flex; gap: 12px; align-items: center;
    font-size: 0.85em; flex-wrap: wrap; justify-content: center;
  }
  .controls label { color: #888; }
  .controls select, .controls button {
    background: #2a2a4a; color: #e0e0e0; border: 1px solid #444;
    padding: 4px 8px; border-radius: 4px; cursor: pointer;
  }
  .controls button:hover { background: #3a3a6a; }
  .status { margin-top: 8px; font-size: 0.75em; color: #666; }
  .status.ok { color: #7fdbca; }
  .status.err { color: #ff5572; }
</style>
</head>
<body>
<h1>&#x1F5A5; Draw Engine — Framebuffer</h1>
<div class="info">
  <span>640&times;480</span> XRGB8888 &nbsp;|&nbsp;
  Refresh: <span id="fps-display">2</span> fps &nbsp;|&nbsp;
  Frame: <span id="frame-count">0</span>
</div>

<div class="main-row">
  <div class="fb-container">
    <canvas id="fb" width="640" height="480"></canvas>
    <div class="uart-input-bar" id="uart-input-bar">
      <span style="color:#555;font-size:12px;align-self:center;">&#x276F;</span>
      <input type="text" id="uart-cmd" placeholder="Type command... (Enter to send)"
             autocomplete="off" spellcheck="false">
      <button onclick="sendUartCmd()">Send</button>
    </div>
    <div id="uart-send-status" class="uart-send-status"></div>
  </div>
  <div class="uart-log-panel">
    <div class="uart-log-header">
      <span>&#x1F4DF; UART Log</span>
      <button onclick="clearUartLog()" title="Clear display">Clear</button>
    </div>
    <div id="uart-log" class="uart-log">(waiting for UART output...)</div>
  </div>
</div>

<div class="controls">
  <label>Scale:</label>
  <select id="scale" onchange="setScale(this.value)">
    <option value="0.5">0.5&times;</option>
    <option value="0.75">0.75&times;</option>
    <option value="1" selected>1&times;</option>
    <option value="1.5">1.5&times;</option>
    <option value="2">2&times;</option>
  </select>
  <label>FPS:</label>
  <select id="fpsctl" onchange="setFps(this.value)">
    <option value="0.5">0.5</option>
    <option value="1">1</option>
    <option value="2" selected>2</option>
    <option value="5">5</option>
    <option value="10">10</option>
  </select>
  <button onclick="fetchFrame()">&#x27f3; Refresh</button>
</div>
<div id="status" class="status">Connecting...</div>

<script>
const cvsFb = document.getElementById('fb');
const ctxFb = cvsFb.getContext('2d');
const statusEl = document.getElementById('status');
const cmdInput = document.getElementById('uart-cmd');
const sendStatus = document.getElementById('uart-send-status');
const uartLogEl = document.getElementById('uart-log');
let interval = 500;
let timer = null;
let logTimer = null;
let frameCount = 0;
let cmdHistory = [];
let histIdx = -1;
let autoScroll = true;

function setScale(s) {
  const sz = parseFloat(s);
  cvsFb.style.width = (640 * sz) + 'px';
  cvsFb.style.height = (480 * sz) + 'px';
  document.querySelector('.uart-input-bar').style.width = (640 * sz) + 'px';
  const logPanel = document.querySelector('.uart-log-panel');
  logPanel.style.height = (480 * sz + 34) + 'px';
}
setScale(1);

function setFps(f) {
  interval = 1000 / parseFloat(f);
  document.getElementById('fps-display').textContent = f;
  if (timer) clearInterval(timer);
  timer = setInterval(fetchFrame, interval);
}

async function loadBmp(url, ctx) {
  const resp = await fetch(url + '?t=' + Date.now());
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const blob = await resp.blob();
  const bmp = await createImageBitmap(blob);
  ctx.drawImage(bmp, 0, 0);
}

async function fetchFrame() {
  try {
    await loadBmp('/frame.bmp', ctxFb);
    frameCount++;
    document.getElementById('frame-count').textContent = frameCount;
    statusEl.className = 'status ok';
    statusEl.textContent = '\u25cf Connected \u2014 frame #' + frameCount;
  } catch(e) {
    statusEl.className = 'status err';
    statusEl.textContent = '\u2716 ' + e.message;
  }
}

let lastLogHash = '';
async function fetchUartLog() {
  try {
    const resp = await fetch('/uart.log?t=' + Date.now());
    if (!resp.ok) return;
    const html = await resp.text();
    /* simple hash to avoid unnecessary DOM updates */
    const h = html.length + ':' + html.slice(-200);
    if (h !== lastLogHash) {
      lastLogHash = h;
      uartLogEl.innerHTML = html;
      if (autoScroll) {
        uartLogEl.scrollTop = uartLogEl.scrollHeight;
      }
    }
  } catch(e) { /* ignore */ }
}

function clearUartLog() {
  uartLogEl.textContent = '';
}

/* Detect manual scroll to pause auto-scroll */
uartLogEl.addEventListener('scroll', () => {
  const atBottom = uartLogEl.scrollTop + uartLogEl.clientHeight >= uartLogEl.scrollHeight - 20;
  autoScroll = atBottom;
});

async function sendUartCmd() {
  const cmd = cmdInput.value;
  try {
    sendStatus.className = 'uart-send-status';
    sendStatus.textContent = 'Sending...';
    const resp = await fetch('/uart.send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: cmd})
    });
    const result = await resp.json();
    if (result.ok) {
      sendStatus.className = 'uart-send-status ok';
      sendStatus.textContent = '\u2713 Sent: ' + cmd;
      if (cmd && (cmdHistory.length === 0 || cmdHistory[cmdHistory.length-1] !== cmd)) {
        cmdHistory.push(cmd);
      }
      histIdx = cmdHistory.length;
      cmdInput.value = '';
      setTimeout(() => { fetchFrame(); fetchUartLog(); }, 500);
    } else {
      sendStatus.className = 'uart-send-status err';
      sendStatus.textContent = '\u2716 ' + (result.error || 'Send failed');
    }
  } catch(e) {
    sendStatus.className = 'uart-send-status err';
    sendStatus.textContent = '\u2716 ' + e.message;
  }
}

cmdInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    sendUartCmd();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (cmdHistory.length > 0 && histIdx > 0) {
      histIdx--;
      cmdInput.value = cmdHistory[histIdx];
    }
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (histIdx < cmdHistory.length - 1) {
      histIdx++;
      cmdInput.value = cmdHistory[histIdx];
    } else {
      histIdx = cmdHistory.length;
      cmdInput.value = '';
    }
  }
});

fetchFrame();
fetchUartLog();
timer = setInterval(fetchFrame, interval);
logTimer = setInterval(fetchUartLog, 1500);
</script>
</body>
</html>"""


class WebViewerHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the browser-based framebuffer viewer."""

    fb_reader = None

    def log_message(self, format, *args):
        pass  # suppress default access log

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = WEB_VIEWER_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/frame.bmp"):
            bmp = self.fb_reader.framebuffer_as_bmp()
            self.send_response(200)
            self.send_header("Content-Type", "image/bmp")
            self.send_header("Content-Length", str(len(bmp)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(bmp)
        elif self.path.startswith("/uart.log"):
            text = self.fb_reader.uart_log_tail(200)
            html_text = self.fb_reader.ansi_to_html(text)
            body = html_text.encode("utf-8", errors="replace")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/uart.send":
            try:
                import json
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                cmd = data.get("cmd", "")
                ok = self.fb_reader.send_uart(cmd)
                resp = json.dumps({"ok": ok}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                resp = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
        else:
            self.send_error(404)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ─── VNC (RFB) Server ─────────────────────────────────────────────────

class VNCServer:
    """RFB/VNC server — macOS Screen Sharing compatible."""

    def __init__(self, fb_reader: RenodeFramebufferReader,
                 vnc_port: int = 5900, fps: float = 2.0):
        self.fb = fb_reader
        self.port = vnc_port
        self.fps = fps
        self.width = fb_reader.width
        self.height = fb_reader.height
        self._running = False
        self._server_sock = None
        # Pixel format: 32bpp, 24 depth, little-endian, true-colour
        self._pixel_format = struct.pack(
            "!BBBBHHHBBBxxx",
            32, 24, 0, 1,       # bpp, depth, big-endian, true-colour
            255, 255, 255,      # r/g/b max
            16, 8, 0,           # r/g/b shift
        )

    def start(self):
        """Start VNC server (blocking)."""
        self._running = True

        # FB polling thread
        threading.Thread(target=self._poll_fb, daemon=True).start()

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._server_sock.settimeout(1.0)

        for attempt in range(10):
            try:
                self._server_sock.bind(("0.0.0.0", self.port))
                break
            except OSError:
                if attempt < 9:
                    log.warning("Port %d busy (%d/10)...",
                                self.port, attempt + 1)
                    time.sleep(2)
                else:
                    raise
        self._server_sock.listen(5)
        log.info("VNC server on port %d", self.port)

        while self._running:
            try:
                cs, addr = self._server_sock.accept()
                log.info("VNC client from %s:%d", addr[0], addr[1])
                threading.Thread(
                    target=self._handle_client,
                    args=(cs, addr), daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._running = False
        if self._server_sock:
            self._server_sock.close()

    def _poll_fb(self):
        interval = 1.0 / self.fps
        while self._running:
            try:
                self.fb.read_framebuffer()
            except Exception:
                pass
            time.sleep(interval)

    def _handle_client(self, sock: socket.socket, addr):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(60.0)
            self._rfb_handshake(sock, addr)
            self._rfb_serve(sock, addr)
        except (ConnectionError, BrokenPipeError) as e:
            log.info("[%s] disconnected: %s", addr[0], e)
        except socket.timeout:
            log.info("[%s] timed out during handshake/serve", addr[0])
        except Exception as e:
            log.error("[%s] error: %s: %s", addr[0], type(e).__name__, e)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            log.info("[%s] closed", addr[0])

    # ── RFB Handshake ────────────────────────────────────────────────

    def _rfb_handshake(self, sock: socket.socket, addr):
        """RFB handshake — supports 3.3, 3.7, 3.8, and 3.889 (Apple)."""

        # Step 1: Exchange versions
        sock.sendall(b"RFB 003.008\n")
        client_ver = _recv_exact(sock, 12)
        ver_str = client_ver.decode("ascii", errors="replace").strip()
        log.info("[%s] version: %s", addr[0], ver_str)

        # Parse version
        minor = 8  # default
        try:
            parts = ver_str.split()
            if len(parts) >= 2:
                minor = int(parts[1].split(".")[1])
        except (ValueError, IndexError):
            pass

        # Step 2: Security negotiation
        if minor < 7:
            # RFB 3.3: server picks security as u32
            sock.sendall(struct.pack("!I", SEC_NONE))
        else:
            # RFB 3.7 / 3.8 / 3.889: send list, client picks
            sock.sendall(bytes([1, SEC_NONE]))  # 1 type offered
            chosen = struct.unpack("!B", _recv_exact(sock, 1))[0]
            log.info("[%s] security: client chose %d", addr[0], chosen)
            if chosen != SEC_NONE:
                reason = b"Only None auth supported"
                sock.sendall(struct.pack("!I", 1))  # fail
                sock.sendall(struct.pack("!I", len(reason)) + reason)
                raise ConnectionError(f"Unsupported security: {chosen}")

            # SecurityResult (3.8+)
            if minor >= 8 or minor == 889:
                sock.sendall(struct.pack("!I", 0))  # OK

        # Step 3: ClientInit
        _recv_exact(sock, 1)  # shared flag

        # Step 4: ServerInit
        name = b"Renode DrawEngine FB"
        init = struct.pack("!HH", self.width, self.height)
        init += self._pixel_format
        init += struct.pack("!I", len(name)) + name
        sock.sendall(init)
        log.info("[%s] handshake OK (%dx%d)", addr[0], self.width, self.height)

    # ── RFB Serve Loop ───────────────────────────────────────────────

    def _rfb_serve(self, sock: socket.socket, addr):
        """Send framebuffer updates to VNC client."""
        last_hash = b""
        frames = 0

        while self._running:
            try:
                # Non-blocking read for client messages
                sock.settimeout(0.05)
                try:
                    b = sock.recv(1)
                    if not b:
                        break
                    self._consume_client_msg(sock, b[0])
                except socket.timeout:
                    pass

                # Send frame if changed
                fb = self.fb.framebuffer
                h = hashlib.md5(fb).digest()
                if h != last_hash:
                    sock.settimeout(30.0)
                    hdr = struct.pack("!BxH", MSG_FB_UPDATE, 1)
                    rect = struct.pack("!HHHHi",
                                       0, 0, self.width, self.height, ENC_RAW)
                    sock.sendall(hdr + rect + fb)
                    last_hash = h
                    frames += 1
                    if frames <= 2:
                        log.info("[%s] frame #%d sent", addr[0], frames)

                time.sleep(1.0 / self.fps)

            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError):
                break

    def _consume_client_msg(self, sock: socket.socket, msg_type: int):
        """Read and discard a client message."""
        sock.settimeout(5.0)
        if msg_type == MSG_SET_PIXEL_FORMAT:
            _recv_exact(sock, 19)
        elif msg_type == MSG_SET_ENCODINGS:
            d = _recv_exact(sock, 3)
            n = struct.unpack("!xH", d)[0]
            _recv_exact(sock, n * 4)
        elif msg_type == MSG_FB_UPDATE_REQUEST:
            _recv_exact(sock, 9)
        elif msg_type == MSG_KEY_EVENT:
            _recv_exact(sock, 7)
        elif msg_type == MSG_POINTER_EVENT:
            _recv_exact(sock, 5)
        elif msg_type == MSG_CLIENT_CUT_TEXT:
            d = _recv_exact(sock, 7)
            length = struct.unpack("!xxxI", d)[0]
            if 0 < length < 10_000_000:
                _recv_exact(sock, length)
        else:
            log.debug("Unknown msg type: %d", msg_type)


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VNC + HTTP viewer for Renode framebuffer")
    parser.add_argument("--port", type=int, default=5900,
                        help="VNC port (default: 5900)")
    parser.add_argument("--web-port", type=int, default=5800,
                        help="HTTP viewer port (default: 5800)")
    parser.add_argument("--renode-host", default="localhost")
    parser.add_argument("--renode-port", type=int, default=1234)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fb-addr", type=lambda x: int(x, 0),
                        default=0x43E00000)
    parser.add_argument("--uart-log", default="/tmp/uart_output_interactive.txt",
                        help="Path to UART log file (default: /tmp/uart_output_interactive.txt)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("Renode FB Server — %dx%d @ %.0f fps", args.width, args.height, args.fps)

    fb = RenodeFramebufferReader(
        args.renode_host, args.renode_port,
        args.fb_addr, args.width, args.height,
        uart_log_path=args.uart_log)

    log.info("Connecting to Renode at %s:%d ...", args.renode_host, args.renode_port)
    if not fb.connect():
        sys.exit(1)

    log.info("Initial FB read...")
    fb.read_framebuffer()

    # HTTP viewer
    WebViewerHandler.fb_reader = fb
    http_srv = ThreadedHTTPServer(("0.0.0.0", args.web_port), WebViewerHandler)
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    log.info("HTTP viewer: http://0.0.0.0:%d", args.web_port)

    # VNC server
    vnc = VNCServer(fb, vnc_port=args.port, fps=args.fps)

    def sig_handler(signum, frame):
        log.info("Shutting down...")
        vnc.stop()
        http_srv.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    log.info("VNC server:  vnc://0.0.0.0:%d", args.port)
    log.info("─── Ready ─── Connect from Mac: ───")
    log.info("  Browser: http://<this-host>:%d", args.web_port)
    log.info("  VNC:     open vnc://<this-host>:%d", args.port)
    vnc.start()


if __name__ == "__main__":
    main()
