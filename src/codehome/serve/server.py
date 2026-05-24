"""ASGI server for the local dev orchestration server.

Uses wesktop for all routing and middleware. Core and plugin routes are
composed into a single wesktop Router. SPA static file serving is handled
by a lightweight fallback wrapper around the wesktop app.

Architecture:
- Core routers (auth, system, features, plugins, services, conductor, agents)
  are wesktop Routers composed into a single wesktop Router.
- Plugin-contributed routers are wesktop Routers mounted on the main router.
- An SPA fallback wrapper serves dashboard static files for non-API paths
  in stable mode (dev mode uses the Vite dev proxy instead).
"""

import asyncio
import mimetypes
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from wesktop import Router, State, create_app as wesktop_create_app

from wesktop.middleware import RequestIDMiddleware, RequestTimingMiddleware

from codehome.serve.csrf import CodehomeCSRFMiddleware

from codehome.config import load_server_config
from codehome.paths import codehome_home
from codehome.serve.agent_sessions import agent_sessions
from codehome.serve.background import background_tasks
from codehome.serve.auth_deps import get_current_user
from codehome.serve.discovery import discover_running
from codehome.serve.error_tracking import init_sentry
from codehome.serve.events import events
from codehome.serve.metrics import metrics_collector
from codehome.serve.monitoring.health import health_checker
from codehome.serve.monitoring.logs import log_aggregator
from codehome.serve.ports import ports
from codehome.serve.push import push_manager
from codehome.serve.questions import question_store
from codehome.serve.services import services
from codehome.serve.updater import UpdateChecker

# Dev mode: set by server_cmd.py via V_SERVER_DEV=1 env var.
_DEV_MODE = os.environ.get("V_SERVER_DEV") == "1"

# Set in lifespan, not at import time, so uptime reflects actual server start.
_server_start_time: float = 0

# CSRF exempt paths -- unauthenticated POSTs that carry no Bearer token.
_CSRF_EXEMPT_PATHS = [
    "/api/auth/login",
    "/api/diagnostics/errors",
    "/api/services/discover",
]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

# State dict populated during lifespan, merged into every wesktop request's
# scope["state"] by create_app's lifespan integration.
_lifespan_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_app: Any) -> AsyncGenerator[dict[str, Any], None]:
    global _server_start_time
    _server_start_time = time.time()

    # Configure structured logging before anything else.
    from codehome.serve.logging_config import configure_logging

    configure_logging()

    from codehome.serve.logging_config import get_logger

    _log = get_logger(component="lifespan")

    # Load server config once at startup; store in lifespan state for dependencies.
    config = load_server_config()
    if not config:
        msg = "Server config not found. Run `v auth setup` first."
        raise RuntimeError(msg)
    _log.info("server starting", port=config.port)

    # Initialize Sentry if a DSN is configured (no-op otherwise).
    init_sentry(config)

    # Build lifespan state dict -- all singletons accessible via request.state.
    state: dict[str, Any] = {
        "config": config,
        "event_manager": events,
        "service_manager": services,
        "port_allocator": ports,
    }

    # Also mirror onto _app_state so that tests setting app.state.X before
    # the lifespan runs (or overriding after) are visible to wesktop routes.
    _app_state.config = config
    _app_state.event_manager = events
    _app_state.service_manager = services
    _app_state.port_allocator = ports

    # Install event bus subscribers: SSE forwarder + JSONL audit log.
    # The bus singleton exists at import time; subscribers are wired here
    # so the server's EventManager is available for SSE forwarding.
    from codehome.bus import bus
    from codehome.bus import registry as _bus_registry
    from codehome.bus.subscribers.audit import install_audit_subscriber
    from codehome.bus.subscribers.sse import install_sse_subscriber

    install_sse_subscriber(bus, _bus_registry, events)
    install_audit_subscriber(bus, _bus_registry)

    # Feature-gated singletons: only assign when the flag is enabled.
    if features.enabled("monitoring"):
        state["metrics_collector"] = metrics_collector
        _app_state.metrics_collector = metrics_collector
    if features.enabled("terminal"):
        from codehome.pty import pty_manager

        state["pty_manager"] = pty_manager
        _app_state.pty_manager = pty_manager
    if features.enabled("conductor"):
        state["agent_session_manager"] = agent_sessions
        state["question_store"] = question_store
        _app_state.agent_session_manager = agent_sessions
        _app_state.question_store = question_store
    if features.enabled("push"):
        state["push_manager"] = push_manager
        _app_state.push_manager = push_manager
        # Wire push notifications into the event broadcast pipeline.
        events.set_push_manager(push_manager)

    # Periodic update checker (compares pyproject.toml version to git tags).
    update_checker = UpdateChecker()
    state["update_checker"] = update_checker
    _app_state.update_checker = update_checker
    update_checker.start()

    # SQLite-backed error log for frontend diagnostics and internal errors.
    from codehome.serve.error_log import ErrorLog

    _superv = codehome_home()
    _superv.mkdir(parents=True, exist_ok=True)
    error_log = ErrorLog(_superv / "error_log.db")
    state["error_log"] = error_log
    _app_state.error_log = error_log
    error_log.prune(days=30)

    await discover_running()
    # Second-pass: scan the Docker daemon for Compose containers started
    # outside the server (raw `docker compose up`, `make up`, etc.).
    # Read-only; safe to call unconditionally.  See discovery.discover_docker.
    try:
        from codehome.core.ops.discovery import discover_docker

        docker_discovered = await discover_docker()
    except Exception:
        docker_discovered = []
        _log.exception("docker discovery failed")
    _log.info(
        "discovery complete",
        services=len(services.list_all()),
        docker_discovered=len(docker_discovered),
    )

    # Monitoring background tasks: metrics, health checks, log aggregation.
    metrics_task = None
    health_task = None
    log_task = None
    if features.enabled("monitoring"):
        metrics_task = asyncio.create_task(metrics_collector.run())
        state["health_checker"] = health_checker
        _app_state.health_checker = health_checker
        health_task = asyncio.create_task(health_checker.run())
        state["log_aggregator"] = log_aggregator
        _app_state.log_aggregator = log_aggregator
        log_task = asyncio.create_task(log_aggregator.run())

    # Start plugin-contributed background tasks (respects feature gates).
    background_tasks.start_all()

    # Expose the fetch_scheduler (if running) on state for the
    # manual-trigger API endpoint -- resolved by name, no plugin import.
    _fs = background_tasks.get_task("fetch_scheduler")
    if _fs is not None:
        state["fetch_scheduler"] = _fs
        _app_state.fetch_scheduler = _fs

    # Dev mode: spawn Vite dev server for HMR and drain its output.
    vite_proc = None
    vite_drain_task = None
    if _DEV_MODE:
        from codehome.serve.vite_dev import drain_vite_output, start_vite_dev

        try:
            vite_proc, vite_port = await start_vite_dev()
            # Set the port on the proxy middleware so it starts forwarding.
            # _vite_proxy is the module-level _LazyViteProxy wrapping wesktop's ViteDevProxy.
            if _vite_proxy is not None:
                _vite_proxy.vite_port = vite_port
            vite_drain_task = asyncio.create_task(drain_vite_output(vite_proc))
            _log.info("vite dev server started", port=vite_port)
        except Exception:
            _log.exception("failed to start Vite dev server -- falling back to static files")

    yield state
    _log.info("server shutting down")

    # Stop Vite dev server.
    if vite_proc is not None:
        from codehome.serve.vite_dev import stop_vite_dev

        await stop_vite_dev(vite_proc)
        if vite_drain_task is not None:
            vite_drain_task.cancel()
    if _vite_proxy is not None:
        await _vite_proxy.close()
    # Stop plugin-contributed background tasks.
    background_tasks.stop_all()
    # Stop all active Conductor sessions to avoid orphaned processes.
    if features.enabled("conductor"):
        from codehome.serve.conductor import (
            _conductors as conductor_registry,
        )
        from codehome.serve.conductor import (
            stop_session as conductor_stop_session,
        )

        for branch in list(conductor_registry):
            try:
                await conductor_stop_session(branch)
            except Exception:
                pass
    if log_task is not None:
        log_aggregator.stop()
        log_task.cancel()
    if health_task is not None:
        health_checker.stop()
        health_task.cancel()
    if metrics_task is not None:
        metrics_collector.stop()
        metrics_task.cancel()
    update_checker.stop()
    if features.enabled("terminal"):
        from codehome.pty import pty_manager

        await pty_manager.cleanup_all()
    error_log.close()


# ---------------------------------------------------------------------------
# Load plugins (must happen before router mounting)
# ---------------------------------------------------------------------------

from codehome.plugins.loader import load_all_plugins as _load_plugins  # noqa: E402

_load_plugins()

from codehome import features  # noqa: E402


# ---------------------------------------------------------------------------
# Wesktop core router composition
# ---------------------------------------------------------------------------

from codehome.serve.routers import (  # noqa: E402
    agents,
    auth,
    conductor,
    system,
)
from codehome.serve.routers import (  # noqa: E402
    features as features_router,
)
from codehome.serve.routers import plugins as plugins_router  # noqa: E402
from codehome.serve.routers import services as services_router  # noqa: E402

# Build the main wesktop Router by composing all core sub-routers.
_wesktop_router = Router()

# Public ping endpoint (no auth, no router file needed).
@_wesktop_router.get("/api/ping")
async def ping(request: Any) -> dict[str, bool]:
    return {"ok": True}

# Public routers (no auth dependency).
_wesktop_router.include_router(auth.public_router)
_wesktop_router.include_router(system.public_router)
_wesktop_router.include_router(services_router.public_router)
_wesktop_router.include_router(features_router.router)

# Feature-gated public routers
if features.enabled("conductor"):
    _wesktop_router.include_router(agents.public_router)

# Push public endpoint (VAPID key retrieval, no auth required).
if features.enabled("push"):
    _push_router = Router()

    @_push_router.get("/api/push/vapid-key")
    async def _get_vapid_key(request: Any) -> object:
        """Return the public VAPID key for push subscription registration."""
        return {"public_key": push_manager.public_key}

    _wesktop_router.include_router(_push_router)

# Authenticated routers (all endpoints require valid JWT via router-level deps).
_auth_deps: dict[str, Any] = {"user": get_current_user}

_wesktop_router.include_router(auth.router, deps=_auth_deps)
_wesktop_router.include_router(services_router.router, deps=_auth_deps)
_wesktop_router.include_router(features_router.authed_router, deps=_auth_deps)

if features.enabled("plugins"):
    _wesktop_router.include_router(plugins_router.router, deps=_auth_deps)
if features.enabled("conductor"):
    _wesktop_router.include_router(conductor.router, deps=_auth_deps)
    _wesktop_router.include_router(agents.router, deps=_auth_deps)


# ---------------------------------------------------------------------------
# Plugin routes (wesktop Router, mounted on _wesktop_router)
# ---------------------------------------------------------------------------

# Plugin-contributed routers (wesktop Router, gated on "plugins" flag).
from codehome.plugins import registry as _plugin_registry  # noqa: E402

if features.enabled("plugins"):
    for _plugin in _plugin_registry.list_plugins():
        if _plugin.router is not None:
            if _plugin.manifest.root_routes:
                _wesktop_router.include_router(
                    _plugin.router,
                    deps=_auth_deps,
                )
            else:
                _wesktop_router.include_router(
                    _plugin.router,
                    prefix=f"/api/p/{_plugin.name}",
                    deps=_auth_deps,
                )
        # Public routers (e.g. WebSocket endpoints with query-param auth)
        if _plugin.public_router is not None:
            if _plugin.manifest.root_routes:
                _wesktop_router.include_router(_plugin.public_router)
            else:
                _wesktop_router.include_router(
                    _plugin.public_router,
                    prefix=f"/api/p/{_plugin.name}",
                )

# ---------------------------------------------------------------------------
# SPA static file fallback (stable mode only)
# ---------------------------------------------------------------------------
# Discover the dashboard plugin's static directory for SPA serving.
# In dev mode, Vite serves all frontend assets via the dev proxy.

def _find_dashboard_static() -> Path | None:
    """Discover the dashboard plugin's static directory.

    Returns the plugin's static/ dir if the plugin is loaded and the
    directory exists, otherwise falls back to the legacy path. Returns
    None if neither exists.
    """
    from codehome.plugins import registry as _pr

    plugin = _pr.get("dashboard")
    if plugin is not None:
        plugin_static = Path(plugin.plugin_dir) / "static"
        if plugin_static.is_dir():
            return plugin_static
    # Fallback: legacy location adjacent to the server module.
    legacy = Path(__file__).parent / "static"
    if legacy.is_dir():
        return legacy
    return None


_SPA_STATIC_DIR: Path | None = None if _DEV_MODE else _find_dashboard_static()


# ---------------------------------------------------------------------------
# App state and dependency overrides (test and lifespan integration)
# ---------------------------------------------------------------------------

# Standalone State and dependency_overrides dict.  The wesktop app receives
# the overrides dict at creation time (shared reference), so mutations to
# app.dependency_overrides[key] are reflected in wesktop's DI resolver.
# _app_state is written to by the lifespan and by tests (via app.state.X);
# it is injected into every request's scope["state"] by _SPAFallbackApp.
_app_state = State()
_dependency_overrides: dict[Any, Any] = {}


# ---------------------------------------------------------------------------
# Wesktop ASGI app
# ---------------------------------------------------------------------------

# Create the wesktop ASGI app with lifespan but NO built-in middleware
# (we apply middleware manually below to control the stack).
_wesktop_app = wesktop_create_app(
    _wesktop_router,
    lifespan=lifespan,
    request_id=False,
    request_timing=False,
    dependency_overrides=_dependency_overrides,
)


# ---------------------------------------------------------------------------
# SPA fallback wrapper
# ---------------------------------------------------------------------------

class _SPAFallbackApp:
    """ASGI wrapper that adds SPA static file serving and state injection.

    Delegates all routing to the wesktop app. For HTTP GET requests that
    don't match any route, serves static files from the dashboard build
    directory with index.html fallback for client-side routing.

    Injects _app_state into every request's scope["state"] so that test
    overrides (app.state.X = ...) are visible to wesktop route handlers.
    """

    def __init__(
        self,
        wesktop_app: Any,
        spa_static_dir: Path | None = None,
    ) -> None:
        object.__setattr__(self, "_wesktop_app", wesktop_app)
        object.__setattr__(self, "_spa_static_dir", spa_static_dir)

    def _inject_app_state(self, scope: Any) -> None:
        """Merge _app_state attributes into scope["state"].

        Tests set app.state.config (which writes to _app_state).
        Wesktop reads from scope["state"]. This bridge ensures wesktop
        handlers see values set on app.state by tests or the lifespan.
        """
        if "state" not in scope:
            scope["state"] = {}
        state_dict = object.__getattribute__(_app_state, "_data")
        for key, value in state_dict.items():
            if key.startswith("_"):
                continue
            # Don't overwrite values already set by the wesktop lifespan.
            if key not in scope["state"]:
                scope["state"][key] = value

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        wesktop_app = object.__getattribute__(self, "_wesktop_app")
        spa_static_dir = object.__getattribute__(self, "_spa_static_dir")

        if scope["type"] == "lifespan":
            await wesktop_app(scope, receive, send)
            return

        # Inject app state into scope for wesktop routes.
        self._inject_app_state(scope)

        if scope["type"] in ("websocket", "http"):
            path = scope.get("path", "")

            # For HTTP requests, check if SPA fallback is needed.
            if scope["type"] == "http":
                method = scope["method"]
                if _wesktop_router.match(method, path):
                    await wesktop_app(scope, receive, send)
                    return

                # SPA fallback: serve static files for unmatched GET requests.
                if method == "GET" and spa_static_dir is not None:
                    if not (path.startswith("/api/") or path.startswith("/events") or path == "/api"):
                        await self._serve_spa(scope, receive, send, spa_static_dir, path)
                        return

                # Unmatched non-GET or API paths -- let wesktop handle (404).
                await wesktop_app(scope, receive, send)
                return

            # WebSocket -- let wesktop handle.
            await wesktop_app(scope, receive, send)
            return

        # Unknown scope type -- let wesktop handle.
        await wesktop_app(scope, receive, send)

    @staticmethod
    async def _serve_spa(
        scope: Any, receive: Any, send: Any, static_dir: Path, path: str,
    ) -> None:
        """Serve a static file or fall back to index.html for SPA routing."""
        # Drain request body (required by ASGI even for GET).
        while True:
            msg = await receive()
            if not msg.get("more_body", False):
                break

        # Try serving an actual file matching the path.
        rel = path.lstrip("/")
        if rel:
            candidate = static_dir / rel
            if candidate.is_file() and static_dir in candidate.resolve().parents:
                body = candidate.read_bytes()
                ct = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", ct.encode()),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return

        # SPA fallback: serve index.html for client-side routing.
        index = static_dir / "index.html"
        if index.is_file():
            body = index.read_bytes()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # No index.html -- 404.
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b'{"detail": "Not found"}'})


_spa_app = _SPAFallbackApp(_wesktop_app, _SPA_STATIC_DIR)


# ---------------------------------------------------------------------------
# Middleware stack wrapping the app
# ---------------------------------------------------------------------------
# Middleware order (innermost first):
#   RequestTimingMiddleware -> CSRF -> RequestIDMiddleware -> [CORS] -> [ViteProxy]

# CSRF middleware with codehome-specific exempt paths and token-file bypass.
_csrf_mw = CodehomeCSRFMiddleware(
    _spa_app,
    exempt_paths=_CSRF_EXEMPT_PATHS,
)

# Request timing (innermost -- needs request ID in scope for log correlation).
_timing_mw = RequestTimingMiddleware(
    _csrf_mw,
    exclude_paths=["/events", "/api/terminal"],
)

# Request ID (outer -- sets ID before timing runs).
_request_id_mw = RequestIDMiddleware(_timing_mw)

# Build the final ASGI app with optional dev-mode middleware.
_outer_app: Any = _request_id_mw

# Dev mode: add CORS (Vite dev server runs on a different origin).
if _DEV_MODE:
    from wesktop.middleware import CORSMiddleware

    _outer_app = CORSMiddleware(
        _outer_app,
        allow_origins=["https://localhost:5173", "http://localhost:5173"],
    )

# -- Vite dev proxy (dev mode only) ----------------------------------------
# Uses wesktop's ViteDevProxy with a lazy-port wrapper: the inner proxy is
# constructed only when vite_port is set (after start_vite_dev() succeeds
# in the lifespan). Until then, all requests pass through to the inner app.
_vite_proxy: Any = None
if _DEV_MODE:
    from wesktop.middleware import ViteDevProxy as _WesktopViteDevProxy

    class _LazyViteProxy:
        """Wraps wesktop's ViteDevProxy with lazy port assignment.

        Falls through to the inner app until vite_port is set.
        """

        def __init__(self, inner_app: Any) -> None:
            self._inner_app = inner_app
            self._proxy: Any = None

        @property
        def vite_port(self) -> int | None:
            return self._proxy.vite_port if self._proxy else None

        @vite_port.setter
        def vite_port(self, port: int) -> None:
            self._proxy = _WesktopViteDevProxy(self._inner_app, vite_port=port)

        async def close(self) -> None:
            if self._proxy is not None:
                await self._proxy.close()

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if self._proxy is not None:
                await self._proxy(scope, receive, send)
            else:
                await self._inner_app(scope, receive, send)

    _vite_proxy = _LazyViteProxy(_outer_app)
    _outer_app = _vite_proxy


# ---------------------------------------------------------------------------
# Module-level app object
# ---------------------------------------------------------------------------

# The module-level ``app`` is ASGI-callable (for Granian) and also exposes
# ``.state`` and ``.dependency_overrides`` (for tests and plugins).
class _ASGIAppProxy:
    """Proxy that wraps the ASGI middleware stack while exposing state.

    Tests use ``app.state.X = val`` and ``app.dependency_overrides[fn] = mock``
    to configure the server for testing without running the lifespan.
    """

    def __init__(self, asgi_app: Any, state: State, dependency_overrides: dict[Any, Any]) -> None:
        object.__setattr__(self, "_asgi_app", asgi_app)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_dependency_overrides", dependency_overrides)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        asgi_app = object.__getattribute__(self, "_asgi_app")
        await asgi_app(scope, receive, send)

    @property
    def state(self) -> State:
        return object.__getattribute__(self, "_state")

    @property
    def dependency_overrides(self) -> dict[Any, Any]:
        return object.__getattribute__(self, "_dependency_overrides")

    def __getattr__(self, name: str) -> Any:
        # Fall through for any other attribute (e.g. test helpers).
        state = object.__getattribute__(self, "_state")
        return getattr(state, name)

    def __setattr__(self, name: str, value: Any) -> None:
        state = object.__getattribute__(self, "_state")
        setattr(state, name, value)


app: Any = _ASGIAppProxy(_outer_app, _app_state, _dependency_overrides)
