"""Smoke tests for the event bus dispatcher."""

from __future__ import annotations

import pytest

from codehome.bus.dispatch import EventBus
from codehome.bus.registry import EventRegistry
from codehome.bus.types import Event, FilterResult, Ok, Transport, Veto


@pytest.fixture()
def registry() -> EventRegistry:
    return EventRegistry()


@pytest.fixture()
def bus(registry: EventRegistry) -> EventBus:
    return EventBus(registry)


# -- fire_sync: DONE transport --


def test_fire_sync_dispatches_to_subscribers(registry: EventRegistry, bus: EventBus) -> None:
    """fire_sync calls all registered sync subscribers for a DONE event."""
    registry.register("test.done", Transport.DONE)
    received: list[str] = []

    def handler(event: Event) -> None:
        received.append(event.name)

    bus.subscribe("test.done", handler)
    bus.fire_sync(Event(name="test.done"))

    assert received == ["test.done"]


def test_fire_sync_multiple_subscribers(registry: EventRegistry, bus: EventBus) -> None:
    """fire_sync calls every subscriber, not just the first."""
    registry.register("test.multi", Transport.DONE)
    calls: list[int] = []

    bus.subscribe("test.multi", lambda e: calls.append(1))
    bus.subscribe("test.multi", lambda e: calls.append(2))
    bus.fire_sync(Event(name="test.multi"))

    assert calls == [1, 2]


# -- fire_sync: FILTER transport --


def test_fire_sync_filter_returns_ok(registry: EventRegistry, bus: EventBus) -> None:
    """FILTER handlers that return Ok are collected."""
    registry.register("test.filter", Transport.FILTER)

    def approver(event: Event) -> FilterResult:
        return Ok()

    bus.subscribe("test.filter", approver)
    results = bus.fire_sync(Event(name="test.filter"))

    assert results is not None
    assert len(results) == 1
    assert isinstance(results[0], Ok)


def test_fire_sync_filter_veto_stops_on_first(registry: EventRegistry, bus: EventBus) -> None:
    """With first_veto=True, processing stops after the first Veto."""
    registry.register("test.veto", Transport.FILTER, first_veto=True)
    calls: list[str] = []

    def vetoer(event: Event) -> FilterResult:
        calls.append("vetoer")
        return Veto("nope")

    def should_not_run(event: Event) -> FilterResult:
        calls.append("second")
        return Ok()

    bus.subscribe("test.veto", vetoer)
    bus.subscribe("test.veto", should_not_run)
    results = bus.fire_sync(Event(name="test.veto"))

    assert results is not None
    assert len(results) == 1
    assert isinstance(results[0], Veto)
    assert calls == ["vetoer"]


# -- Registry discovery --


def test_events_can_be_registered_and_discovered(registry: EventRegistry) -> None:
    """Registered event types are discoverable via get() and list()."""
    registry.register("app.started", Transport.DONE, description="App started")

    assert registry.get("app.started") is not None
    assert registry.get("app.started").transport is Transport.DONE  # type: ignore[union-attr]
    assert any(et.name == "app.started" for et in registry.list())


def test_unregistered_event_returns_none(registry: EventRegistry, bus: EventBus) -> None:
    """Firing an unregistered event returns None without error."""
    result = bus.fire_sync(Event(name="nonexistent"))
    assert result is None
