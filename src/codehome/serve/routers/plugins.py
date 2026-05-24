"""Plugin discovery API for the dashboard frontend."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel
from wesktop import Router, HTTPError, Request, StreamResponse

from codehome.plugins import registry
from codehome.serve.sdui.commands import CommandError
from codehome.serve.sdui_providers import _SDUI_PROVIDERS
from codehome.state.service_registry import services

router = Router()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/plugins")
async def list_plugins(request: Request) -> list[dict[str, Any]]:
    """Return metadata for all loaded plugins."""
    result = []
    for plugin in registry.list_plugins():
        manifest = plugin.manifest
        entry: dict[str, Any] = {
            "name": plugin.name,
            "description": plugin.description,
            "has_dashboard": manifest.dashboard is not None,
            "has_cli": bool(plugin.manifest.commands),
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


@router.get("/api/plugins/{name}")
async def get_plugin(request: Request) -> dict[str, Any]:
    """Return detailed metadata for a single plugin.

    When a SDUI provider is registered for the plugin, the response
    includes ``ui`` (SDUINode tree) and ``state`` (initial state dict)
    so the frontend can render a server-driven layout.
    """
    name = request.path_params["name"]
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPError(404, f"Plugin '{name}' not found")
    manifest = plugin.manifest
    response: dict[str, Any] = {
        "name": plugin.name,
        "description": plugin.description,
        "has_dashboard": manifest.dashboard is not None,
        "has_cli": bool(plugin.manifest.commands),
        "has_checks": bool(plugin.manifest.checks),
        "commands": [{"name": c.name, "description": c.description} for c in manifest.commands],
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


@router.post("/api/plugins/{name}/command")
async def dispatch_command(request: Request) -> dict[str, Any]:
    """Dispatch a SDUI button command to the plugin's own API.

    The command name maps 1:1 to a POST endpoint on the plugin's router.
    Instead of making an HTTP round-trip to ourselves, we look up the
    matching route on the plugin's wesktop Router and call the endpoint
    function directly, resolving dependencies manually.
    """
    name = request.path_params["name"]
    payload: dict[str, Any] = request.json or {}

    plugin = registry.get(name)
    if plugin is None:
        raise HTTPError(404, f"Plugin '{name}' not found")
    if plugin.router is None:
        raise HTTPError(400, f"Plugin '{name}' has no routes")

    command = payload.get("command", "")
    if not command:
        raise HTTPError(400, "Missing 'command' in payload")
    # Prevent path traversal: only allow safe command names.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", command):
        raise HTTPError(400, "Invalid command name")

    # Find the matching POST route on the plugin's wesktop Router.
    # Routes are stored as (method, parsed_segments, handler, deps, model)
    # tuples. A single-segment literal path like "/ping" has parsed_segments
    # = [("ping", None, None)].
    endpoint_fn = None
    for method, segments, handler, _deps, _model in plugin.router._routes:
        if method != "POST":
            continue
        # Match single-segment literal paths (e.g. "/ping" -> [("ping", None, None)]).
        if len(segments) == 1:
            lit, _name, _conv = segments[0]
            if lit == command:
                endpoint_fn = handler
                break

    if endpoint_fn is None:
        raise HTTPError(404, f"Command '{command}' not found on plugin '{name}'")

    # Resolve dependencies and build kwargs for the endpoint call.
    # Plugin endpoints commonly use:
    #   - events: EventManager  (resolved from app.state via wesktop)
    #   - Pydantic BaseModel body parameters
    # We inspect the handler signature and resolve each from the request context.
    sig = inspect.signature(endpoint_fn)
    kwargs: dict[str, Any] = {}
    params = payload.get("params", {})

    for param_name, param in sig.parameters.items():
        # Wesktop handlers take 'request' as the first positional argument.
        if param_name == "request":
            kwargs[param_name] = request
        # Dependency-injected EventManager: resolve from app.state.
        elif param.annotation is not inspect.Parameter.empty and _is_event_manager(param.annotation):
            from codehome.serve.dependencies import get_event_manager

            kwargs[param_name] = get_event_manager(request)
        # Pydantic BaseModel body: construct from the params dict.
        elif param.annotation is not inspect.Parameter.empty and _is_pydantic_model(param.annotation):
            try:
                kwargs[param_name] = param.annotation(**params)
            except Exception as exc:
                raise HTTPError(422, str(exc)) from None
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


@router.post("/api/plugins/{name}/commands/{command}")
async def stream_command(request: Request) -> StreamResponse:
    """Execute a plugin streaming command and return progress/result as SSE.

    Streaming commands are registered in the service registry with the
    naming convention ``{plugin}.cmd.{command}``.  The handler must be an
    async generator yielding CommandProgress / CommandResult / CommandError
    instances.
    """
    name = request.path_params["name"]
    command = request.path_params["command"]

    plugin = registry.get(name)
    if plugin is None:
        raise HTTPError(404, f"Plugin '{name}' not found")

    # Prevent path traversal: only allow safe command names.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", command):
        raise HTTPError(400, "Invalid command name")

    service_name = f"{name}.cmd.{command}"
    if not services.has(service_name):
        raise HTTPError(404, f"Command '{command}' not found for plugin '{name}'")

    # Parse the request body (may be empty for parameterless commands).
    body = request.json or {}

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

    return StreamResponse(event_stream(), content_type="text/event-stream")


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
    error_log = request.state.get("error_log")
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
