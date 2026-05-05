"""CDP (Chrome DevTools Protocol) client for live browser inspection.

Connects to a browser's debug port via HTTP (tab discovery) and WebSocket
(event streaming). Works with both Chrome and Firefox when launched with
--remote-debugging-port=<port>.

Uses only stdlib modules: urllib for HTTP, socket+hashlib for the WebSocket
handshake, and struct for frame parsing. No third-party dependencies needed.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# -- Tab discovery -----------------------------------------------------------


def discover_tabs(port: int = 9222) -> list[dict[str, Any]]:
    """Fetch the list of open tabs from the browser's debug port.

    Returns a list of tab dicts, each containing keys like 'url', 'title',
    'webSocketDebuggerUrl', 'type', etc.

    Raises ConnectionError if the debug port is unreachable.
    """
    url = f"http://localhost:{port}/json"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except (urllib.error.URLError, OSError) as e:
        msg = f"Cannot reach debug port {port}: {e}"
        raise ConnectionError(msg) from e


def find_dashboard_tab(
    port: int = 9222,
    tabs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Find the dashboard tab (127.0.0.1:9100 or localhost:9100).

    If *tabs* is provided, searches that list directly instead of
    making another HTTP request to discover_tabs().

    Returns the tab dict if found, None otherwise.
    """
    if tabs is None:
        tabs = discover_tabs(port)
    for tab in tabs:
        tab_url = tab.get("url", "")
        if "127.0.0.1:9100" in tab_url or "localhost:9100" in tab_url:
            return tab
    return None


# -- Minimal sync WebSocket client ------------------------------------------
# CDP only needs text frames (JSON messages), so we implement just enough
# of RFC 6455 to do the handshake, send text frames, and receive them.


class _WebSocket:
    """Minimal synchronous WebSocket client for CDP (text frames only)."""

    def __init__(self, url: str):
        self._sock: socket.socket | None = None
        self._url = url
        self._closed = False

    def connect(self) -> None:
        """Perform the WebSocket opening handshake."""
        # Parse ws://host:port/path from the URL.
        if not self._url.startswith("ws://"):
            msg = f"Only ws:// URLs are supported, got: {self._url}"
            raise ValueError(msg)
        without_scheme = self._url[5:]
        slash_idx = without_scheme.find("/")
        if slash_idx == -1:
            host_port = without_scheme
            path = "/"
        else:
            host_port = without_scheme[:slash_idx]
            path = without_scheme[slash_idx:]

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 80

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(10)
        self._sock.connect((host, port))

        # Generate a random key for the handshake.
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(handshake.encode())

        # Read the HTTP response (we only need the status line to verify 101).
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                msg = "WebSocket handshake failed: connection closed"
                raise ConnectionError(msg)
            response += chunk

        status_line = response.split(b"\r\n", 1)[0].decode()
        if "101" not in status_line:
            msg = f"WebSocket handshake failed: {status_line}"
            raise ConnectionError(msg)

    def send(self, text: str) -> None:
        """Send a text frame (masked, as required by RFC 6455 for clients)."""
        if self._sock is None or self._closed:
            msg = "WebSocket is not connected"
            raise ConnectionError(msg)

        payload = text.encode("utf-8")
        length = len(payload)

        # Frame header: FIN=1, opcode=1 (text), MASK=1.
        header = bytearray()
        header.append(0x81)  # FIN + text opcode

        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))

        # Masking key + masked payload.
        mask_key = os.urandom(4)
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        self._sock.sendall(header + mask_key + masked)

    def recv(self) -> str | None:
        """Receive the next text frame. Returns None on close.

        Non-text frames (ping, pong, binary) are handled internally via
        a loop rather than recursion, avoiding stack overflow if the
        server sends many control frames in succession.
        """
        if self._sock is None or self._closed:
            return None

        try:
            while True:
                # Read the 2-byte frame header.
                header = self._recv_exact(2)
                if header is None:
                    return None

                opcode = header[0] & 0x0F
                masked = bool(header[1] & 0x80)
                length = header[1] & 0x7F

                if length == 126:
                    ext = self._recv_exact(2)
                    if ext is None:
                        return None
                    length = struct.unpack(">H", ext)[0]
                elif length == 127:
                    ext = self._recv_exact(8)
                    if ext is None:
                        return None
                    length = struct.unpack(">Q", ext)[0]

                # Servers typically don't mask frames, but handle it if they do.
                mask_key = None
                if masked:
                    mask_key = self._recv_exact(4)
                    if mask_key is None:
                        return None

                payload = self._recv_exact(length)
                if payload is None:
                    return None

                if mask_key:
                    payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

                # Handle close frame.
                if opcode == 0x8:
                    self._closed = True
                    return None

                # Ping -> send pong, then continue to next frame.
                if opcode == 0x9:
                    self._send_pong(payload)
                    continue

                # Pong -> ignore, continue to next frame.
                if opcode == 0xA:
                    continue

                # Text frame.
                if opcode == 0x1:
                    return payload.decode("utf-8", errors="replace")

                # Binary or other -- skip, continue to next frame.

        except (OSError, ConnectionError):
            self._closed = True
            return None

    def close(self) -> None:
        """Send a close frame and shut down the socket."""
        if self._sock and not self._closed:
            try:
                # Send close frame: FIN=1, opcode=8, MASK=1, length=0.
                mask_key = os.urandom(4)
                self._sock.sendall(b"\x88\x80" + mask_key)
            except OSError:
                pass
            self._closed = True
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _recv_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes from the socket."""
        if self._sock is None:
            return None
        data = bytearray()
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _send_pong(self, payload: bytes) -> None:
        """Send a pong frame in response to a ping."""
        if self._sock is None or self._closed:
            return
        length = len(payload)
        header = bytearray()
        header.append(0x8A)  # FIN + pong opcode
        mask_key = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        else:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        try:
            self._sock.sendall(header + mask_key + masked)
        except OSError:
            pass


# -- CDP event streaming -----------------------------------------------------


def stream_events(ws_url: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
    """Stream CDP events from a browser tab and dispatch to callback.

    Enables Runtime and Network domains, then loops reading events.

    callback(event_type, data) where event_type is one of:
      - 'console': {level, text, stack}
      - 'exception': {description, stack}
      - 'network': {url, error, method}

    Runs until KeyboardInterrupt or WebSocket close.
    """
    ws = _WebSocket(ws_url)
    ws.connect()

    _msg_id = 0

    def _send_cmd(method: str, params: dict[str, Any] | None = None) -> None:
        nonlocal _msg_id
        _msg_id += 1
        msg = {"id": _msg_id, "method": method}
        if params:
            msg["params"] = params
        ws.send(json.dumps(msg))

    # Enable the CDP domains we care about.
    _send_cmd("Runtime.enable")
    _send_cmd("Network.enable")

    try:
        while True:
            raw = ws.recv()
            if raw is None:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            method = msg.get("method", "")

            if method == "Runtime.consoleAPICalled":
                params = msg.get("params", {})
                level = params.get("type", "log")
                # Concatenate all args' string representations.
                args = params.get("args", [])
                text = " ".join(a.get("value", a.get("description", str(a))) for a in args)
                stack = _extract_stack(params.get("stackTrace"))
                callback("console", {"level": level, "text": text, "stack": stack})

            elif method == "Runtime.exceptionThrown":
                params = msg.get("params", {})
                details = params.get("exceptionDetails", {})
                exc = details.get("exception", {})
                description = exc.get("description", details.get("text", "unknown"))
                stack = _extract_stack(details.get("stackTrace"))
                callback("exception", {"description": description, "stack": stack})

            elif method == "Network.loadingFailed":
                params = msg.get("params", {})
                request_id = params.get("requestId", "")
                error = params.get("errorText", "unknown")
                # Network.loadingFailed doesn't include the URL directly;
                # the URL comes from the corresponding requestWillBeSent event.
                # We include requestId so callers can correlate if needed.
                url = f"(requestId={request_id})"
                callback(
                    "network",
                    {
                        "url": url,
                        "error": error,
                        "method": params.get("type", ""),
                    },
                )

    except KeyboardInterrupt:
        pass
    finally:
        ws.close()


def _extract_stack(stack_trace: dict[str, Any] | None) -> str:
    """Extract a human-readable stack string from a CDP stackTrace object."""
    if not stack_trace:
        return ""
    frames = stack_trace.get("callFrames", [])
    lines = []
    for f in frames:
        fn = f.get("functionName", "(anonymous)")
        url = f.get("url", "")
        line = f.get("lineNumber", 0)
        col = f.get("columnNumber", 0)
        lines.append(f"  at {fn} ({url}:{line}:{col})")
    return "\n".join(lines)
