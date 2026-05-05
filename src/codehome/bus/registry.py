"""Event type registry: declares known event names, transport, and behaviour."""

from __future__ import annotations

from dataclasses import dataclass

from codehome.bus.types import Transport


@dataclass
class EventType:
    name: str
    transport: Transport
    audit: bool = False
    description: str = ""
    # When True (FILTER only), stop calling handlers after the first Veto.
    first_veto: bool = False


class EventRegistry:
    """Central catalog of declared event types."""

    def __init__(self) -> None:
        self._types: dict[str, EventType] = {}

    def register(
        self,
        name: str,
        transport: Transport,
        *,
        audit: bool = False,
        description: str = "",
        first_veto: bool = False,
    ) -> EventType:
        if name in self._types:
            raise ValueError(f"Duplicate event type: {name}")
        if first_veto and transport is not Transport.FILTER:
            raise ValueError(f"first_veto is only valid for FILTER events, got {transport.value}")
        et = EventType(
            name=name,
            transport=transport,
            audit=audit,
            description=description,
            first_veto=first_veto,
        )
        self._types[name] = et
        return et

    def get(self, name: str) -> EventType | None:
        return self._types.get(name)

    def list(self) -> list[EventType]:
        return list(self._types.values())

    def clear(self) -> None:
        """Remove all registered types. Intended for test teardown."""
        self._types.clear()
