"""CSRF protection via double-submit cookie pattern.

On login, a `csrf_token` cookie is set (httponly=false so JS can read it).
The frontend must echo this token value back in the `X-CSRF-Token` header
on every state-changing request.  The middleware validates that the cookie
and header values match.

Safe methods (GET, HEAD, OPTIONS) are always exempt.  Requests carrying a
Bearer Authorization header are also exempt -- CSRF exploits cookie-based
auth only, so Bearer callers (CLI, agent hooks) are inherently safe.

The path-based exemptions below cover the remaining edge cases: endpoints
that accept unauthenticated POSTs with no Bearer token.

Implemented as a pure ASGI middleware (no BaseHTTPMiddleware) to avoid
wrapping streaming responses in memory-object streams, which can silently
terminate long-lived SSE connections.
"""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from typing import Any

# ASGI type aliases for readability.
Scope = dict[str, Any]
Receive = Any
Send = Any
ASGIApp = Any

# Methods that do not change state -- exempt from CSRF checks.
# ASGI scope["method"] is always a str per the spec.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Paths exempt from CSRF validation -- only unauthenticated POSTs that
# carry no Bearer token.  Bearer-protected endpoints (CLI, agent hooks)
# are handled by the Bearer check above, not listed here.
# ASGI scope["path"] is always a str per the spec.
_EXEMPT_PREFIXES = (
    "/api/auth/login",  # No token exists yet at login time
    "/api/diagnostics/errors",  # Fire-and-forget frontend error reports (no auth)
    "/api/services/discover",  # Unauthenticated Docker rescan (read-only)
)


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes:
    """Return the first header value matching *name* (lowercase), or b""."""
    for hdr_name, hdr_value in headers:
        if hdr_name == name:
            return hdr_value
    return b""


def _get_cookie(headers: list[tuple[bytes, bytes]], cookie_name: str) -> str:
    """Parse the Cookie header and return a single cookie value, or ""."""
    raw = _get_header(headers, b"cookie")
    if not raw:
        return ""
    sc: SimpleCookie = SimpleCookie()
    sc.load(raw.decode("latin-1"))
    morsel = sc.get(cookie_name)
    return morsel.value if morsel else ""


class CSRFMiddleware:
    """Double-submit cookie CSRF protection (pure ASGI).

    For state-changing requests (POST, PUT, PATCH, DELETE) that aren't
    exempt, validates that:
      1. A ``csrf_token`` cookie is present.
      2. An ``X-CSRF-Token`` header is present.
      3. The two values match.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only inspect HTTP requests; let websocket/lifespan pass through.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Allow tests to disable CSRF validation via app.state.
        # With pure ASGI, the Starlette app is accessible via scope["app"].
        app_obj = scope.get("app")
        if app_obj is not None and getattr(getattr(app_obj, "state", None), "csrf_disabled", False):
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope["headers"]
        method: str = scope["method"]
        path: str = scope["path"]

        # Safe methods and exempt paths skip validation.
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        # Bearer token callers (CLI) are not vulnerable to CSRF attacks --
        # those exploit cookie-based auth only.  Skip validation if the
        # request carries a Bearer Authorization header.  Token validity is
        # verified downstream by the route's own auth dependency.
        auth_header = _get_header(headers, b"authorization")
        if auth_header.startswith(b"Bearer "):
            # Cheap structural check: a real JWT has exactly 3 dot-separated
            # segments (header.payload.signature).  Reject malformed tokens
            # so "Bearer garbage" falls through to normal CSRF validation.
            token = auth_header[7:]
            parts = token.split(b".")
            if len(parts) == 3 and all(parts):
                await self.app(scope, receive, send)
                return

        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Token-file auth (CLI logged in, browser has no cookies) is not
        # vulnerable to CSRF -- the auth source is a local file, not a cookie.
        if not _get_cookie(headers, "session"):
            from supervisor.http_client import _resolve_token_file

            try:
                tf = _resolve_token_file()
                if tf.is_file() and tf.read_text().strip():
                    await self.app(scope, receive, send)
                    return
            except OSError:
                pass

        # Validate double-submit: cookie value must match header value.
        cookie_token = _get_cookie(headers, "csrf_token")
        header_token = _get_header(headers, b"x-csrf-token").decode("latin-1")

        if not cookie_token or not header_token:
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"detail":"Missing CSRF token"}'})
            return

        if not secrets.compare_digest(cookie_token, header_token):
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"detail":"CSRF token mismatch"}'})
            return

        await self.app(scope, receive, send)
