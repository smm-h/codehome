"""System-level operations for the server: health check, diagnostics, self-update.

Extracted from routers/system.py so route handlers stay thin. Functions here have
no FastAPI dependencies (no Request, Response, HTTPException).
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from codehome.serve.updater import _PROJECT_ROOT

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
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


def supervisor_disk_usage() -> int:
    """Return total bytes used by ~/.superv/ and .supervisor/ directories."""
    from codehome.paths import SUPERVISOR_DIR, superv_home

    total = 0
    for d in (superv_home(), SUPERVISOR_DIR):
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

    Returns {status, uptime_seconds, python_version, ...} plus HTTP status code.
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
    disk_bytes = await asyncio.to_thread(supervisor_disk_usage)

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
        "supervisor_disk_bytes": disk_bytes,
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


def list_repos_info() -> list[dict[str, Any]]:
    """Return the list of configured repos with their remotes."""
    from codehome.config import list_repos

    repos = list_repos()
    return [
        {
            "name": r.name,
            "remote": r.remote,
            "base_branch": r.base_branch,
            "staging_branch": r.staging_branch,
        }
        for r in repos
    ]


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


async def _run_step(cmd: list[str], cwd: str = str(_PROJECT_ROOT), step_timeout: int = 300) -> tuple[bool, str]:
    """Run a subprocess and return (success, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=step_timeout)
    output = stdout.decode(errors="replace") if stdout else ""
    return proc.returncode == 0, output


async def _rollback(commit: str) -> AsyncGenerator[str, None]:
    """Roll back to a previous commit: git reset, uv sync, npm rebuild."""
    rollback_steps = [
        ("rollback_git", ["git", "reset", "--hard", commit]),
        ("rollback_deps", ["uv", "sync"]),
        ("rollback_build", ["bash", "-c", "cd dashboard && npm ci && npm run build"]),
    ]
    for step_key, cmd in rollback_steps:
        yield f'data: {{"step": "{step_key}", "status": "running"}}\n\n'
        try:
            ok, output = await _run_step(cmd)
            status = "done" if ok else "error"
            msg: dict[str, str] = {"step": step_key, "status": status}
            if not ok:
                msg["output"] = output
            yield f"data: {_json.dumps(msg)}\n\n"
        except Exception as exc:
            yield f'data: {{"step": "{step_key}", "status": "error", "output": {_json.dumps(str(exc))}}}\n\n'


def find_logo() -> Path | None:
    """Return the first logo.* file found (prefers ~/.superv/, falls back to .supervisor/)."""
    from codehome.paths import resolve_global

    for ext in (".png", ".jpg", ".svg"):
        candidate = resolve_global(f"logo{ext}")
        if candidate.is_file():
            return candidate
    return None


def remove_logo() -> bool:
    """Delete all logo files from both ~/.superv/ and .supervisor/."""
    from codehome.paths import SUPERVISOR_DIR, superv_home

    removed = False
    for d in (superv_home(), SUPERVISOR_DIR):
        for ext in (".png", ".jpg", ".svg"):
            p = d / f"logo{ext}"
            if p.is_file():
                p.unlink()
                removed = True
    return removed


def save_logo(data: bytes, ext: str) -> Path:
    """Save logo data to ~/.superv/logo{ext}. Removes any previous logo first."""
    from codehome.paths import superv_home

    remove_logo()
    dest = superv_home() / f"logo{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _rollback_done_event(commit: str) -> str:
    """Build the SSE event for a completed rollback."""
    short = commit[:8]
    payload = _json.dumps({"step": "rollback_complete", "status": "done", "message": f"Rolled back to {short}"})
    return f"data: {payload}\n\n"


async def stream_self_update() -> AsyncGenerator[str, None]:
    """Yield SSE events for each step of the self-update sequence.

    Steps: save commit, git pull, uv sync, npm build, validate, restart.
    On failure, rolls back to the saved commit.
    """
    # Save current commit hash for rollback.
    try:
        head_proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        head_out, _ = await head_proc.communicate()
        saved_commit = head_out.decode().strip()
    except Exception:
        saved_commit = ""

    yield f'data: {{"step": "saved_commit", "commit": {_json.dumps(saved_commit)}}}\n\n'

    steps = [
        ("pulling", ["git", "pull", "origin", "main"]),
        ("installing_deps", ["uv", "sync"]),
        ("building", ["bash", "-c", "cd dashboard && npm ci && npm run build"]),
    ]

    for step_key, cmd in steps:
        yield f'data: {{"step": "{step_key}", "status": "running"}}\n\n'
        try:
            ok, output = await _run_step(cmd)
            if not ok:
                yield f'data: {{"step": "{step_key}", "status": "error", "output": {_json.dumps(output)}}}\n\n'
                if saved_commit:
                    async for event in _rollback(saved_commit):
                        yield event
                    yield _rollback_done_event(saved_commit)
                return
            yield f'data: {{"step": "{step_key}", "status": "done"}}\n\n'
        except TimeoutError:
            yield f'data: {{"step": "{step_key}", "status": "error", "output": "Timed out after 5 minutes"}}\n\n'
            if saved_commit:
                async for event in _rollback(saved_commit):
                    yield event
                yield _rollback_done_event(saved_commit)
            return
        except Exception as exc:
            yield f'data: {{"step": "{step_key}", "status": "error", "output": {_json.dumps(str(exc))}}}\n\n'
            if saved_commit:
                async for event in _rollback(saved_commit):
                    yield event
                yield _rollback_done_event(saved_commit)
            return

    # Validate the new code by importing the app module.
    yield 'data: {"step": "validating", "status": "running"}\n\n'
    try:
        ok, output = await _run_step(
            [sys.executable, "-c", "from codehome.serve.server import app; print('ok')"],
        )
        if not ok:
            yield f'data: {{"step": "validating", "status": "error", "output": {_json.dumps(output)}}}\n\n'
            if saved_commit:
                async for event in _rollback(saved_commit):
                    yield event
                yield _rollback_done_event(saved_commit)
            return
        yield 'data: {"step": "validating", "status": "done"}\n\n'
    except Exception as exc:
        yield f'data: {{"step": "validating", "status": "error", "output": {_json.dumps(str(exc))}}}\n\n'
        if saved_commit:
            async for event in _rollback(saved_commit):
                yield event
            yield _rollback_done_event(saved_commit)
        return

    # Validation passed -- schedule a restart.
    yield 'data: {"step": "restarting", "status": "running"}\n\n'
    asyncio.get_event_loop().call_later(1.0, os.kill, os.getpid(), signal.SIGTERM)
    yield 'data: {"step": "restarting", "status": "done"}\n\n'
