"""Vite dev server subprocess manager.

In dev mode, the server spawns a Vite dev server for the dashboard.
Reverse proxying is handled by wesktop's ViteDevProxy middleware
(wrapped in _LazyViteProxy in server.py).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from pathlib import Path

logger = logging.getLogger("codehome.serve.vite_dev")

# Directory containing the dashboard source (vite.config.ts lives here).
# Discovered from the dashboard plugin if loaded; falls back to the legacy
# relative path from the server module.
def _find_dashboard_dir() -> Path:
    """Discover the dashboard frontend source directory.

    Looks two levels up from the dashboard plugin dir (plugins/dashboard/
    -> project root -> dashboard/). Falls back to the legacy four-level
    traversal from this file's location.
    """
    from codehome.plugins import registry as plugin_registry

    plugin = plugin_registry.get("dashboard")
    if plugin is not None:
        # Plugin lives at <root>/plugins/dashboard/; frontend at <root>/dashboard/
        candidate = Path(plugin.plugin_dir).parent.parent / "dashboard"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent.parent / "dashboard"


DASHBOARD_DIR = _find_dashboard_dir()


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
    # proxy (the server is the entry point, not Vite).
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
