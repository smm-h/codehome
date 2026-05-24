"""Backward-compatible shim for request tracing middleware.

The actual middleware implementation is now in wesktop.middleware.  This
module provides proxy access to the timing middleware instance's counters
(request_count, request_history) so that existing code in system.py and
routers/system.py can import them without changes.

The ``_timing_mw`` instance is created at import time in server.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections import deque

    from wesktop.middleware import RequestEntry


def _get_timing_mw():
    """Lazily resolve the timing middleware instance from server.py."""
    from codehome.serve.server import _timing_mw

    return _timing_mw


class _RequestCountProxy:
    """Proxy that reads request_count from the wesktop timing middleware."""

    def __int__(self) -> int:
        return _get_timing_mw().request_count

    def __repr__(self) -> str:
        return repr(int(self))

    def __eq__(self, other: object) -> bool:
        return int(self) == other

    def __lt__(self, other: object) -> bool:
        return int(self) < other  # type: ignore[operator]

    def __gt__(self, other: object) -> bool:
        return int(self) > other  # type: ignore[operator]

    def __le__(self, other: object) -> bool:
        return int(self) <= other  # type: ignore[operator]

    def __ge__(self, other: object) -> bool:
        return int(self) >= other  # type: ignore[operator]

    def __add__(self, other: object) -> int:
        return int(self) + other  # type: ignore[operator]

    def __radd__(self, other: object) -> int:
        return other + int(self)  # type: ignore[operator]

    def __sub__(self, other: object) -> int:
        return int(self) - other  # type: ignore[operator]

    def __rsub__(self, other: object) -> int:
        return other - int(self)  # type: ignore[operator]

    def __format__(self, format_spec: str) -> str:
        return format(int(self), format_spec)


# These names are imported by codehome.serve.system and routers/system.py.
# request_count behaves like an int; request_history is the deque itself.
request_count: int = _RequestCountProxy()  # type: ignore[assignment]


class _RequestHistoryProxy:
    """Proxy that delegates to the wesktop timing middleware's ring buffer."""

    def __iter__(self):
        return iter(_get_timing_mw().request_history)

    def __len__(self) -> int:
        return len(_get_timing_mw().request_history)

    def __bool__(self) -> bool:
        return bool(_get_timing_mw().request_history)

    def __getitem__(self, idx):
        return list(_get_timing_mw().request_history)[idx]

    def append(self, item) -> None:
        _get_timing_mw().request_history.append(item)


request_history: deque[RequestEntry] = _RequestHistoryProxy()  # type: ignore[assignment]
