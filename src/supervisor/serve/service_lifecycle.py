"""Service lifecycle orchestration: start, stop, cleanup.

Extracted from routers/services.py so that route handlers remain thin wrappers.
All Docker/Supabase/compose orchestration lives here; the router only handles
HTTP validation, dependency injection, and response formatting.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from supervisor.bus import Event, fire
from supervisor.serve import supabase as sb
from supervisor.serve.services import ServiceInstance, State

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from supervisor.serve.error_log import ErrorLog
    from supervisor.serve.events import EventManager
    from supervisor.serve.operation_progress import OperationTracker
    from supervisor.serve.ports import PortAllocator
    from supervisor.serve.services import ServiceManager


def _make_output_callback(
    op: OperationTracker | None,
    loop: asyncio.AbstractEventLoop,
) -> Any:
    """Build a sync callback that bridges subprocess output to async SSE events.

    The returned callable has signature (stream: str, line: str) -> None and is
    safe to call from a background thread. If *op* is None, returns a no-op.
    """
    if op is None:
        return lambda _stream, _line: None

    def _callback(stream: str, line: str) -> None:
        # Schedule the async emit_output coroutine on the event loop from
        # the synchronous reader thread.
        asyncio.run_coroutine_threadsafe(op.emit_output(stream, line), loop)

    return _callback


async def start_service(
    svc: ServiceInstance,
    services: ServiceManager,
    events: EventManager,
    ports: PortAllocator,
    error_log: ErrorLog | None = None,
    *,
    emit_progress: bool = True,
    tracker: OperationTracker | None = None,
) -> dict[str, Any]:
    """Start a service (supabase, compose, or compose-native).

    Caller must hold svc.lock and have verified can_start().
    When *emit_progress* is False, no operation.progress events are emitted
    (useful when a parent operation like reinstall-deps or restart already
    tracks its own progress).
    When *tracker* is provided, it is used as-is (caller already called
    ``await tracker.start()``).  When omitted and *emit_progress* is True,
    a new tracker is created internally.
    Returns {ok, state, port}.
    """
    from supervisor.serve.operation_progress import OperationTracker

    # Use a pre-created tracker if provided, otherwise build one internally.
    op: OperationTracker | None = tracker
    if op is None and emit_progress:
        op = OperationTracker(events, "start", svc.key, "Starting service")
        await op.start()

    svc.transition(State.STARTING)
    await fire(Event(name="service.state", payload=svc.to_dict()))

    if op:
        await op.update("Starting container...")

    try:
        if svc.service_type == "supabase":
            await _start_supabase(svc, events, ports, error_log=error_log, tracker=op)
        elif svc.service_type == "compose":
            await _start_compose(svc, ports, error_log=error_log, tracker=op)
        elif svc.service_type == "compose-native":
            await _start_compose_native(svc, ports, error_log=error_log, tracker=op)
        else:
            svc.transition(State.FAILED, error=f"Unknown service type: {svc.service_type}")
    except Exception as exc:
        log.exception("Unhandled error starting service %s", svc.key)
        # Only transition if still STARTING (type-specific starter may have already
        # moved to FAILED before raising).
        if svc.state == State.STARTING:
            svc.transition(State.FAILED, error=str(exc))

    await fire(Event(name="service.state", payload=svc.to_dict()))

    # Fire a non-blocking deps staleness check for vite-type services so the
    # frontend shows a staleness badge immediately after start.
    if svc.state == State.RUNNING:
        _maybe_schedule_deps_check(svc, events)

    # Finalize progress tracking.
    if op:
        if svc.state == State.RUNNING:
            await op.complete("Service started")
        else:
            await op.fail(svc.error or "Start failed")

    return {"ok": svc.state == State.RUNNING, "state": svc.state.value, "port": svc.port}


async def stop_service(
    svc: ServiceInstance,
    events: EventManager,
    ports: PortAllocator,
    force: bool = False,
    error_log: ErrorLog | None = None,
    *,
    emit_progress: bool = True,
    tracker: OperationTracker | None = None,
) -> dict[str, Any]:
    """Stop a running service.

    Caller must hold svc.lock. When force=True and state is FAILED, resets to
    STOPPED without actually stopping anything.
    When *emit_progress* is False, no operation.progress events are emitted
    (useful when a parent operation like restart already tracks its own progress).
    When *tracker* is provided, it is used as-is (caller already called
    ``await tracker.start()``).  When omitted and *emit_progress* is True,
    a new tracker is created internally.
    Returns {ok, state}.
    """
    from supervisor.serve.operation_progress import OperationTracker

    _svc_ctx = {"service_key": svc.key, "service_type": svc.service_type, "branch": svc.branch}

    # Force-clear paths skip progress -- they're instant transitions.
    # If a pre-created tracker was passed, complete it before returning.
    if force and svc.state == State.FAILED:
        svc.transition(State.STOPPED)
        await fire(Event(name="service.state", payload=svc.to_dict()))
        if tracker:
            await tracker.complete("Force-stopped (was failed)")
        return {"ok": True, "state": svc.state.value}

    if force and svc.state == State.STARTING:
        # Service may have partially started Docker containers; attempt cleanup.
        # Best-effort: don't let cleanup failure block the force-stop.
        try:
            if svc.service_type == "supabase":
                wt = Path(svc.metadata.get("worktree", ""))
                await asyncio.to_thread(sb.stop, wt)
                await asyncio.to_thread(sb.restore_config, wt)
                ports.release_supabase_slot(svc.branch)
            elif svc.service_type == "compose":
                from supervisor.serve import docker as dk

                compose_service = svc.metadata.get("compose_service", "")
                tdd = svc.metadata.get("tdd", False)
                await asyncio.to_thread(dk.compose_down, svc.branch, compose_service, tdd)
                ports.release(svc.key)
            elif svc.service_type == "compose-native":
                from supervisor.serve import docker as dk

                compose_file = Path(svc.metadata.get("compose_file", ""))
                compose_service = svc.metadata.get("compose_service", "")
                project_name = f"veliu-{dk.compose_project_name(svc.branch)}"
                await asyncio.to_thread(dk.compose_native_down, compose_file, compose_service, project_name)
                ports.release_native(svc.key)
        except Exception:
            log.warning("Best-effort cleanup failed for force-stopped STARTING service %s", svc.key, exc_info=True)
        svc.transition(State.STOPPED)
        await fire(Event(name="service.state", payload=svc.to_dict()))
        if tracker:
            await tracker.complete("Force-stopped (was starting)")
        return {"ok": True, "state": svc.state.value}

    if force and svc.state == State.STOPPING:
        svc.transition(State.FAILED, error="Force-stopped from stuck STOPPING state")
        svc.transition(State.STOPPED)
        await fire(Event(name="service.state", payload=svc.to_dict()))
        if tracker:
            await tracker.complete("Force-stopped (was stuck stopping)")
        return {"ok": True, "state": svc.state.value}

    # Use a pre-created tracker if provided, otherwise build one internally.
    op: OperationTracker | None = tracker
    if op is None and emit_progress:
        op = OperationTracker(events, "stop", svc.key, "Stopping service")
        await op.start()

    svc.transition(State.STOPPING)
    await fire(Event(name="service.state", payload=svc.to_dict()))

    if op:
        await op.update("Stopping container...")

    try:
        if svc.service_type == "supabase":
            await _stop_supabase(svc, ports, force, tracker=op)
        elif svc.service_type == "compose":
            await _stop_compose(svc, ports, force, tracker=op)
        elif svc.service_type == "compose-native":
            await _stop_compose_native(svc, ports, force, tracker=op)
        else:
            svc.transition(State.STOPPED)

        await fire(Event(name="service.state", payload=svc.to_dict()))

        if op:
            if svc.state == State.STOPPED:
                await op.complete("Service stopped")
            else:
                await op.fail(svc.error or "Stop failed")
    except Exception as exc:
        log.exception("Failed to stop service %s", svc.key)
        svc.transition(State.FAILED, error="Stop failed unexpectedly; use force stop")
        await fire(Event(name="service.state", payload=svc.to_dict()))
        if op:
            await op.fail(str(exc))
        if error_log:
            error_log.log(
                "backend",
                "lifecycle",
                f"Failed to stop service {svc.key}",
                detail={"error": str(exc)},
                context=_svc_ctx,
            )

    return {"ok": svc.state == State.STOPPED, "state": svc.state.value}


async def reinstall_deps(
    svc: ServiceInstance,
    services: ServiceManager,
    events: EventManager,
    ports: PortAllocator,
    error_log: ErrorLog | None = None,
) -> dict[str, Any]:
    """Force-reinstall node_modules by removing the volume and recreating the container.

    Caller must hold svc.lock. Only works on compose services with an app_dir
    (i.e. vite services that use a named node_modules volume).

    Emits operation.progress SSE events for each step (6 total, determinate).
    Broadcasts service.state as the service transitions through states,
    and service.deps on completion to clear the staleness badge.
    """
    from supervisor.serve.deps import check_deps_staleness, resolve_container_name, resolve_host_app_dir
    from supervisor.serve.docker import compose_project_name, volume_rm
    from supervisor.serve.operation_progress import operation_progress

    app_dir = svc.metadata.get("app_dir")
    worktree = svc.metadata.get("worktree")
    assert isinstance(worktree, str), f"service {svc.key} missing 'worktree' metadata"
    compose_service = svc.metadata.get("compose_service", "vite")
    project = compose_project_name(svc.branch)
    container = resolve_container_name(project, compose_service)
    host_app = resolve_host_app_dir(worktree, app_dir)
    assert host_app is not None, f"could not resolve host app dir for {svc.key}"
    volume_name = f"{project}_vite_nm"

    async with operation_progress(events, "reinstall-deps", svc.key, "Reinstalling dependencies") as op:
        # Step 1: Check staleness (0/6).
        await op.update("Checking lockfile staleness...", progress=0)
        staleness = await asyncio.to_thread(check_deps_staleness, container, host_app)
        if not staleness["stale"]:
            await op.update("Already up to date", progress=100)
            await fire(Event(name="service.deps", payload={"service_key": svc.key, "stale": False}))
            return {"ok": True, "skipped": True, "reason": "Dependencies already up to date"}

        # Step 2: Stop container (1/6).
        await op.update("Stopping container...", progress=17)
        svc.transition(State.STOPPING)
        await fire(Event(name="service.state", payload=svc.to_dict()))

        from supervisor.serve import docker as dk

        tdd = svc.metadata.get("tdd", False)
        stop_cmd = ["docker", "compose", "-p", project, "-f", str(dk.COMPOSE_FILE)]
        if tdd:
            stop_cmd += ["-f", str(dk.COMPOSE_TDD_FILE)]
        stop_cmd += ["stop", compose_service]
        stop_ok = await asyncio.to_thread(
            subprocess.run,
            stop_cmd,
            capture_output=True,
            text=True,
            timeout=dk.TIMEOUT_COMPOSE_STOP,
        )
        if stop_ok.returncode != 0:
            err = stop_ok.stderr.strip() or f"exit {stop_ok.returncode}"
            svc.transition(State.FAILED, error=f"Stop failed: {err}")
            await fire(Event(name="service.state", payload=svc.to_dict()))
            raise RuntimeError(f"Failed to stop container: {err}")

        # Step 3: Remove container (2/6).
        await op.update("Removing container...", progress=33)
        rm_cmd = ["docker", "compose", "-p", project, "-f", str(dk.COMPOSE_FILE)]
        if tdd:
            rm_cmd += ["-f", str(dk.COMPOSE_TDD_FILE)]
        rm_cmd += ["rm", "-f", compose_service]
        rm_result = await asyncio.to_thread(
            subprocess.run,
            rm_cmd,
            capture_output=True,
            text=True,
            timeout=dk.TIMEOUT_COMPOSE_RM,
        )
        if rm_result.returncode != 0:
            err = rm_result.stderr.strip() or f"exit {rm_result.returncode}"
            svc.transition(State.FAILED, error=f"Remove failed: {err}")
            await fire(Event(name="service.state", payload=svc.to_dict()))
            raise RuntimeError(f"Failed to remove container: {err}")

        svc.transition(State.STOPPED)
        await fire(Event(name="service.state", payload=svc.to_dict()))
        ports.release(svc.key)

        # Step 4: Remove volume (3/6).
        await op.update(f"Removing volume {volume_name}...", progress=50)
        vol_ok, vol_msg = await asyncio.to_thread(volume_rm, volume_name)
        # Volume might not exist (first run) -- only fail on genuine errors.
        if not vol_ok and "No such volume" not in vol_msg:
            svc.transition(State.FAILED, error=f"Volume removal failed: {vol_msg}")
            await fire(Event(name="service.state", payload=svc.to_dict()))
            raise RuntimeError(f"Failed to remove volume: {vol_msg}")

        # Step 5: Recreate container (4/6) -- entrypoint will run npm ci.
        # start_service expects STOPPED/FAILED state and does its own STARTING transition.
        # emit_progress=False: this parent operation already tracks progress.
        await op.update("Recreating container (npm ci will run)...", progress=67)
        start_result = await start_service(
            svc,
            services,
            events,
            ports,
            error_log=error_log,
            emit_progress=False,
        )
        if not start_result.get("ok"):
            # start_service already transitioned to FAILED and broadcast.
            raise RuntimeError(f"Failed to recreate container: {svc.error}")

        # Step 6: Verify (5/6) -- confirm container is running and deps are fresh.
        await op.update("Verifying dependencies...", progress=83)
        # Re-resolve container name since the container was recreated.
        container = resolve_container_name(project, compose_service)
        verify = await asyncio.to_thread(check_deps_staleness, container, host_app)

        await fire(Event(name="service.deps", payload={"service_key": svc.key, "stale": verify["stale"]}))

        if verify["stale"]:
            log.warning("Deps still stale after reinstall for %s: %s", svc.key, verify)
            # Not a hard failure -- the container is running, npm ci might still be in progress.
            await op.update("Container running, but deps may still be installing", progress=100)
        else:
            await op.update("Dependencies reinstalled successfully", progress=100)

    return {"ok": True, "skipped": False, "stale_after": verify["stale"]}


async def cleanup_branch_services(
    branch: str,
    services: ServiceManager,
    events: EventManager,
    ports: PortAllocator,
    remove_volumes: bool = False,
) -> int:
    """Stop all services for a branch and release resources. Returns count cleaned."""
    from supervisor.serve.operation_progress import OperationTracker

    branch_services = services.list_for_branch(branch)
    count = len(branch_services)

    # Use the branch as the service_key for this branch-level operation.
    op = OperationTracker(events, "cleanup", branch, "Cleaning up branch")
    await op.start()

    try:
        if count > 0:
            await op.update(f"Stopping {count} service(s)...")
        else:
            await op.update("No services to stop")

        async def _stop_one(svc: ServiceInstance) -> None:
            # Acquire per-service lock to prevent concurrent state mutation
            # with HTTP handlers that may be mid-operation on the same service.
            async with svc.lock:
                if svc.state in (State.RUNNING, State.STARTING, State.STOPPING):
                    if svc.state == State.RUNNING:
                        svc.transition(State.STOPPING)
                    if svc.service_type == "supabase":
                        wt = Path(svc.metadata.get("worktree", ""))
                        await asyncio.to_thread(sb.stop, wt)
                        # Undo the port-patched config.toml so git status stays clean after cleanup.
                        await asyncio.to_thread(sb.restore_config, wt)
                        ports.release_supabase_slot(svc.branch)
                    elif svc.service_type == "compose":
                        from supervisor.serve import docker as dk

                        compose_service = svc.metadata.get("compose_service", "")
                        tdd = svc.metadata.get("tdd", False)
                        await asyncio.to_thread(dk.compose_down, branch, compose_service, tdd)
                        ports.release(svc.key)
                    elif svc.service_type == "compose-native":
                        from supervisor.serve import docker as dk

                        compose_file = Path(svc.metadata.get("compose_file", ""))
                        compose_service = svc.metadata.get("compose_service", "")
                        project_name = f"veliu-{dk.compose_project_name(svc.branch)}"
                        await asyncio.to_thread(
                            dk.compose_native_down,
                            compose_file,
                            compose_service,
                            project_name,
                        )
                        ports.release_native(svc.key)
                    if svc.state != State.STOPPED:  # type: ignore[comparison-overlap]
                        svc.transition(State.STOPPED)
                services.unregister(svc.key)

        results = await asyncio.gather(
            *[_stop_one(svc) for svc in branch_services],
            return_exceptions=True,
        )
        # Log individual failures but continue -- cleanup is best-effort.
        for svc, result in zip(branch_services, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "Failed to stop service %s during cleanup: %s",
                    svc.key,
                    result,
                )

        if remove_volumes:
            from supervisor.serve import docker as dk

            await op.update("Removing volumes...")
            await asyncio.to_thread(dk.compose_down, branch, remove_volumes=True)

        await op.complete(f"Cleaned up {count} service(s)")
    except BaseException as exc:
        await op.fail(str(exc))
        raise

    await fire(Event(name="branch.switch", payload={"branch": branch, "action": "cleanup"}))
    return count


async def list_remaining_volumes(qualified: str) -> list[str]:
    """List Docker volumes remaining after cleanup for a branch."""
    from supervisor.serve.docker import compose_project_name

    project = compose_project_name(qualified)
    volumes_removed: list[str] = []
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "volume", "ls", "--filter", f"name={project}_", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        remaining = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not remaining:
            volumes_removed.append(f"{project} (all volumes)")
    except Exception:
        pass
    return volumes_removed


def unregister_service_ports(svc: ServiceInstance, ports: PortAllocator) -> None:
    """Release allocated ports/slots for a service before unregistering."""
    if svc.service_type == "supabase":
        ports.release_supabase_slot(svc.branch)
    else:
        ports.release(svc.key)


async def setup_branch_services(
    qualified: str,
    services: ServiceManager,
    events: EventManager,
) -> list[dict[str, Any]]:
    """Register all services from .services.json for a branch.

    Returns list of registered service dicts.
    """
    from supervisor.serve.templates import load_services_config, resolve_placeholders

    repo, branch = qualified.split(":", 1)
    svc_defs = await asyncio.to_thread(load_services_config, repo, branch)
    if svc_defs is None:
        return []  # Caller should check and raise 404

    resolved = resolve_placeholders(svc_defs, qualified, repo, branch)
    registered = []

    for svc_def in resolved:
        key = svc_def["key"]
        existing = services.get(key)
        if existing:
            registered.append(existing.to_dict())
            continue

        instance = ServiceInstance(
            key=key,
            service_type=svc_def["service_type"],
            branch=qualified,
            display_name=svc_def["display_name"],
            depends_on=svc_def.get("depends_on", []),
            metadata=svc_def.get("metadata", {}),
        )
        services.register(instance)
        await fire(Event(name="service.state", payload=instance.to_dict()))
        registered.append(instance.to_dict())

    return registered


def _maybe_schedule_deps_check(svc: ServiceInstance, events: EventManager) -> None:
    """Schedule a background deps staleness check if the service uses a lockfile volume.

    Only applies to compose services with an app_dir (i.e. vite services).
    Non-blocking, non-fatal -- failures are logged but don't affect the service.
    """
    app_dir = svc.metadata.get("app_dir")
    worktree = svc.metadata.get("worktree")
    if not app_dir or not worktree:
        return

    async def _check() -> None:
        from supervisor.serve.deps import check_deps_staleness, resolve_container_name, resolve_host_app_dir
        from supervisor.serve.docker import compose_project_name

        host_app = resolve_host_app_dir(worktree, app_dir)
        if not host_app:
            return
        project = compose_project_name(svc.branch)
        compose_service = svc.metadata.get("compose_service", "vite")
        container = resolve_container_name(project, compose_service)

        try:
            result = await asyncio.to_thread(check_deps_staleness, container, host_app)
            await fire(Event(name="service.deps", payload={"service_key": svc.key, "stale": result["stale"]}))
        except Exception:
            log.exception("Background deps check failed for %s", svc.key)

    asyncio.create_task(_check())


# -- Private helpers -------------------------------------------------------


async def _start_supabase(
    svc: ServiceInstance,
    events: EventManager,
    ports: PortAllocator,
    *,
    error_log: ErrorLog | None = None,
    tracker: OperationTracker | None = None,
) -> None:
    """Start a Supabase service instance."""
    _svc_ctx = {"service_key": svc.key, "service_type": svc.service_type, "branch": svc.branch}
    wt = Path(svc.metadata["worktree"])
    slot_result = ports.get_supabase_slot(svc.branch)
    if not slot_result:
        _slot, slot_ports = ports.allocate_supabase_slot(svc.branch)
    else:
        _, slot_ports = slot_result

    project_id = sb.project_id_for(svc.branch)
    callback = _make_output_callback(tracker, asyncio.get_running_loop())

    def _start() -> tuple[bool, str]:
        sb.patch_config(wt, slot_ports, project_id)
        sb.skip_worktree_config(wt)
        return sb.start_streaming(wt, callback)

    success, msg = await asyncio.to_thread(_start)

    if success:
        status_dict = await asyncio.to_thread(sb.status, wt)
        if status_dict:
            details = sb.extract_connection_details(status_dict)
            svc.metadata["connection"] = details
            svc.port = slot_ports["api_port"]
        svc.transition(State.RUNNING)

        # Apply migrations in background (non-blocking, non-fatal).
        # Capture error_log ref for the closure.
        _el = error_log

        async def _migrate() -> None:
            ok, migrate_msg = await asyncio.to_thread(sb.apply_migrations, wt)
            if not ok:
                parsed = sb.parse_migration_output(migrate_msg)
                svc.metadata["migration_error"] = parsed
                await fire(Event(name="service.state", payload=svc.to_dict()))
                if _el:
                    _el.log(
                        "backend",
                        "migration",
                        f"Migration failed for {svc.key}",
                        detail={"raw": migrate_msg, "parsed": parsed},
                        context=_svc_ctx,
                    )
            else:
                # Clear any stale error from a previous attempt.
                svc.metadata.pop("migration_error", None)
                await fire(Event(name="service.state", payload=svc.to_dict()))

        asyncio.create_task(_migrate())
    else:
        # Start failed -- restore config.toml to committed state.
        await asyncio.to_thread(sb.restore_config, wt)
        svc.transition(State.FAILED, error=msg)
        ports.release_supabase_slot(svc.branch)
        if error_log:
            error_log.log(
                "backend",
                "service",
                f"Supabase start failed for {svc.key}",
                detail={"error": msg},
                context=_svc_ctx,
            )


async def _start_compose(
    svc: ServiceInstance,
    ports: PortAllocator,
    *,
    error_log: ErrorLog | None = None,
    tracker: OperationTracker | None = None,
) -> None:
    """Start a Docker Compose service instance."""
    from supervisor.serve import docker as dk
    from supervisor.serve.env import resolve_supabase_env

    _svc_ctx = {"service_key": svc.key, "service_type": svc.service_type, "branch": svc.branch}
    wt = Path(svc.metadata["worktree"])
    compose_service = svc.metadata["compose_service"]
    tdd = svc.metadata.get("tdd", False)

    sb_env = resolve_supabase_env(svc.branch)
    if not sb_env:
        svc.transition(State.FAILED, error="Supabase connection details not available")
        if error_log:
            error_log.log(
                "backend",
                "service",
                f"Compose start failed for {svc.key}: no Supabase env",
                detail={"error": "Supabase connection details not available"},
                context=_svc_ctx,
            )
        return

    port = ports.allocate(svc.key)
    svc.port = port

    if compose_service in ("vite", "tdd-vite-bag", "tdd-vite-orders"):
        app_dir = svc.metadata.get("app_dir", "bag.veliu.com")
        env = dk.build_vite_env(
            svc.branch,
            wt,
            app_dir,
            port,
            int(sb_env["api_port"]),
            str(sb_env["anon_key"]),
            compose_service=compose_service,
        )
    else:
        env = dk.build_functions_env(
            svc.branch,
            wt,
            int(sb_env["api_port"]),
            str(sb_env["anon_key"]),
            str(sb_env["service_role_key"]),
            compose_service=compose_service,
        )

    callback = _make_output_callback(tracker, asyncio.get_running_loop())
    success, msg = await asyncio.to_thread(
        dk.compose_up_streaming,
        svc.branch,
        compose_service,
        env,
        callback,
        tdd,
    )
    if success:
        svc.transition(State.RUNNING)
    else:
        svc.transition(State.FAILED, error=msg)
        ports.release(svc.key)
        if error_log:
            error_log.log(
                "backend",
                "service",
                f"Compose start failed for {svc.key}",
                detail={"error": msg, "compose_service": compose_service},
                context=_svc_ctx,
            )


async def _start_compose_native(
    svc: ServiceInstance,
    ports: PortAllocator,
    *,
    error_log: ErrorLog | None = None,
    tracker: OperationTracker | None = None,
) -> None:
    """Start a native Docker Compose service instance."""
    from supervisor.serve import docker as dk

    _svc_ctx = {"service_key": svc.key, "service_type": svc.service_type, "branch": svc.branch}
    compose_file = Path(svc.metadata["compose_file"])
    compose_service = svc.metadata["compose_service"]
    port_env = svc.metadata.get("port_env")

    port = ports.allocate_native(
        svc.key,
        str(compose_file),
        compose_service,
    )
    svc.port = port
    native_env: dict[str, str] = {}
    if port_env:
        native_env[port_env] = str(port)

    callback = _make_output_callback(tracker, asyncio.get_running_loop())
    project_name = f"veliu-{dk.compose_project_name(svc.branch)}"
    success, msg = await asyncio.to_thread(
        dk.compose_native_up_streaming,
        compose_file,
        compose_service,
        project_name,
        native_env,
        callback,
    )
    if success:
        svc.transition(State.RUNNING)
    else:
        svc.transition(State.FAILED, error=msg)
        ports.release_native(svc.key)
        if error_log:
            error_log.log(
                "backend",
                "service",
                f"Compose-native start failed for {svc.key}",
                detail={"error": msg, "compose_service": compose_service},
                context=_svc_ctx,
            )


async def _stop_supabase(
    svc: ServiceInstance,
    ports: PortAllocator,
    force: bool = False,
    tracker: OperationTracker | None = None,
) -> None:
    """Stop a Supabase service instance."""
    wt = Path(svc.metadata["worktree"])
    callback = _make_output_callback(tracker, asyncio.get_running_loop())
    success, msg = await asyncio.to_thread(sb.stop_streaming, wt, callback)
    if success or force:
        # Undo the port-patched config.toml so git status stays clean after stop.
        await asyncio.to_thread(sb.restore_config, wt)
        svc.transition(State.STOPPED)
        ports.release_supabase_slot(svc.branch)
    else:
        svc.transition(State.FAILED, error=msg)


async def _stop_compose(
    svc: ServiceInstance,
    ports: PortAllocator,
    force: bool = False,
    tracker: OperationTracker | None = None,
) -> None:
    """Stop a Docker Compose service instance."""
    from supervisor.serve import docker as dk

    compose_service = svc.metadata["compose_service"]
    tdd = svc.metadata.get("tdd", False)
    callback = _make_output_callback(tracker, asyncio.get_running_loop())
    success, msg = await asyncio.to_thread(
        dk.compose_down_streaming,
        svc.branch,
        compose_service,
        callback,
        tdd,
    )
    if success or force:
        svc.transition(State.STOPPED)
        ports.release(svc.key)
    else:
        svc.transition(State.FAILED, error=msg)


async def _stop_compose_native(
    svc: ServiceInstance,
    ports: PortAllocator,
    force: bool = False,
    tracker: OperationTracker | None = None,
) -> None:
    """Stop a native Docker Compose service instance."""
    from supervisor.serve import docker as dk

    compose_file = Path(svc.metadata.get("compose_file", ""))
    compose_service = svc.metadata["compose_service"]
    project_name = f"veliu-{dk.compose_project_name(svc.branch)}"
    callback = _make_output_callback(tracker, asyncio.get_running_loop())

    success, msg = await asyncio.to_thread(
        dk.compose_native_down_streaming,
        compose_file,
        compose_service,
        project_name,
        callback,
    )
    if success or force:
        svc.transition(State.STOPPED)
        ports.release_native(svc.key)
    else:
        svc.transition(State.FAILED, error=msg)
