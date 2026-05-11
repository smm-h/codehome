"""Tests for the plugin command dispatch endpoint.

Covers: validation (missing plugin, missing router, path traversal,
missing command), successful dispatch, and response normalisation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter

from codehome.plugins import registry
from codehome.plugins.manifest import DashboardDecl, PluginManifest
from codehome.plugins.registry import LoadedPlugin
from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import get_event_manager
from codehome.serve.error_log import ErrorLog
from codehome.serve.events import EventManager
from codehome.serve.server import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_USER = {"sub": "testadmin", "role": "admin"}


async def _fake_current_user():
    return _FAKE_USER


def _make_plugin(name: str, *, with_router: bool = True) -> LoadedPlugin:
    """Create a test plugin, optionally with a simple router."""
    manifest = PluginManifest(
        name=name,
        description="test",
        dashboard=DashboardDecl(group="root", route=f"/{name}") if with_router else None,
    )
    plugin_router = None
    if with_router:
        plugin_router = APIRouter()

        @plugin_router.post("/ping")
        async def ping():
            return {"pong": True}

        @plugin_router.post("/echo")
        async def echo(message: str = ""):
            return {"echoed": message}

    return LoadedPlugin(
        name=name,
        description="test",
        plugin_dir=f"/fake/{name}",
        manifest=manifest,
        router=plugin_router,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    """Async HTTP client with auth bypass and minimal dependency overrides."""
    test_events = EventManager()
    error_log_tmpdir = tempfile.TemporaryDirectory()
    test_error_log = ErrorLog(Path(error_log_tmpdir.name) / "err.db")

    app.dependency_overrides[get_current_user] = _fake_current_user
    app.dependency_overrides[get_event_manager] = lambda: test_events
    app.state.event_manager = test_events
    app.state.error_log = test_error_log
    app.state.csrf_disabled = True

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    app.state.csrf_disabled = False
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_event_manager, None)
    test_error_log._conn.close()
    error_log_tmpdir.cleanup()


@pytest.fixture(autouse=True)
def _clean_plugin_registry():
    """Ensure each test starts with a clean plugin registry."""
    registry.clear()
    yield
    registry.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_nonexistent_plugin(client):
    """Dispatching to an unregistered plugin returns 404."""
    resp = await client.post(
        "/api/plugins/ghost/command",
        json={"command": "ping"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_plugin_without_router(client):
    """Dispatching to a plugin that has no router returns 400."""
    plugin = _make_plugin("no-routes", with_router=False)
    registry.register(plugin)

    resp = await client.post(
        "/api/plugins/no-routes/command",
        json={"command": "ping"},
    )
    assert resp.status_code == 400
    assert "no routes" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_missing_command_field(client):
    """Payload without a 'command' key returns 400."""
    plugin = _make_plugin("test-plug")
    registry.register(plugin)

    resp = await client.post(
        "/api/plugins/test-plug/command",
        json={"params": {}},
    )
    assert resp.status_code == 400
    assert "command" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_path_traversal_rejected(client):
    """Command names with path traversal characters are rejected."""
    plugin = _make_plugin("test-plug")
    registry.register(plugin)

    for bad_name in ["../../etc/passwd", "../secrets", "cmd/sub", "cmd;rm"]:
        resp = await client.post(
            "/api/plugins/test-plug/command",
            json={"command": bad_name},
        )
        assert resp.status_code == 400, f"Expected 400 for {bad_name!r}, got {resp.status_code}"
        assert "invalid" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_unknown_command(client):
    """A command with no matching route returns 404."""
    plugin = _make_plugin("test-plug")
    registry.register(plugin)

    resp = await client.post(
        "/api/plugins/test-plug/command",
        json={"command": "nonexistent"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dispatch_valid_command(client):
    """A valid command dispatches to the plugin's endpoint and returns the result."""
    plugin = _make_plugin("test-plug")
    registry.register(plugin)

    resp = await client.post(
        "/api/plugins/test-plug/command",
        json={"command": "ping"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}
