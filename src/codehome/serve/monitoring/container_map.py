"""Map Docker container names to ServiceInstance keys.

MetricsCollector keys metrics by ``container.name`` (e.g.
``bag-navchat-vite-1``), but health checks and log streaming need
``ServiceInstance.key`` (e.g. ``bag:navchat/vite``).  This module bridges
the two naming schemes by inspecting registered services and running
containers.

This is a blocking module -- callers must use ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import docker

    from codehome.serve.services import ServiceManager

log = logging.getLogger(__name__)


def build_container_map(
    services: ServiceManager,
    client: docker.DockerClient,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build a mapping from Docker container name to ServiceInstance key.

    Iterates over all registered services and attempts to match each one
    to a running Docker container:

    - **compose** / **compose-native**: container name follows the pattern
      ``{project_name}-{compose_service}-{replica}``.  The project name is
      derived from the branch via ``compose_project_name``.
    - **supabase**: containers carry the label ``com.supabase.cli.project``
      whose value is the deterministic project ID for the branch.

    Returns a tuple of (container_map, containers_by_name) where
    container_map maps ``container.name`` to ``ServiceInstance.key`` and
    containers_by_name maps ``container.name`` to the Docker container
    object.  Containers with no matching service and services with no
    running container are silently skipped.
    """
    from codehome.serve.supabase import project_id_for

    result: dict[str, str] = {}

    # Collect all running containers once to avoid repeated Docker API calls.
    try:
        all_containers: list[Any] = client.containers.list()
    except Exception:
        log.warning("Failed to list Docker containers")
        return result, {}

    # Build a lookup: container.name -> container object.
    containers_by_name: dict[str, Any] = {c.name: c for c in all_containers}

    # Build a lookup for Supabase containers: project_id -> list of containers.
    # Supabase containers are labelled with com.supabase.cli.project=<project_id>.
    supabase_containers: dict[str, list[Any]] = {}
    for c in all_containers:
        labels: dict[str, str] = c.labels or {}
        project_label = labels.get("com.supabase.cli.project")
        if project_label:
            supabase_containers.setdefault(project_label, []).append(c)

    for svc in services.list_all():
        if svc.service_type in ("compose", "compose-native"):
            _map_compose_service(svc, containers_by_name, result)
        elif svc.service_type == "supabase":
            _map_supabase_service(svc, supabase_containers, project_id_for, result)

    return result, containers_by_name


def _map_compose_service(
    svc: Any,
    containers_by_name: dict[str, Any],
    result: dict[str, str],
) -> None:
    """Match a compose or compose-native service to its container(s).

    Container naming convention: ``{project}-{service}-{replica}``.
    For compose services the project is ``compose_project_name(branch)``.
    For compose-native services the project is ``veliu-{compose_project_name(branch)}``.
    """
    from codehome.serve.docker import compose_project_name

    compose_service = svc.metadata.get("compose_service")
    if not compose_service:
        return

    if svc.service_type == "compose-native":
        project = f"veliu-{compose_project_name(svc.branch)}"
    else:
        project = compose_project_name(svc.branch)

    # Docker Compose names containers as {project}-{service}-{replica}.
    # We match any replica number.
    prefix = f"{project}-{compose_service}-"
    for container_name in containers_by_name:
        if container_name.startswith(prefix):
            result[container_name] = svc.key


def _map_supabase_service(
    svc: Any,
    supabase_containers: dict[str, list[Any]],
    project_id_fn: Any,
    result: dict[str, str],
) -> None:
    """Match a Supabase service to all its labelled containers."""
    project_id = project_id_fn(svc.branch)
    containers = supabase_containers.get(project_id, [])
    for c in containers:
        result[c.name] = svc.key
