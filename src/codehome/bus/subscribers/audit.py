"""JSONL audit subscriber: writes audit=true events to daily log files.

Drop-in replacement for the legacy ``codehome.events.emit()`` system.
Writes the same envelope format to the same directory so existing tooling
(dashboards, scripts) keeps working unchanged.

The handler is intentionally **sync** so it works in both:
- CLI context (``bus.fire_sync``)
- Server context (``bus.fire`` dispatches sync handlers via ``asyncio.to_thread``)
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from codehome.paths import superv_home

if TYPE_CHECKING:
    from codehome.bus.dispatch import EventBus
    from codehome.bus.registry import EventRegistry
    from codehome.bus.types import Event

logger = logging.getLogger(__name__)


def audit_subscriber(event: Event) -> None:
    """Append an audit event to today's JSONL file with fcntl locking."""
    events_dir = superv_home() / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    payload = event.payload
    envelope = {
        "ts": datetime.now(UTC).astimezone().isoformat(),
        "type": event.name,
        "session": payload.get("session"),
        "repo": payload.get("repo"),
        "branch": payload.get("branch"),
        "data": payload.get("data"),
    }

    path = events_dir / f"{datetime.now(UTC).date()}.jsonl"
    line = json.dumps(envelope, separators=(",", ":")) + "\n"

    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        # Lock released on close.


def install_audit_subscriber(bus: EventBus, registry: EventRegistry) -> None:
    """Subscribe the audit handler to every audit=True event in the registry."""
    count = 0
    for etype in registry.list():
        if etype.audit:
            bus.subscribe(etype.name, audit_subscriber)
            count += 1
    logger.debug("Installed audit subscriber on %d event types", count)
