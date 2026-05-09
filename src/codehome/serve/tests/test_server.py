"""Integration tests for the FastAPI server endpoints.

Uses httpx.AsyncClient with ASGITransport to test endpoints without
starting a real server.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest_asyncio

from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import (
    get_event_manager,
    get_metrics_collector,
    get_port_allocator,
    get_service_manager,
)
from codehome.serve.error_log import ErrorLog
from codehome.serve.events import EventManager
from codehome.serve.metrics import MetricsCollector
from codehome.serve.ports import PortAllocator
from codehome.serve.server import app
from codehome.serve.services import ServiceInstance, ServiceManager, State

# ---------------------------------------------------------------------------
# Auth override: bypass JWT validation in tests by providing a fake user.
# ---------------------------------------------------------------------------

_FAKE_USER = {"sub": "testadmin", "role": "admin"}


async def _fake_current_user():
    """Dependency override that returns a fake admin user."""
    return _FAKE_USER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    """Async HTTP client wired directly to the ASGI app (no real server).

    Overrides get_current_user so all authenticated endpoints pass without
    a real JWT token. Also overrides manager dependencies with fresh
    instances so each test is isolated without hacking private attributes.
    """
    # Fresh instances for each test session.
    test_services = ServiceManager()
    test_events = EventManager()
    test_metrics = MetricsCollector()
    # PortAllocator reads from disk on init; use a temp file to isolate.
    test_ports = PortAllocator.__new__(PortAllocator)
    test_ports._compose = {}
    test_ports._supabase_slots = {}
    test_ports._compose_native = {}

    # ErrorLog is required by the CSRF middleware via get_error_log dep
    # resolution that fires before route handlers (mirrors production
    # lifespan in server.py). Use a temp directory for the SQLite DB.
    error_log_tmpdir = tempfile.TemporaryDirectory()
    test_error_log = ErrorLog(Path(error_log_tmpdir.name) / "error_log.db")

    app.dependency_overrides[get_current_user] = _fake_current_user
    app.dependency_overrides[get_service_manager] = lambda: test_services
    app.dependency_overrides[get_event_manager] = lambda: test_events
    app.dependency_overrides[get_metrics_collector] = lambda: test_metrics
    app.dependency_overrides[get_port_allocator] = lambda: test_ports

    # Store test instances on app.state so non-DI code paths also work.
    app.state.service_manager = test_services
    app.state.event_manager = test_events
    app.state.metrics_collector = test_metrics
    app.state.port_allocator = test_ports
    app.state.error_log = test_error_log

    # Disable CSRF middleware in tests (tests don't send CSRF tokens).
    app.state.csrf_disabled = True

    # Patch the module-level `services` singleton in env.py so that
    # resolve_supabase_env() finds services registered in the test's manager.
    import codehome.serve.env as env_mod

    _original_env_services = env_mod.services
    env_mod.services = test_services

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        # Expose test instances on the client for direct manipulation.
        c._test_services = test_services
        c._test_events = test_events
        c._test_metrics = test_metrics
        c._test_ports = test_ports
        yield c

    env_mod.services = _original_env_services
    app.state.csrf_disabled = False
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_service_manager, None)
    app.dependency_overrides.pop(get_event_manager, None)
    app.dependency_overrides.pop(get_metrics_collector, None)
    app.dependency_overrides.pop(get_port_allocator, None)

    # Close the ErrorLog's sqlite connection and remove the temp directory.
    test_error_log._conn.close()
    error_log_tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_service(
    client,
    key: str = "test/svc",
    service_type: str = "compose",
    branch: str = "test",
    display_name: str = "Test",
    depends_on: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    state: State = State.STOPPED,
) -> ServiceInstance:
    """Register a service via the test's ServiceManager instance."""
    svc = ServiceInstance(
        key=key,
        service_type=service_type,
        branch=branch,
        display_name=display_name,
        depends_on=depends_on or [],
        metadata=metadata or {},
    )
    # Override state if needed (dataclass default is STOPPED).
    if state != State.STOPPED:
        svc.state = state
    client._test_services.register(svc)
    return svc


# ---------------------------------------------------------------------------
# 1. GET /api/ping
# ---------------------------------------------------------------------------


async def test_ping(client):
    resp = await client.get("/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# 2. GET /api/services -- empty
# ---------------------------------------------------------------------------


async def test_list_services_empty(client):
    resp = await client.get("/api/services")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 3. POST /api/services/register then GET
# ---------------------------------------------------------------------------


async def test_register_then_list(client):
    # Register via the API.
    resp = await client.post(
        "/api/services/register",
        json={
            "key": "mybranch/vite",
            "service_type": "compose",
            "branch": "mybranch",
            "display_name": "Vite",
            "depends_on": [],
            "metadata": {"worktree": "/tmp/wt"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # List should now contain exactly 1 service.
    resp = await client.get("/api/services")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["key"] == "mybranch/vite"
    assert data[0]["state"] == "stopped"
    assert data[0]["type"] == "compose"
    assert data[0]["name"] == "Vite"
    assert data[0]["branch"] == "mybranch"


# ---------------------------------------------------------------------------
# 4. GET /api/services/{key} -- 404
# ---------------------------------------------------------------------------


async def test_get_service_not_found(client):
    resp = await client.get("/api/services/nonexistent/key")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. GET /api/services/{key} -- found
# ---------------------------------------------------------------------------


async def test_get_service_found(client):
    _register_service(client, key="branch/supabase", display_name="Supabase")

    resp = await client.get("/api/services/branch/supabase")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "branch/supabase"
    assert data["name"] == "Supabase"
    assert data["state"] == "stopped"


# ---------------------------------------------------------------------------
# 6. POST start -- 409 on unmet dependency
# ---------------------------------------------------------------------------


async def test_start_blocked_by_dependency(client):
    # Register supabase (stopped) and vite (depends on supabase).
    _register_service(
        client,
        key="br/supabase",
        service_type="supabase",
        display_name="Supabase",
        branch="br",
    )
    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        display_name="Vite",
        branch="br",
        depends_on=["br/supabase"],
    )

    resp = await client.post("/api/services/br/vite/start")
    assert resp.status_code == 409
    assert "Supabase" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 7. POST stop -- 409 when not running
# ---------------------------------------------------------------------------


async def test_stop_not_running(client):
    _register_service(client, key="br/svc", display_name="Svc")

    resp = await client.post("/api/services/br/svc/stop", json={})
    assert resp.status_code == 409
    assert "stopped" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 8. GET /api/port-allocations (authed dashboard view)
# ---------------------------------------------------------------------------


async def test_get_port_allocations(client):
    # Pre-populate a compose allocation so the response isn't just empty dicts.
    # Directly set on the test PortAllocator (no disk I/O needed).
    client._test_ports._compose["test-branch/vite"] = 8080

    resp = await client.get("/api/port-allocations")
    assert resp.status_code == 200
    data = resp.json()

    # Top-level keys must be present and be dicts.
    assert isinstance(data["compose"], dict)
    assert isinstance(data["supabase"], dict)

    # The allocation we just made should appear under compose.
    assert "test-branch/vite" in data["compose"]
    assert data["compose"]["test-branch/vite"] == 8080


# ---------------------------------------------------------------------------
# 8b. GET /api/ports (narrow, unauthenticated)
# ---------------------------------------------------------------------------


async def test_api_ports_unauth_lists_running_vite_services(client):
    """Return running Vite services on the narrow unauth ``/api/ports`` endpoint.

    The endpoint must respond without an Authorization header, and must
    expose only (branch, app, port, state) -- no other metadata leaks.
    """
    # Register two Vite services (one running, one not) + one non-Vite service.
    running = _register_service(
        client,
        key="bag:lisa/vite-bag",
        branch="bag:lisa",
        display_name="Vite Bag",
    )
    running.port = 59899
    running.state = State.RUNNING

    stopped = _register_service(
        client,
        key="bag:lisa/vite-orders",
        branch="bag:lisa",
        display_name="Vite Orders",
    )
    stopped.port = 59900  # present but irrelevant since state != running

    _register_service(
        client,
        key="bag:lisa/supabase",
        branch="bag:lisa",
        display_name="Supabase",
        service_type="supabase",
    )

    # Drop the auth override for this request to prove no token is needed.
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = await client.get("/api/ports")
    finally:
        # Restore the override for subsequent tests in this client fixture.
        app.dependency_overrides[get_current_user] = _fake_current_user

    assert resp.status_code == 200
    entries = resp.json()
    assert isinstance(entries, list)

    # Exactly one entry: the running Vite service.
    assert len(entries) == 1
    entry = entries[0]
    assert entry == {
        "branch": "bag:lisa",
        "app": "bag",
        "port": 59899,
        "state": "running",
    }
    # Hard-check the narrowness: no extra fields should leak.
    assert set(entry.keys()) == {"branch", "app", "port", "state"}


# ---------------------------------------------------------------------------
# 9. GET /api/metrics/history
# ---------------------------------------------------------------------------


async def test_metrics_history(client):
    # Pre-populate the collector with sample data so we can verify structure.
    sample_entry = {
        "ts": time.time(),
        "containers": [
            {
                "key": "veliu-vite-1",
                "cpu_pct": 12.5,
                "mem_mb": 64.0,
                "mem_limit_mb": 512.0,
                "net_rx": 1024,
                "net_tx": 2048,
            },
        ],
    }
    client._test_metrics.history.append(sample_entry)

    resp = await client.get("/api/metrics/history")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1

    entry = data[0]
    assert "ts" in entry
    assert isinstance(entry["ts"], float)
    assert "containers" in entry
    assert isinstance(entry["containers"], list)
    assert len(entry["containers"]) == 1

    container = entry["containers"][0]
    assert container["key"] == "veliu-vite-1"
    assert container["cpu_pct"] == 12.5
    assert container["mem_mb"] == 64.0
    assert container["mem_limit_mb"] == 512.0
    assert container["net_rx"] == 1024
    assert container["net_tx"] == 2048


# ---------------------------------------------------------------------------
# 10. GET /api/tests
# ---------------------------------------------------------------------------


async def test_list_tests(client):
    # Mock discover_suites with realistic data and verify it passes through.
    mock_suites = [
        {
            "suite": "trade-in",
            "path": "/repos/bag/tests/trade-in.tests.json",
            "test_count": 5,
            "type": "repo",
        },
        {
            "suite": "bag:navchat",
            "path": "/repos/bag/branches/bag:navchat/tests.json",
            "test_count": 3,
            "type": "branch",
        },
    ]
    with patch("codehome.serve.routers.services.discover_suites", return_value=mock_suites):
        resp = await client.get("/api/tests")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # Verify each suite has the expected fields and values.
    first = data[0]
    assert first["suite"] == "trade-in"
    assert first["path"] == "/repos/bag/tests/trade-in.tests.json"
    assert first["test_count"] == 5
    assert first["type"] == "repo"

    second = data[1]
    assert second["suite"] == "bag:navchat"
    assert second["test_count"] == 3
    assert second["type"] == "branch"


# ---------------------------------------------------------------------------
# 11. GET /api/p/tdd/status -- idle (TDD is now a plugin)
# ---------------------------------------------------------------------------


async def test_tdd_status_idle(client):
    resp = await client.get("/api/p/tdd/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    # When idle, the response should contain ONLY the "running" key --
    # no run_id, phase, or status fields (those appear only when active).
    assert set(data.keys()) == {"running"}


# ---------------------------------------------------------------------------
# 12. POST /api/branch/switch
# ---------------------------------------------------------------------------


async def test_branch_switch(client):
    # The endpoint fires events via bus_fire() which goes through the global
    # EventBus singleton.  The SSE subscriber bridge (install_sse_subscriber)
    # is only installed during server lifespan, which tests skip.  Patch
    # bus_fire in the branches router to forward directly to the test's
    # EventManager so we can verify the SSE broadcast.
    test_events = client._test_events

    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    test_events._clients.add(q)

    async def _forwarding_bus_fire(event):
        """Forward bus events to the test EventManager."""
        await test_events.broadcast(event.name, event.payload)

    try:
        with patch("_plugin_core_routes.bus_fire", side_effect=_forwarding_bus_fire):
            resp = await client.post("/api/branch/switch", json={"branch": "bag:feat-x"})
        assert resp.status_code == 200
        data = resp.json()
        # Verify full response shape (only "ok" key).
        assert data == {"ok": True}

        # Verify the SSE broadcast happened with the correct payload.
        msg = q.get_nowait()
        assert "branch.switch" in msg
        assert "bag:feat-x" in msg
    finally:
        test_events._clients.discard(q)


# ---------------------------------------------------------------------------
# 13. POST /api/services/{key}/start -- successful compose start
# ---------------------------------------------------------------------------


async def test_start_compose_service(client):
    """Happy path: register a compose service, mock docker, start it."""
    # A running Supabase service is required for resolve_supabase_env.
    _register_service(
        client,
        key="br/supabase",
        service_type="supabase",
        branch="br",
        display_name="Supabase",
        state=State.RUNNING,
        metadata={
            "connection": {
                "api_url": "http://127.0.0.1:54321",
                "anon_key": "test-anon-key",
                "service_role_key": "test-srk",
            },
        },
    )

    # Register the compose service to be started.
    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        branch="br",
        display_name="Vite",
        metadata={
            "compose_service": "vite",
            "worktree": "/tmp/wt",
            "app_dir": "bag.veliu.com",
        },
    )

    with (
        patch("codehome.serve.ports.PortAllocator.allocate", return_value=8080),
        patch("codehome.serve.docker.compose_up", return_value=(True, "Started vite.")),
        patch("codehome.serve.docker.build_vite_env", return_value={"VITE_PORT": "8080"}),
    ):
        resp = await client.post("/api/services/br/vite/start")

    # Start is now async (202 Accepted) with an operation_id for progress tracking.
    assert resp.status_code == 202
    data = resp.json()
    assert "operation_id" in data
    assert data["service_key"] == "br/vite"


# ---------------------------------------------------------------------------
# 14. POST /api/services/{key}/stop -- successful compose stop
# ---------------------------------------------------------------------------


async def test_stop_compose_service(client):
    """Happy path: stop a running compose service."""
    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        branch="br",
        display_name="Vite",
        state=State.RUNNING,
        metadata={"compose_service": "vite"},
    )

    with patch("codehome.serve.docker.compose_down", return_value=(True, "Stopped.")):
        resp = await client.post("/api/services/br/vite/stop", json={})

    # Stop is now async (202 Accepted) with an operation_id for progress tracking.
    assert resp.status_code == 202
    data = resp.json()
    assert "operation_id" in data
    assert data["service_key"] == "br/vite"


# ---------------------------------------------------------------------------
# 15. POST /api/services/{key}/restart -- successful restart
# ---------------------------------------------------------------------------


async def test_restart_compose_service(client):
    """Happy path: restart a running compose service (stop then start)."""
    # Running Supabase needed for the start phase.
    _register_service(
        client,
        key="br/supabase",
        service_type="supabase",
        branch="br",
        display_name="Supabase",
        state=State.RUNNING,
        metadata={
            "connection": {
                "api_url": "http://127.0.0.1:54321",
                "anon_key": "test-anon-key",
                "service_role_key": "test-srk",
            },
        },
    )

    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        branch="br",
        display_name="Vite",
        state=State.RUNNING,
        metadata={
            "compose_service": "vite",
            "worktree": "/tmp/wt",
            "app_dir": "bag.veliu.com",
        },
    )

    with (
        patch("codehome.serve.docker.compose_down", return_value=(True, "Stopped.")),
        patch("codehome.serve.docker.compose_up", return_value=(True, "Started vite.")),
        patch("codehome.serve.ports.PortAllocator.allocate", return_value=8080),
        patch("codehome.serve.docker.build_vite_env", return_value={"VITE_PORT": "8080"}),
    ):
        resp = await client.post("/api/services/br/vite/restart")

    # Restart is now async (202 Accepted) with an operation_id for progress tracking.
    assert resp.status_code == 202
    data = resp.json()
    assert "operation_id" in data
    assert data["service_key"] == "br/vite"


# ---------------------------------------------------------------------------
# 16. DELETE /api/services/{key} -- successful unregister
# ---------------------------------------------------------------------------


async def test_unregister_stopped_service(client):
    """Happy path: unregister a stopped service, then verify 404."""
    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        branch="br",
        display_name="Vite",
        state=State.STOPPED,
    )

    resp = await client.delete("/api/services/br/vite")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Service should be gone.
    resp = await client.get("/api/services/br/vite")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 17. POST /api/services/cleanup -- successful branch cleanup
# ---------------------------------------------------------------------------


async def test_cleanup_branch_services(client):
    """Happy path: cleanup stops and removes all services for a branch."""
    # Register a running compose service and a running supabase service.
    _register_service(
        client,
        key="br/supabase",
        service_type="supabase",
        branch="br",
        display_name="Supabase",
        state=State.RUNNING,
        metadata={"worktree": "/tmp/wt"},
    )
    _register_service(
        client,
        key="br/vite",
        service_type="compose",
        branch="br",
        display_name="Vite",
        state=State.RUNNING,
        metadata={"compose_service": "vite"},
    )

    with (
        patch("codehome.serve.service_lifecycle.sb.stop", return_value=(True, "ok")),
        # sb.restore_config is called after sb.stop for supabase services; mock it
        # to avoid shelling out to real `git restore`/`git update-index` commands.
        patch("codehome.serve.service_lifecycle.sb.restore_config", return_value=None),
        patch("codehome.serve.docker.compose_down", return_value=(True, "Stopped.")),
    ):
        resp = await client.post(
            "/api/services/cleanup",
            json={"branch": "br"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["cleaned"] == 2

    # Both services should be gone from the registry.
    resp = await client.get("/api/services/br/supabase")
    assert resp.status_code == 404
    resp = await client.get("/api/services/br/vite")
    assert resp.status_code == 404
