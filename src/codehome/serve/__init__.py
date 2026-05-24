"""Local dev orchestration server — wesktop ASGI server and SSE events."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from codehome.paths import STATE_DIR, resolve_global

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_PORT = 9100
# PORT_FILE stores JSON: {"port": int, "pid": int, "scheme": str}. The pid
# is the server root process (in reload mode, Granian's reloader; in
# stable mode, the Granian server itself). The scheme is "https" when TLS
# certs were available at launch, "http" otherwise. Tracking the pid lets
# the CLI stop the server with a real signal instead of an in-process HTTP
# self-kill.
PORT_FILE: Path = resolve_global("server.port")

_LEGACY_PORT_FILE: Path = STATE_DIR / "dashboard.port"


def _migrate_port_file() -> None:
    """One-time migration: rename dashboard.port to server.port."""
    if not PORT_FILE.exists() and _LEGACY_PORT_FILE.exists():
        _LEGACY_PORT_FILE.rename(PORT_FILE)


_migrate_port_file()  # one-time rename on first import

# Unverified SSL context for localhost liveness probes. Safe because we only
# ever connect to 127.0.0.1 and the mkcert CA is a local dev convenience,
# not a trust boundary.
_LOCALHOST_SSL_CTX = ssl.create_default_context()
_LOCALHOST_SSL_CTX.check_hostname = False
_LOCALHOST_SSL_CTX.verify_mode = ssl.CERT_NONE


def _read_scheme() -> str:
    """Read the scheme field from the port file, defaulting to 'http'."""
    try:
        raw = PORT_FILE.read_text().strip()
        data = json.loads(raw)
        return str(data.get("scheme", "http"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return "http"


def read_server_info_raw() -> tuple[int, int] | None:
    """Read (port, pid) from the port file without liveness checking.

    Returns None only if the file is missing or malformed. Used by
    `v server stop` / restart to find a zombie process whose port file is
    still on disk but whose HTTP /api/ping no longer responds.
    """
    try:
        raw = PORT_FILE.read_text().strip()
    except FileNotFoundError:
        return None

    try:
        data = json.loads(raw)
        port = int(data["port"])
        pid = int(data["pid"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None

    return port, pid


def read_server_info() -> tuple[int, int] | None:
    """Read the running server's (port, pid) from the port file.

    Returns (port, pid) if the server is running and responding, None
    otherwise. Verifies the server is actually alive via /api/ping so
    stale port files don't mislead callers.
    """
    raw = read_server_info_raw()
    if raw is None:
        return None
    port, pid = raw

    # Quick liveness check -- confirms the server isn't a stale port file.
    scheme = _read_scheme()
    url = f"{scheme}://127.0.0.1:{port}"
    ssl_ctx = _LOCALHOST_SSL_CTX if scheme == "https" else None
    try:
        req = urllib.request.Request(f"{url}/api/ping", method="GET")
        with urllib.request.urlopen(req, timeout=1, context=ssl_ctx) as resp:
            if resp.status == 200:
                return port, pid
    except (urllib.error.URLError, OSError, ValueError):
        return None

    return None


def read_server_url() -> str | None:
    """Read the running server's URL from the port file.

    Returns the full URL (scheme://host:port) if the server is running,
    None otherwise. The scheme is 'https' when the server was started
    with TLS certs, 'http' otherwise. Verifies the server is actually
    responding (not just a stale file).
    """
    info = read_server_info()
    if info is None:
        return None
    port, _ = info
    scheme = _read_scheme()
    return f"{scheme}://127.0.0.1:{port}"
