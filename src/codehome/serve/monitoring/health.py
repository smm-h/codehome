"""Health checker: polls Docker container health and HTTP endpoints."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.request
from typing import Any

from codehome.bus import Event, fire
from codehome.serve.services import State, services

log = logging.getLogger(__name__)

# Poll interval in seconds.
_POLL_INTERVAL = 10
# Stop logging after this many consecutive collection errors.
_MAX_CONSECUTIVE_ERRORS = 5

# Supabase HTTP probe endpoints: (path, label, HTTP method).
_SUPABASE_PROBES: list[tuple[str, str, str]] = [
    ("/rest-admin/v1/ready", "postgrest", "HEAD"),
    ("/auth/v1/health", "auth", "GET"),
    ("/functions/v1/_internal/health", "functions", "HEAD"),
]

# HTTP probe timeout in seconds.
_PROBE_TIMEOUT = 3


class HealthChecker:
    """Polls Docker container health and HTTP endpoints every 10 seconds."""

    def __init__(self) -> None:
        self.current: dict[str, dict[str, object]] = {}
        self._running: bool = False
        self._client: Any = None

    async def run(self) -> None:
        """Poll health every 10 seconds."""
        self._running = True
        # Import docker SDK lazily (not everyone will have it installed yet).
        try:
            import docker

            self._client = docker.from_env()
        except Exception:
            log.info("Health checks disabled: Docker SDK unavailable or daemon not running")
            return

        consecutive_errors = 0
        while self._running:
            try:
                snapshot = await asyncio.to_thread(self._collect, self._client, services)
                self.current = snapshot
                await fire(
                    Event(
                        name="service.health",
                        payload={"ts": time.time(), "services": snapshot},
                    )
                )
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors <= _MAX_CONSECUTIVE_ERRORS:
                    log.warning("Health check failed: %s", exc)
                elif consecutive_errors == _MAX_CONSECUTIVE_ERRORS + 1:
                    log.warning("Health checks failing repeatedly, suppressing")
            await asyncio.sleep(_POLL_INTERVAL)

    @staticmethod
    def _collect(client: Any, svc_manager: Any) -> dict[str, dict[str, object]]:
        """Blocking: run Docker + HTTP health checks for all mapped services."""
        from codehome.serve.monitoring.container_map import build_container_map

        container_map, containers_by_name = build_container_map(svc_manager, client)

        # Invert: service_key -> list of containers.
        svc_containers: dict[str, list[Any]] = {}

        for container_name, svc_key in container_map.items():
            container = containers_by_name.get(container_name)
            if container:
                svc_containers.setdefault(svc_key, []).append(container)

        result: dict[str, dict[str, object]] = {}

        for svc_key, containers in svc_containers.items():
            svc = svc_manager.get(svc_key)
            if not svc or svc.state != State.RUNNING:
                continue

            if svc.service_type == "supabase":
                result[svc_key] = _check_supabase(svc)
            else:
                # compose / compose-native: check Docker HEALTHCHECK.
                result[svc_key] = _check_docker_health(containers)

        return result

    def stop(self) -> None:
        self._running = False
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def get_health(self) -> dict[str, object]:
        """Return current health snapshot."""
        return {"services": dict(self.current)}


def _check_docker_health(containers: list[Any]) -> dict[str, object]:
    """Check Docker HEALTHCHECK status for compose/compose-native containers."""
    # Use the first container's health (typically only one replica).
    docker_health: str | None = None
    for c in containers:
        try:
            c.reload()
            health = c.attrs.get("State", {}).get("Health")
            if health:
                docker_health = health.get("Status")
            else:
                # No HEALTHCHECK defined -- container is running but unverified.
                docker_health = "unknown"
            break
        except Exception:
            log.debug("Failed to reload container for health check", exc_info=True)
            continue

    status = docker_health or "unknown"
    return {
        "status": status,
        "docker_health": docker_health,
        "checks": {},
    }


def _check_supabase(svc: Any) -> dict[str, object]:
    """Probe Supabase sub-services through Kong and determine overall health."""
    # Resolve the api_port from the service's connection metadata.
    api_port = _resolve_api_port(svc)
    if api_port is None:
        return {
            "status": "unknown",
            "docker_health": None,
            "checks": {},
        }

    checks: dict[str, bool] = {}
    for path, label, method in _SUPABASE_PROBES:
        checks[label] = _http_probe(f"http://127.0.0.1:{api_port}{path}", method=method)

    # Determine overall status from probe results.
    passing = sum(checks.values())
    total = len(checks)
    if passing == total:
        status = "healthy"
    elif passing == 0:
        status = "unhealthy"
    else:
        status = "degraded"

    return {
        "status": status,
        "docker_health": None,
        "checks": checks,
    }


def _resolve_api_port(svc: Any) -> int | None:
    """Extract the Kong API port from ServiceInstance metadata."""
    # First try the service's own port field.
    if svc.port:
        return int(svc.port)
    # Then try connection metadata (set by discovery/startup).
    conn = svc.metadata.get("connection", {})
    api_url = conn.get("api_url", "")
    if api_url:
        try:
            return int(api_url.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            pass
    return None


def _http_probe(url: str, *, method: str = "GET") -> bool:
    """Send a quick HTTP request; return True if response is 2xx."""
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            return 200 <= resp.status < 300  # type: ignore[no-any-return]
    except Exception:
        return False


# Singleton.
health_checker = HealthChecker()
