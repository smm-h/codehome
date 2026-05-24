"""Service endpoints: start/stop/restart, register, cleanup, templates, ports, metrics, tests."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from wesktop import Router, HTTPError, Request, JSONResponse, StreamResponse

from codehome.bus import Event
from codehome.bus import fire as bus_fire
from codehome.paths import resolve_global, codehome_home
from codehome.serve.dependencies import (
    get_error_log,
    get_event_manager,
    get_metrics_collector,
    get_port_allocator,
    get_service_manager,
)
from codehome.serve.error_log import ErrorLog
from codehome.serve.events import EventManager
from codehome.serve.metrics import MetricsCollector
from codehome.serve.operation_progress import OperationTracker
from codehome.serve.ports import PortAllocator
from codehome.serve.service_lifecycle import cleanup_branch_services, list_remaining_volumes, unregister_service_ports
from codehome.serve.service_lifecycle import reinstall_deps as lifecycle_reinstall_deps
from codehome.serve.service_lifecycle import setup_branch_services as lifecycle_setup
from codehome.serve.service_lifecycle import start_service as lifecycle_start
from codehome.serve.service_lifecycle import stop_service as lifecycle_stop
from codehome.serve.services import ServiceInstance, ServiceManager, State
from codehome.serve.templates import load_services_config, resolve_placeholders
from codehome.serve.test_runner import discover_suites, run_tests, stop_test_run

router = Router()

# Public (no auth) router -- narrow, port-only surface.  See notes on
# `/api/ports` below for why this is safe to expose unauthenticated.
public_router = Router()

_log = logging.getLogger(__name__)

# On-disk mirror of what ``GET /api/ports`` returns.  Used by the DOM
# inspector's resolver as a last-resort fallback when the server is
# unreachable (see ``codehome.inspect.resolver``).  Schema matches the
# HTTP response exactly so the resolver has a single decoder.
# Read path: resolve_global checks ~/.codehome/ first, falls back to .codehome/.
# Write path: write_vite_ports_state_from_registry() writes to ~/.codehome/ directly.
VITE_PORTS_STATE_FILE: Path = resolve_global("vite-ports.json")


_sse_diag_log: list[dict[str, object]] = []


@public_router.post("/api/diagnostics/sse")
async def sse_diagnostic(request: Request) -> dict[str, str]:
    """Receive SSE connection diagnostics from the browser. No auth required."""
    body: dict[str, Any] = request.json or {}
    _sse_diag_log.append(body)
    while len(_sse_diag_log) > 100:
        _sse_diag_log.pop(0)
    _log.warning("SSE diagnostic: %s", body)
    return {"ok": "received"}


@public_router.get("/api/diagnostics/sse")
async def sse_diagnostic_log(request: Request) -> list[dict[str, object]]:
    """Return collected SSE diagnostics."""
    return _sse_diag_log


@public_router.post("/api/services/discover")
async def trigger_discover_services(request: Request) -> list[dict[str, object]]:
    """Rescan the Docker daemon for externally-started Compose services.

    Unauthenticated by design, same posture as ``/api/ports`` above.  The
    endpoint runs a read-only ``docker ps`` + ``docker inspect`` pass (see
    ``codehome.serve.discovery.discover_docker``) and registers any
    previously-unknown Compose containers in ``ServiceManager``.

    Returns the current Vite-port list -- same shape as ``/api/ports`` --
    so a caller that missed a lookup on ``/api/ports`` can issue this POST
    and read the refreshed answer from the same response.  A dedicated
    lookup endpoint would force two round-trips; merging avoids that.

    The endpoint is idempotent: existing services are never clobbered, only
    net-new ones are registered.  Safe to call on every resolver miss.
    """
    from codehome.core.ops.discovery import discover_docker

    services: ServiceManager = get_service_manager(request)
    await discover_docker()
    return _collect_vite_ports(services)


@public_router.get("/api/ports")
async def list_vite_ports(request: Request) -> list[dict[str, object]]:
    """List running Vite ports per (branch, app).  Unauthenticated by design.

    This endpoint exists so the DOM inspector (``v inspect``) can resolve
    ``<branch>/<app>/<route>`` shorthand to an absolute URL without requiring
    a codehome JWT token.  CI, fresh-shell, and unauthenticated agents
    are the intended callers.

    Why this is safe to expose without auth:
      - The response contains *only* branch name, app name, port number, and
        state ("running").  No service metadata, config, paths, env vars,
        logs, or commands.
      - Port numbers themselves are not secret -- anything that can reach
        the server can already port-scan localhost.
      - The server is bound to 127.0.0.1; cross-host exposure is already
        the user's deliberate choice elsewhere.

    Response shape:
        [{"branch": "bag:lisa", "app": "bag", "port": 59899, "state": "running"}, ...]

    Only services whose key ends with ``/vite-<app>`` are returned (the
    naming convention for Vite services per ``.services.template.json``).
    Supabase, edge functions, and other service types are intentionally
    excluded from this surface -- this endpoint is port lookup for the
    DOM inspector, not a general service list.
    """
    services: ServiceManager = get_service_manager(request)
    return _collect_vite_ports(services)


def _collect_vite_ports(services: ServiceManager) -> list[dict[str, object]]:
    """Extract {branch, app, port, state} for Vite services in the registry.

    Shared between the HTTP handler and the disk-state serializer (see
    ``write_vite_ports_state_from_registry``) so the wire and disk schemas
    stay identical.
    """
    out: list[dict[str, object]] = []
    for svc in services.list_all():
        key = svc.key or ""
        # Key format: "<branch>/vite-<app>".  The "/vite-" marker is the
        # naming convention for Vite services in .services.template.json.
        marker = "/vite-"
        idx = key.find(marker)
        if idx < 0:
            continue
        app = key[idx + len(marker) :]
        if not app:
            continue
        if svc.port is None:
            continue
        if svc.state != State.RUNNING:
            continue
        out.append(
            {
                "branch": svc.branch,
                "app": app,
                "port": int(svc.port),
                "state": svc.state.value,
            },
        )
    return out


def write_vite_ports_state_from_registry() -> None:
    """Persist the current Vite port snapshot to ``VITE_PORTS_STATE_FILE``.

    Called from ``events.EventManager.broadcast`` on every
    ``service.state`` event so the disk fallback stays current without
    having to patch every state-transition call site individually.

    Uses the module-level ``services`` singleton rather than going through
    DI because this runs from event broadcasts, which are outside
    of any request scope.  Atomic-write via tmp + rename so partial writes
    never leave a half-JSON file on disk.
    """
    # Local import to avoid an import cycle: codehome.serve.services <-
    # this module <- codehome.serve.events <- this module.
    from codehome.serve.services import services as services_singleton

    entries = _collect_vite_ports(services_singleton)
    try:
        dest = codehome_home() / "vite-ports.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        tmp.replace(dest)
    except OSError:
        _log.exception("Failed to write vite-ports.json")


@router.get("/api/services")
async def list_services(request: Request) -> object:
    services: ServiceManager = get_service_manager(request)
    return [s.to_dict() for s in services.list_all()]


@router.get("/api/services/{key:path}")
async def get_service(request: Request) -> object:
    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")
    return svc.to_dict()


@router.post("/api/services/{key:path}/start")
async def start_service(request: Request) -> JSONResponse:
    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)
    error_log: ErrorLog = get_error_log(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")

    # Synchronous validation: check startability without holding the lock.
    can, reason = services.can_start(key)
    if not can:
        raise HTTPError(409, reason)

    # Lock check: if another operation is already in progress, reject immediately.
    if svc.lock.locked():
        raise HTTPError(409, "Operation already in progress for this service")

    # Create tracker before spawning the background task so we have the
    # operation_id to return in the 202 response.
    op = OperationTracker(events, "start", svc.key, "Starting service")

    async def _run_start() -> None:
        async with svc.lock:
            # Re-check startability inside the lock to prevent TOCTOU races.
            can_inner, reason_inner = services.can_start(key)
            if not can_inner:
                await op.start()
                await op.fail(reason_inner or "Cannot start")
                return
            await op.start()
            try:
                await lifecycle_start(svc, services, events, ports, error_log=error_log, tracker=op)
            except Exception as exc:
                _log.exception("Background start failed for %s", key)
                await op.fail(str(exc))

    asyncio.create_task(_run_start())
    return JSONResponse(
        {"operation_id": op.operation_id, "service_key": key},
        status=202,
    )


class StopRequest(BaseModel):
    force: bool = False


@router.post("/api/services/{key:path}/stop")
async def stop_service(request: Request) -> JSONResponse:
    key = request.path_params["key"]
    req = request.json_as(StopRequest)
    force = req.force
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)
    error_log: ErrorLog = get_error_log(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")

    # Synchronous validation: check state before acquiring the lock.
    if not force and svc.state != State.RUNNING:
        raise HTTPError(409, f"Service is {svc.state.value}, not running")
    if force and svc.state not in (State.RUNNING, State.FAILED, State.STOPPING, State.STARTING):
        raise HTTPError(409, f"Service is {svc.state.value}, nothing to stop")

    # Lock check: if another operation is already in progress, reject immediately.
    if svc.lock.locked():
        raise HTTPError(409, "Operation already in progress for this service")

    # Create tracker before spawning the background task so we have the
    # operation_id to return in the 202 response.
    op = OperationTracker(events, "stop", svc.key, "Stopping service")

    async def _run_stop() -> None:
        async with svc.lock:
            # Re-check state inside the lock to prevent TOCTOU races.
            if not force and svc.state != State.RUNNING:
                await op.start()
                await op.fail(f"Service is {svc.state.value}, not running")
                return
            await op.start()
            try:
                await lifecycle_stop(svc, events, ports, force=force, error_log=error_log, tracker=op)
            except Exception as exc:
                _log.exception("Background stop failed for %s", key)
                await op.fail(str(exc))

    asyncio.create_task(_run_stop())
    return JSONResponse(
        {"operation_id": op.operation_id, "service_key": key},
        status=202,
    )


class RegisterRequest(BaseModel):
    key: str
    service_type: str
    branch: str
    display_name: str
    depends_on: list[str] = []
    metadata: dict[str, Any] = {}


@router.post("/api/services/register")
async def register_service(request: Request) -> object:
    req = request.json_as(RegisterRequest)
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)

    instance = ServiceInstance(
        key=req.key,
        service_type=req.service_type,
        branch=req.branch,
        display_name=req.display_name,
        depends_on=req.depends_on,
        metadata=req.metadata,
    )
    try:
        services.register(instance)
    except ValueError as e:
        raise HTTPError(409, str(e)) from None
    await bus_fire(Event(name="service.state", payload=instance.to_dict()))
    return {"ok": True}


@router.post("/api/services/{key:path}/restart")
async def restart_service(request: Request) -> JSONResponse:
    """Stop then start a service. Only works on running services.

    Returns 202 immediately; the actual stop+start runs in a background task.
    A single lock acquisition covers the state check, stop, and start so
    that no concurrent operation can interfere between phases.
    """
    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)
    error_log: ErrorLog = get_error_log(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")

    # Synchronous validation: check state before acquiring the lock.
    if svc.state != State.RUNNING:
        raise HTTPError(409, f"Service is {svc.state.value}, not running")

    # Lock check: if another operation is already in progress, reject immediately.
    if svc.lock.locked():
        raise HTTPError(409, "Operation already in progress for this service")

    # Create tracker before spawning the background task so we have the
    # operation_id to return in the 202 response.
    op = OperationTracker(events, "restart", svc.key, "Restarting service")

    async def _run_restart() -> None:
        async with svc.lock:
            await op.start()
            try:
                # Re-check state inside the lock to prevent TOCTOU races.
                if svc.state != State.RUNNING:
                    await op.fail(f"Service is {svc.state.value}, not running")
                    return

                # Stop phase -- emit_progress=False so the nested stop doesn't
                # create its own progress bar; this restart operation covers the
                # whole flow.
                await op.update("Stopping...")
                stop_result = await lifecycle_stop(
                    svc,
                    events,
                    ports,
                    error_log=error_log,
                    emit_progress=False,
                )
                if not stop_result.get("ok"):
                    await op.fail(f"Stop failed: {svc.error or 'unknown'}")
                    return

                # Start phase.
                await op.update("Starting...")
                can, reason = services.can_start(svc.key)
                if not can:
                    await op.fail(f"Cannot start after stop: {reason}")
                    return
                start_result = await lifecycle_start(
                    svc,
                    services,
                    events,
                    ports,
                    error_log=error_log,
                    emit_progress=False,
                )
                if not start_result.get("ok"):
                    await op.fail(f"Start failed: {svc.error or 'unknown'}")
                    return

                await op.complete("Service restarted")
            except Exception as exc:
                _log.exception("Background restart failed for %s", key)
                await op.fail(str(exc))

    asyncio.create_task(_run_restart())
    return JSONResponse(
        {"operation_id": op.operation_id, "service_key": key},
        status=202,
    )


@router.post("/api/services/{key:path}/retry-migrations")
async def retry_migrations(request: Request) -> object:
    """Re-run supabase migrations on a running service."""
    from codehome.serve import supabase as sb

    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")
    if svc.service_type != "supabase":
        raise HTTPError(409, "Only supabase services support migrations")

    wt = Path(svc.metadata["worktree"])
    async with svc.lock:
        # State check inside the lock to prevent TOCTOU races.
        if svc.state != State.RUNNING:
            raise HTTPError(409, "Service must be running to retry migrations")
        ok, msg = await asyncio.to_thread(sb.apply_migrations, wt)

        if ok:
            svc.metadata.pop("migration_error", None)
            await bus_fire(Event(name="service.state", payload=svc.to_dict()))
            return {"ok": True}

        parsed = sb.parse_migration_output(msg)
        svc.metadata["migration_error"] = parsed
        await bus_fire(Event(name="service.state", payload=svc.to_dict()))
        return {"ok": False, "error": parsed}


@router.get("/api/services/{key:path}/deps-status")
async def deps_status(request: Request) -> object:
    """Check whether a service's node_modules volume is stale.

    Compares the host lockfile hash against the fingerprint stored in the
    container's named volume.  Broadcasts a service.deps SSE event with the result.
    """
    from codehome.serve.deps import check_deps_staleness, resolve_container_name, resolve_host_app_dir
    from codehome.serve.docker import compose_project_name

    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")

    # Only compose services with an app_dir have node_modules to check.
    app_dir = svc.metadata.get("app_dir")
    worktree = svc.metadata.get("worktree")
    if not app_dir or not worktree:
        raise HTTPError(409, "Service does not use a lockfile-managed volume")

    host_app = resolve_host_app_dir(worktree, app_dir)
    if not host_app:
        raise HTTPError(409, "Could not resolve host app directory")

    project = compose_project_name(svc.branch)
    compose_service = svc.metadata.get("compose_service", "vite")
    container = resolve_container_name(project, compose_service)

    result = await asyncio.to_thread(check_deps_staleness, container, host_app)

    # Broadcast so the frontend can reactively update a staleness badge.
    await bus_fire(Event(name="service.deps", payload={"service_key": key, "stale": result["stale"]}))

    return result


@router.post("/api/services/{key:path}/reinstall-deps")
async def reinstall_deps(request: Request) -> object:
    """Force-reinstall dependencies by removing the stale volume and recreating the container.

    Only works on compose services with an app_dir (vite services).
    The service must be running -- it will be stopped, volume removed,
    and container recreated with a fresh npm ci.
    """
    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)
    error_log: ErrorLog = get_error_log(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")
    if svc.service_type != "compose":
        raise HTTPError(409, "Only compose services support dependency reinstall")

    app_dir = svc.metadata.get("app_dir")
    worktree = svc.metadata.get("worktree")
    if not app_dir or not worktree:
        raise HTTPError(409, "Service does not use a lockfile-managed volume")

    async with svc.lock:
        # State check inside the lock to prevent TOCTOU races.
        if svc.state != State.RUNNING:
            raise HTTPError(409, f"Service is {svc.state.value}, must be running")
        return await lifecycle_reinstall_deps(svc, services, events, ports, error_log=error_log)


@router.delete("/api/services/{key:path}")
async def unregister_service(request: Request) -> object:
    """Remove a service from the registry. Must be stopped first."""
    key = request.path_params["key"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)

    svc = services.get(key)
    if not svc:
        raise HTTPError(404, f"Service not found: {key}")
    if svc.state == State.RUNNING:
        raise HTTPError(409, "Stop the service before unregistering")
    unregister_service_ports(svc, ports)
    services.unregister(key)
    await bus_fire(Event(name="service.state", payload={**svc.to_dict(), "state": "unregistered"}))
    return {"ok": True}


# -- Service template endpoints ------------------------------------------------


@router.get("/api/branches/{qualified}/services/available")
async def get_available_services(request: Request) -> object:
    """Return service definitions from .services.json, with registration status.

    Cross-references with already-registered services to show which are set up.
    """
    qualified = request.path_params["qualified"]
    services: ServiceManager = get_service_manager(request)

    repo, branch = qualified.split(":", 1)
    svc_defs = await asyncio.to_thread(load_services_config, repo, branch)
    if svc_defs is None:
        return {"available": False, "services": []}

    resolved = resolve_placeholders(svc_defs, qualified, repo, branch)
    # Annotate each definition with its current registration/state status.
    for svc_def in resolved:
        existing = services.get(svc_def["key"])
        svc_def["registered"] = existing is not None
        svc_def["state"] = existing.state.value if existing else None
    return {"available": True, "services": resolved}


@router.post("/api/branches/{qualified}/services/setup")
async def setup_branch_services(request: Request) -> object:
    """Register all services from .services.json for a branch."""
    qualified = request.path_params["qualified"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)

    registered = await lifecycle_setup(qualified, services, events)
    if not registered and registered is not None:
        # lifecycle_setup returns [] when no config found; distinguish from empty list.
        # Check if config exists to decide 404 vs empty.
        repo, branch = qualified.split(":", 1)
        svc_defs = await asyncio.to_thread(load_services_config, repo, branch)
        if svc_defs is None:
            raise HTTPError(
                404,
                "No service configuration found. Create .services.template.json in the repo root.",
            )
    return {"ok": True, "services": registered}


class CleanupRequest(BaseModel):
    branch: str
    remove_volumes: bool = False


@router.post("/api/services/cleanup")
async def cleanup_services(request: Request) -> object:
    """Stop all services for a branch and release resources."""
    req = request.json_as(CleanupRequest)
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)

    cleaned = await cleanup_branch_services(
        req.branch,
        services,
        events,
        ports,
        req.remove_volumes,
    )
    return {"ok": True, "cleaned": cleaned}


@router.post("/api/branches/{qualified}/cleanup")
async def api_branch_cleanup(request: Request) -> object:
    """Stop all services and remove Docker volumes for a branch."""
    qualified = request.path_params["qualified"]
    services: ServiceManager = get_service_manager(request)
    events: EventManager = get_event_manager(request)
    ports: PortAllocator = get_port_allocator(request)

    stopped_names = [svc.key for svc in services.list_for_branch(qualified)]

    await cleanup_branch_services(qualified, services, events, ports, remove_volumes=True)
    volumes_removed = await list_remaining_volumes(qualified)

    return {
        "services_stopped": stopped_names,
        "volumes_removed": volumes_removed,
    }


@router.get("/api/port-allocations")
async def get_port_allocations(request: Request) -> object:
    """Raw port-allocation map used by the authenticated dashboard UI.

    Previously exposed at ``/api/ports``; renamed when that path was
    reclaimed for the narrow unauthenticated ``list_vite_ports`` endpoint
    (see ``public_router`` above).
    """
    ports: PortAllocator = get_port_allocator(request)
    return ports.all_allocations()


@router.get("/api/metrics/history")
async def metrics_history(request: Request) -> object:
    minutes = request.query("minutes", default=5, type_=int)
    metrics_collector: MetricsCollector = get_metrics_collector(request)
    return metrics_collector.get_history(minutes)


# -- Test endpoints --------------------------------------------------------


@router.get("/api/tests")
async def list_tests(request: Request) -> object:
    repo = request.query("repo", default="bag")
    return discover_suites(repo)


class TestRunRequest(BaseModel):
    branch: str
    suite: str
    pattern: str | None = None
    headed: bool = False


@router.post("/api/tests/run")
async def run_test_suite(request: Request) -> object:
    from codehome.serve.test_ops import resolve_test_env, resolve_test_file, tests_dir

    req = request.json_as(TestRunRequest)
    test_file = resolve_test_file(req.suite)
    if not test_file:
        raise HTTPError(404, f"Test suite not found: {req.suite}")

    env_overrides = resolve_test_env(req.branch)

    run_id = await run_tests(
        req.branch,
        req.suite,
        tests_dir(),
        test_file,
        env_overrides=env_overrides,
        pattern=req.pattern,
        headed=req.headed,
    )
    return {"ok": True, "run_id": run_id}


@router.post("/api/tests/stop/{run_id}")
async def stop_tests(request: Request) -> object:
    """Stop an active test run by run_id."""
    run_id = request.path_params["run_id"]
    stopped = stop_test_run(run_id)
    if not stopped:
        raise HTTPError(404, f"No active run: {run_id}")
    return {"ok": True}


# -- Operation registry endpoints ------------------------------------------


@router.get("/api/operations")
async def list_operations(request: Request) -> object:
    """List all active and recently-completed operations.

    Returns operation metadata (no event payloads). Use the per-operation
    events endpoint to stream or catch up on individual operation output.
    """
    from codehome.serve.operations import operation_registry

    ops = await operation_registry.list_all()
    return [op.to_dict() for op in ops]


@router.get("/api/operations/{operation_id:path}/detail")
async def get_operation_detail(request: Request) -> dict[str, Any]:
    """Return operation metadata + buffered events as JSON.

    Used by the ProgressDrawer to catch up on events emitted before the
    drawer opened (e.g. after dismiss + reopen).
    """
    from codehome.serve.operations import operation_registry

    operation_id = request.path_params["operation_id"]
    op = await operation_registry.get(operation_id)
    if not op:
        raise HTTPError(404, "Operation not found")
    result = op.to_dict()
    result["events"] = await operation_registry.get_events(operation_id)
    return result


@router.get("/api/operations/{operation_id:path}/events")
async def operation_events_stream(request: Request) -> StreamResponse:
    """SSE stream of events for a specific operation.

    Query params:
      - since: 0-based event index for catch-up (returns buffered events
        from that index onward, then switches to live streaming).

    The stream emits:
      - Buffered events (catch-up) as ``event: operation:catchup``
      - Live events as ``event: operation:progress`` or ``event: operation:output``
      - A final ``event: operation:done`` when the operation completes
      - SSE heartbeats every 25 seconds

    The stream closes automatically when the operation finishes and all
    buffered events have been sent.
    """
    from codehome.serve.operations import operation_registry

    operation_id = request.path_params["operation_id"]
    since = request.query("since", default=0, type_=int)
    events: EventManager = get_event_manager(request)

    op = await operation_registry.get(operation_id)
    if not op:
        raise HTTPError(404, f"Operation not found: {operation_id}")

    async def generate() -> AsyncGenerator[str, None]:
        import json

        # Phase 1: Catch-up -- send buffered events the client missed.
        buffered = await operation_registry.get_events(operation_id, since=since)
        for evt in buffered:
            yield f"event: operation:catchup\ndata: {json.dumps(evt)}\n\n"

        # If already done, send final event and close.
        rec = await operation_registry.get(operation_id)
        if rec and rec.status != "running":
            yield f"event: operation:done\ndata: {json.dumps(rec.to_dict())}\n\n"
            return

        # Phase 2: Live -- subscribe to the global event stream and filter
        # for this operation's events.
        msg_count = 0
        async with events.subscribe() as stream:
            async for msg in stream:
                msg_count += 1

                # msg is a raw SSE string like "event: ...\ndata: ...\n\n"
                # Parse minimally to check if it belongs to this operation.
                if f'"operation_id": "{operation_id}"' in msg:
                    yield msg

                    # Check if this was a done event.
                    if '"done": true' in msg:
                        # Send final summary and close.
                        rec = await operation_registry.get(operation_id)
                        if rec:
                            yield f"event: operation:done\ndata: {json.dumps(rec.to_dict())}\n\n"
                        return

                # Heartbeat passthrough (SSE comments start with ':')
                if msg.startswith(":"):
                    yield msg

                # Safety: if the operation was externally completed (e.g. error
                # during catch-up race), close the stream. Check every ~50
                # messages to avoid per-message registry lookups.
                if msg_count % 50 == 0:
                    rec = await operation_registry.get(operation_id)
                    if rec and rec.status != "running":
                        yield f"event: operation:done\ndata: {json.dumps(rec.to_dict())}\n\n"
                        return

    return StreamResponse(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
