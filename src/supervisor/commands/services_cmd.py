"""v services: manage per-branch services (supabase, vite, edge).

Thin CLI over the server's /api/services endpoints. Services are
registered in the server's in-memory ServiceManager, so most commands
require `v server` to be running -- the exception is `delete-volumes`,
which falls back to a local docker compose down -v when the server is
offline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from supervisor.cli_helpers import dispatch_subcommand, resolve_from_args, resolve_optional
from supervisor.utils import die

if TYPE_CHECKING:
    import argparse


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cmd_services(args: argparse.Namespace) -> None:
    """Dispatch to services subcommand.

    .. feature:: Manages per-branch docker services (supabase stack, vite, edge runtime)
    .. feature:: CLI over the server's /api/services endpoints
    .. feature:: ``delete-volumes`` falls back to local docker compose when server is offline

    .. rule:: Most commands require ``v server`` to be running
    .. rule:: Use ``v services restart functions`` after editing edge function source
    .. rule:: Use ``docker ps`` to discover branch Vite container ports (``lsof`` won't find them)
    """
    dispatch_subcommand(
        args,
        "services_command",
        {
            "list": _services_list,
            "restart": _services_restart,
            "start": _services_start,
            "stop": _services_stop,
            "setup": _services_setup,
            "delete-volumes": _services_delete_volumes,
            "status": _services_status,
            "logs": _services_logs,
            "migrate": _services_migrate,
            "deps": _services_deps,
            "orphans": _services_orphans,
        },
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fetch_services() -> list[dict[str, Any]]:
    """Fetch all registered services from the server."""
    from supervisor import http_client
    from supervisor.dispatch import server_running

    if not server_running():
        die("v server is not running; start it first with `v server`")
    result = http_client.get("/api/services")
    if not isinstance(result, list):
        die(f"unexpected /api/services response: {type(result).__name__}")
    return result


def _resolve_service_key(branch_qualified: str, target: str) -> str:
    """Find the full service key by matching the suffix against registered services."""
    services = _fetch_services()
    matches = [
        s for s in services if s.get("branch") == branch_qualified and s.get("key", "").split("/", 1)[-1] == target
    ]
    if not matches:
        die(f"no service matching '{target}' for {branch_qualified}")
    if len(matches) > 1:
        die(f"ambiguous target '{target}': {[s['key'] for s in matches]}")
    return str(matches[0]["key"])


def _print_table(services: list[dict[str, Any]]) -> None:
    """Print a formatted table of services."""
    header = ("SERVICE", "BRANCH", "STATE", "PORT", "UPTIME")
    rows: list[tuple[str, str, str, str, str]] = []
    for s in services:
        key = s.get("key", "?")
        short = key.split("/", 1)[-1]
        branch = s.get("branch", "?")
        state = s.get("state", "?")
        port = s.get("port")
        uptime = s.get("uptime")
        rows.append(
            (
                short,
                branch,
                str(state),
                str(port) if port is not None else "-",
                _fmt_uptime(uptime) if uptime else "-",
            )
        )
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(5)]
    print("  ".join(header[i].ljust(widths[i]) for i in range(5)))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(5)))


def _print_grouped(services: list[dict[str, Any]]) -> None:
    """Print services grouped by branch with a summary line per branch."""
    from collections import defaultdict

    by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in services:
        by_branch[s.get("branch", "?")].append(s)

    for branch, svcs in sorted(by_branch.items()):
        total = len(svcs)
        running = sum(1 for s in svcs if s.get("state") == "running")
        if running == total:
            summary = "running"
        elif running == 0:
            summary = "stopped"
        else:
            summary = f"{running}/{total} running"
        print(f"{branch}  {total} service(s), {summary}")
        for s in svcs:
            key = s.get("key", "?").split("/", 1)[-1]
            state = s.get("state", "?")
            port = s.get("port")
            port_str = f":{port}" if port is not None else ""
            print(f"  {key}  {state}{port_str}")


def _topo_sort(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topological sort of services by depends_on field.

    Services with no (or satisfied) dependencies come first.
    Returns a new list in dependency order (start order).
    """
    by_key: dict[str, dict[str, Any]] = {s["key"]: s for s in services}
    key_set = set(by_key)
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()

    def _visit(key: str) -> None:
        if key in visited or key not in key_set:
            return
        visited.add(key)
        for dep in by_key[key].get("depends_on") or []:
            _visit(dep)
        ordered.append(by_key[key])

    for key in by_key:
        _visit(key)
    return ordered


def _services_for_branch(branch_qualified: str) -> list[dict[str, Any]]:
    """Fetch all services for a specific branch."""
    return [s for s in _fetch_services() if s.get("branch") == branch_qualified]


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    return f"{s // 86400}d"


# States that indicate the operation is still in progress.
_TRANSIENT_STATES = {"starting", "stopping"}


def _poll_until_settled(key: str, t0: float, timeout_s: int) -> None:
    """Poll GET /api/services/{key} every 2s until state leaves STARTING/STOPPING.

    Times out after *timeout_s* seconds (per-service) with a clear error.
    """
    from supervisor import http_client

    while True:
        time.sleep(2)
        elapsed = time.time() - t0
        detail = http_client.get(f"/api/services/{key}")
        state = detail.get("state", "unknown")
        if state not in _TRANSIENT_STATES:
            if state in ("running", "stopped"):
                print(f"done ({elapsed:.1f}s)")
            else:
                print(f"failed ({elapsed:.1f}s) -- state: {state}")
            return
        if elapsed > timeout_s:
            # Determine what we were waiting for based on the transient state.
            target = "running" if state == "starting" else "stopped"
            die(f"timed out after {timeout_s}s waiting for {key} to reach {target} (current: {state})")


def _send_action(
    key: str, action: str, poll: bool, *, body: dict[str, Any] | None = None, label: str = "", poll_timeout_s: int = 120
) -> None:
    """POST an action (start/stop/restart) to a service and handle poll/no-poll.

    With poll=True: prints "accepted", then polls until settled.
    With poll=False: prints "accepted (round-trip Xs)" and returns.
    *poll_timeout_s* caps how long we wait per-service when polling.
    """
    from supervisor import http_client

    display = label or key
    timeout = 60 if action == "stop" else 180
    print(f"{action}ing {display} ...", end=" ", flush=True)
    t0 = time.time()
    http_client.post(f"/api/services/{key}/{action}", body=body or {}, timeout=timeout)
    if poll:
        print("accepted ...", end=" ", flush=True)
        _poll_until_settled(key, t0, poll_timeout_s)
    else:
        print(f"accepted (round-trip {time.time() - t0:.1f}s)")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _services_list(args: argparse.Namespace) -> None:
    """List services -- all if no branch selected or --all-branches, filtered if -B given."""
    all_branches = getattr(args, "all_branches", False)
    ctx = resolve_optional(args)
    all_services = _fetch_services()
    # --all-branches overrides -B filtering.
    if ctx and not all_branches:
        matches = [s for s in all_services if s.get("branch") == ctx.qualified]
        if not matches:
            print(f"No services registered for {ctx.qualified}.")
            return
    else:
        matches = all_services
        if not matches:
            print("No services registered.")
            return

    if getattr(args, "group", False):
        _print_grouped(matches)
    else:
        _print_table(matches)


def _services_restart(args: argparse.Namespace) -> None:
    """Restart a service (or all for the branch).

    With --all-services: stop all in reverse dependency order, then start
    all in dependency order. Without it: restart individually via the
    server's /restart endpoint.
    """
    from supervisor import cli_defaults

    all_svcs = getattr(args, "all_services", False)
    target = getattr(args, "target", None)
    poll = args.poll
    poll_timeout_s = int(str(cli_defaults.get("services.poll_timeout_s")))

    if target and all_svcs:
        die("cannot combine TARGET with --all-services")
    if not target and not all_svcs:
        die("either TARGET or --all-services is required for restart")

    ctx = resolve_from_args(args)

    if all_svcs:
        services = _services_for_branch(ctx.qualified)
        if not services:
            die(f"no services registered for {ctx.qualified}")
        ordered = _topo_sort(services)

        # Phase 1: stop all in reverse dependency order.
        print("-- stopping all services --")
        failed: list[str] = []
        for svc in reversed(ordered):
            try:
                _send_action(svc["key"], "stop", poll, poll_timeout_s=poll_timeout_s)
            except SystemExit as exc:
                detail = f": {exc.code}" if isinstance(exc.code, str) else ""
                print(f"FAILED{detail}")
                failed.append(svc["key"])

        # Phase 2: start all in dependency order.
        print("-- starting all services --")
        for svc in ordered:
            try:
                _send_action(svc["key"], "start", poll, poll_timeout_s=poll_timeout_s)
            except SystemExit as exc:
                detail = f": {exc.code}" if isinstance(exc.code, str) else ""
                print(f"FAILED{detail}")
                failed.append(svc["key"])

        if failed:
            die(f"restart failed for: {', '.join(failed)}")
        return

    # Single-target restart via the server's /restart endpoint.
    assert isinstance(target, str)
    key = _resolve_service_key(ctx.qualified, target)
    _send_action(key, "restart", poll, poll_timeout_s=poll_timeout_s)


def _services_start(args: argparse.Namespace) -> None:
    """Start a service, or all services for the branch with --all-services."""
    from supervisor import cli_defaults

    all_svcs = getattr(args, "all_services", False)
    target = getattr(args, "target", None)
    poll = args.poll
    poll_timeout_s = int(str(cli_defaults.get("services.poll_timeout_s")))

    if not target and not all_svcs:
        die("either TARGET or --all-services is required for start")
    if target and all_svcs:
        die("cannot combine TARGET with --all-services")

    ctx = resolve_from_args(args)

    if all_svcs:
        services = _services_for_branch(ctx.qualified)
        if not services:
            die(f"no services registered for {ctx.qualified}")
        ordered = _topo_sort(services)
        failed: list[str] = []
        for svc in ordered:
            try:
                _send_action(svc["key"], "start", poll, poll_timeout_s=poll_timeout_s)
            except SystemExit as exc:
                detail = f": {exc.code}" if isinstance(exc.code, str) else ""
                print(f"FAILED{detail}")
                failed.append(svc["key"])
        if failed:
            die(f"start failed for: {', '.join(failed)}")
        return

    assert isinstance(target, str)
    key = _resolve_service_key(ctx.qualified, target)
    _send_action(key, "start", poll, poll_timeout_s=poll_timeout_s)


def _services_stop(args: argparse.Namespace) -> None:
    """Stop a service, all for the branch, or all across all branches."""
    from supervisor import cli_defaults

    all_svcs = getattr(args, "all_services", False)
    all_branches = getattr(args, "all_branches", False)
    target = getattr(args, "target", None)
    force = getattr(args, "force", False)
    poll = args.poll
    poll_timeout_s = int(str(cli_defaults.get("services.poll_timeout_s")))
    stop_body = {"force": force}

    if all_branches and not all_svcs:
        die("--all-branches requires --all-services")
    if not target and not all_svcs:
        die("either TARGET or --all-services is required for stop")
    if target and all_svcs:
        die("cannot combine TARGET with --all-services")

    if all_branches:
        # Stop all services across every branch.
        all_services = _fetch_services()
        if not all_services:
            die("no services registered on any branch")
        from collections import defaultdict

        by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in all_services:
            by_branch[s.get("branch", "?")].append(s)
        failed: list[str] = []
        for branch, svcs in sorted(by_branch.items()):
            print(f"-- {branch} --")
            ordered = list(reversed(_topo_sort(svcs)))
            for svc in ordered:
                try:
                    label = f"  {svc['key']}{' (force)' if force else ''}"
                    _send_action(svc["key"], "stop", poll, body=stop_body, label=label, poll_timeout_s=poll_timeout_s)
                except SystemExit as exc:
                    detail = f": {exc.code}" if isinstance(exc.code, str) else ""
                    print(f"FAILED{detail}")
                    failed.append(svc["key"])
        if failed:
            die(f"stop failed for: {', '.join(failed)}")
        return

    ctx = resolve_from_args(args)

    if all_svcs:
        services = _services_for_branch(ctx.qualified)
        if not services:
            die(f"no services registered for {ctx.qualified}")
        # Stop in reverse dependency order (dependents first).
        ordered = list(reversed(_topo_sort(services)))
        failed = []
        for svc in ordered:
            try:
                label = f"{svc['key']}{' (force)' if force else ''}"
                _send_action(svc["key"], "stop", poll, body=stop_body, label=label, poll_timeout_s=poll_timeout_s)
            except SystemExit as exc:
                detail = f": {exc.code}" if isinstance(exc.code, str) else ""
                print(f"FAILED{detail}")
                failed.append(svc["key"])
        if failed:
            die(f"stop failed for: {', '.join(failed)}")
        return

    assert isinstance(target, str)
    key = _resolve_service_key(ctx.qualified, target)
    label = f"{key}{' (force)' if force else ''}"
    _send_action(key, "stop", poll, body=stop_body, label=label, poll_timeout_s=poll_timeout_s)


def _services_setup(args: argparse.Namespace) -> None:
    """Register and set up all template services for a branch."""
    from supervisor import http_client

    ctx = resolve_from_args(args)
    print(f"setting up services for {ctx.qualified} ...", end=" ", flush=True)
    start = time.time()
    result = http_client.post(
        f"/api/branches/{ctx.qualified}/services/setup",
        body={},
        timeout=120,
    )
    registered = result.get("services") or []
    elapsed = time.time() - start
    print(f"done ({elapsed:.1f}s)")
    if registered:
        for svc in registered:
            name = svc if isinstance(svc, str) else svc.get("key", svc)
            print(f"  registered: {name}")
    else:
        print("  (no new services registered)")


def _services_delete_volumes(args: argparse.Namespace) -> None:
    """Remove Docker volumes + orphan containers for stopped services.

    All services for the branch must be STOPPED before running this
    command. If any are still running, the command fails with an error
    suggesting the exact stop commands needed.

    Without --all-branches: resolve the branch and delete its volumes.
    With --all-branches: do the above for every branch that has services.

    Delegates to the server if running, otherwise runs docker compose
    down -v directly.
    """
    from supervisor.serve.docker import COMPOSE_FILE, compose_project_name

    all_branches = getattr(args, "all_branches", False)

    if all_branches:
        _delete_volumes_all_branches(COMPOSE_FILE, compose_project_name)
    else:
        ctx = resolve_from_args(args)
        _delete_volumes_for_branch(ctx.qualified, COMPOSE_FILE, compose_project_name)


def _require_all_stopped(branch_qualified: str) -> None:
    """Check that all services for a branch are stopped; die with helpful message if not."""
    from supervisor.dispatch import server_running

    if not server_running():
        # No server means no service state to check; volumes can still
        # be removed via docker compose directly.
        return

    services = _services_for_branch(branch_qualified)
    if not services:
        return

    not_stopped = [s for s in services if s.get("state") not in ("stopped", "failed", None)]
    if not_stopped:
        # Build a summary line: "supabase (RUNNING), vite-bag (STARTING)"
        summaries = []
        for s in not_stopped:
            name = s.get("key", "?").split("/", 1)[-1]
            state = (s.get("state") or "unknown").upper()
            summaries.append(f"{name} ({state})")
        # Build per-service stop commands.
        stop_cmds = []
        for s in not_stopped:
            name = s.get("key", "?").split("/", 1)[-1]
            stop_cmds.append(f"  v services stop {name} -B {branch_qualified} --no-poll")
        die(
            f"Services still running for {branch_qualified}: {', '.join(summaries)}. "
            f"Stop them first:\n" + "\n".join(stop_cmds)
        )


def _delete_volumes_for_branch(branch_qualified: str, compose_file: Path, compose_project_name_fn: Callable[[str], str]) -> None:
    """Validate services are stopped, then remove volumes for a single branch."""
    from supervisor.dispatch import dispatched

    project = compose_project_name_fn(branch_qualified)

    # All services must already be stopped; fail if any are running.
    _require_all_stopped(branch_qualified)

    dispatched(
        server_impl=lambda: _cleanup_via_dashboard(branch_qualified),
        local_impl=lambda: _cleanup_local(project, compose_file),
    )

    # Verify no volumes remain.
    remaining = _list_volumes(project)
    if remaining:
        print(f"warning: {len(remaining)} volume(s) still exist for project '{project}':")
        for vol in remaining:
            print(f"  {vol}")
    else:
        print(f"All volumes removed for project '{project}'.")


def _delete_volumes_all_branches(compose_file: Path, compose_project_name_fn: Callable[[str], str]) -> None:
    """Validate all services are stopped, then remove volumes for every branch."""
    from collections import defaultdict

    from supervisor.dispatch import server_running

    if not server_running():
        die("v server is not running; --all-branches requires a running server to discover branches")

    all_services = _fetch_services()
    if not all_services:
        die("no services registered on any branch")

    by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in all_services:
        by_branch[s.get("branch", "?")].append(s)

    # Pre-validate: all services across all branches must be stopped.
    for branch in sorted(by_branch):
        _require_all_stopped(branch)

    for branch in sorted(by_branch):
        print(f"-- {branch} --")
        _delete_volumes_for_branch(branch, compose_file, compose_project_name_fn)


def _cleanup_via_dashboard(branch_qualified: str) -> None:
    """POST to the server's cleanup endpoint with remove_volumes=True (used by delete-volumes)."""
    from supervisor import http_client

    result = http_client.post(
        "/api/services/cleanup",
        body={"branch": branch_qualified, "remove_volumes": True},
        timeout=30,
    )
    cleaned = result.get("cleaned", 0)
    print(f"Removed volumes for {cleaned} service(s) via server.")


def _cleanup_local(project: str, compose_file: Path) -> None:
    """Run docker compose down -v --remove-orphans directly (no server)."""
    import subprocess

    cmd = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(compose_file),
        "down",
        "-v",
        "--remove-orphans",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        die(f"docker compose down failed: {msg}")
    print(f"Removed volumes for project '{project}'.")


def _list_volumes(project: str) -> list[str]:
    """Return names of Docker volumes matching the project name."""
    import subprocess

    result = subprocess.run(
        ["docker", "volume", "ls", "--filter", f"name={project}_", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line.strip()]


def _services_status(args: argparse.Namespace) -> None:
    """Show detailed status for a single service, including health."""
    from supervisor import http_client
    from supervisor.dispatch import server_running

    if not server_running():
        die("v server is not running; start it first with `v server`")

    ctx = resolve_from_args(args)
    target = args.target
    key = _resolve_service_key(ctx.qualified, target)
    detail = http_client.get(f"/api/services/{key}")

    # Print all key-value pairs from the service detail.
    print(f"Service: {key}")
    for k, v in sorted(detail.items()):
        if k == "key":
            continue
        print(f"  {k}: {v}")

    # Fetch and display health data if available.
    try:
        health_data = http_client.get("/api/monitoring/health")
        svc_health = health_data.get("services", {}).get(key)
        if svc_health:
            status = svc_health.get("status", "unknown")
            checks = svc_health.get("checks", {})
            if checks:
                failed = [name for name, ok in checks.items() if not ok]
                if failed:
                    print(f"  health: {status} ({', '.join(failed)}: down)")
                else:
                    print(f"  health: {status}")
            else:
                print(f"  health: {status}")
        else:
            print("  health: no data")
    except SystemExit:
        # Health endpoint unavailable; skip silently.
        pass


def _services_logs(args: argparse.Namespace) -> None:
    """Show logs for a service via docker logs."""
    import subprocess

    ctx = resolve_from_args(args)
    target = args.target

    # Resolve the service key to find its container metadata.
    from supervisor.dispatch import server_running

    if not server_running():
        die("v server is not running; start it first with `v server`")

    key = _resolve_service_key(ctx.qualified, target)

    # Fetch the service detail to find the container name.
    from supervisor import http_client

    detail = http_client.get(f"/api/services/{key}")
    metadata = detail.get("metadata", {})
    container = metadata.get("container_name") or metadata.get("container")

    if not container:
        # Fall back to docker compose project naming.
        from supervisor.serve.docker import compose_project_name

        project = compose_project_name(ctx.qualified)
        # Target is e.g. "vite-bag", "supabase" -- use as compose service name.
        container = f"{project}-{target}-1"

    tail = getattr(args, "tail", 100)
    cmd = ["docker", "logs", "--tail", str(tail), container]
    if getattr(args, "follow", False):
        cmd.insert(2, "-f")
    subprocess.run(cmd)


def _services_migrate(args: argparse.Namespace) -> None:
    """Re-run Supabase migrations on a running service."""
    from supervisor import http_client

    ctx = resolve_from_args(args)
    target = args.target
    key = _resolve_service_key(ctx.qualified, target)

    print(f"running migrations on {key} ...", end=" ", flush=True)
    start = time.time()
    result = http_client.post(f"/api/services/{key}/retry-migrations", body={}, timeout=120)
    elapsed = time.time() - start

    if result.get("ok"):
        print(f"done ({elapsed:.1f}s)")
    else:
        print(f"FAILED ({elapsed:.1f}s)")
        error = result.get("error", "unknown error")
        die(f"migration failed: {error}")


def _services_deps(args: argparse.Namespace) -> None:
    """Check or reinstall node_modules for a service."""
    from supervisor import http_client

    ctx = resolve_from_args(args)
    target = args.target
    if not target:
        die("TARGET is required for deps")

    key = _resolve_service_key(ctx.qualified, target)
    reinstall = getattr(args, "reinstall", False)

    if reinstall:
        print(f"reinstalling deps for {key} ...", end=" ", flush=True)
        start = time.time()
        http_client.post(f"/api/services/{key}/reinstall-deps", body={}, timeout=300)
        print(f"done ({time.time() - start:.1f}s)")
    else:
        result = http_client.get(f"/api/services/{key}/deps-status")
        stale = result.get("stale", False)
        host_hash = result.get("host_hash", "?")
        container_hash = result.get("container_hash", "?")
        if stale:
            print(f"{key}: deps are STALE (host={host_hash[:12]}, container={container_hash[:12]})")
            print("  run with --reinstall to fix")
        else:
            print(f"{key}: deps are up to date")


# ---------------------------------------------------------------------------
# Orphan detection (works without a running server)
# ---------------------------------------------------------------------------


def _find_docker_orphans() -> list[dict[str, str]]:
    """Find running Docker containers managed by v that have no server tracking them.

    Detects two kinds of containers:
    - Compose containers with ``com.veliu.managed=true`` (vite, functions)
    - Supabase CLI containers with ``com.supabase.cli.project`` label

    If the server is running, containers tracked by the ServiceManager are
    excluded. If the server is down, all labeled containers are orphans.
    """
    import json
    import subprocess

    # Collect container IDs from both label families.
    ids: list[str] = []
    for label_filter in ("com.veliu.managed=true", "com.supabase.cli.project"):
        try:
            ps = subprocess.run(
                ["docker", "ps", "-q", "--filter", f"label={label_filter}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if ps.returncode == 0:
            ids.extend(line.strip() for line in ps.stdout.splitlines() if line.strip())

    if not ids:
        return []

    # De-duplicate (a container might match both labels).
    ids = list(dict.fromkeys(ids))

    # Inspect all at once.
    try:
        ins = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", *ids],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if ins.returncode != 0:
        return []

    containers: list[dict[str, str]] = []
    for line in ins.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        name = str(obj.get("Name") or "").lstrip("/")
        state = (obj.get("State") or {}).get("Status") or "unknown"
        labels = (obj.get("Config") or {}).get("Labels") or {}
        started = (obj.get("State") or {}).get("StartedAt") or ""
        project = labels.get("com.docker.compose.project", "")
        service = labels.get("com.docker.compose.service", "")
        containers.append(
            {
                "name": name,
                "state": state,
                "project": project,
                "service": service,
                "started": started[:19].replace("T", " ") if started else "",
            }
        )

    if not containers:
        return []

    # If the server is running, exclude containers it's tracking.
    from supervisor.dispatch import server_running

    if server_running():
        try:
            from supervisor import http_client

            tracked = http_client.get("/api/services")
            if isinstance(tracked, list):
                # Build a set of container names the server knows about.
                # The server stores container_name in service metadata.
                tracked_names: set[str] = set()
                for svc in tracked:
                    meta = svc.get("metadata") or {}
                    cn = meta.get("container_name") or ""
                    if cn:
                        tracked_names.add(cn)
                    # Also match by compose project + service pattern.
                    key = svc.get("key", "")
                    if key:
                        tracked_names.add(key)
                containers = [c for c in containers if c["name"] not in tracked_names]
        except Exception:
            pass

    return containers


def _services_orphans(args: argparse.Namespace) -> None:
    """List or stop Docker containers not tracked by the server."""
    import subprocess

    stop = getattr(args, "stop", False)
    orphans = _find_docker_orphans()

    if not orphans:
        print("no orphaned containers found")
        return

    print(f"found {len(orphans)} orphaned container(s):\n")
    header = ("CONTAINER", "STATE", "PROJECT", "SERVICE", "STARTED")
    rows = [(o["name"], o["state"], o["project"] or "-", o["service"] or "-", o["started"] or "-") for o in orphans]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    print("  ".join(header[i].ljust(widths[i]) for i in range(len(header))))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(header))))

    if not stop:
        print("\nrun `v services orphans --stop` to stop them")
        return

    print()
    names = [o["name"] for o in orphans]
    try:
        result = subprocess.run(
            ["docker", "stop", *names],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        die("timed out stopping containers")

    if result.returncode == 0:
        print(f"stopped {len(names)} container(s)")
    else:
        die(f"docker stop failed: {result.stderr.strip()}")
