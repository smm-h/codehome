"""HTTP client for CLI-to-server communication.

Thin wrapper around urllib that handles authentication (JWT from ~/.superv/token,
with fallback to ~/.supervisor/token), server discovery (via serve.read_server_url),
and error formatting. All CLI commands that need to talk to the server should use
get/post/put/patch/delete from this module instead of building their own urllib calls.
"""

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from codehome.paths import superv_home
from codehome.utils import die

# Unverified SSL context for localhost HTTPS connections. The server may
# run with a self-signed mkcert certificate; since we only ever connect to
# 127.0.0.1, skipping verification is safe and avoids requiring the mkcert
# CA to be in the system trust store for every urllib call.
_LOCALHOST_SSL_CTX = ssl.create_default_context()
_LOCALHOST_SSL_CTX.check_hostname = False
_LOCALHOST_SSL_CTX.verify_mode = ssl.CERT_NONE

# Legacy token path; kept as module-level constant for backward compat.
# Actual reads use _resolve_token_file() for dual-read fallback.
_LEGACY_TOKEN = Path.home() / ".supervisor" / "token"
TOKEN_FILE = superv_home() / "token"


def _resolve_token_file() -> Path:
    """Resolve the token file: prefer ~/.superv/token, fall back to ~/.supervisor/token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE
    return _LEGACY_TOKEN

# One-time-per-process flag for the "routing via server" stderr banner.
# When a `v` CLI subcommand (`v telemac screen ...`, `v tests red-green`, `v services delete-volumes`) has a
# running server to forward to, the actual work happens in a long-lived
# `v server` process -- NOT the freshly-invoked CLI. That handoff is invisible
# at the output level and has caused real debugging pain (stale modules in
# the server's memory diverging from fresh source on disk). We emit a
# single stderr banner on the first server-routed request per CLI
# invocation so the dispatch is visible and the server pid is a `ps` away.
_routing_banner_emitted = False


def _get_token() -> str | None:
    """Read the stored JWT token, or None if not logged in."""
    tf = _resolve_token_file()
    if tf.exists():
        return tf.read_text().strip()
    return None


def _server_info() -> tuple[str, int | None]:
    """Resolve the running server's (URL, pid) or die with a helpful message.

    Returns the base URL (with correct scheme) plus the server pid when
    known. The pid is used only for the routing banner -- a missing pid
    (stale/malformed port file) does not block the request;
    `read_server_url()` already did liveness.
    """
    from codehome.serve import read_server_url

    url = read_server_url()
    if not url:
        die("Server is not running. Start it with: v server")

    # Extract pid from server info for the routing banner.
    from codehome.serve import read_server_info

    info = read_server_info()
    pid = info[1] if info is not None else None
    return url, pid


def _emit_routing_banner(url: str, pid: int | None) -> None:
    """Print a one-time stderr banner announcing server-routed dispatch.

    Idempotent per-process via a module-level flag. Goes to stderr so it
    never pollutes captured stdout.
    """
    global _routing_banner_emitted
    if _routing_banner_emitted:
        return
    _routing_banner_emitted = True
    pid_part = f" pid={pid}" if pid is not None else ""
    print(f"[v: routing via server at {url}{pid_part}]", file=sys.stderr)  # noqa: T201


def _request(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    """Make an authenticated HTTP request to the codehome server.

    Handles JSON encoding/decoding, token injection, and translates HTTP
    errors into user-facing die() messages.
    """
    base_url, pid = _server_info()
    _emit_routing_banner(base_url, pid)
    url = f"{base_url}{path}"
    token = _get_token()

    data = json.dumps(body).encode() if body else None
    headers: dict[str, str] = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # Use unverified SSL for localhost HTTPS (mkcert self-signed certs).
    ssl_ctx = _LOCALHOST_SSL_CTX if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body_text).get("detail", body_text)
        except (json.JSONDecodeError, AttributeError):
            detail = body_text
        if e.code == 401:
            die("Authentication required. Run: v auth login")
        die(f"Server error ({e.code}): {detail}")
    except urllib.error.URLError as e:
        die(f"Cannot reach server: {e}")

    # Unreachable -- die() calls sys.exit() -- but keeps type-checkers happy.
    return {}  # pragma: no cover


def get(path: str, **kwargs: Any) -> dict[str, Any]:
    """GET request to the server."""
    return _request("GET", path, **kwargs)


def post(path: str, body: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """POST request to the server."""
    return _request("POST", path, body, **kwargs)


def put(path: str, body: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """PUT request to the server."""
    return _request("PUT", path, body, **kwargs)


def patch(path: str, body: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """PATCH request to the server."""
    return _request("PATCH", path, body, **kwargs)


def delete(path: str, **kwargs: Any) -> dict[str, Any]:
    """DELETE request to the server."""
    return _request("DELETE", path, **kwargs)
