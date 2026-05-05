"""Log aggregator: streams Docker container logs and broadcasts via SSE."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from codehome.bus import Event, fire
from codehome.serve.services import services

log = logging.getLogger(__name__)

# Ring buffer size per service (number of log entries).
_BUFFER_SIZE = 1000
# How often to rebuild container map and manage threads (seconds).
_MAP_REBUILD_INTERVAL = 5.0
# How often to drain pending entries and broadcast (seconds).
_BROADCAST_INTERVAL = 0.2
# Stop logging after this many consecutive map-rebuild errors.
_MAX_CONSECUTIVE_ERRORS = 5
# Timeout when joining threads during shutdown (seconds).
_THREAD_JOIN_TIMEOUT = 2.0
# Max entries per SSE broadcast to avoid oversized payloads.
_MAX_BATCH_SIZE = 200


def _parse_docker_timestamp(raw: str) -> float:
    """Parse Docker RFC3339Nano timestamp to Unix epoch float.

    Docker timestamps look like ``2026-04-06T12:00:00.123456789Z``.
    Python's ``fromisoformat`` only handles up to 6 fractional digits
    (microseconds), so we trim nanosecond precision before parsing.
    """
    # Strip trailing newline/whitespace.
    raw = raw.strip()
    # Handle nanosecond precision: find the decimal point and trim to 6 digits.
    if "." in raw:
        dot_idx = raw.index(".")
        # Find the end of the fractional part (before 'Z' or '+' or '-' offset).
        frac_end = dot_idx + 1
        while frac_end < len(raw) and raw[frac_end].isdigit():
            frac_end += 1
        # Keep at most 6 fractional digits.
        max_frac = min(frac_end, dot_idx + 7)
        raw = raw[:max_frac] + raw[frac_end:]
    # fromisoformat handles 'Z' suffix since Python 3.11.
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


class LogAggregator:
    """Streams Docker container logs and broadcasts via SSE."""

    def __init__(self) -> None:
        self._running: bool = False
        self._client: Any = None  # Docker SDK client (lazy)
        # Per-service ring buffer of recent log entries.
        self._buffers: dict[str, deque[dict[str, object]]] = {}
        # container_id -> threading.Thread (one thread per container).
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock: threading.Lock = threading.Lock()
        # Pending log entries from worker threads, drained by main loop.
        self._pending: list[dict[str, object]] = []
        self._pending_lock: threading.Lock = threading.Lock()

    async def run(self) -> None:
        """Manage log streaming threads and batch SSE broadcasts.

        Two interleaved timers:
        - Every 5 seconds: rebuild container map, start/stop threads.
        - Every 200ms: drain pending entries and batch SSE broadcast.
        """
        self._running = True
        # Lazy import docker SDK.
        try:
            import docker

            self._client = docker.from_env()
        except Exception:
            log.info("Log aggregator disabled: Docker SDK unavailable or daemon not running")
            return

        consecutive_errors = 0
        last_map_rebuild = 0.0

        while self._running:
            now = time.monotonic()

            # Rebuild container map periodically.
            if now - last_map_rebuild >= _MAP_REBUILD_INTERVAL:
                last_map_rebuild = now
                try:
                    await asyncio.to_thread(self._manage_threads)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    if consecutive_errors <= _MAX_CONSECUTIVE_ERRORS:
                        log.warning("Log thread management failed: %s", exc)
                    elif consecutive_errors == _MAX_CONSECUTIVE_ERRORS + 1:
                        log.warning("Log thread management failing repeatedly, suppressing")

            # Drain pending entries and broadcast.
            entries = self._drain_pending()
            if entries:
                # Also store in per-service ring buffers.
                for entry in entries:
                    svc_key = str(entry["service_key"])
                    buf = self._buffers.get(svc_key)
                    if buf is None:
                        buf = deque(maxlen=_BUFFER_SIZE)
                        self._buffers[svc_key] = buf
                    buf.append(entry)

                await fire(Event(name="service.log", payload={"entries": entries}))

            await asyncio.sleep(_BROADCAST_INTERVAL)

    def stop(self) -> None:
        """Stop all threads and main loop."""
        self._running = False
        # Close Docker client -- causes blocking generators to error out.
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        # Join all threads with timeout.
        with self._threads_lock:
            threads = list(self._threads.values())
            self._threads.clear()
        for thread in threads:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT)

    def get_logs(self, service_key: str, limit: int = 100) -> list[dict[str, object]]:
        """Return recent log entries for a service from the ring buffer."""
        buf = self._buffers.get(service_key)
        if not buf:
            return []
        # Slice directly from the deque to avoid copying the entire buffer.
        n = len(buf)
        start = max(0, n - limit)
        return [buf[i] for i in range(start, n)]

    def _manage_threads(self) -> None:
        """Rebuild container map and start/stop streaming threads.

        Called from asyncio.to_thread -- this is blocking code.
        """
        from codehome.serve.monitoring.container_map import build_container_map

        if not self._client:
            return

        container_map, containers_by_name = build_container_map(services, self._client)

        with self._threads_lock:
            # Clean up dead threads (container stopped, thread exited naturally).
            dead = [cid for cid, t in self._threads.items() if not t.is_alive()]
            for cid in dead:
                del self._threads[cid]

            # Build set of container IDs we should be streaming.
            # container_map maps container_name -> service_key; we need container_id.
            active_ids: dict[str, str] = {}  # container_id -> service_key
            for container_name, svc_key in container_map.items():
                container = containers_by_name.get(container_name)
                if container:
                    active_ids[container.id] = svc_key

            # Start threads for new containers not already tracked.
            for cid, svc_key in active_ids.items():
                if cid not in self._threads:
                    thread = threading.Thread(
                        target=self._stream_container,
                        args=(cid, svc_key),
                        name=f"log-{cid[:12]}",
                        daemon=True,
                    )
                    self._threads[cid] = thread
                    thread.start()

            # Threads for removed containers will exit naturally when their
            # generator exhausts (container stops) or when the client is closed.

    def _stream_container(self, container_id: str, service_key: str) -> None:
        """Worker thread: follows container logs (blocking).

        Uses demux=True so stdout/stderr are separated without manual
        header parsing. Yields (stdout_bytes, stderr_bytes) tuples where
        one side is None.
        """
        generator = None
        try:
            if not self._client:
                return
            container = self._client.containers.get(container_id)
            generator = container.logs(
                stream=True,
                follow=True,
                since=int(time.time()),
                timestamps=True,
                demux=True,
            )
            for stdout_chunk, stderr_chunk in generator:
                if not self._running:
                    break
                # Process each non-None chunk.
                for chunk, stream_name in [
                    (stdout_chunk, "stdout"),
                    (stderr_chunk, "stderr"),
                ]:
                    if chunk is None:
                        continue
                    self._process_chunk(chunk, service_key, stream_name)
        except Exception as exc:
            # Container may have stopped or client closed -- expected during
            # shutdown or container restarts.
            if self._running:
                log.debug("Log stream ended for %s (%s): %s", service_key, container_id[:12], exc)
        finally:
            # CRITICAL: close the generator to prevent socket leaks.
            if generator is not None:
                try:
                    generator.close()
                except Exception:
                    pass

    def _process_chunk(self, chunk: bytes, service_key: str, stream: str) -> None:
        """Parse a raw log chunk into entries and enqueue them."""
        text = chunk.decode("utf-8", errors="replace")
        # A chunk may contain multiple lines.
        for line in text.splitlines():
            if not line:
                continue
            # Docker prepends RFC3339Nano timestamp when timestamps=True.
            ts: float
            log_text: str
            space_idx = line.find(" ")
            if space_idx > 0:
                try:
                    ts = _parse_docker_timestamp(line[:space_idx])
                    log_text = line[space_idx + 1 :].rstrip("\n\r")
                except (ValueError, IndexError):
                    # If timestamp parsing fails, use current time.
                    ts = time.time()
                    log_text = line.rstrip("\n\r")
            else:
                ts = time.time()
                log_text = line.rstrip("\n\r")

            entry: dict[str, object] = {
                "ts": ts,
                "service_key": service_key,
                "stream": stream,
                "text": log_text,
            }
            with self._pending_lock:
                self._pending.append(entry)

    def _drain_pending(self) -> list[dict[str, object]]:
        """Drain pending entries (thread-safe), capped at _MAX_BATCH_SIZE.

        If more entries remain, they'll be picked up in the next cycle.
        """
        with self._pending_lock:
            if not self._pending:
                return []
            if len(self._pending) <= _MAX_BATCH_SIZE:
                entries = self._pending
                self._pending = []
                return entries
            # Take only the first batch; leave the rest for next cycle.
            entries = self._pending[:_MAX_BATCH_SIZE]
            self._pending = self._pending[_MAX_BATCH_SIZE:]
            return entries


# Singleton.
log_aggregator = LogAggregator()
