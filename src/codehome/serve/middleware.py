"""Accessor functions for request tracing middleware counters.

The actual middleware implementation is in wesktop.middleware.  These
functions provide access to the timing middleware instance's counters
(request_count, request_history) so that existing code in system.py
can retrieve them without importing server.py directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections import deque

    from wesktop.middleware import RequestEntry


def get_request_count() -> int:
    """Get the current request count from the timing middleware."""
    from codehome.serve.server import _timing_mw

    return _timing_mw.request_count if _timing_mw else 0


def get_request_history() -> "deque[RequestEntry] | list":
    """Get the request history deque from the timing middleware."""
    from codehome.serve.server import _timing_mw

    return _timing_mw.request_history if _timing_mw else []
