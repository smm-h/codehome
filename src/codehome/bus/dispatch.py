"""EventBus: subscribe to events and dispatch them through FILTER/DONE transports."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from codehome.bus.types import Event, FilterResult, Ok, Transport, Veto

if TYPE_CHECKING:
    from collections.abc import Callable

    from codehome.bus.registry import EventRegistry

logger = logging.getLogger(__name__)


class EventBus:
    """Single-tenant event bus. Async-first with a sync fallback for CLI use."""

    def __init__(self, registry: EventRegistry) -> None:
        self._registry = registry
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: Callable[..., Any]) -> None:
        self._subscribers[event_name].append(handler)

    def subscriber_count(self, event_name: str) -> int:
        return len(self._subscribers[event_name])

    async def fire(self, event: Event) -> list[FilterResult] | None:
        """Dispatch an event. Primary async path (ASGI server context).

        FILTER: sequential calls, returns list of FilterResult.
        DONE: parallel calls via asyncio.gather, returns None.
        """
        et = self._registry.get(event.name)
        if et is None:
            logger.warning("Unregistered event fired: %s", event.name)
            return None

        handlers = self._subscribers.get(event.name, [])

        if et.transport is Transport.FILTER:
            return await self._fire_filter(handlers, event, first_veto=et.first_veto)

        # DONE: run all handlers in parallel
        tasks = []
        for h in handlers:
            if asyncio.iscoroutinefunction(h):
                tasks.append(h(event))
            else:
                tasks.append(asyncio.to_thread(h, event))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for h, result in zip(handlers, results, strict=True):
                if isinstance(result, BaseException):
                    logger.exception("DONE handler error: %s", h.__name__, exc_info=result)
        return None

    def fire_sync(self, event: Event) -> list[FilterResult] | None:
        """Dispatch an event synchronously. CLI fallback when no event loop is running.

        FILTER: calls handlers directly (they must be sync).
        DONE: calls sync handlers directly, skips async-only ones.
        """
        et = self._registry.get(event.name)
        if et is None:
            logger.warning("Unregistered event fired (sync): %s", event.name)
            return None

        handlers = self._subscribers.get(event.name, [])

        if et.transport is Transport.FILTER:
            return self._fire_filter_sync(handlers, event, first_veto=et.first_veto)

        # DONE sync path: call sync handlers, skip async-only ones
        for h in handlers:
            if asyncio.iscoroutinefunction(h):
                logger.debug("Skipping async DONE handler in sync context: %s", h.__name__)
                continue
            try:
                h(event)
            except Exception:
                logger.exception("DONE handler error (sync): %s", h.__name__)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fire_filter(self, handlers: list[Callable[..., Any]], event: Event, *, first_veto: bool) -> list[FilterResult]:
        results: list[FilterResult] = []
        for h in handlers:
            try:
                if asyncio.iscoroutinefunction(h):
                    result = await h(event)
                else:
                    result = h(event)
            except Exception:
                logger.exception("FILTER handler error: %s", h.__name__)
                continue
            if not isinstance(result, FilterResult):
                logger.warning(
                    "FILTER handler %s returned %r (expected Ok or Veto), treating as Ok",
                    h.__name__,
                    type(result).__name__,
                )
                result = Ok()
            results.append(result)
            if first_veto and isinstance(result, Veto):
                break
        return results

    def _fire_filter_sync(self, handlers: list[Callable[..., Any]], event: Event, *, first_veto: bool) -> list[FilterResult]:
        results: list[FilterResult] = []
        for h in handlers:
            if asyncio.iscoroutinefunction(h):
                logger.warning("Async FILTER handler in sync context, skipping: %s", h.__name__)
                continue
            try:
                result = h(event)
            except Exception:
                logger.exception("FILTER handler error (sync): %s", h.__name__)
                continue
            if not isinstance(result, FilterResult):
                logger.warning(
                    "FILTER handler %s returned %r (expected Ok or Veto), treating as Ok",
                    h.__name__,
                    type(result).__name__,
                )
                result = Ok()
            results.append(result)
            if first_veto and isinstance(result, Veto):
                break
        return results
