"""Rate limiting compatibility layer.

Re-exports ``wesktop.auth.rate_limit`` for convenience. All routers now
use wesktop directly; this module can be removed once no external plugins
import from it.
"""

from typing import Any

# Re-export wesktop's rate_limit for new code.
from wesktop.auth import rate_limit  # noqa: F401

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
