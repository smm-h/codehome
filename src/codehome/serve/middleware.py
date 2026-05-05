"""Request tracing middleware: request IDs and timing.

Provides two pure-ASGI middleware classes (no BaseHTTPMiddleware):
- RequestIDMiddleware: assigns a UUID4 to each request (or reuses
  an incoming X-Request-Id header), stores it in scope state, and
  returns it as a response header.  Streaming-safe.
- RequestTimingMiddleware: logs method, path, status, duration,
  request ID, and user for every request using structured JSON logging.
  Also maintains a global request counter for diagnostics.  Uses
  try/finally so SSE streams get timed when the client disconnects.
"""

import time
import uuid
from collections import deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codehome.serve.logging_config import get_logger

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False

logger = get_logger(component="request")

# Global counter for diagnostics stats endpoint.  Only incremented by
# the timing middleware; read by routers/system.py.
request_count: int = 0

# Ring buffer for request history.  Each entry is a tuple of
# (timestamp, method, path, status_code, duration_ms).
# CPython GIL makes deque.append atomic -- safe for concurrent ASGI tasks.
_EXCLUDED_PATHS = frozenset({"/events", "/api/terminal"})

RequestEntry = tuple[float, str, str, int, float]
request_history: deque[RequestEntry] = deque(maxlen=10_000)


class RequestIDMiddleware:
    """Assign or propagate a unique request ID per request.

    Pure ASGI implementation -- no BaseHTTPMiddleware wrapper, so
    streaming responses (SSE) pass through without being buffered.

    If the incoming request carries an X-Request-Id header, that value
    is reused (useful for cross-service tracing).  Otherwise a new
    UUID4 is generated.  The ID is stored in ``scope["state"]["request_id"]``
    (accessible as ``request.state.request_id`` in Starlette) and
    returned in the ``X-Request-Id`` response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID from raw ASGI headers.
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        # Store in scope state so downstream code (including Starlette's
        # Request.state) can access it.
        scope.setdefault("state", {})["request_id"] = request_id

        # Bind request ID to structlog context so all log entries within
        # this request automatically include it.
        if _HAS_STRUCTLOG:
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=request_id)

        # Wrap send to inject X-Request-Id into the response headers.
        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class RequestTimingMiddleware:
    """Log every request with method, path, status, duration, and context.

    Pure ASGI implementation.  Wraps ``send`` to capture the response
    status code from ``http.response.start``, then uses try/finally
    around the inner app call so timing fires even for long-lived SSE
    streams (when the client disconnects, the ASGI handler returns).

    Emits a structured JSON log line via the ``codehome.serve.request``
    logger.  Also increments the module-level ``request_count`` counter
    so the diagnostics stats endpoint can report total requests served.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        global request_count

        path = scope["path"]
        method = scope["method"]
        start = time.monotonic()
        status_code = 0

        # Intercept http.response.start to capture the status code.
        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)

            request_count += 1

            # Append to ring buffer for metrics, excluding long-lived connections.
            if not any(path.startswith(p) for p in _EXCLUDED_PATHS):
                request_history.append((time.time(), method, path, status_code, duration_ms))

            # Include authenticated user if available (set by auth dependency).
            state = scope.get("state", {})
            user_sub = None
            user = state.get("_user")
            if user:
                user_sub = user.get("sub")

            request_id = state.get("request_id")

            # Log 5xx errors to the centralized error log for dashboard visibility.
            if status_code >= 500:
                # scope["app"] is the Starlette/FastAPI application instance.
                app_instance = scope.get("app")
                error_log = getattr(getattr(app_instance, "state", None), "error_log", None)
                if error_log:
                    error_log.log(
                        "api",
                        "request",
                        f"{method} {path} returned {status_code}",
                        detail={"status_code": status_code, "duration_ms": round(duration_ms, 1)},
                        context={"method": method, "path": path, "request_id": request_id, "user": user_sub},
                    )

            logger.debug(
                "request",
                method=method,
                path=path,
                status=status_code,
                duration_ms=duration_ms,
                user=user_sub,
            )
