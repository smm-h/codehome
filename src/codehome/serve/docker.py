"""Docker Compose invocation and container management."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

from codehome.serve.supabase import read_supabase_version

if TYPE_CHECKING:
    from pathlib import Path

    from codehome.subprocesses import OutputCallback

# Subprocess timeouts (seconds) — configurable.
TIMEOUT_COMPOSE_UP = 300
TIMEOUT_COMPOSE_STOP = 60
TIMEOUT_COMPOSE_RM = 30
TIMEOUT_COMPOSE_PS = 30
TIMEOUT_VOLUME_RM = 30

# Docker compose files live at repos/bag/docker/.
# Lazy to avoid top-level plugin import (repo_dir is in supervisor plugin).
def _docker_dir() -> "Path":
    from codehome.state.service_registry import services
    from codehome.service_protocols import ProjectLayout
    layout = services.get_typed("supervisor.layout", ProjectLayout)
    if layout is None:
        raise RuntimeError("ProjectLayout not registered (supervisor plugin not loaded)")
    return layout.repo_dir("bag") / "docker"


def _compose_file() -> "Path":
    return _docker_dir() / "compose.yml"


def _compose_tdd_file() -> "Path":
    return _docker_dir() / "compose.tdd.yml"


def compose_project_name(branch: str) -> str:
    """Generate a Docker Compose project name from a branch name.

    Docker project names must be lowercase alphanumeric + hyphens.
    """
    import re

    # Replace known separators, then strip anything that isn't [a-z0-9-].
    name = branch.replace(":", "-").replace("/", "-").lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    # Collapse runs of hyphens and strip leading/trailing hyphens.
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "default"


def compose_up(
    branch: str,
    service: str,
    env: dict[str, str],
    tdd: bool = False,
) -> tuple[bool, str]:
    """Start a Compose service. Returns (success, message)."""
    project = compose_project_name(branch)
    cmd = ["docker", "compose", "-p", project, "-f", str(_compose_file())]
    if tdd:
        cmd += ["-f", str(_compose_tdd_file())]
    cmd += ["up", "-d", "--build", service]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            timeout=TIMEOUT_COMPOSE_UP,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_COMPOSE_UP}s"
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return False, msg
    return True, f"Started {service}."


def compose_down(
    branch: str,
    service: str | None = None,
    tdd: bool = False,
    remove_volumes: bool = False,
) -> tuple[bool, str]:
    """Stop Compose service(s). If service is None, stops all."""
    project = compose_project_name(branch)

    def _base_cmd() -> list[str]:
        cmd = ["docker", "compose", "-p", project, "-f", str(_compose_file())]
        if tdd:
            cmd += ["-f", str(_compose_tdd_file())]
        return cmd

    try:
        if service:
            # docker compose down doesn't accept service names; use stop + rm.
            stop_result = subprocess.run(
                [*_base_cmd(), "stop", service],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_COMPOSE_STOP,
            )
            if stop_result.returncode != 0:
                msg = stop_result.stderr.strip() or stop_result.stdout.strip() or f"exit {stop_result.returncode}"
                return False, msg
            result = subprocess.run(
                [*_base_cmd(), "rm", "-f", service],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_COMPOSE_RM,
            )
        else:
            cmd = [*_base_cmd(), "down"]
            if remove_volumes:
                cmd += ["-v", "--remove-orphans"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_STOP)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_COMPOSE_STOP}s"

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return False, msg
    return True, "Stopped."


def docker_inspect_compose_containers() -> list[dict[str, object]]:
    """List every running container that carries a Compose project label.

    Read-only Docker query used by the startup-time Docker discovery path
    (``serve.discovery.discover_docker``) and the on-demand
    ``POST /api/services/discover`` endpoint.  Returns a list of normalized
    dicts with the fields the discovery layer needs:

        [
            {
                "name": "bag-lisa-vite-1",
                "project": "bag-lisa",            # com.docker.compose.project
                "service": "vite",                # com.docker.compose.service
                "container_number": 1,            # com.docker.compose.container-number
                "state": "running",
                "host_ports": {5173: 59899, ...}, # container_port -> host_port
            },
            ...
        ]

    Strategy:
      1. ``docker ps -q --filter label=com.docker.compose.project`` to cheaply
         collect container IDs for every running Compose-managed container.
      2. ``docker inspect <ids...>`` once to pull all labels + port bindings
         in a single subprocess call.  Falls back to an empty list if either
         step fails -- this is opportunistic discovery, never a hard error.

    Only running containers are included; a stopped container has nothing
    for discovery to register.
    """
    # Step 1: list container IDs.
    try:
        ps = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_COMPOSE_PS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if ps.returncode != 0:
        return []

    ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not ids:
        return []

    # Step 2: inspect them all in one call.  ``docker inspect`` with multiple
    # IDs returns a JSON array; using ``--format '{{json .}}'`` streams one
    # JSON object per line, which is easier to parse incrementally.
    try:
        ins = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", *ids],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_COMPOSE_PS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if ins.returncode != 0:
        return []

    out: list[dict[str, object]] = []
    for line in ins.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue

        labels = (obj.get("Config") or {}).get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        if not project or not service:
            # Not a Compose container (unreachable given the filter, but
            # be defensive in case of future Docker label changes).
            continue

        state = (obj.get("State") or {}).get("Status") or ""
        if state != "running":
            # Discovery never registers stopped services; skip.
            continue

        # Container-number is a string in labels; parse defensively.
        raw_num = labels.get("com.docker.compose.container-number", "1")
        try:
            container_number = int(raw_num)
        except (TypeError, ValueError):
            container_number = 1

        # NetworkSettings.Ports maps "<container-port>/<proto>" to either
        # None (no host binding) or a list of {HostIp, HostPort} dicts.
        # We want IPv4 host-port bindings keyed by the container port.
        host_ports: dict[int, int] = {}
        raw_ports = (obj.get("NetworkSettings") or {}).get("Ports") or {}
        for cport_proto, bindings in raw_ports.items():
            if not bindings:
                continue
            # cport_proto is like "5173/tcp"; take the numeric part.
            cport_str = str(cport_proto).split("/", 1)[0]
            try:
                cport = int(cport_str)
            except (TypeError, ValueError):
                continue
            # Prefer the IPv4 binding (HostIp == "0.0.0.0"); fall back to
            # the first binding if only IPv6 is present.
            chosen: int | None = None
            for b in bindings:
                if not isinstance(b, dict):
                    continue
                host_ip = str(b.get("HostIp") or "")
                host_port_str = str(b.get("HostPort") or "")
                if not host_port_str:
                    continue
                try:
                    host_port = int(host_port_str)
                except (TypeError, ValueError):
                    continue
                if host_ip == "0.0.0.0":  # noqa: S104 -- matching Docker's literal
                    chosen = host_port
                    break
                if chosen is None:
                    chosen = host_port
            if chosen is not None:
                host_ports[cport] = chosen

        # Container name from inspect includes a leading "/".
        raw_name = str(obj.get("Name") or "")
        name = raw_name.lstrip("/")

        out.append(
            {
                "name": name,
                "project": project,
                "service": service,
                "container_number": container_number,
                "state": state,
                "host_ports": host_ports,
            },
        )
    return out


def compose_ps(branch: str, tdd: bool = False) -> list[dict[str, str]]:
    """List running containers for a branch's project."""
    project = compose_project_name(branch)
    cmd = ["docker", "compose", "-p", project, "-f", str(_compose_file())]
    if tdd:
        cmd += ["-f", str(_compose_tdd_file())]
    cmd += ["ps", "--format", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_PS)
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    try:
        # docker compose ps --format json outputs one JSON object per line.
        return [json.loads(line) for line in result.stdout.strip().splitlines() if line.strip()]
    except (json.JSONDecodeError, ValueError):
        return []


def compose_native_down(
    compose_file: Path,
    service_name: str,
    project_name: str,
) -> tuple[bool, str]:
    """Stop a native compose service (stop + rm)."""
    try:
        stop_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "-p", project_name, "stop", service_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_COMPOSE_STOP,
        )
        if stop_result.returncode != 0:
            msg = stop_result.stderr.strip() or stop_result.stdout.strip() or f"exit {stop_result.returncode}"
            return False, msg
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "-p", project_name, "rm", "-f", service_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_COMPOSE_RM,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_COMPOSE_STOP}s"
    return True, "Stopped."


def compose_native_ps(
    compose_file: Path,
    project_name: str,
) -> list[dict[str, str]]:
    """List running containers for a native compose project."""
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        project_name,
        "ps",
        "--format",
        "json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_COMPOSE_PS)
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    try:
        return [json.loads(line) for line in result.stdout.strip().splitlines() if line.strip()]
    except (json.JSONDecodeError, ValueError):
        return []


def volume_rm(volume_name: str) -> tuple[bool, str]:
    """Remove a Docker volume by name. Returns (success, message).

    The volume must not be in use by any running container.
    """
    try:
        result = subprocess.run(
            ["docker", "volume", "rm", volume_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_VOLUME_RM,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_VOLUME_RM}s"
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return False, msg
    return True, f"Removed volume {volume_name}."


def compose_up_streaming(
    branch: str,
    service: str,
    env: dict[str, str],
    callback: OutputCallback,
    tdd: bool = False,
) -> tuple[bool, str]:
    """Start a Compose service with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    project = compose_project_name(branch)
    cmd = ["docker", "compose", "-p", project, "-f", str(_compose_file())]
    if tdd:
        cmd += ["-f", str(_compose_tdd_file())]
    cmd += ["up", "-d", "--build", service]
    ok, msg = run_streaming(cmd, callback, env=env, timeout=TIMEOUT_COMPOSE_UP)
    if ok:
        return True, f"Started {service}."
    return False, msg


def compose_down_streaming(
    branch: str,
    service: str | None,
    callback: OutputCallback,
    tdd: bool = False,
    remove_volumes: bool = False,
) -> tuple[bool, str]:
    """Stop Compose service(s) with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    project = compose_project_name(branch)

    def _base_cmd() -> list[str]:
        cmd = ["docker", "compose", "-p", project, "-f", str(_compose_file())]
        if tdd:
            cmd += ["-f", str(_compose_tdd_file())]
        return cmd

    if service:
        # stop + rm (same as compose_down).
        ok, msg = run_streaming(
            [*_base_cmd(), "stop", service],
            callback,
            timeout=TIMEOUT_COMPOSE_STOP,
        )
        if not ok:
            return False, msg
        ok, msg = run_streaming(
            [*_base_cmd(), "rm", "-f", service],
            callback,
            timeout=TIMEOUT_COMPOSE_RM,
        )
        if not ok:
            return False, msg
        return True, "Stopped."
    cmd = [*_base_cmd(), "down"]
    if remove_volumes:
        cmd += ["-v", "--remove-orphans"]
    ok, msg = run_streaming(cmd, callback, timeout=TIMEOUT_COMPOSE_STOP)
    if ok:
        return True, "Stopped."
    return False, msg


def compose_native_up_streaming(
    compose_file: Path,
    service_name: str,
    project_name: str,
    env: dict[str, str],
    callback: OutputCallback,
) -> tuple[bool, str]:
    """Start a native compose service with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "-p",
        project_name,
        "up",
        "-d",
        "--build",
        service_name,
    ]
    ok, msg = run_streaming(cmd, callback, env=env, timeout=TIMEOUT_COMPOSE_UP)
    if ok:
        return True, f"Started {service_name}."
    return False, msg


def compose_native_down_streaming(
    compose_file: Path,
    service_name: str,
    project_name: str,
    callback: OutputCallback,
) -> tuple[bool, str]:
    """Stop a native compose service with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    ok, msg = run_streaming(
        ["docker", "compose", "-f", str(compose_file), "-p", project_name, "stop", service_name],
        callback,
        timeout=TIMEOUT_COMPOSE_STOP,
    )
    if not ok:
        return False, msg
    run_streaming(
        ["docker", "compose", "-f", str(compose_file), "-p", project_name, "rm", "-f", service_name],
        callback,
        timeout=TIMEOUT_COMPOSE_RM,
    )
    return True, "Stopped."


def build_vite_env(
    branch: str,
    worktree: Path,
    app_dir: str,
    vite_port: int,
    supabase_api_port: int,
    supabase_anon_key: str,
    compose_service: str = "vite",
) -> dict[str, str]:
    """Build environment variables for a Vite compose service.

    Handles both base (vite) and TDD (tdd-vite-bag, tdd-vite-orders) services,
    which use different env var names in their respective compose files.
    """
    app_src = worktree / app_dir
    env_local = app_src / ".env.local"
    env = {
        "PROJECT_NAME": compose_project_name(branch),
        "_docker_dir()": str(_docker_dir()),
        "SUPABASE_API_PORT": str(supabase_api_port),
        "SUPABASE_ANON_KEY": supabase_anon_key,
    }
    # Base vite service uses VITE_PORT/APP_SRC/ENV_LOCAL_PATH.
    # TDD services use TDD_BAG_PORT/PROD_BAG_SRC or TDD_ORDERS_PORT/PROD_ORDERS_SRC.
    if compose_service == "tdd-vite-bag":
        env["TDD_BAG_PORT"] = str(vite_port)
        env["PROD_BAG_SRC"] = str(app_src)
    elif compose_service == "tdd-vite-orders":
        env["TDD_ORDERS_PORT"] = str(vite_port)
        env["PROD_ORDERS_SRC"] = str(app_src)
    else:
        env["VITE_PORT"] = str(vite_port)
        env["APP_SRC"] = str(app_src)
        env["ENV_LOCAL_PATH"] = str(env_local) if env_local.exists() else str(_docker_dir() / ".env.placeholder")
    return env


def build_functions_env(
    branch: str,
    worktree: Path,
    supabase_api_port: int,
    supabase_anon_key: str,
    supabase_service_role_key: str,
    compose_service: str = "functions",
) -> dict[str, str]:
    """Build environment variables for a functions compose service.

    Handles both base (functions) and TDD (tdd-functions) services.
    """
    functions_src = str(worktree / "supabase" / "functions")
    env = {
        "PROJECT_NAME": compose_project_name(branch),
        "_docker_dir()": str(_docker_dir()),
        "SUPABASE_API_PORT": str(supabase_api_port),
        "SUPABASE_ANON_KEY": supabase_anon_key,
        "SUPABASE_SERVICE_ROLE_KEY": supabase_service_role_key,
        "SUPABASE_VERSION": read_supabase_version(worktree),
    }
    # TDD functions use PROD_FUNCTIONS_SRC instead of FUNCTIONS_SRC.
    if compose_service == "tdd-functions":
        env["PROD_FUNCTIONS_SRC"] = functions_src
    else:
        env["FUNCTIONS_SRC"] = functions_src
    return env
