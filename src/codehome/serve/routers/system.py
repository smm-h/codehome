"""System public endpoints (no auth): health, diagnostics intake, branding logo, server info.

The authenticated system endpoints have moved to the core plugin.
This stub retains only the public_router that server.py mounts without auth.
"""

import asyncio
import mimetypes
import time

from pydantic import BaseModel, Field
from wesktop import Router, HTTPError, Request, JSONResponse, FileResponse

from codehome.serve.dependencies import get_error_log, get_event_manager
from codehome.serve.error_log import ErrorLog
from codehome.serve.events import EventManager

# -- Public diagnostics (no auth) ------------------------------------------

public_router = Router()


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
async def post_diagnostic_errors(request: Request) -> object:
    """Accept frontend error reports (public, no auth required).

    Rate limiting is handled at the wesktop level (not via slowapi decorator).
    """
    payload = request.json_as(_DiagnosticErrorsPayload)
    error_log: ErrorLog = get_error_log(request)
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
async def health_check(request: Request) -> object:
    """Public health endpoint for uptime monitoring.

    Returns server status information and HTTP 200 if healthy,
    503 if critical issues detected.
    """
    from codehome.serve.system import build_health_status

    result = await build_health_status()
    return JSONResponse(result["body"], status=result["status_code"])


# -- Branding logo (public GET) --------------------------------------------

# Allowed MIME types and their canonical extensions for logo uploads.
_LOGO_MIME_MAP: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
}

_LOGO_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


@public_router.get("/api/branding/logo")
async def get_branding_logo(request: Request) -> FileResponse:
    """Serve the stored logo file (public, no auth)."""
    from codehome.serve.system import find_logo

    logo = await asyncio.to_thread(find_logo)
    if logo is None:
        raise HTTPError(404, "No logo uploaded")
    content_type = mimetypes.guess_type(str(logo))[0] or "application/octet-stream"
    return FileResponse(path=str(logo), content_type=content_type)


@public_router.get("/api/server/info")
async def server_info(request: Request) -> object:
    """Return server uptime and connected client count."""
    from codehome.serve.server import _server_start_time

    events: EventManager = get_event_manager(request)
    return {
        "uptime": time.time() - _server_start_time,
        "clients": events.client_count,
    }
