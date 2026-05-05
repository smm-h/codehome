"""Rate limiting configuration using slowapi.

Uses in-memory storage (no Redis needed) since the dashboard serves
a small number of users behind Twingate.
"""

from typing import Any

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    # Key function: extract client IP from request.
    limiter: Any = Limiter(key_func=get_remote_address)
    SLOWAPI_AVAILABLE = True
except ImportError:

    class _NoOpLimiter:
        """Pass-through stub when slowapi is not installed."""

        def limit(self, *_a: object, **_kw: object) -> Any:
            def identity(func: Any) -> Any:
                return func

            return identity

        def shared_limit(self, *_a: object, **_kw: object) -> Any:
            def identity(func: Any) -> Any:
                return func

            return identity

    limiter = _NoOpLimiter()
    SLOWAPI_AVAILABLE = False
