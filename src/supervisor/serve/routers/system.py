"""System endpoints: diagnostics, health, preferences, SSE events, server info, shutdown."""

import asyncio
import logging
import mimetypes
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import StreamingResponse

from supervisor.serve.auth_deps import get_current_user, require_admin
from supervisor.serve.dependencies import get_error_log, get_event_manager, get_update_checker
from supervisor.serve.error_log import ErrorLog
from supervisor.serve.events import EventManager
from supervisor.serve.preferences import (
    DEFAULT_PREFERENCES,
    delete_preference,
    get_preference,
    get_user_preferences,
    load_global_preferences,
    save_global_preferences,
    set_preference,
    set_user_preferences,
)
from supervisor.serve.rate_limit import limiter
from supervisor.serve.system import get_system_status, run_health_checks
from supervisor.serve.system_ops import (
    build_health_status,
    find_logo,
    get_diagnostics_stats,
    get_request_metrics,
    list_repos_info,
    remove_logo,
    save_logo,
    shutdown_all_services,
    stream_self_update,
)
from supervisor.serve.updater import UpdateChecker

# -- Public diagnostics (no auth) ------------------------------------------

public_router = APIRouter()


class _DiagnosticError(BaseModel):
    message: str = Field(max_length=1000)
    source: str = Field(default="", max_length=500)
    line: int = 0
    stack: str = Field(default="", max_length=1000)
    timestamp: str = Field(default="", max_length=40)
    category: str = Field(default="uncaught", max_length=50)


class _DiagnosticErrorsPayload(BaseModel):
    errors: list[_DiagnosticError] = Field(max_length=10)


@public_router.post("/api/diagnostics/errors")
@limiter.limit("10/minute")  # type: ignore[untyped-decorator]
async def post_diagnostic_errors(
    payload: _DiagnosticErrorsPayload,
    request: Request,
    error_log: ErrorLog = Depends(get_error_log),
) -> object:
    """Accept frontend error reports (public, no auth required)."""
    for entry in payload.errors[:10]:
        await asyncio.to_thread(
            error_log.log,
            "frontend",
            entry.category,
            entry.message,
            severity="error",
            detail={"source": entry.source, "line": entry.line, "stack": entry.stack},
        )
    return {"ok": True}


@public_router.get("/api/health")
async def health_check() -> object:
    """Public health endpoint for uptime monitoring.

    Returns server status information and HTTP 200 if healthy,
    503 if critical issues detected.
    """
    result = await build_health_status()
    return JSONResponse(content=result["body"], status_code=result["status_code"])


# -- Branding logo (public GET) --------------------------------------------

# Allowed MIME types and their canonical extensions for logo uploads.
_LOGO_MIME_MAP: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
}

_LOGO_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


@public_router.get("/api/branding/logo")
async def get_branding_logo() -> FileResponse:
    """Serve the stored logo file (public, no auth)."""
    logo = await asyncio.to_thread(find_logo)
    if logo is None:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    content_type = mimetypes.guess_type(str(logo))[0] or "application/octet-stream"
    return FileResponse(path=str(logo), media_type=content_type)


# -- Authenticated ---------------------------------------------------------

router = APIRouter()


# -- SSE (authenticated) --------------------------------------------------


@router.get("/events")
async def sse_endpoint(events: EventManager = Depends(get_event_manager)) -> StreamingResponse:
    _sse_log = logging.getLogger("supervisor.serve.sse")

    async def generate() -> AsyncGenerator[str, None]:
        _sse_log.warning("SSE client connected")
        msg_count = 0
        try:
            async with events.subscribe() as stream:
                async for msg in stream:
                    msg_count += 1
                    yield msg
        except (asyncio.CancelledError, GeneratorExit):
            # The ASGI server (Granian) cancels the task when the TCP
            # connection is recycled or the client disconnects. Normal for SSE.
            pass
        except Exception as exc:
            _sse_log.warning("SSE exception after %d messages: %s: %s", msg_count, type(exc).__name__, exc)
        finally:
            _sse_log.warning("SSE client disconnected after %d messages", msg_count)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -- Branding logo (admin only) --------------------------------------------


@router.post("/api/branding/logo")
async def upload_branding_logo(
    file: UploadFile,
    _admin: dict[str, str] = Depends(require_admin),
) -> object:
    """Upload a logo image (PNG, JPG, or SVG, max 1 MB). Admin only."""
    ct = (file.content_type or "").lower()
    if ct not in _LOGO_MIME_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ct}")

    data = await file.read()
    if len(data) > _LOGO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 1 MB limit")

    ext = _LOGO_MIME_MAP[ct]
    await asyncio.to_thread(save_logo, data, ext)
    return {"url": "/api/branding/logo"}


@router.delete("/api/branding/logo")
async def delete_branding_logo(
    _admin: dict[str, str] = Depends(require_admin),
) -> object:
    """Remove the stored logo. Admin only."""
    removed = await asyncio.to_thread(remove_logo)
    if not removed:
        raise HTTPException(status_code=404, detail="No logo to delete")
    return {"ok": True}


# -- Server management ----------------------------------------------------


@router.post("/api/shutdown/cleanup")
async def shutdown_with_cleanup() -> object:
    """Stop all managed services (Docker containers, ports, etc.).

    Does NOT terminate the server process itself -- the CLI (`v server stop`)
    signals the Granian parent directly after this call. Self-SIGTERM here
    would kill only the Granian worker subprocess under reload mode and
    leave the reloader parent zombie, still holding the listening socket.
    """
    total_cleaned = await shutdown_all_services()
    return {"ok": True, "cleaned": total_cleaned}


@public_router.get("/api/server/info")
async def server_info(events: EventManager = Depends(get_event_manager)) -> object:
    """Return server uptime and connected client count."""
    from supervisor.serve.server import _server_start_time

    return {
        "uptime": time.time() - _server_start_time,
        "clients": events.client_count,
    }


# -- System endpoints ------------------------------------------------------


@router.get("/api/system/sentry-config")
async def sentry_config() -> object:
    """Return the Sentry DSN for frontend SDK initialization.

    Returns {dsn: string | null}. The DSN is read from the server
    config (.supervisor/config.json). Returns null if not configured.
    """
    from supervisor.config import load_server_config

    cfg = load_server_config()
    dsn = cfg.sentry_dsn if cfg and cfg.sentry_dsn else None
    return {"dsn": dsn}


@router.get("/api/system/status")
async def system_status() -> object:
    """Return host-level system status (disk, CPU, memory, Docker)."""
    return await asyncio.to_thread(get_system_status)


@router.get("/api/system/health")
async def system_health() -> object:
    """Run fast health checks and return results."""
    return await asyncio.to_thread(run_health_checks)


@router.get("/api/system/repos")
async def system_repos() -> object:
    """Return the list of configured repos with their remotes."""
    return await asyncio.to_thread(list_repos_info)


@router.get("/api/system/update-status")
async def update_status(checker: UpdateChecker = Depends(get_update_checker)) -> object:
    """Return the last update check result."""
    return checker.status()


@router.post("/api/system/update")
async def system_update(_admin: dict[str, str] = Depends(require_admin)) -> object:
    """Run the self-update sequence and stream progress via SSE.

    Before restarting, validates the new code by importing the app module. If the import
    fails, rolls back to the previous commit and rebuilds. Requires admin auth.
    """
    return StreamingResponse(
        stream_self_update(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -- User preferences (flat settings with defaults) ------------------------


@router.get("/api/preferences")
async def api_get_preferences(user: dict[str, str] = Depends(get_current_user)) -> object:
    """Return the current user's preferences with defaults applied."""
    return await asyncio.to_thread(get_user_preferences, user["sub"])


@router.put("/api/preferences")
async def api_update_preferences(request: Request, user: dict[str, str] = Depends(get_current_user)) -> object:
    """Partial-merge update of the current user's preferences.

    Only known keys with valid values are accepted; unknown keys and
    invalid values are silently dropped.
    """
    body = await request.json()
    allowed = set(DEFAULT_PREFERENCES)
    updates = {k: v for k, v in body.items() if k in allowed}
    await asyncio.to_thread(set_user_preferences, user["sub"], updates)
    return await asyncio.to_thread(get_user_preferences, user["sub"])


# -- Namespaced preferences (component-level state) ------------------------


@router.get("/api/preferences/{key}")
async def api_get_preference(key: str, user: dict[str, str] = Depends(get_current_user)) -> object:
    result = await asyncio.to_thread(get_preference, user["sub"], key)
    if result is None:
        return {}
    return result


@router.put("/api/preferences/{key}")
async def api_set_preference(key: str, request: Request, user: dict[str, str] = Depends(get_current_user)) -> object:
    body = await request.json()
    await asyncio.to_thread(set_preference, user["sub"], key, body)
    return {"ok": True}


@router.delete("/api/preferences/{key}")
async def api_delete_preference(key: str, user: dict[str, str] = Depends(get_current_user)) -> object:
    await asyncio.to_thread(delete_preference, user["sub"], key)
    return {"ok": True}


@router.get("/api/diagnostics/stats")
async def diagnostics_stats() -> object:
    """Return server diagnostics: uptime, request count, connections, services, agents, memory."""
    return get_diagnostics_stats()


@router.get("/api/diagnostics/errors")
async def get_diagnostic_errors(
    error_log: ErrorLog = Depends(get_error_log),
    since: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> object:
    """Return stored errors with optional filtering."""
    filters = {"since": since, "category": category, "severity": severity, "source": source}
    results = await asyncio.to_thread(error_log.get, **filters, limit=limit, offset=offset)
    total = await asyncio.to_thread(error_log.count, **filters)
    return {"errors": results, "count": total}


@router.delete("/api/diagnostics/errors")
async def clear_diagnostic_errors(
    error_log: ErrorLog = Depends(get_error_log),
    before: str | None = None,
) -> object:
    """Clear stored errors, optionally only those before a given timestamp."""
    deleted = await asyncio.to_thread(error_log.clear, before=before)
    return {"status": "cleared", "deleted": deleted}


@router.get("/api/diagnostics/request-metrics")
async def diagnostics_request_metrics() -> object:
    """Return error rates and p95 latency metrics from the request ring buffer."""
    return get_request_metrics()


# -- Global preferences (shared across all users) --------------------------


@router.get("/api/global-preferences/{namespace}")
async def api_get_global_preference(namespace: str, _user: dict[str, str] = Depends(get_current_user)) -> object:
    """Read a global preference namespace. Any authenticated user can read."""
    return await asyncio.to_thread(load_global_preferences, namespace)


@router.put("/api/global-preferences/{namespace}")
async def api_set_global_preference(
    namespace: str,
    request: Request,
    _admin: dict[str, str] = Depends(require_admin),
) -> object:
    """Write a global preference namespace. Admin only."""
    body = await request.json()
    await asyncio.to_thread(save_global_preferences, namespace, body)
    return {"ok": True}
