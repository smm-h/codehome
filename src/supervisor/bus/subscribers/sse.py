"""SSE transport subscriber: bridges bus events to EventManager for SSE delivery.

Receives DONE events from the bus and forwards them to the existing
EventManager.broadcast() method, which handles:
- SSE delivery to connected browser clients
- Push notifications (_PUSH_EVENT_MAP)
- Vite ports snapshot persistence

The subscriber broadcasts bus event names directly -- the frontend
subscribes to the same dot-separated names used in the bus registry
(e.g. "branch.create.DONE", "service.state").
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.bus.dispatch import EventBus
    from supervisor.bus.registry import EventRegistry
    from supervisor.bus.types import Event
    from supervisor.serve.events import EventManager

logger = logging.getLogger(__name__)

# Events with no SSE equivalent -- bus-only, no broadcast needed.
_NO_SSE: frozenset[str] = frozenset(
    {
        "branch.push",  # JSONL audit only, never had an SSE name
        "linear.mutation",  # JSONL audit only
        "linear.sync",  # JSONL audit only
    }
)


def make_sse_subscriber(event_manager: EventManager) -> Callable[[Event], Coroutine[Any, Any, None]]:
    """Create an async SSE subscriber closure bound to an EventManager."""

    async def sse_subscriber(event: Event) -> None:
        if event.name in _NO_SSE:
            return
        await event_manager.broadcast(event.name, event.payload)

    return sse_subscriber


def install_sse_subscriber(
    bus: EventBus,
    registry: EventRegistry,
    event_manager: EventManager,
) -> None:
    """Subscribe the SSE forwarder to all DONE events in the registry.

    Unlike the audit subscriber (which only subscribes to audit=True
    events), the SSE subscriber handles ALL DONE events -- any event
    gets forwarded to connected clients (except those in _NO_SSE).
    """
    from supervisor.bus.types import Transport

    handler = make_sse_subscriber(event_manager)
    count = 0
    for etype in registry.list():
        if etype.transport is Transport.DONE:
            bus.subscribe(etype.name, handler)
            count += 1
    logger.debug("Installed SSE subscriber on %d event types", count)
