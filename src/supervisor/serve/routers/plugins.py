"""Plugin discovery API for the dashboard frontend."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from supervisor.plugins import registry
from supervisor.serve.sdui.commands import CommandError
from supervisor.serve.sdui_providers import _SDUI_PROVIDERS
from supervisor.state.service_registry import services

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_plugins() -> list[dict[str, Any]]:
    """Return metadata for all loaded plugins."""
    result = []
    for plugin in registry.list_plugins():
        manifest = plugin.manifest
        entry: dict[str, Any] = {
            "name": plugin.name,
            "version": plugin.version,
            "description": plugin.description,
            "has_dashboard": manifest.dashboard is not None,
            "has_cli": plugin.cli_registrar is not None,
            "has_checks": bool(plugin.manifest.checks),
        }
        if manifest.dashboard is not None:
            entry["dashboard"] = {
                "group": manifest.dashboard.group,
                "route": manifest.dashboard.route,
                "icon": manifest.dashboard.icon,
                "label": manifest.dashboard.label,
                "event_types": list(manifest.dashboard.event_types),
            }
        result.append(entry)
    return result


@router.get("/{name}")
async def get_plugin(name: str) -> dict[str, Any]:
    """Return detailed metadata for a single plugin.

    When a SDUI provider is registered for the plugin, the response
    includes ``ui`` (SDUINode tree) and ``state`` (initial state dict)
    so the frontend can render a server-driven layout.
    """
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    manifest = plugin.manifest
    response: dict[str, Any] = {
        "name": plugin.name,
        "version": plugin.version,
        "description": plugin.description,
        "has_dashboard": manifest.dashboard is not None,
        "has_cli": plugin.cli_registrar is not None,
        "has_checks": bool(plugin.manifest.checks),
        "commands": [{"name": c.name, "description": c.description, "group": c.group} for c in manifest.commands],
        "checks": [{"name": c.name, "group": c.group, "timeout": c.timeout} for c in manifest.checks],
        "dashboard": {
            "group": manifest.dashboard.group,
            "route": manifest.dashboard.route,
            "icon": manifest.dashboard.icon,
            "label": manifest.dashboard.label,
            "event_types": list(manifest.dashboard.event_types),
        }
        if manifest.dashboard
        else None,
    }

    # Attach SDUI tree and initial state if a provider is registered.
    provider = _SDUI_PROVIDERS.get(name)
    if provider is not None:
        ui_tree, initial_state = await provider()
        response["ui"] = ui_tree
        response["state"] = initial_state

    return response


@router.post("/{name}/command")
async def dispatch_command(
    name: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Dispatch a SDUI button command to the plugin's own API.

    The command name maps 1:1 to a POST endpoint on the plugin's router.
    Instead of making an HTTP round-trip to ourselves, we look up the
    matching route on the plugin's ``APIRouter`` and call the endpoint
    function directly, resolving FastAPI dependencies manually.
    """
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    if plugin.router is None:
        raise HTTPException(status_code=400, detail=f"Plugin '{name}' has no routes")

    command = payload.get("command", "")
    if not command:
        raise HTTPException(status_code=400, detail="Missing 'command' in payload")
    # Prevent path traversal: only allow safe command names.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", command):
        raise HTTPException(status_code=400, detail="Invalid command name")

    # Find the matching POST route on the plugin's router.
    target_path = f"/{command}"
    endpoint_fn = None
    for route in plugin.router.routes:
        if (
            hasattr(route, "path")
            and route.path == target_path
            and "POST" in (getattr(route, "methods", None) or set())
        ):
            endpoint_fn = route.endpoint
            break

    if endpoint_fn is None:
        raise HTTPException(status_code=404, detail=f"Command '{command}' not found on plugin '{name}'")

    # Resolve dependencies and build kwargs for the endpoint call.
    # Plugin endpoints commonly use:
    #   - events: EventManager = Depends(get_event_manager)  (telemac)
    #   - Pydantic BaseModel body parameters                 (tdd)
    # We resolve these from the request context.
    sig = inspect.signature(endpoint_fn)
    kwargs: dict[str, Any] = {}
    params = payload.get("params", {})

    for param_name, param in sig.parameters.items():
        # Dependency-injected EventManager: resolve from app.state.
        if param.annotation is not inspect.Parameter.empty and _is_event_manager(param.annotation):
            from supervisor.serve.dependencies import get_event_manager

            kwargs[param_name] = get_event_manager(request)
        # Pydantic BaseModel body: construct from the params dict.
        elif param.annotation is not inspect.Parameter.empty and _is_pydantic_model(param.annotation):
            try:
                kwargs[param_name] = param.annotation(**params)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        # Plain parameters matching keys in params (primitive args).
        elif param_name in params:
            kwargs[param_name] = params[param_name]

    try:
        result = await endpoint_fn(**kwargs)
    finally:
        # Audit log: record every plugin command dispatch (including failures).
        _audit_log(request, name, command, params)

    # Endpoint returns a dict or Pydantic model; normalise to dict.
    if isinstance(result, dict):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump()
    return {"status": "ok"}


@router.post("/{name}/commands/{command}")
async def stream_command(name: str, command: str, request: Request) -> StreamingResponse:
    """Execute a plugin streaming command and return progress/result as SSE.

    Streaming commands are registered in the service registry with the
    naming convention ``{plugin}.cmd.{command}``.  The handler must be an
    async generator yielding CommandProgress / CommandResult / CommandError
    instances.
    """
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")

    # Prevent path traversal: only allow safe command names.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", command):
        raise HTTPException(status_code=400, detail="Invalid command name")

    service_name = f"{name}.cmd.{command}"
    if not services.has(service_name):
        raise HTTPException(status_code=404, detail=f"Command '{command}' not found for plugin '{name}'")

    # Parse the request body (may be empty for parameterless commands).
    try:
        body = await request.json()
    except Exception:
        body = {}

    handler = services.get_handler(service_name)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for msg in handler(body):
                data = msg.model_dump_json()
                yield f"data: {data}\n\n"
        except Exception as e:
            error = CommandError(message=str(e))
            yield f"data: {error.model_dump_json()}\n\n"

    # Audit log the streaming command dispatch.
    _audit_log(request, name, command, body)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _is_event_manager(annotation: object) -> bool:
    """Check if *annotation* is the EventManager class (avoids top-level import)."""
    # Use string comparison to avoid importing EventManager at module level,
    # which would create a tighter coupling than necessary.
    return getattr(annotation, "__name__", None) == "EventManager"


def _is_pydantic_model(annotation: object) -> bool:
    """Check if *annotation* is a Pydantic BaseModel subclass."""
    try:
        return isinstance(annotation, type) and issubclass(annotation, BaseModel)
    except TypeError:
        return False


def _audit_log(request: Request, plugin: str, command: str, params: dict[str, Any]) -> None:
    """Record a plugin command dispatch in the error log (severity=info)."""
    error_log = getattr(request.app.state, "error_log", None)
    if error_log is None:
        return
    user = getattr(request.state, "user", None)
    caller = user.get("sub", "unknown") if isinstance(user, dict) else "unknown"
    error_log.log(
        source=f"plugin:{plugin}",
        category="command",
        message=f"{caller} dispatched {plugin}/{command}",
        severity="info",
        detail={"command": command, "params": params},
        context={"caller": caller},
    )
