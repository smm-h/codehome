"""Re-discover services that survived a server restart.

On startup the in-memory ServiceManager is empty, but serve-ports.json
may still hold port allocations from a previous session.  This module
probes those allocations and either re-registers them or releases stale
entries.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from pathlib import Path

from codehome.bus import Event, fire
from codehome.serve import supabase as sb
from codehome.serve.ports import ports
from codehome.serve.services import ServiceInstance, State, services
from codehome.serve.templates import load_services_config, resolve_placeholders

_log = logging.getLogger(__name__)


def _port_in_use(port: int) -> bool:
    """Check if something is listening on localhost:port via a connect attempt."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
        return True
    except (OSError, ConnectionRefusedError):
        return False


async def discover_running() -> None:
    """Re-register services whose containers survived a server restart.

    On startup the in-memory ServiceManager is empty, but serve-ports.json
    may still hold port allocations from a previous session.  This function
    reads those allocations, probes whether the underlying containers/ports
    are still alive, and either re-registers them as RUNNING or releases the
    stale allocation.
    """
    from codehome.serve import docker as dk

    alloc = ports.all_allocations()
    compose_map: dict[str, int] = alloc.get("compose", {})  # type: ignore[assignment]
    supabase_map: dict[str, dict[str, int]] = alloc.get("supabase", {})  # type: ignore[assignment]
    discovered: list[dict[str, object]] = []

    # -- Compose services -------------------------------------------------
    # Group service keys by branch so we only call compose_ps once per branch.
    # Key format: "{branch}/{service_name}" (e.g. "bag:navchat/vite").
    branch_keys: dict[str, list[tuple[str, str, int]]] = {}
    for service_key, port in compose_map.items():
        # Split on last "/" to separate branch from service name.
        if "/" not in service_key:
            # Malformed key -- release it.
            ports.release(service_key)
            continue
        branch, service_name = service_key.rsplit("/", 1)
        branch_keys.setdefault(branch, []).append((service_key, service_name, port))

    # Pre-load service templates per branch so compose services can recover
    # depends_on and richer metadata (display_name, etc.) from .services.json.
    # Keyed by (branch, compose_service) for O(1) lookup.
    _template_by_compose_svc: dict[tuple[str, str], dict[str, object]] = {}
    for branch in branch_keys:
        if ":" not in branch:
            continue
        repo, br = branch.split(":", 1)
        raw = await asyncio.to_thread(load_services_config, repo, br)
        if raw is not None:
            resolved = resolve_placeholders(raw, branch, repo, br)
            for svc_def in resolved:
                meta = svc_def.get("metadata") or {}
                cs = meta.get("compose_service", "") if isinstance(meta, dict) else ""
                if cs:
                    _template_by_compose_svc[(branch, cs)] = svc_def

    for branch, key_list in branch_keys.items():
        # Check if any containers are running for this branch's project.
        containers = await asyncio.to_thread(dk.compose_ps, branch)
        running_services = {c.get("Service", "") for c in containers if c.get("State") == "running"}

        for service_key, service_name, port in key_list:
            # A TDD compose service key starts with "tdd-".
            is_tdd = service_name.startswith("tdd-")
            found = service_name in running_services

            # TDD services use a separate compose file; check that too.
            if not found and is_tdd:
                tdd_containers = await asyncio.to_thread(dk.compose_ps, branch, tdd=True)
                tdd_running = {c.get("Service", "") for c in tdd_containers if c.get("State") == "running"}
                found = service_name in tdd_running

            if not found:
                ports.release(service_key)
                continue

            # Recover depends_on and metadata from the branch's .services.json
            # template if available (mirrors what setup_branch_services does).
            tpl = _template_by_compose_svc.get((branch, service_name))
            if tpl is not None:
                depends_on = tpl.get("depends_on", [])
                if not isinstance(depends_on, list):
                    depends_on = []
                display = str(tpl.get("display_name") or service_name.replace("-", " ").title())
                raw_meta = tpl.get("metadata") or {}
                metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
                # Ensure tdd flag is present regardless of template content.
                metadata.setdefault("compose_service", service_name)
                metadata["tdd"] = is_tdd
            else:
                depends_on = []
                display = service_name.replace("-", " ").title()
                metadata = {"compose_service": service_name, "tdd": is_tdd}

            # Container is running -- re-register.
            instance = ServiceInstance(
                key=service_key,
                service_type="compose",
                branch=branch,
                display_name=display,
                depends_on=list(depends_on),
                state=State.STOPPED,  # Will transition to RUNNING below.
                metadata=metadata,
            )
            instance.port = port
            # Bypass normal state machine: set directly since we know it's running.
            instance.state = State.RUNNING
            instance.started_at = time.time()
            try:
                services.register(instance)
                discovered.append(instance.to_dict())
            except ValueError:
                pass  # Already registered (shouldn't happen on fresh startup).

    # -- Supabase slots ----------------------------------------------------
    for branch, slot_ports in supabase_map.items():
        api_port = slot_ports.get("api_port")
        if not api_port:
            ports.release_supabase_slot(branch)
            continue

        # Check if the Supabase API port is responding (no worktree needed).
        alive = await asyncio.to_thread(_port_in_use, api_port)
        if not alive:
            ports.release_supabase_slot(branch)
            continue

        # Supabase is running -- re-register with full connection metadata.
        # Resolve worktree path from branch name so stop helpers can find it.
        key = f"{branch}/supabase"
        from codehome.service_protocols import ProjectLayout
        from codehome.state.service_registry import services as _svc_reg

        _layout = _svc_reg.get_typed("core.layout", ProjectLayout)
        if _layout is None:
            ports.release_supabase_slot(branch)
            continue
        repo, br = branch.split(":", 1)
        wt_path = _layout.worktree_path(repo, br)
        wt = str(wt_path)

        # Best-effort: recover full connection details (anon_key,
        # service_role_key, db_url, studio_url, graphql_url) via
        # `supabase status`.  Fall back to partial api_url-only metadata
        # if the status command fails.
        connection: dict[str, str] = {"api_url": f"http://127.0.0.1:{api_port}"}
        try:
            status_dict = await asyncio.to_thread(sb.status, wt_path)
            if status_dict:
                connection = sb.extract_connection_details(status_dict)
        except Exception:
            _log.debug("supabase status failed for %s; using partial metadata", branch)

        instance = ServiceInstance(
            key=key,
            service_type="supabase",
            branch=branch,
            display_name="Supabase",
            state=State.STOPPED,
            metadata={
                "worktree": wt,
                "connection": connection,
            },
        )
        instance.port = api_port
        instance.state = State.RUNNING
        instance.started_at = time.time()
        try:
            services.register(instance)
            discovered.append(instance.to_dict())
        except ValueError:
            pass

    # -- Compose-native services -------------------------------------------
    # compose_native map stores {key: {port, compose_file, compose_service}}.
    native_map: dict[str, dict[str, object]] = alloc.get("compose_native", {})  # type: ignore[assignment]

    # Group by (compose_file, project_name) to avoid duplicate compose_ps calls.
    native_projects: dict[tuple[str, str], list[tuple[str, dict[str, object]]]] = {}
    for service_key, info in native_map.items():
        if "/" not in service_key:
            ports.release_native(service_key)
            continue
        branch = service_key.rsplit("/", 1)[0]
        project_name = f"veliu-{dk.compose_project_name(branch)}"
        compose_file = str(info.get("compose_file", ""))
        group_key = (compose_file, project_name)
        native_projects.setdefault(group_key, []).append((service_key, info))

    for (compose_file, project_name), entries in native_projects.items():
        cf_path = Path(compose_file)
        if not cf_path.exists():
            for service_key, _ in entries:
                ports.release_native(service_key)
            continue

        containers = await asyncio.to_thread(dk.compose_native_ps, cf_path, project_name)
        running_services = {c.get("Service", "") for c in containers if c.get("State") == "running"}

        for service_key, info in entries:
            compose_service = str(info.get("compose_service", ""))
            svc_port: int | None = info.get("port")  # type: ignore[assignment]
            if compose_service not in running_services:
                ports.release_native(service_key)
                continue

            branch = service_key.rsplit("/", 1)[0]
            display = compose_service.replace("-", " ").title()
            instance = ServiceInstance(
                key=service_key,
                service_type="compose-native",
                branch=branch,
                display_name=display,
                state=State.STOPPED,
                metadata={
                    "compose_file": compose_file,
                    "compose_service": compose_service,
                },
            )
            instance.port = svc_port
            instance.state = State.RUNNING
            instance.started_at = time.time()
            try:
                services.register(instance)
                discovered.append(instance.to_dict())
            except ValueError:
                pass

    # -- Broadcast discovered services ------------------------------------
    for svc_dict in discovered:
        await fire(Event(name="service.state", payload=svc_dict))
