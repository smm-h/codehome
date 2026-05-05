"""Metrics collector: polls docker stats, broadcasts via SSE."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from codehome.bus import Event, fire

log = logging.getLogger(__name__)

# Rolling buffer: 150 data points = 5 minutes at 2s intervals.
_HISTORY_SIZE = 150
# Stop logging after this many consecutive collection errors.
_MAX_CONSECUTIVE_ERRORS = 5


class MetricsCollector:
    def __init__(self) -> None:
        self.history: deque[dict[str, object]] = deque(maxlen=_HISTORY_SIZE)
        self._running = False
        self._client: Any = None

    async def run(self) -> None:
        """Poll docker stats every 2 seconds."""
        self._running = True
        # Import docker SDK lazily (not everyone will have it installed yet).
        try:
            import docker

            self._client = docker.from_env()
        except Exception:
            # Docker SDK not available or Docker not running.
            log.info("Metrics disabled: Docker SDK unavailable or daemon not running")
            return

        consecutive_errors = 0
        while self._running:
            try:
                metrics = await asyncio.to_thread(self._collect, self._client)
                entry = {"ts": time.time(), "containers": metrics}
                self.history.append(entry)
                await fire(Event(name="service.metrics", payload=entry))
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors <= _MAX_CONSECUTIVE_ERRORS:
                    log.warning("Metrics collection failed: %s", exc)
                elif consecutive_errors == _MAX_CONSECUTIVE_ERRORS + 1:
                    log.warning("Metrics collection failing repeatedly, suppressing")
            await asyncio.sleep(2)

    def stop(self) -> None:
        self._running = False
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def get_history(self, minutes: int = 5) -> list[dict[str, object]]:
        """Return metrics history for the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        return [e for e in self.history if e["ts"] >= cutoff]  # type: ignore[operator]

    @staticmethod
    def _collect(client: Any) -> list[dict[str, object]]:
        """Collect stats from all managed containers (blocking)."""
        metrics = []

        # Our containers (Vite, functions-serve).
        managed = client.containers.list(filters={"label": "com.veliu.managed=true"})
        # Supabase containers.
        supabase = client.containers.list(filters={"label": "com.supabase.cli.project"})

        for c in managed + supabase:
            try:
                stats = c.stats(stream=False)
                metrics.append(
                    {
                        "key": c.name,
                        "cpu_pct": _calc_cpu(stats),
                        "mem_mb": _calc_mem_mb(stats),
                        "mem_limit_mb": _calc_mem_limit_mb(stats),
                        "net_rx": _calc_net(stats, "rx_bytes"),
                        "net_tx": _calc_net(stats, "tx_bytes"),
                    },
                )
            except Exception:
                # Container may have stopped between list and stats.
                pass

        return metrics


def _calc_cpu(stats: dict[str, Any]) -> float:
    """Calculate CPU percentage from docker stats."""
    try:
        cpu = stats["cpu_stats"]
        precpu = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - precpu["system_cpu_usage"]
        if system_delta > 0 and cpu_delta > 0:
            num_cpus = cpu["online_cpus"]
            return round((cpu_delta / system_delta) * num_cpus * 100, 2)  # type: ignore[no-any-return]
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return 0.0


def _calc_mem_mb(stats: dict[str, Any]) -> float:
    """Calculate memory usage in MB."""
    try:
        usage = stats["memory_stats"]["usage"]
        # Subtract cache if available (Linux cgroups v1).
        cache = stats["memory_stats"].get("stats", {}).get("cache", 0)
        return round((usage - cache) / (1024 * 1024), 1)  # type: ignore[no-any-return]
    except (KeyError, TypeError):
        return 0.0


def _calc_mem_limit_mb(stats: dict[str, Any]) -> float:
    """Calculate memory limit in MB."""
    try:
        return round(stats["memory_stats"]["limit"] / (1024 * 1024), 1)  # type: ignore[no-any-return]
    except (KeyError, TypeError):
        return 0.0


def _calc_net(stats: dict[str, Any], field: str) -> int:
    """Calculate total network bytes (rx or tx) across all interfaces."""
    try:
        networks = stats.get("networks", {})
        return sum(iface.get(field, 0) for iface in networks.values())
    except (TypeError, AttributeError):
        return 0


# Singleton.
metrics_collector = MetricsCollector()
