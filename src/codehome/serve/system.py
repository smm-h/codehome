"""Generic server operations: health check, diagnostics, branding, metrics.

Extracted from the core plugin's system_ops.py so core server
endpoints (health, logo, diagnostics) work without any plugin installed.
Functions here have no FastAPI dependencies and no plugin imports.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def check_docker() -> bool:
    """Check Docker availability via a fast subprocess call."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def codehome_disk_usage() -> int:
    """Return total bytes used by ~/.codehome/ and .supervisor/ directories."""
    from codehome.paths import SUPERVISOR_DIR, codehome_home

    total = 0
    for d in (codehome_home(), SUPERVISOR_DIR):
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total


async def build_health_status() -> dict[str, Any]:
    """Build the full health check response dict.

    Returns {status, uptime_seconds, ...} plus HTTP status code.
    """
    from codehome.serve.events import events as _events
    from codehome.serve.server import _server_start_time
    from codehome.serve.services import State
    from codehome.serve.services import services as _services

    uptime = time.time() - _server_start_time if _server_start_time else 0
    sse_clients = _events.client_count
    tracked_services = len(_services.list_all())
    running_services = len([s for s in _services.list_all() if s.state == State.RUNNING])

    docker_ok = await asyncio.to_thread(check_docker)
    disk_bytes = await asyncio.to_thread(codehome_disk_usage)

    status = "healthy"
    issues: list[str] = []

    if disk_bytes > 500 * 1024 * 1024:
        issues.append(f".supervisor/ disk usage is {disk_bytes // (1024 * 1024)} MB")
    if issues:
        status = "degraded"

    body = {
        "status": status,
        "uptime_seconds": round(uptime, 1),
        "python_version": sys.version,
        "active_sse_connections": sse_clients,
        "tracked_services": tracked_services,
        "running_services": running_services,
        "codehome_disk_bytes": disk_bytes,
        "docker_available": docker_ok,
    }
    if issues:
        body["issues"] = issues

    code = 200 if status == "healthy" else 503
    return {"body": body, "status_code": code}


def get_diagnostics_stats() -> dict[str, Any]:
    """Gather server diagnostics: uptime, requests, connections, services, agents, memory."""
    import resource

    from codehome.serve.agent_sessions import agent_sessions
    from codehome.serve.events import events as _events
    from codehome.serve.middleware import request_count
    from codehome.serve.server import _server_start_time
    from codehome.serve.services import State
    from codehome.serve.services import services as _services

    running_services = [s for s in _services.list_all() if s.state == State.RUNNING]
    active_agents = [s for s in agent_sessions.list_sessions() if s.status in ("pending", "running")]

    usage = resource.getrusage(resource.RUSAGE_SELF)
    memory_mb = round(usage.ru_maxrss / 1024, 1)  # Linux: ru_maxrss is in KB

    return {
        "uptime": time.time() - _server_start_time,
        "total_requests": request_count,
        "active_sse_connections": _events.client_count,
        "running_services": len(running_services),
        "active_agent_sessions": len(active_agents),
        "memory_mb": memory_mb,
    }


def _empty_error_window() -> dict[str, int | float]:
    """Return a zeroed error-rate window dict."""
    return {
        "total": 0,
        "server_errors": 0,
        "client_errors": 0,
        "server_error_pct": 0.0,
        "client_error_pct": 0.0,
    }


def get_request_metrics() -> dict[str, Any]:
    """Compute error rates and p95 latency from the middleware ring buffer.

    Returns a dict with:
    - error_rates: server/client error counts and percentages for 1h and 24h windows
    - slow_endpoints: top 10 endpoints by p95 latency
    - window_seconds: actual time span covered by the buffer
    """
    from codehome.serve.middleware import request_history

    now = time.time()
    # Snapshot the deque into a list to avoid mutation during iteration.
    entries = list(request_history)

    if not entries:
        return {
            "error_rates": {
                "1h": _empty_error_window(),
                "24h": _empty_error_window(),
            },
            "slow_endpoints": [],
            "window_seconds": 0.0,
        }

    oldest_ts = entries[0][0]
    window_seconds = now - oldest_ts

    # -- Error rates for 1h and 24h windows --
    error_rates: dict[str, dict[str, object]] = {}
    for label, window_secs in [("1h", 3600), ("24h", 86400)]:
        cutoff = now - window_secs
        window_entries = [e for e in entries if e[0] >= cutoff]
        total = len(window_entries)
        server_errors = sum(1 for e in window_entries if e[3] >= 500)
        client_errors = sum(1 for e in window_entries if 400 <= e[3] < 500)
        error_rates[label] = {
            "total": total,
            "server_errors": server_errors,
            "client_errors": client_errors,
            "server_error_pct": round(server_errors / total * 100, 2) if total else 0.0,
            "client_error_pct": round(client_errors / total * 100, 2) if total else 0.0,
        }

    # -- Slow endpoints: p95 latency per (method, path), top 10 --
    from collections import defaultdict

    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for _ts, method, path, _status, duration_ms in entries:
        groups[(method, path)].append(duration_ms)

    slow_endpoints: list[dict[str, object]] = []
    for (method, path), durations in groups.items():
        durations.sort()
        n = len(durations)
        p95_idx = int(n * 0.95)
        if p95_idx >= n:
            p95_idx = n - 1
        p95 = durations[p95_idx]
        slow_endpoints.append(
            {
                "method": method,
                "path": path,
                "p95_ms": round(p95, 2),
                "count": n,
            }
        )

    slow_endpoints.sort(key=lambda x: float(str(x["p95_ms"])), reverse=True)
    slow_endpoints = slow_endpoints[:10]

    return {
        "error_rates": error_rates,
        "slow_endpoints": slow_endpoints,
        "window_seconds": round(window_seconds, 1),
    }


async def shutdown_all_services() -> int:
    """Stop all managed services across all branches. Returns count cleaned."""
    from codehome.serve.events import events as _events
    from codehome.serve.ports import ports as _ports_singleton
    from codehome.serve.service_lifecycle import cleanup_branch_services
    from codehome.serve.services import services as _services

    all_services = _services.list_all()
    branches: dict[str, bool] = {}
    for svc in all_services:
        branches[svc.branch] = True

    total_cleaned = 0
    for branch in branches:
        count = await cleanup_branch_services(
            branch,
            _services,
            _events,
            _ports_singleton,
            remove_volumes=False,
        )
        total_cleaned += count

    return total_cleaned


# -- Branding logo operations ------------------------------------------------


def find_logo() -> Path | None:
    """Return the first logo.* file found (prefers ~/.codehome/, falls back to .supervisor/)."""
    from codehome.paths import resolve_global

    for ext in (".png", ".jpg", ".svg"):
        candidate = resolve_global(f"logo{ext}")
        if candidate.is_file():
            return candidate
    return None


def remove_logo() -> bool:
    """Delete all logo files from both ~/.codehome/ and .supervisor/."""
    from codehome.paths import SUPERVISOR_DIR, codehome_home

    removed = False
    for d in (codehome_home(), SUPERVISOR_DIR):
        for ext in (".png", ".jpg", ".svg"):
            p = d / f"logo{ext}"
            if p.is_file():
                p.unlink()
                removed = True
    return removed


def save_logo(data: bytes, ext: str) -> Path:
    """Save logo data to ~/.codehome/logo{ext}. Removes any previous logo first."""
    from codehome.paths import codehome_home

    remove_logo()
    dest = codehome_home() / f"logo{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
