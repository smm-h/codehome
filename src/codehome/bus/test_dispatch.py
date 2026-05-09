"""Tests for EventBus dispatch: DONE transport, FILTER transport, registry validation, audit subscriber."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from codehome.bus.dispatch import EventBus
from codehome.bus.registry import EventRegistry
from codehome.bus.subscribers.audit import install_audit_subscriber
from codehome.bus.types import Event, Ok, Transport, Veto

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> EventRegistry:
    """Fresh registry per test."""
    return EventRegistry()


@pytest.fixture
def bus(registry: EventRegistry) -> EventBus:
    """Fresh bus wired to the test registry."""
    return EventBus(registry)


# ===========================================================================
# DONE transport (async)
# ===========================================================================


class TestDoneTransport:
    """Tests for DONE (fire-and-forget, parallel) event dispatch."""

    # -- 1. Two async subscribers both run -----------------------------------

    async def test_two_async_subscribers_both_run(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.done", Transport.DONE)
        calls: list[str] = []

        async def handler_a(event: Event) -> None:
            calls.append("a")

        async def handler_b(event: Event) -> None:
            calls.append("b")

        bus.subscribe("test.done", handler_a)
        bus.subscribe("test.done", handler_b)

        result = await bus.fire(Event(name="test.done"))

        assert result is None  # DONE always returns None
        assert sorted(calls) == ["a", "b"]

    # -- 2. Exception isolation: one fails, other still runs ----------------

    async def test_exception_isolation(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.done.iso", Transport.DONE)
        calls: list[str] = []

        async def failing_handler(event: Event) -> None:
            msg = "handler exploded"
            raise RuntimeError(msg)

        async def good_handler(event: Event) -> None:
            calls.append("ok")

        bus.subscribe("test.done.iso", failing_handler)
        bus.subscribe("test.done.iso", good_handler)

        # Should not raise despite one handler failing.
        result = await bus.fire(Event(name="test.done.iso"))

        assert result is None
        assert calls == ["ok"]


# ===========================================================================
# DONE transport (sync fallback)
# ===========================================================================


class TestDoneTransportSync:
    """Tests for fire_sync() with DONE events."""

    # -- 3. Sync handler executes -------------------------------------------

    def test_sync_handler_executes(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.done.sync", Transport.DONE)
        calls: list[str] = []

        def sync_handler(event: Event) -> None:
            calls.append("sync-ok")

        bus.subscribe("test.done.sync", sync_handler)
        result = bus.fire_sync(Event(name="test.done.sync"))

        assert result is None
        assert calls == ["sync-ok"]

    # -- 4. Async handler skipped in sync context (with debug log) ----------

    def test_async_handler_skipped_in_sync_context(
        self, registry: EventRegistry, bus: EventBus, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry.register("test.done.skip", Transport.DONE)
        calls: list[str] = []

        async def async_handler(event: Event) -> None:
            calls.append("should-not-run")

        bus.subscribe("test.done.skip", async_handler)

        with caplog.at_level(logging.DEBUG, logger="codehome.bus.dispatch"):
            result = bus.fire_sync(Event(name="test.done.skip"))

        assert result is None
        assert calls == []  # Handler was skipped, not called
        assert "Skipping async DONE handler in sync context" in caplog.text

    # -- 5. Unregistered event does not crash -------------------------------

    def test_unregistered_event_no_crash_sync(self, bus: EventBus, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="codehome.bus.dispatch"):
            result = bus.fire_sync(Event(name="totally.unknown"))

        assert result is None
        assert "Unregistered event fired" in caplog.text

    async def test_unregistered_event_no_crash_async(self, bus: EventBus, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="codehome.bus.dispatch"):
            result = await bus.fire(Event(name="totally.unknown.async"))

        assert result is None
        assert "Unregistered event fired" in caplog.text


# ===========================================================================
# FILTER transport (async)
# ===========================================================================


class TestFilterTransport:
    """Tests for FILTER (sequential, collect-all) event dispatch."""

    # -- 6. Two Ok subscribers -> no vetoes ---------------------------------

    async def test_two_ok_subscribers_no_vetoes(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.filter.ok", Transport.FILTER)

        async def approve_a(event: Event) -> Ok:
            return Ok()

        async def approve_b(event: Event) -> Ok:
            return Ok()

        bus.subscribe("test.filter.ok", approve_a)
        bus.subscribe("test.filter.ok", approve_b)

        results = await bus.fire(Event(name="test.filter.ok"))

        assert results is not None
        assert len(results) == 2
        assert all(isinstance(r, Ok) for r in results)
        assert not any(isinstance(r, Veto) for r in results)

    # -- 7. One Veto in collect-all mode: all run, veto captured ------------

    async def test_veto_in_collect_all_mode(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.filter.veto", Transport.FILTER)
        calls: list[str] = []

        async def approver(event: Event) -> Ok:
            calls.append("approver")
            return Ok()

        async def rejecter(event: Event) -> Veto:
            calls.append("rejecter")
            return Veto("not allowed")

        # rejecter first, approver second -- both should still run
        bus.subscribe("test.filter.veto", rejecter)
        bus.subscribe("test.filter.veto", approver)

        results = await bus.fire(Event(name="test.filter.veto"))

        assert results is not None
        assert len(results) == 2
        assert calls == ["rejecter", "approver"]  # Both ran (sequential order)

        vetoes = [r for r in results if isinstance(r, Veto)]
        assert len(vetoes) == 1
        assert vetoes[0].reason == "not allowed"

    # -- 8. first_veto=True short-circuits ----------------------------------

    async def test_first_veto_short_circuits(self, registry: EventRegistry, bus: EventBus) -> None:
        registry.register("test.filter.fv", Transport.FILTER, first_veto=True)
        calls: list[str] = []

        async def rejecter(event: Event) -> Veto:
            calls.append("rejecter")
            return Veto("stop here")

        async def should_not_run(event: Event) -> Ok:
            calls.append("should-not-run")
            return Ok()

        bus.subscribe("test.filter.fv", rejecter)
        bus.subscribe("test.filter.fv", should_not_run)

        results = await bus.fire(Event(name="test.filter.fv"))

        assert results is not None
        assert len(results) == 1
        assert isinstance(results[0], Veto)
        assert results[0].reason == "stop here"
        assert calls == ["rejecter"]  # Second handler never ran

    # -- 9. Non-FilterResult return coerced to Ok ---------------------------

    async def test_non_filter_result_coerced_to_ok(
        self, registry: EventRegistry, bus: EventBus, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry.register("test.filter.coerce", Transport.FILTER)

        async def bad_return(event: Event) -> str:
            return "oops"

        bus.subscribe("test.filter.coerce", bad_return)

        with caplog.at_level(logging.WARNING, logger="codehome.bus.dispatch"):
            results = await bus.fire(Event(name="test.filter.coerce"))

        assert results is not None
        assert len(results) == 1
        assert isinstance(results[0], Ok)
        assert "treating as Ok" in caplog.text


# ===========================================================================
# Registry validation
# ===========================================================================


class TestRegistryValidation:
    """Tests for EventRegistry.register() validation rules."""

    # -- 10. Duplicate event name raises ------------------------------------

    def test_duplicate_event_name_raises(self, registry: EventRegistry) -> None:
        registry.register("dupe.event", Transport.DONE)

        with pytest.raises(ValueError, match="Duplicate event type"):
            registry.register("dupe.event", Transport.DONE)

    # -- 11. first_veto on DONE raises --------------------------------------

    def test_first_veto_on_done_raises(self, registry: EventRegistry) -> None:
        with pytest.raises(ValueError, match="first_veto is only valid for FILTER"):
            registry.register("bad.event", Transport.DONE, first_veto=True)


# ===========================================================================
# Audit subscriber
# ===========================================================================


class TestAuditSubscriber:
    """Tests for the JSONL audit subscriber."""

    # -- 12. audit=True writes JSONL ----------------------------------------

    async def test_audit_true_writes_jsonl(self, registry: EventRegistry, bus: EventBus, tmp_path: Path) -> None:
        registry.register("test.audited", Transport.DONE, audit=True)
        install_audit_subscriber(bus, registry)

        event = Event(
            name="test.audited",
            payload={"session": "s1", "repo": "bag", "branch": "main", "data": {"key": "val"}},
            audit=True,
        )

        # Redirect codehome_home so events land in tmp_path/events.
        with patch("codehome.bus.subscribers.audit.codehome_home", return_value=tmp_path):
            await bus.fire(event)

        # Find the written JSONL file.
        jsonl_files = list((tmp_path / "events").glob("*.jsonl"))
        assert len(jsonl_files) == 1

        lines = jsonl_files[0].read_text().strip().splitlines()
        assert len(lines) == 1

        envelope = json.loads(lines[0])
        assert envelope["type"] == "test.audited"
        assert envelope["session"] == "s1"
        assert envelope["repo"] == "bag"
        assert envelope["branch"] == "main"
        assert envelope["data"] == {"key": "val"}
        # Timestamp should be an ISO string.
        assert "ts" in envelope

    # -- 13. audit=False (default) -> no JSONL written ----------------------

    async def test_audit_false_no_jsonl(self, registry: EventRegistry, bus: EventBus, tmp_path: Path) -> None:
        # Register a non-audited event.
        registry.register("test.silent", Transport.DONE, audit=False)
        install_audit_subscriber(bus, registry)

        event = Event(name="test.silent", payload={"data": "ignored"})

        with patch("codehome.bus.subscribers.audit.codehome_home", return_value=tmp_path):
            await bus.fire(event)

        # No JSONL files should exist -- install_audit_subscriber only
        # subscribes to audit=True events, so the handler never fires.
        events_dir = tmp_path / "events"
        jsonl_files = list(events_dir.glob("*.jsonl")) if events_dir.exists() else []
        assert len(jsonl_files) == 0
