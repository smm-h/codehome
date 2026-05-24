"""Dependency providers for service managers.

Each function retrieves the corresponding singleton from ``request.app.state``
(where it was stored during the lifespan startup).  Route handlers use these
via ``Depends()`` for testability -- tests can override any dependency with
``app.dependency_overrides`` instead of hacking private attributes.

Non-route code (background tasks, helper modules) continues to import the
module-level singletons directly.  Both paths reference the same instance
because the lifespan stores the module-level instance on ``app.state``.

During the hybrid migration phase, these functions work with both FastAPI's
Starlette Request (request.app.state.*) and wesktop's Request
(request.state.*). The ``_get_state_attr`` helper abstracts the difference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from starlette.requests import Request
from wesktop.asgi import HTTPError

from codehome.serve.agent_sessions import AgentSessionManager
from codehome.serve.error_log import ErrorLog
from codehome.serve.events import EventManager
from codehome.serve.metrics import MetricsCollector
from codehome.serve.monitoring.health import HealthChecker
from codehome.serve.monitoring.logs import LogAggregator
from codehome.serve.ports import PortAllocator
from codehome.serve.push import PushManager
from codehome.serve.questions import QuestionStore
from codehome.serve.services import ServiceManager
from codehome.serve.updater import UpdateChecker

if TYPE_CHECKING:
    from codehome.pty import PTYManager


def _get_state_attr(request: Request, name: str) -> Any:
    """Retrieve a named attribute from request state.

    Handles both FastAPI (request.app.state.X) and wesktop (request.state.X)
    access patterns. Returns None if the attribute doesn't exist in either.
    """
    # FastAPI pattern: request.app.state.<name>
    app = getattr(request, "app", None)
    if app is not None:
        val = getattr(getattr(app, "state", None), name, None)
        if val is not None:
            return val
    # wesktop pattern: request.state.<name>
    state = getattr(request, "state", None)
    if state is not None:
        val = getattr(state, name, None)
        if val is not None:
            return val
    return None


def get_error_log(request: Request) -> ErrorLog:
    return cast("ErrorLog", _get_state_attr(request, "error_log"))


def get_event_manager(request: Request) -> EventManager:
    return cast("EventManager", _get_state_attr(request, "event_manager"))


def get_service_manager(request: Request) -> ServiceManager:
    return cast("ServiceManager", _get_state_attr(request, "service_manager"))


def get_port_allocator(request: Request) -> PortAllocator:
    return cast("PortAllocator", _get_state_attr(request, "port_allocator"))


def get_metrics_collector(request: Request) -> MetricsCollector:
    obj = _get_state_attr(request, "metrics_collector")
    if obj is None:
        raise HTTPError(503, "Monitoring feature is disabled")
    return cast("MetricsCollector", obj)


def get_health_checker(request: Request) -> HealthChecker:
    return cast("HealthChecker", _get_state_attr(request, "health_checker"))


def get_log_aggregator(request: Request) -> LogAggregator:
    return cast("LogAggregator", _get_state_attr(request, "log_aggregator"))


def get_pty_manager(request: Request) -> PTYManager:
    obj = _get_state_attr(request, "pty_manager")
    if obj is None:
        raise HTTPError(503, "Terminal feature is disabled")
    return cast("PTYManager", obj)


def get_agent_session_manager(request: Request) -> AgentSessionManager:
    obj = _get_state_attr(request, "agent_session_manager")
    if obj is None:
        raise HTTPError(503, "Conductor feature is disabled")
    return cast("AgentSessionManager", obj)


def get_question_store(request: Request) -> QuestionStore:
    obj = _get_state_attr(request, "question_store")
    if obj is None:
        raise HTTPError(503, "Conductor feature is disabled")
    return cast("QuestionStore", obj)


def get_push_manager(request: Request) -> PushManager:
    obj = _get_state_attr(request, "push_manager")
    if obj is None:
        raise HTTPError(503, "Push feature is disabled")
    return cast("PushManager", obj)


def get_update_checker(request: Request) -> UpdateChecker:
    return cast("UpdateChecker", _get_state_attr(request, "update_checker"))


def get_gh_token(request: Request) -> str | None:
    """Resolve the current user's GitHub PAT for gh CLI subprocess calls.

    Returns the decrypted token string, or None if the user has no token
    stored (in which case gh falls back to its own auth).  Returns None
    if the user identity is unavailable (e.g. the router-level auth
    dependency hasn't run yet).

    Uses the unified connections store which already falls back to the
    legacy github_tokens.py store internally.
    """
    from codehome.credentials import get_token

    user = getattr(request.state, "_user", None)
    if not user:
        return None
    config = _get_state_attr(request, "config")
    if config is None:
        return None
    jwt_secret = config.jwt_secret if hasattr(config, "jwt_secret") else config["jwt_secret"]
    return get_token(user["sub"], "github", jwt_secret)
