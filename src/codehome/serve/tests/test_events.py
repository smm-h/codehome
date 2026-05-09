"""Tests for SSE EventManager: broadcast, subscribe, heartbeat."""

import asyncio
import json
from unittest.mock import patch

import pytest

from codehome.serve.events import EventManager


@pytest.fixture
def em():
    """Fresh EventManager instance (not the singleton)."""
    return EventManager()


# ---------------------------------------------------------------------------
# 1. broadcast sends to all subscribed clients
# ---------------------------------------------------------------------------


class TestBroadcast:
    async def test_broadcast_sends_to_all_subscribers(self, em):
        async with em.subscribe() as stream_a, em.subscribe() as stream_b:
            await em.broadcast("status", {"ok": True})

            # Both subscribers should receive the same message.
            msg_a = await asyncio.wait_for(stream_a.__anext__(), timeout=1)
            msg_b = await asyncio.wait_for(stream_b.__anext__(), timeout=1)
            expected = 'event: status\ndata: {"ok": true}\n\n'
            assert msg_a == expected
            assert msg_b == expected

    async def test_broadcast_no_clients_does_not_crash(self, em):
        # No subscribers -- should silently succeed.
        await em.broadcast("ping", {"ts": 1})

    async def test_broadcast_drops_on_queue_full(self, em):
        async with em.subscribe() as stream:
            # Fill the queue (maxsize=256).
            for i in range(256):
                await em.broadcast("fill", {"i": i})

            # 257th message should be silently dropped, not raise.
            await em.broadcast("overflow", {"dropped": True})

            # Client still exists -- not evicted.
            assert em.client_count == 1

            # Drain the queue: we should get exactly 256 messages.
            received = []
            for _ in range(256):
                msg = await asyncio.wait_for(stream.__anext__(), timeout=1)
                received.append(msg)
            assert len(received) == 256

            # The overflow message was dropped; verify none of the received
            # messages contain it.
            assert all('"dropped": true' not in m for m in received)


# ---------------------------------------------------------------------------
# 2. subscribe lifecycle
# ---------------------------------------------------------------------------


class TestSubscribe:
    async def test_subscribe_adds_and_removes_client(self, em):
        assert em.client_count == 0
        async with em.subscribe():
            assert em.client_count == 1
        # After context exit, client is removed.
        assert em.client_count == 0

    async def test_subscribe_cleanup_on_exception(self, em):
        with pytest.raises(RuntimeError, match="boom"):
            async with em.subscribe():
                assert em.client_count == 1
                msg = "boom"
                raise RuntimeError(msg)
        assert em.client_count == 0


# ---------------------------------------------------------------------------
# 3. client_count property
# ---------------------------------------------------------------------------


class TestClientCount:
    async def test_client_count_tracks_multiple(self, em):
        assert em.client_count == 0
        async with em.subscribe():
            assert em.client_count == 1
            async with em.subscribe():
                assert em.client_count == 2
                async with em.subscribe():
                    assert em.client_count == 3
                assert em.client_count == 2
            assert em.client_count == 1
        assert em.client_count == 0


# ---------------------------------------------------------------------------
# 4. Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_heartbeat_on_idle(self, em):
        # Patch HEARTBEAT_INTERVAL to a tiny value so the test doesn't wait.
        with patch("codehome.serve.events.HEARTBEAT_INTERVAL", 0.05):
            async with em.subscribe() as stream:
                # Don't broadcast anything -- the stream should yield a
                # heartbeat comment after the short interval.
                msg = await asyncio.wait_for(stream.__anext__(), timeout=2)
                assert msg == ": heartbeat\n\n"

    async def test_heartbeat_resets_after_real_event(self, em):
        with patch("codehome.serve.events.HEARTBEAT_INTERVAL", 0.05):
            async with em.subscribe() as stream:
                # Send a real event first.
                await em.broadcast("ping", {"n": 1})
                msg = await asyncio.wait_for(stream.__anext__(), timeout=2)
                assert msg.startswith("event: ping")

                # Now idle -- next message should be a heartbeat.
                msg = await asyncio.wait_for(stream.__anext__(), timeout=2)
                assert msg == ": heartbeat\n\n"


# ---------------------------------------------------------------------------
# 5. Multiple concurrent subscribers get independent queues
# ---------------------------------------------------------------------------


class TestIndependentQueues:
    async def test_independent_consumption(self, em):
        async with em.subscribe() as stream_a, em.subscribe() as stream_b:
            await em.broadcast("ev", {"x": 1})
            await em.broadcast("ev", {"x": 2})

            # Consume both from stream_a first.
            a1 = await asyncio.wait_for(stream_a.__anext__(), timeout=1)
            a2 = await asyncio.wait_for(stream_a.__anext__(), timeout=1)

            # stream_b should still have both messages independently.
            b1 = await asyncio.wait_for(stream_b.__anext__(), timeout=1)
            b2 = await asyncio.wait_for(stream_b.__anext__(), timeout=1)

            assert a1 == b1
            assert a2 == b2


# ---------------------------------------------------------------------------
# 6. Event format
# ---------------------------------------------------------------------------


class TestEventFormat:
    async def test_format_structure(self, em):
        payload = {"key": "value", "num": 42}
        async with em.subscribe() as stream:
            await em.broadcast("update", payload)
            msg = await asyncio.wait_for(stream.__anext__(), timeout=1)

        lines = msg.split("\n")
        assert lines[0] == "event: update"
        assert lines[1] == f"data: {json.dumps(payload)}"
        # SSE requires trailing blank line (double newline terminator).
        assert msg.endswith("\n\n")

    async def test_format_with_nested_data(self, em):
        payload = {"a": {"b": [1, 2, 3]}, "c": None}
        async with em.subscribe() as stream:
            await em.broadcast("nested", payload)
            msg = await asyncio.wait_for(stream.__anext__(), timeout=1)

        assert f"data: {json.dumps(payload)}" in msg
        assert msg.startswith("event: nested\n")
