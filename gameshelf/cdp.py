"""A small Chrome DevTools Protocol client (Edge and Chrome speak it), for reading a page the
way the page itself loads it: in a browser window Sunbeam opens with its own profile.

Only the standard library: a minimal WebSocket client (RFC 6455) and the browser's
DevToolsActivePort file. Used by meta/nintendo_web.py for your Nintendo wish list.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.parse
from pathlib import Path


class CDPError(Exception):
    pass


# ------------------------------------------------------------------ WebSocket

class _WS:
    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("The browser closed the connection.")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("The browser refused the connection.")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise CDPError("The browser closed the connection.")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str) -> None:
        self._send(0x1, text.encode())

    def _send(self, op: int, data: bytes) -> None:
        head = bytes([0x80 | op])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise CDPError("The browser closed the connection.")
            if op == 0x9:  # ping: answer with a pong
                self._send(0xA, data)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2, 0x0):
                parts.append(data)
                if b1 & 0x80:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ the browser

class Browser:
    """A browser window with its own profile, driven over the DevTools protocol."""

    def __init__(self, exe: str, profile: Path, *, visible: bool, size=(1000, 760), app_url: str | None = None,
                 extra_args: list[str] | None = None):
        self.profile = profile
        profile.mkdir(parents=True, exist_ok=True)
        port_file = profile / "DevToolsActivePort"
        try:
            port_file.unlink()
        except OSError:
            pass
        args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run",
                "--no-default-browser-check", "--disable-sync", f"--window-size={size[0]},{size[1]}"]
        if not visible:
            # A real window, just out of sight (not a "headless" browser): the page runs as it does for you.
            args += ["--window-position=-32000,-32000"]
        args += extra_args or []
        args.append(f"--app={app_url or 'about:blank'}")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW (for the console, not the browser window)
        self.proc = subprocess.Popen(args, creationflags=flags, close_fds=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                lines = port_file.read_text().splitlines()
                if len(lines) >= 2 and lines[0].strip().isdigit():
                    self.port, self.browser_path = int(lines[0]), lines[1].strip()
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            self.kill()
            raise CDPError("The browser didn't start in time.")
        self.page = self._page_ws()
        self.next_id = 0
        self.events: list[dict] = []

    def _page_ws(self) -> _WS:
        import urllib.request
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                    targets = json.loads(r.read().decode())
                pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    return _WS(pages[0]["webSocketDebuggerUrl"])
            except OSError:
                pass
            time.sleep(0.3)
        raise CDPError("Couldn't reach the browser's page.")

    def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.page.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.page.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(str(msg["error"].get("message") or msg["error"]))
                return msg.get("result") or {}
            if "method" in msg:
                self.events.append(msg)
        raise CDPError(f"No answer to {method}.")

    def event(self, timeout: float) -> dict | None:
        """The next event (queued ones first), or None after timeout seconds."""
        if self.events:
            return self.events.pop(0)
        self.page.sock.settimeout(timeout)
        try:
            msg = json.loads(self.page.recv())
        except (socket.timeout, TimeoutError):
            return None
        finally:
            self.page.sock.settimeout(30)
        return msg if "method" in msg else None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        try:
            ws = _WS(f"ws://127.0.0.1:{self.port}{self.browser_path}", timeout=5)
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            ws.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.kill()
        finally:
            self.page.close()

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
