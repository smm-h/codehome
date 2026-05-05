"""In-memory registry of active and recently-completed operations.

Provides:
- Listing in-flight operations
- Per-operation event ring buffer (catch-up on missed events)
- Auto-expiry of completed operations after a retention period

The registry is the single source of truth for operation state; the
OperationTracker in operation_progress.py records events here as a
side-effect of emitting SSE broadcasts.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# How long to keep completed/failed operations before expiry (seconds).
COMPLETED_RETENTION = 300  # 5 minutes

# Max events stored per operation ring buffer.
MAX_EVENTS_PER_OP = 1000


@dataclass
class OperationRecord:
    """A single tracked operation."""

    operation_id: str
    service_key: str
    operation_type: str  # "start", "stop", "restart", "cleanup", etc.
    label: str
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    status: str = "running"  # "running", "completed", "failed"
    error: str | None = None
    # Ring buffer of event dicts (same shape as operation.progress SSE payloads).
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS_PER_OP))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "service_key": self.service_key,
            "operation_type": self.operation_type,
            "label": self.label,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "error": self.error,
            "event_count": len(self.events),
        }


class OperationRegistry:
    """Thread-safe in-memory registry of operations."""

    def __init__(self) -> None:
        self._ops: dict[str, OperationRecord] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        operation_id: str,
        service_key: str,
        operation_type: str,
        label: str,
    ) -> OperationRecord:
        """Register a new operation. Returns the record."""
        record = OperationRecord(
            operation_id=operation_id,
            service_key=service_key,
            operation_type=operation_type,
            label=label,
        )
        async with self._lock:
            self._ops[operation_id] = record
        return record

    async def record_event(self, operation_id: str, event: dict[str, Any]) -> None:
        """Append an event to the operation's ring buffer."""
        async with self._lock:
            rec = self._ops.get(operation_id)
            if rec:
                rec.events.append(event)

    async def mark_complete(self, operation_id: str) -> None:
        """Mark an operation as completed."""
        async with self._lock:
            rec = self._ops.get(operation_id)
            if rec and rec.status == "running":
                rec.status = "completed"
                rec.completed_at = time.time()

    async def mark_failed(self, operation_id: str, error: str) -> None:
        """Mark an operation as failed."""
        async with self._lock:
            rec = self._ops.get(operation_id)
            if rec and rec.status == "running":
                rec.status = "failed"
                rec.error = error
                rec.completed_at = time.time()

    async def get(self, operation_id: str) -> OperationRecord | None:
        async with self._lock:
            return self._ops.get(operation_id)

    async def list_active(self) -> list[OperationRecord]:
        """Return all currently running operations."""
        async with self._lock:
            return [r for r in self._ops.values() if r.status == "running"]

    async def list_all(self) -> list[OperationRecord]:
        """Return all operations (active + recently completed)."""
        async with self._lock:
            self._expire_locked()
            return list(self._ops.values())

    async def get_events(self, operation_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Return events for an operation, optionally starting from an index.

        *since* is a 0-based event index for catch-up: returns events[since:].
        """
        async with self._lock:
            rec = self._ops.get(operation_id)
            if not rec:
                return []
            events_list = list(rec.events)
            if since > 0:
                return events_list[since:]
            return events_list

    def _expire_locked(self) -> None:
        """Remove completed/failed operations older than COMPLETED_RETENTION.

        Must be called while holding self._lock.
        """
        now = time.time()
        expired = [
            oid
            for oid, rec in self._ops.items()
            if rec.status != "running"
            and rec.completed_at is not None
            and (now - rec.completed_at) > COMPLETED_RETENTION
        ]
        for oid in expired:
            del self._ops[oid]

    async def expire(self) -> int:
        """Expire old completed operations. Returns count expired."""
        async with self._lock:
            before = len(self._ops)
            self._expire_locked()
            return before - len(self._ops)


# Singleton instance.
operation_registry = OperationRegistry()
