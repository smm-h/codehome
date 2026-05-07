"""FastAPI dependency providers for service managers.

Each function retrieves the corresponding singleton from ``request.app.state``
(where it was stored during the lifespan startup).  Route handlers use these
via ``Depends()`` for testability -- tests can override any dependency with
``app.dependency_overrides`` instead of hacking private attributes.

Non-route code (background tasks, helper modules) continues to import the
module-level singletons directly.  Both paths reference the same instance
because the lifespan stores the module-level instance on ``app.state``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import HTTPException, Request

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

# app.state attributes are always ``Any`` in Starlette's type stubs.
# We use ``cast()`` to tell mypy the actual type stored by the lifespan.


def get_error_log(request: Request) -> ErrorLog:
    return cast("ErrorLog", request.app.state.error_log)


def get_event_manager(request: Request) -> EventManager:
    return cast("EventManager", request.app.state.event_manager)


def get_service_manager(request: Request) -> ServiceManager:
    return cast("ServiceManager", request.app.state.service_manager)


def get_port_allocator(request: Request) -> PortAllocator:
    return cast("PortAllocator", request.app.state.port_allocator)


def get_metrics_collector(request: Request) -> MetricsCollector:
    obj = getattr(request.app.state, "metrics_collector", None)
    if obj is None:
        raise HTTPException(status_code=503, detail="Monitoring feature is disabled")
    return cast("MetricsCollector", obj)


def get_health_checker(request: Request) -> HealthChecker:
    return cast("HealthChecker", request.app.state.health_checker)


def get_log_aggregator(request: Request) -> LogAggregator:
    return cast("LogAggregator", request.app.state.log_aggregator)


def get_pty_manager(request: Request) -> PTYManager:
    obj = getattr(request.app.state, "pty_manager", None)
    if obj is None:
        raise HTTPException(status_code=503, detail="Terminal feature is disabled")
    return cast("PTYManager", obj)


def get_agent_session_manager(request: Request) -> AgentSessionManager:
    obj = getattr(request.app.state, "agent_session_manager", None)
    if obj is None:
        raise HTTPException(status_code=503, detail="Conductor feature is disabled")
    return cast("AgentSessionManager", obj)


def get_question_store(request: Request) -> QuestionStore:
    obj = getattr(request.app.state, "question_store", None)
    if obj is None:
        raise HTTPException(status_code=503, detail="Conductor feature is disabled")
    return cast("QuestionStore", obj)


def get_push_manager(request: Request) -> PushManager:
    obj = getattr(request.app.state, "push_manager", None)
    if obj is None:
        raise HTTPException(status_code=503, detail="Push feature is disabled")
    return cast("PushManager", obj)


def get_update_checker(request: Request) -> UpdateChecker:
    return cast("UpdateChecker", request.app.state.update_checker)


def get_gh_token(request: Request) -> str | None:
    """Resolve the current user's GitHub PAT for gh CLI subprocess calls.

    Returns the decrypted token string, or None if the user has no token
    stored (in which case gh falls back to its own auth).  Returns None
    if the user identity is unavailable (e.g. the router-level auth
    dependency hasn't run yet).

    Uses the unified connections store which already falls back to the
    legacy github_tokens.py store internally.
    """
    from codehome.supervisor.ops.connections import get_token

    user = getattr(request.state, "_user", None)
    if not user:
        return None
    config = request.app.state.config
    return get_token(user["sub"], "github", config.jwt_secret)
