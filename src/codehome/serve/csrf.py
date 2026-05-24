"""CSRF protection: codehome wrapper around wesktop's CSRFMiddleware.

Adds codehome-specific behavior:
- CLI token file exemption (~/.codehome/token): requests authenticated via
  the local token file are not vulnerable to CSRF (the auth source is a
  local file, not a browser cookie).
- Test disable flag via scope["state"]["csrf_disabled"].

The generic double-submit cookie validation, Bearer exemption, safe-method
exemption, and path-based exemptions are handled by wesktop's CSRF middleware.
"""

from __future__ import annotations

import logging
from typing import Any

from wesktop.auth import CSRFMiddleware as _WesktopCSRFMiddleware

logger = logging.getLogger(__name__)

# Re-export for backward compatibility (routers/auth.py imports this).
from wesktop.auth import CSRFMiddleware  # noqa: F401


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token.

    Re-exported for backward compatibility with routers/auth.py.
    """
    import secrets

    return secrets.token_urlsafe(32)


class CodehomeCSRFMiddleware:
    """Codehome-specific CSRF wrapper adding CLI token file exemption.

    Wraps wesktop's CSRFMiddleware with two additional checks:
    1. Test disable flag (scope["state"]["csrf_disabled"])
    2. CLI token file (~/.codehome/token) -- if present and non-empty,
       the request is exempt from CSRF validation.
    """

    def __init__(
        self,
        app: Any,
        *,
        exempt_paths: list[str] | None = None,
        disabled: bool = False,
    ) -> None:
        # The inner wesktop CSRF middleware.
        self._csrf = _WesktopCSRFMiddleware(
            app,
            exempt_paths=exempt_paths,
            disabled=disabled,
        )
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            await self._csrf(scope, receive, send)
            return

        # Test disable flag (set via app.state.csrf_disabled = True).
        state = scope.get("state")
        if state is not None and state.get("csrf_disabled", False):
            await self._app(scope, receive, send)
            return

        method: str = scope.get("method", "GET")
        # Safe methods pass through regardless.
        if method in {"GET", "HEAD", "OPTIONS"}:
            await self._csrf(scope, receive, send)
            return

        # CLI token file exemption: if the token file exists and is non-empty,
        # the request is inherently safe (not a browser-cookie-based attack).
        from codehome.http_client import _resolve_token_file

        try:
            tf = _resolve_token_file()
            if tf.is_file() and tf.read_text().strip():
                await self._app(scope, receive, send)
                return
        except OSError:
            logger.debug("Could not read CLI token file for CSRF check")

        # Delegate to wesktop's generic CSRF validation.
        await self._csrf(scope, receive, send)
