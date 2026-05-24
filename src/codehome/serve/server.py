"""ASGI server for the local dev orchestration server.

Uses wesktop for core routing and middleware. Plugin routes remain on FastAPI
during the transition (Phase 10 migrates plugin routes to wesktop).

Architecture:
- Core routers (auth, system, features, plugins, services, conductor, agents)
  are wesktop Routers composed into a single wesktop Router.
- Plugin-contributed routers remain FastAPI APIRouters on a FastAPI sub-app.
- A composite ASGI app tries the wesktop app first, falling back to the
  FastAPI app for plugin routes, with SPA static file fallback.
"""

import asyncio
import mimetypes
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from wesktop import Router, create_app as wesktop_create_app

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
# Lifespan (shared by both wesktop and FastAPI apps)
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

    # Also store on FastAPI app.state for plugin routes that still use
    # request.app.state.X during the hybrid migration phase.
    _fastapi_app.state.config = config
    _fastapi_app.state.event_manager = events
    _fastapi_app.state.service_manager = services
    _fastapi_app.state.port_allocator = ports

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
        _fastapi_app.state.metrics_collector = metrics_collector
    if features.enabled("terminal"):
        from codehome.pty import pty_manager

        state["pty_manager"] = pty_manager
        _fastapi_app.state.pty_manager = pty_manager
    if features.enabled("conductor"):
        state["agent_session_manager"] = agent_sessions
        state["question_store"] = question_store
        _fastapi_app.state.agent_session_manager = agent_sessions
        _fastapi_app.state.question_store = question_store
    if features.enabled("push"):
        state["push_manager"] = push_manager
        _fastapi_app.state.push_manager = push_manager
        # Wire push notifications into the event broadcast pipeline.
        events.set_push_manager(push_manager)

    # Periodic update checker (compares pyproject.toml version to git tags).
    update_checker = UpdateChecker()
    state["update_checker"] = update_checker
    _fastapi_app.state.update_checker = update_checker
    update_checker.start()

    # SQLite-backed error log for frontend diagnostics and internal errors.
    from codehome.serve.error_log import ErrorLog

    _superv = codehome_home()
    _superv.mkdir(parents=True, exist_ok=True)
    error_log = ErrorLog(_superv / "error_log.db")
    state["error_log"] = error_log
    _fastapi_app.state.error_log = error_log
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
        _fastapi_app.state.health_checker = health_checker
        health_task = asyncio.create_task(health_checker.run())
        state["log_aggregator"] = log_aggregator
        _fastapi_app.state.log_aggregator = log_aggregator
        log_task = asyncio.create_task(log_aggregator.run())

    # Start plugin-contributed background tasks (respects feature gates).
    background_tasks.start_all()

    # Expose the fetch_scheduler (if running) on state for the
    # manual-trigger API endpoint -- resolved by name, no plugin import.
    _fs = background_tasks.get_task("fetch_scheduler")
    if _fs is not None:
        state["fetch_scheduler"] = _fs
        _fastapi_app.state.fetch_scheduler = _fs

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
# Plugin routes (now wesktop Router, mounted on _wesktop_router)
# ---------------------------------------------------------------------------

# FastAPI sub-app retained only for backward compatibility with tests that
# set app.dependency_overrides. Plugin routes now live on _wesktop_router.
_fastapi_app = FastAPI(title="Veliu Dev Dashboard")

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
# Composite ASGI app: wesktop core + FastAPI plugins
# ---------------------------------------------------------------------------

# Create the wesktop ASGI app with lifespan but NO built-in middleware
# (we apply middleware manually below to wrap both wesktop + FastAPI).
# Share FastAPI's dependency_overrides dict so tests that set
# app.dependency_overrides[get_current_user] also affect wesktop DI.
_wesktop_app = wesktop_create_app(
    _wesktop_router,
    lifespan=lifespan,
    request_id=False,
    request_timing=False,
    dependency_overrides=_fastapi_app.dependency_overrides,
)


class _CompositeApp:
    """ASGI app that routes to wesktop for core endpoints and FastAPI for plugins.

    Lifespan is handled by the wesktop app. HTTP requests try the wesktop
    router first; if no route matches (404), the request falls through to
    the FastAPI app for plugin routes. Unmatched GET requests in stable
    mode are served by the SPA fallback (dashboard static files).

    Exposes .state and .dependency_overrides from the FastAPI app for
    backward compatibility with plugins and tests.
    """

    def __init__(
        self,
        wesktop_app: Any,
        fastapi_app: FastAPI,
        wesktop_router: Router,
        spa_static_dir: Path | None = None,
    ) -> None:
        object.__setattr__(self, "_wesktop_app", wesktop_app)
        object.__setattr__(self, "_fastapi_app", fastapi_app)
        object.__setattr__(self, "_wesktop_router", wesktop_router)
        object.__setattr__(self, "_spa_static_dir", spa_static_dir)

    def _inject_fastapi_state(self, scope: Any) -> None:
        """Merge FastAPI app.state attributes into scope["state"].

        During the hybrid phase, tests set app.state.config (which goes to
        FastAPI's state).  Wesktop reads from scope["state"].  This bridge
        ensures wesktop handlers see the same state as FastAPI handlers.

        Also ensures that when running with the real lifespan, any test
        overrides on FastAPI state are visible to wesktop routes.
        """
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        fa_state = getattr(fastapi_app, "state", None)
        if fa_state is None:
            return
        if "state" not in scope:
            scope["state"] = {}
        # Starlette's State stores attributes in _state dict; dir() won't
        # list them.  Access the internal dict directly.
        state_dict = getattr(fa_state, "_state", None)
        if state_dict is None:
            return
        for key, value in state_dict.items():
            if key.startswith("_"):
                continue
            # Don't overwrite values already set by the wesktop lifespan.
            if key not in scope["state"]:
                scope["state"][key] = value

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        wesktop_app = object.__getattribute__(self, "_wesktop_app")
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        wesktop_router = object.__getattribute__(self, "_wesktop_router")
        spa_static_dir = object.__getattribute__(self, "_spa_static_dir")

        if scope["type"] == "lifespan":
            # wesktop handles lifespan (state dict propagation).
            await wesktop_app(scope, receive, send)
            return

        # Inject FastAPI state into scope for wesktop routes.
        self._inject_fastapi_state(scope)

        if scope["type"] == "websocket":
            # Check wesktop WS routes first, fall back to FastAPI.
            path = scope.get("path", "")
            if wesktop_router.match_ws(path):
                await wesktop_app(scope, receive, send)
            else:
                await fastapi_app(scope, receive, send)
            return

        if scope["type"] == "http":
            method = scope["method"]
            path = scope["path"]

            # Check if wesktop router has a matching route.
            if wesktop_router.match(method, path):
                await wesktop_app(scope, receive, send)
                return

            # Try FastAPI for plugin routes (non-SPA paths).
            if path.startswith("/api/") or path.startswith("/events") or path == "/api":
                await fastapi_app(scope, receive, send)
                return

            # Plugin routes that use root_routes=true may live at any path;
            # try FastAPI for non-GET methods (SPA fallback only serves GET).
            if method != "GET":
                await fastapi_app(scope, receive, send)
                return

            # SPA fallback: serve static files from the dashboard build.
            if spa_static_dir is not None:
                await self._serve_spa(scope, receive, send, spa_static_dir, path)
                return

            # No SPA dir available -- let FastAPI handle (it will 404).
            await fastapi_app(scope, receive, send)
            return

        # Unknown scope type -- try wesktop.
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

    def __getattr__(self, name: str) -> Any:
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        return getattr(fastapi_app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        setattr(fastapi_app, name, value)


_composite = _CompositeApp(_wesktop_app, _fastapi_app, _wesktop_router, _SPA_STATIC_DIR)


# ---------------------------------------------------------------------------
# Middleware stack wrapping the composite app
# ---------------------------------------------------------------------------
# Middleware order (innermost first):
#   RequestTimingMiddleware -> CSRF -> RequestIDMiddleware -> [CORS] -> [ViteProxy]

# CSRF middleware with codehome-specific exempt paths and token-file bypass.
_csrf_mw = CodehomeCSRFMiddleware(
    _composite,
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


# The module-level ``app`` is ASGI-callable (for Granian) and also exposes
# ``.state`` and ``.dependency_overrides`` (for plugins and tests).
class _ASGIAppProxy:
    """Proxy that wraps the ASGI middleware stack while exposing FastAPI's state."""

    def __init__(self, asgi_app: Any, fastapi_app: FastAPI) -> None:
        object.__setattr__(self, "_asgi_app", asgi_app)
        object.__setattr__(self, "_fastapi_app", fastapi_app)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        asgi_app = object.__getattribute__(self, "_asgi_app")
        await asgi_app(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        return getattr(fastapi_app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        fastapi_app = object.__getattribute__(self, "_fastapi_app")
        setattr(fastapi_app, name, value)


app: Any = _ASGIAppProxy(_outer_app, _fastapi_app)
