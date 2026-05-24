"""Authentication dependencies for route handlers.

Wraps wesktop's auth module to add codehome-specific behavior:
- 4th token source: CLI token file (~/.codehome/token)
- Sentry user context integration
- request.state._user assignment for timing middleware

During the hybrid migration phase, these functions are used both as
FastAPI Depends() targets and as wesktop DI factories (both receive
a request object as the first argument). The ``request`` parameter
is typed as ``Any`` to accept both Starlette and wesktop Request
objects without importing either.
"""

import logging
from typing import Any

from wesktop.asgi import HTTPError
from wesktop.auth import verify_token

from codehome.http_client import _resolve_token_file
from codehome.serve.error_tracking import set_user_context

log = logging.getLogger(__name__)


async def get_current_user(request: Any) -> dict[str, str]:
    """Extract and validate JWT from Authorization header, session cookie, query param, or CLI token file.

    Token resolution order:
    1. Authorization: Bearer <token> header
    2. session cookie
    3. ?token= query parameter
    4. CLI token file (~/.codehome/token)

    Returns the decoded claims dict (with 'sub' and 'role' keys).
    Raises HTTPError(401) if no valid token is found.
    """
    token: str | None = None

    # 1. Bearer header
    # Works with both Starlette Request (.headers is a Headers mapping) and
    # wesktop Request (.header() method).
    if hasattr(request, "headers") and hasattr(request.headers, "get"):
        auth_header = request.headers.get("authorization", "")
    elif hasattr(request, "header"):
        auth_header = request.header("authorization", "") or ""
    else:
        auth_header = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2. Session cookie
    # Starlette: request.cookies dict; wesktop: request.cookie() method.
    if not token:
        if hasattr(request, "cookies") and isinstance(request.cookies, dict):
            token = request.cookies.get("session") or None
        elif hasattr(request, "cookie"):
            token = request.cookie("session") or None

    # 3. Query parameter
    # Both Starlette and wesktop support request.query_params with .get().
    if not token:
        qp = getattr(request, "query_params", None)
        if qp is not None and hasattr(qp, "get"):
            token = qp.get("token") or None

    # 4. CLI token file (~/.codehome/token) fallback
    from_token_file = False
    if not token:
        try:
            tf = _resolve_token_file()
            if tf.is_file():
                token = tf.read_text().strip() or None
                if token:
                    from_token_file = True
        except OSError:
            log.debug("Could not read CLI token file")

    if not token:
        raise HTTPError(401, "Not authenticated")

    # Resolve JWT secret from request state (works with both FastAPI and wesktop).
    config = _get_config(request)
    jwt_secret = config.jwt_secret if hasattr(config, "jwt_secret") else config["jwt_secret"]
    claims = verify_token(token, jwt_secret)
    if not claims:
        raise HTTPError(401, "Invalid or expired token")

    # Store on request.state so the timing middleware can log the user and
    # Sentry can attach user context to error reports.
    request.state._user = claims
    request.state._auth_from_token_file = from_token_file
    username = claims.get("sub")
    if username:
        set_user_context(username)

    return claims


async def require_admin(request: Any) -> dict[str, str]:
    """Dependency that ensures the current user has the admin role.

    Works as both a FastAPI Depends() target and a wesktop DI factory.
    """
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPError(403, "Admin access required")
    return user


def _get_config(request: Any) -> Any:
    """Extract server config from request state, handling both FastAPI and wesktop patterns."""
    # FastAPI pattern: request.app.state.config
    app = getattr(request, "app", None)
    if app is not None:
        config = getattr(getattr(app, "state", None), "config", None)
        if config is not None:
            return config

    # wesktop pattern: request.state.config or request.state["config"]
    state = getattr(request, "state", None)
    if state is not None:
        config = getattr(state, "config", None)
        if config is not None:
            return config
        if hasattr(state, "get"):
            config = state.get("config")
            if config is not None:
                return config

    raise HTTPError(401, "Server config unavailable")
