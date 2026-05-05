"""Unified event bus: subscribe, filter, and broadcast events."""

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from supervisor.bus.dispatch import EventBus
from supervisor.bus.events import build_registry, registry
from supervisor.bus.instance import bus
from supervisor.bus.registry import EventRegistry, EventType
from supervisor.bus.types import Event, FilterResult, Ok, Transport, Veto

_F = TypeVar("_F", bound=Callable[..., Any])


def fire(event: Event) -> Coroutine[Any, Any, list[FilterResult] | None]:
    """Fire an event through the application bus (async)."""
    return bus.fire(event)


def fire_sync(event: Event) -> list[FilterResult] | None:
    """Fire an event through the application bus (sync, for CLI)."""
    return bus.fire_sync(event)


def on(event_name: str) -> Callable[[_F], _F]:
    """Mark a function as an event subscriber (no-op at runtime).

    The AST parser (v plugins rescan) discovers these decorators and
    writes events.toml. The decorator itself does nothing at import time.
    """

    def decorator(fn: _F) -> _F:
        # Attach metadata for runtime introspection (optional, not used by loader).
        fn._bus_event = event_name  # type: ignore[attr-defined]
        return fn

    return decorator


__all__ = [
    "Event",
    "EventBus",
    "EventRegistry",
    "EventType",
    "FilterResult",
    "Ok",
    "Transport",
    "Veto",
    "build_registry",
    "bus",
    "fire",
    "fire_sync",
    "on",
    "registry",
]
