"""FastAPI server for the local dev orchestration server."""

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI

from codehome.config import load_server_config
from codehome.paths import codehome_home
from codehome.serve.agent_sessions import agent_sessions
from codehome.serve.background import background_tasks
from codehome.serve.auth_deps import get_current_user
from codehome.core.ops.discovery import discover_docker
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


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _server_start_time
    _server_start_time = time.time()

    # Configure structured logging before anything else.
    from codehome.serve.logging_config import configure_logging

    configure_logging()

    from codehome.serve.logging_config import get_logger

    _log = get_logger(component="lifespan")

    # Load server config once at startup; store on app.state for dependencies.
    config = load_server_config()
    if not config:
        msg = "Server config not found. Run `v auth setup` first."
        raise RuntimeError(msg)
    app.state.config = config
    _log.info("server starting", port=config.port)

    # Initialize Sentry if a DSN is configured (no-op otherwise).
    init_sentry(config)

    # Store module-level singletons on app.state so route handlers can access
    # them via FastAPI Depends() (see dependencies.py).  Both the DI path and
    # direct module imports reference the same instance.
    app.state.event_manager = events
    app.state.service_manager = services
    app.state.port_allocator = ports

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
        app.state.metrics_collector = metrics_collector
    if features.enabled("terminal"):
        from codehome.pty import pty_manager

        app.state.pty_manager = pty_manager
    if features.enabled("conductor"):
        app.state.agent_session_manager = agent_sessions
        app.state.question_store = question_store
    if features.enabled("push"):
        app.state.push_manager = push_manager
        # Wire push notifications into the event broadcast pipeline.
        events.set_push_manager(push_manager)

    # Periodic update checker (compares pyproject.toml version to git tags).
    update_checker = UpdateChecker()
    app.state.update_checker = update_checker
    update_checker.start()

    # Background git fetch scheduler: registered as a background task by the
    # core plugin (started via background_tasks.start_all() below).
    # Exposed on app.state after start_all() for the manual-trigger API endpoint.

    # Slack notification dispatcher: registered as a background task by the
    # core plugin (started via background_tasks.start_all() below).

    # SQLite-backed error log for frontend diagnostics and internal errors.
    from codehome.serve.error_log import ErrorLog

    _superv = codehome_home()
    _superv.mkdir(parents=True, exist_ok=True)
    error_log = ErrorLog(_superv / "error_log.db")
    app.state.error_log = error_log
    error_log.prune(days=30)

    await discover_running()
    # Second-pass: scan the Docker daemon for Compose containers started
    # outside the server (raw `docker compose up`, `make up`, etc.).
    # Read-only; safe to call unconditionally.  See discovery.discover_docker.
    try:
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
        app.state.health_checker = health_checker
        health_task = asyncio.create_task(health_checker.run())
        app.state.log_aggregator = log_aggregator
        log_task = asyncio.create_task(log_aggregator.run())

    # Start plugin-contributed background tasks (respects feature gates).
    background_tasks.start_all()

    # Expose the fetch_scheduler (if running) on app.state for the
    # manual-trigger API endpoint -- resolved by name, no plugin import.
    _fs = background_tasks.get_task("fetch_scheduler")
    if _fs is not None:
        app.state.fetch_scheduler = _fs

    # Dev mode: spawn Vite dev server for HMR and drain its output.
    vite_proc = None
    vite_drain_task = None
    if _DEV_MODE:
        from codehome.serve.vite_dev import drain_vite_output, start_vite_dev

        try:
            vite_proc, vite_port = await start_vite_dev()
            # Set the port on the proxy middleware so it starts forwarding.
            # _vite_proxy is the module-level ViteProxyMiddleware instance.
            if _vite_proxy is not None:
                _vite_proxy.vite_port = vite_port
            vite_drain_task = asyncio.create_task(drain_vite_output(vite_proc))
            _log.info("vite dev server started", port=vite_port)
        except Exception:
            _log.exception("failed to start Vite dev server -- falling back to static files")

    yield
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
# App creation and router registration
# ---------------------------------------------------------------------------

app = FastAPI(title="Veliu Dev Dashboard", lifespan=lifespan)

# -- Rate limiting middleware -----------------------------------------------
from codehome.serve.rate_limit import SLOWAPI_AVAILABLE, limiter  # noqa: E402

if SLOWAPI_AVAILABLE:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    # SlowAPIMiddleware intentionally NOT added: it extends BaseHTTPMiddleware
    # which wraps streaming responses in anyio memory streams, silently killing
    # long-lived SSE connections via CancelledError. Rate limiting on a localhost
    # dev server is not worth breaking SSE.

# -- CSRF protection middleware --------------------------------------------
from codehome.serve.csrf import CSRFMiddleware  # noqa: E402

app.add_middleware(CSRFMiddleware)

# -- Request tracing middleware --------------------------------------------
from codehome.serve.middleware import RequestIDMiddleware, RequestTimingMiddleware  # noqa: E402

# Order matters: Starlette wraps outermost-last, so add timing first (inner)
# then ID (outer).  This ensures the request ID is available when timing logs.
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(RequestIDMiddleware)


# Public ping endpoint (no auth, no router file needed).
@app.get("/api/ping")
async def ping() -> dict[str, bool]:
    return {"ok": True}


# -- Load plugins (must happen before router mounting) ---------------------
from codehome.plugins.loader import load_all_plugins as _load_plugins  # noqa: E402

_load_plugins()

# -- Import and mount routers ---------------------------------------------

from codehome import features  # noqa: E402
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

# Public routers (no auth dependency).
app.include_router(auth.public_router)
app.include_router(system.public_router)
app.include_router(services_router.public_router)
app.include_router(features_router.router)

# Feature-gated public routers: disable via features.json
if features.enabled("conductor"):
    app.include_router(agents.public_router)

# Push public endpoint (VAPID key retrieval, no auth required).
if features.enabled("push"):
    from fastapi import APIRouter as _APIRouter  # noqa: E402

    _push_public = _APIRouter(prefix="/api/push", tags=["push"])

    @_push_public.get("/vapid-key")
    async def _get_vapid_key() -> object:
        """Return the public VAPID key for push subscription registration."""
        return {"public_key": push_manager.public_key}

    app.include_router(_push_public)

# Authenticated routers (all endpoints require valid JWT).
# NOTE: branches, git, system (authed), and matrix routers moved to core plugin.
_authed_routers: list[Any] = [
    auth.router,
    services_router.router,
    features_router.authed_router,
]

# Feature-gated routers: disable via features.json
if features.enabled("plugins"):
    _authed_routers.append(plugins_router.router)
if features.enabled("conductor"):
    _authed_routers.append(conductor.router)
    _authed_routers.append(agents.router)
for _r in _authed_routers:
    app.include_router(_r, dependencies=[Depends(get_current_user)])

# -- Plugin-contributed routers (authenticated, gated on "plugins" flag) ----
from codehome.plugins import registry as _plugin_registry  # noqa: E402

if features.enabled("plugins"):
    for _plugin in _plugin_registry.list_plugins():
        if _plugin.router is not None:
            if _plugin.manifest.root_routes:
                # Mount at API root (endpoints define their own /api/ paths).
                app.include_router(
                    _plugin.router,
                    dependencies=[Depends(get_current_user)],
                )
            else:
                app.include_router(
                    _plugin.router,
                    prefix=f"/api/p/{_plugin.name}",
                    dependencies=[Depends(get_current_user)],
                )
        # Public routers (e.g. WebSocket endpoints with query-param auth)
        # are mounted without the auth dependency.
        if _plugin.public_router is not None:
            if _plugin.manifest.root_routes:
                app.include_router(_plugin.public_router)
            else:
                app.include_router(
                    _plugin.public_router,
                    prefix=f"/api/p/{_plugin.name}",
                )

# -- SPA static file fallback / Vite dev proxy -----------------------------
# In dev mode the Vite proxy middleware is added below (outermost ASGI layer).
# In stable mode the pre-built static files are served via a catch-all router.
if not _DEV_MODE:
    from codehome.serve.static_files import router as _static_router

    app.include_router(_static_router)


# -- Vite dev proxy (dev mode only) ----------------------------------------
# Added AFTER app construction so it wraps the entire ASGI app.  The
# middleware intercepts non-API requests and proxies them to Vite.
# The vite_port is set during lifespan startup via the _vite_proxy reference.
_vite_proxy = None
if _DEV_MODE:
    from codehome.serve.vite_dev import ViteProxyMiddleware

    _vite_proxy = ViteProxyMiddleware(app)
    app = _vite_proxy  # type: ignore[assignment]
