"""Vite dev server subprocess manager and reverse proxy.

In dev mode (default), v server spawns a Vite dev server for the dashboard
and reverse-proxies all non-API, non-SSE requests to it.  This gives us
Vite HMR for the frontend while keeping FastAPI as the single entry point
on :9100.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("codehome.serve.vite_dev")

# Directory containing the dashboard source (vite.config.ts lives here).
DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent.parent / "dashboard"


def _find_free_port() -> int:
    """Find an ephemeral port that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


async def start_vite_dev(dashboard_dir: Path | None = None) -> tuple[asyncio.subprocess.Process, int]:
    """Spawn ``npx vite`` on an ephemeral port and wait until it's ready.

    Returns (process, port).  The caller is responsible for calling
    ``stop_vite_dev()`` on shutdown.
    """
    dashboard_dir = dashboard_dir or DASHBOARD_DIR
    port = _find_free_port()

    # VITE_EMBEDDED=1 tells vite.config.ts to disable its /api and /events
    # proxy (FastAPI is the entry point, not Vite).
    env = {**os.environ, "VITE_EMBEDDED": "1"}

    proc = await asyncio.create_subprocess_exec(
        "npx",
        "vite",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--strictPort",
        cwd=str(dashboard_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Wait for Vite to be ready by polling the port.
    # Time out after 30s to avoid hanging forever on broken installs.
    deadline = asyncio.get_event_loop().time() + 30
    ready = False
    while asyncio.get_event_loop().time() < deadline:
        if proc.returncode is not None:
            remaining = await proc.stdout.read()  # type: ignore[union-attr]
            raise RuntimeError(f"Vite exited with code {proc.returncode}: {remaining.decode()}")

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=0.5,
            )
            writer.close()
            await writer.wait_closed()
            ready = True
            break
        except (ConnectionRefusedError, OSError, TimeoutError):
            await asyncio.sleep(0.2)

    if not ready:
        await stop_vite_dev(proc)
        raise RuntimeError("Vite dev server did not become ready within 30s")

    logger.info("Vite dev server ready on port %d (pid %d)", port, proc.pid)
    return proc, port


async def stop_vite_dev(proc: asyncio.subprocess.Process) -> None:
    """Gracefully stop the Vite subprocess."""
    if proc.returncode is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass
    logger.info("Vite dev server stopped")


async def drain_vite_output(proc: asyncio.subprocess.Process) -> None:
    """Read Vite stdout/stderr lines and log them (run as background task)."""
    stdout = proc.stdout
    if stdout is None:
        return
    while True:
        line = await stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            logger.debug("[vite] %s", text)


# ---------------------------------------------------------------------------
# Reverse proxy middleware
# ---------------------------------------------------------------------------


def _is_api_request(path: str) -> bool:
    """Return True if this path should be handled by FastAPI, not proxied."""
    return path.startswith(("/api/", "/events")) or path == "/api"


class ViteProxyMiddleware:
    """ASGI middleware that proxies non-API requests to the Vite dev server.

    Handles both regular HTTP requests and WebSocket upgrades (for HMR).
    Must be added LAST (outermost) so it wraps the entire FastAPI app and
    only intercepts requests that would otherwise 404 to static files.

    The ``vite_port`` attribute is set to ``None`` at construction and updated
    to the actual port by the lifespan handler once Vite is ready.  Until then,
    all requests fall through to FastAPI (graceful degradation if Vite fails).
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.vite_port: int | None = None
        self._http_client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=60, follow_redirects=False)
        return self._http_client

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        vite_port = self.vite_port

        if scope["type"] == "http":
            path = scope.get("path", "")
            if not _is_api_request(path) and vite_port is not None:
                await self._proxy_http(scope, receive, send, vite_port)
                return

        elif scope["type"] == "websocket":
            path = scope.get("path", "")
            if not _is_api_request(path) and vite_port is not None:
                await self._proxy_ws(scope, receive, send, vite_port)
                return

        # Fall through to FastAPI for API/events/lifespan/no-vite-port.
        await self.app(scope, receive, send)

    async def _proxy_http(self, scope: dict[str, Any], receive: Any, send: Any, vite_port: int) -> None:
        """Forward an HTTP request to the Vite dev server."""
        path = scope.get("path", "/")
        qs = scope.get("query_string", b"")
        target = f"http://127.0.0.1:{vite_port}{path}"
        if qs:
            target += f"?{qs.decode('latin-1')}"

        method = scope.get("method", "GET")

        # Collect request body.
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        # Forward a subset of headers.
        fwd_headers = {}
        for raw_name, raw_val in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            if name in (
                "accept",
                "accept-encoding",
                "accept-language",
                "cookie",
                "if-none-match",
                "if-modified-since",
                "cache-control",
                "content-type",
            ):
                fwd_headers[name] = raw_val.decode("latin-1")

        client = self._get_client()
        try:
            resp = await client.request(
                method=method,
                url=target,
                headers=fwd_headers,
                content=body or None,
            )
        except httpx.ConnectError:
            # Vite not reachable -- return 502.
            await send({"type": "http.response.start", "status": 502, "headers": []})
            await send({"type": "http.response.body", "body": b"Vite dev server not reachable"})
            return

        # Build response headers, excluding hop-by-hop headers.
        excluded = {"transfer-encoding", "content-encoding", "content-length"}
        resp_headers = [
            (k.encode("latin-1"), v.encode("latin-1")) for k, v in resp.headers.items() if k.lower() not in excluded
        ]
        # Add content-length for the actual body.
        resp_headers.append((b"content-length", str(len(resp.content)).encode("latin-1")))

        await send(
            {
                "type": "http.response.start",
                "status": resp.status_code,
                "headers": resp_headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": resp.content,
            }
        )

    async def _proxy_ws(self, scope: dict[str, Any], receive: Any, send: Any, vite_port: int) -> None:
        """Bidirectional WebSocket proxy to Vite (for HMR)."""
        from websockets.asyncio.client import connect

        path = scope.get("path", "/")
        qs = scope.get("query_string", b"")
        target = f"ws://127.0.0.1:{vite_port}{path}"
        if qs:
            target += f"?{qs.decode('latin-1')}"

        # Extract subprotocol from client headers (Vite HMR sends a token here).
        subprotocol = None
        for raw_name, raw_val in scope.get("headers", []):
            if raw_name.decode("latin-1").lower() == "sec-websocket-protocol":
                subprotocol = raw_val.decode("latin-1")
                break

        # Accept the client WebSocket, echoing back the subprotocol.
        msg = await receive()
        if msg["type"] != "websocket.connect":
            return
        accept_msg: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol:
            accept_msg["subprotocol"] = subprotocol
        await send(accept_msg)

        ws_kwargs: dict[str, Any] = {"ping_interval": None, "close_timeout": 5}
        if subprotocol:
            ws_kwargs["subprotocols"] = [subprotocol]

        try:
            async with connect(target, **ws_kwargs) as vite_ws:

                async def client_to_vite() -> None:
                    """Forward messages from the browser client to Vite."""
                    while True:
                        msg = await receive()
                        if msg["type"] == "websocket.receive":
                            text = msg.get("text")
                            if text is not None:
                                await vite_ws.send(text)
                            else:
                                data = msg.get("bytes", b"")
                                await vite_ws.send(data)
                        elif msg["type"] == "websocket.disconnect":
                            break

                async def vite_to_client() -> None:
                    """Forward messages from Vite to the browser client."""
                    async for data in vite_ws:
                        if isinstance(data, str):
                            await send({"type": "websocket.send", "text": data})
                        else:
                            await send({"type": "websocket.send", "bytes": data})

                _done, pending = await asyncio.wait(
                    [asyncio.ensure_future(client_to_vite()), asyncio.ensure_future(vite_to_client())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except Exception:
            pass
        finally:
            try:
                await send({"type": "websocket.close", "code": 1000})
            except Exception:
                pass
