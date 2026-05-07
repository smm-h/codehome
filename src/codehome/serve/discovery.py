"""Re-discover services that survived a server restart.

On startup the in-memory ServiceManager is empty, but serve-ports.json
may still hold port allocations from a previous session.  This module
probes those allocations and either re-registers them or releases stale
entries.

It also runs a secondary Docker-level scan (``discover_docker``) that
picks up any Compose containers started outside ``v server`` -- e.g. a
manual ``docker compose up`` or a ``make`` invocation -- so the DOM
inspector's resolver can find them.  This scan is read-only: it never
starts or stops containers, only registers matches in ``ServiceManager``.
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

        _layout = _svc_reg.get_typed("supervisor.layout", ProjectLayout)
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


# ---------------------------------------------------------------------------
# Docker-level discovery (external `docker compose up` detection)
# ---------------------------------------------------------------------------


async def discover_docker() -> list[dict[str, object]]:
    """Find running Compose containers that are not yet in ``ServiceManager``.

    Closes the gap where ``docker compose up`` is invoked outside the
    server (e.g. via ``make up`` or a raw ``docker compose up`` during a
    debugging session) and the ``v inspect`` resolver can therefore never
    find the resulting port.

    Strategy: list every running container labeled with a Compose project
    (read-only), map ``project -> qualified branch`` by inverting
    ``docker.compose_project_name`` over the branches on disk, look up the
    matching service definition from that branch's ``.services.json``, and
    register any service whose key is not already in ``ServiceManager``.

    Returns the list of service dicts that were *newly* registered (same
    shape as ``ServiceInstance.to_dict()``).  Never starts or stops
    containers; never writes to the ``PortAllocator`` (it owns its own
    state machine).  Safe to call repeatedly from startup and from the
    ``POST /api/services/discover`` endpoint.
    """
    from codehome.serve import docker as dk
    from codehome.supervisor.ops.branches import list_branches

    # 1. List candidate containers.  Offload to a thread -- subprocess I/O.
    containers = await asyncio.to_thread(dk.docker_inspect_compose_containers)
    if not containers:
        return []

    # 2. Build a project-name -> qualified-branch map.  Multiple branches
    # could in principle collapse to the same Compose project name (colon
    # vs. dash), so we keep the last write; duplicates here would already
    # be a misconfiguration.
    project_to_branch: dict[str, tuple[str, str, str]] = {}
    branches = await asyncio.to_thread(list_branches)
    for b in branches:
        qualified = str(b.get("qualified") or "")
        repo = str(b.get("repo") or "")
        name = str(b.get("branch") or "")
        if not qualified or not repo or not name:
            continue
        proj = dk.compose_project_name(qualified)
        project_to_branch[proj] = (qualified, repo, name)

    # 3. Per-branch template cache so we don't re-read the same JSON for
    # every container in the same project.
    template_cache: dict[str, list[dict[str, object]]] = {}

    def _resolved_services_for(qualified: str, repo: str, name: str) -> list[dict[str, object]]:
        if qualified in template_cache:
            return template_cache[qualified]
        raw = load_services_config(repo, name)
        if raw is None:
            template_cache[qualified] = []
            return []
        resolved = resolve_placeholders(raw, qualified, repo, name)
        template_cache[qualified] = resolved
        return resolved

    discovered: list[dict[str, object]] = []

    for c in containers:
        project = str(c.get("project") or "")
        compose_service = str(c.get("service") or "")
        if not project or not compose_service:
            continue

        matched = project_to_branch.get(project)
        if not matched:
            # Container doesn't correspond to any known supervisor branch
            # (e.g. Supabase CLI's own containers, unrelated dev containers).
            # Skip -- strict naming-convention match is the spec's guardrail
            # against false positives.
            continue
        qualified, repo, name = matched

        # Find the service definition whose metadata.compose_service matches.
        # The container name format is `<project>-<compose_service>-<n>`;
        # the supervisor key is `<qualified>/<key_suffix>`.  The two are
        # bridged through the template's metadata.compose_service field.
        resolved_services = await asyncio.to_thread(
            _resolved_services_for,
            qualified,
            repo,
            name,
        )
        def_entry: dict[str, object] | None = None
        for svc_def in resolved_services:
            metadata = svc_def.get("metadata") or {}
            if isinstance(metadata, dict) and metadata.get("compose_service") == compose_service:
                def_entry = svc_def
                break
        if def_entry is None:
            # The container's compose service isn't described by the branch's
            # template.  Register nothing -- we have no display name, no
            # metadata, no stable key_suffix.
            continue

        key = str(def_entry.get("key") or "")
        if not key:
            continue
        if services.get(key) is not None:
            # Already known (either from `discover_running` moments ago or
            # from a previous call to this function).  Don't touch it --
            # avoid clobbering live state machine metadata.
            continue

        # Pick the right host port.  Prefer the explicit `internal_port`
        # metadata when present (compose-native services carry it);
        # otherwise fall back to 5173 for Vite, or the first mapped port.
        host_ports = c.get("host_ports") or {}
        if not isinstance(host_ports, dict):
            host_ports = {}
        raw_meta = def_entry.get("metadata") or {}
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}

        port: int | None = None
        internal_port = metadata.get("internal_port")
        try:
            internal_port_int = int(internal_port) if internal_port is not None else None
        except (TypeError, ValueError):
            internal_port_int = None
        if internal_port_int is not None and internal_port_int in host_ports:
            port = int(host_ports[internal_port_int])
        elif compose_service.startswith("vite") and 5173 in host_ports:
            port = int(host_ports[5173])
        elif host_ports:
            # Deterministic fallback: pick the smallest container port
            # that has a host binding.  Better than relying on dict order.
            first = sorted(host_ports.keys())[0]
            port = int(host_ports[first])

        service_type = str(def_entry.get("service_type") or "compose")
        display_name = str(def_entry.get("display_name") or compose_service.title())
        depends_on = def_entry.get("depends_on") or []
        if not isinstance(depends_on, list):
            depends_on = []

        instance = ServiceInstance(
            key=key,
            service_type=service_type,
            branch=qualified,
            display_name=display_name,
            depends_on=list(depends_on),
            state=State.STOPPED,  # Transitioned to RUNNING below.
            metadata=metadata,
        )
        instance.port = port
        instance.state = State.RUNNING
        instance.started_at = time.time()
        try:
            services.register(instance)
        except ValueError:
            # Race: registered concurrently between our `services.get` check
            # above and now.  Treat as already-known; skip.
            continue

        svc_dict = instance.to_dict()
        discovered.append(svc_dict)
        _log.info(
            "docker discovery registered %s (container=%s port=%s)",
            key,
            c.get("name"),
            port,
        )

    # Broadcast so SSE subscribers (dashboard UI, test harness) see the
    # newly-appeared services the same way they see server-launched ones.
    for svc_dict in discovered:
        await fire(Event(name="service.state", payload=svc_dict))

    return discovered
