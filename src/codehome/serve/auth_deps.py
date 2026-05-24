"""Authentication dependencies for route handlers.

Wraps wesktop's auth module to add codehome-specific behavior:
- 4th token source: CLI token file (~/.codehome/token)
- Sentry user context integration
- request.state._user assignment for timing middleware

These are wesktop DI factories: each receives a wesktop Request as
the first argument and is registered via ``deps={"user": get_current_user}``.
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
    auth_header = request.header("authorization", "") or ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2. Session cookie
    if not token:
        token = request.cookie("session") or None

    # 3. Query parameter
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

    # Resolve JWT secret from request state.
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
    """Dependency that ensures the current user has the admin role."""
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPError(403, "Admin access required")
    return user


def _get_config(request: Any) -> Any:
    """Extract server config from request.state."""
    state = getattr(request, "state", None)
    if state is not None:
        config = getattr(state, "config", None)
        if config is not None:
            return config
    raise HTTPError(401, "Server config unavailable")
