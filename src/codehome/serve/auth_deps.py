"""FastAPI authentication dependencies used by router modules.

Extracted from server.py to break circular import chains -- routers
import these functions without pulling in the full app module.
"""

import logging

from fastapi import Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import Request

from codehome.http_client import _resolve_token_file
from codehome.serve.auth import verify_token
from codehome.serve.error_tracking import set_user_context

log = logging.getLogger(__name__)

# Optional bearer scheme -- does not auto-reject missing tokens (we handle that).
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: str | None = Cookie(default=None),
) -> dict[str, str]:
    """Extract and validate JWT from Authorization header, session cookie, query param, or CLI token file.

    The ``?token=`` query-param fallback exists because browser APIs like
    ``EventSource`` (SSE) cannot set custom headers -- same pattern used by
    the WebSocket terminal endpoint.

    The ``~/.codehome/token`` file fallback enables bidirectional auth sync:
    logging in via the CLI (which writes this file) automatically authenticates
    the dashboard browser session.

    Returns the decoded claims dict (with 'sub' and 'role' keys).
    Raises 401 if no valid token is found.
    """
    token: str | None = None
    if credentials:
        token = credentials.credentials
    elif session:
        token = session
    elif request.query_params.get("token"):
        token = request.query_params["token"]

    # Fallback: read CLI token file (~/.codehome/token or legacy ~/.supervisor/token)
    # so that CLI login automatically works in the browser without a separate
    # dashboard login.
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
        raise HTTPException(status_code=401, detail="Not authenticated")

    config = request.app.state.config
    claims = verify_token(token, config.jwt_secret)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Store on request.state so the timing middleware can log the user and
    # Sentry can attach user context to error reports.
    request.state._user = claims
    request.state._auth_from_token_file = from_token_file
    username = claims.get("sub")
    if username:
        set_user_context(username)

    return claims


async def require_admin(user: dict[str, str] = Depends(get_current_user)) -> dict[str, str]:
    """Dependency that ensures the current user has the admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
