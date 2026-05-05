"""Lightweight operation progress tracking via SSE.

Emits ``operation.progress`` events through the existing EventManager so the
frontend can show progress bars / spinners for long-running operations
(container start/stop, dependency reinstall, git actions, etc.).

Also records all events in the OperationRegistry so the frontend can
catch up on missed events and list in-flight operations.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

from supervisor.bus import Event
from supervisor.bus import fire as bus_fire

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from supervisor.serve.events import EventManager


class OperationTracker:
    """Tracks a single long-running operation and emits progress events.

    Can be used standalone (start/update/complete/fail) or via the
    ``operation_progress`` async context manager for auto-completion.

    Automatically registers with the OperationRegistry on start() and
    marks complete/failed on finish.
    """

    def __init__(
        self,
        events: EventManager,
        operation_type: str,
        service_key: str,
        label: str,
    ) -> None:
        self._events = events
        self.operation_id = f"{operation_type}-{service_key}-{uuid4().hex[:8]}"
        self.operation_type = operation_type
        self.service_key = service_key
        self.label = label

        self._determinate = False
        self._progress: int | None = None
        self._done = False

    async def _emit(
        self,
        phase: str,
        *,
        done: bool = False,
        error: str | None = None,
    ) -> None:
        """Broadcast a single progress event and record it in the registry."""
        event_data = {
            "operation_id": self.operation_id,
            "service_key": self.service_key,
            "label": self.label,
            "phase": phase,
            "determinate": self._determinate,
            "progress": self._progress,
            "done": done,
            "error": error,
        }
        await bus_fire(Event(name="operation.progress", payload=event_data))

        # Record in registry (best-effort, never break the broadcast path).
        try:
            from supervisor.serve.operations import operation_registry

            await operation_registry.record_event(self.operation_id, event_data)
        except Exception:
            pass

    async def emit_output(self, stream: str, line: str) -> None:
        """Emit a raw terminal output line as an operation.output SSE event.

        *stream* is "stdout" or "stderr". ANSI codes are preserved.
        """
        event_data = {
            "operation_id": self.operation_id,
            "service_key": self.service_key,
            "stream": stream,
            "text": line,
        }
        await bus_fire(Event(name="operation.output", payload=event_data))

        # Also record in the ring buffer so the frontend can catch up.
        try:
            from supervisor.serve.operations import operation_registry

            await operation_registry.record_event(
                self.operation_id,
                {
                    "type": "output",
                    **event_data,
                },
            )
        except Exception:
            pass

    async def start(self) -> None:
        """Emit the initial 'Starting...' event and register the operation."""
        self._determinate = False
        self._progress = None

        # Register in the operation registry.
        try:
            from supervisor.serve.operations import operation_registry

            await operation_registry.register(
                self.operation_id,
                self.service_key,
                self.operation_type,
                self.label,
            )
        except Exception:
            pass

        await self._emit("Starting...")

    async def update(
        self,
        phase: str,
        *,
        progress: int | None = None,
        determinate: bool | None = None,
    ) -> None:
        """Emit a progress update.

        If *progress* is provided, *determinate* defaults to True so callers
        don't have to specify both every time.
        """
        if progress is not None:
            self._determinate = True if determinate is None else determinate
            self._progress = max(0, min(100, progress))
        elif determinate is not None:
            self._determinate = determinate
            self._progress = None
        else:
            # Neither provided -- indeterminate pulse.
            self._determinate = False
            self._progress = None

        await self._emit(phase)

    async def complete(self, phase: str = "Done") -> None:
        """Mark the operation as successfully finished."""
        if self._done:
            return
        self._done = True
        self._progress = 100 if self._determinate else None
        await self._emit(phase, done=True)

        try:
            from supervisor.serve.operations import operation_registry

            await operation_registry.mark_complete(self.operation_id)
        except Exception:
            pass

    async def fail(self, reason: str, phase: str = "Failed") -> None:
        """Mark the operation as failed."""
        if self._done:
            return
        self._done = True
        await self._emit(phase, done=True, error=reason)

        try:
            from supervisor.serve.operations import operation_registry

            await operation_registry.mark_failed(self.operation_id, reason)
        except Exception:
            pass


@asynccontextmanager
async def operation_progress(
    events: EventManager,
    operation_type: str,
    service_key: str,
    label: str,
) -> AsyncGenerator[OperationTracker, None]:
    """Async context manager that auto-emits start/done/error progress events.

    Usage::

        async with operation_progress(events, "reinstall-deps", key, "Reinstalling") as op:
            await op.update("Checking lockfile...")
            await op.update("Running npm ci...", progress=50)
            # auto-completes on normal exit, auto-fails on exception
    """
    op = OperationTracker(events, operation_type, service_key, label)
    await op.start()
    try:
        yield op
        await op.complete()
    except BaseException as exc:
        await op.fail(str(exc))
        raise
