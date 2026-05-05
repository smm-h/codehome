"""SSE event broadcast manager."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.serve.push import PushManager

logger = logging.getLogger(__name__)

# Heartbeat interval in seconds.  Keeps connections alive through proxies
# and causes a write that detects dead TCP sockets on cleanup.
HEARTBEAT_INTERVAL = 15

# Map SSE event types to push notification categories.
# Only events in this map trigger push notifications.
# High-frequency / streaming events (service.log, agent.output, tdd.output,
# tests.output, metrics, ports, rebase.progress, conductor.autonomy,
# design.status) are intentionally excluded.
_PUSH_EVENT_MAP: dict[str, str] = {
    # Deployments
    "pipeline.state": "pipeline_failure",  # refined in _build_push_content
    # AI
    "agent.question": "agent_question",
    "agent.run.DONE": "agent_completion",
    "conductor.message": "conductor_message",
    "conductor.stop": "conductor_completion",
    "conductor.crash": "conductor_crash",
    # Services
    "service.state": "service_state",
    # Git / Branches
    "branch.switch": "branch_activity",
    "branch.create.DONE": "branch_activity",
    "branch.close.DONE": "branch_activity",
    "branch.rename.DONE": "branch_activity",
    "rebase.DONE": "rebase_result",
    "rebase.conflict": "rebase_result",
    # Quality
    "tests.run.DONE": "test_results",
    # Reviews
    "review.post.DONE": "review_activity",
    "review.resolve.DONE": "review_activity",
}


class EventManager:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._push_manager: PushManager | None = None
        # Prevent fire-and-forget push tasks from being garbage-collected.
        self._background_tasks: set[asyncio.Task[None]] = set()

    def set_push_manager(self, pm: "PushManager") -> None:
        """Attach a PushManager so broadcasts also send push notifications."""
        self._push_manager = pm

    @property
    def client_count(self) -> int:
        """Number of currently connected SSE clients."""
        return len(self._clients)

    async def broadcast(self, event_type: str, data: dict[str, object]) -> None:
        """Send an event to all connected clients and push if applicable."""
        msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        for q in self._clients:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # drop message for slow client; framework handles disconnect

        # Fire push notification for mapped event types.
        self._maybe_push(event_type, data)

        # Persist Vite port snapshot on every service-state change so the
        # resolver's disk fallback (see supervisor.inspect.resolver) stays
        # fresh even when the server is later stopped.  Best-effort,
        # non-blocking: runs in a thread to avoid stalling the event loop
        # (synchronous file I/O here was causing SSE heartbeat misses
        # during multi-container supabase stop).
        if event_type == "service.state":
            asyncio.get_running_loop().run_in_executor(None, self._maybe_write_vite_ports_state)

    def _maybe_write_vite_ports_state(self) -> None:
        """Best-effort refresh of the on-disk Vite ports snapshot.

        Imported lazily to avoid a circular import with routers/services.
        Any failure here is swallowed -- a stale snapshot is better than
        a broken broadcast path.
        """
        try:
            from supervisor.serve.routers.services import write_vite_ports_state_from_registry

            write_vite_ports_state_from_registry()
        except Exception:
            logger.exception("failed to refresh vite-ports.json snapshot")

    def _maybe_push(self, event_type: str, data: dict[str, object]) -> None:
        """Schedule a push notification without blocking the event loop.

        The actual HTTP delivery is offloaded to a thread via
        PushManager.broadcast_async(), wrapped in a fire-and-forget task
        so the caller never awaits it.
        """
        if not self._push_manager:
            return
        push_category = _PUSH_EVENT_MAP.get(event_type)
        if not push_category:
            return

        # Build notification content from event data.
        title, body, url = _build_push_content(event_type, data)
        if not title:
            return

        # pipeline.state maps to different categories based on status.
        if event_type == "pipeline.state":
            status = data.get("status", "")
            if status == "success":
                push_category = "pipeline_success"
            # "failure" keeps the default "pipeline_failure" from the map.

        # Fire-and-forget: schedule async delivery without awaiting.
        task = asyncio.create_task(self._deliver_push(event_type, push_category, title, body, url))
        # Prevent the task from being garbage-collected before completion.
        task.add_done_callback(self._background_tasks.discard)
        self._background_tasks.add(task)

    async def _deliver_push(
        self,
        event_type: str,
        push_category: str,
        title: str,
        body: str,
        url: str,
    ) -> None:
        """Await the async push broadcast in a background task."""
        try:
            assert self._push_manager is not None  # guarded by _maybe_push
            await self._push_manager.broadcast_async(
                title=title,
                body=body,
                url=url,
                tag=event_type,
                event_type=push_category,
            )
        except Exception:
            logger.exception("Failed to send push notification for %s", event_type)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncGenerator[str, None], None]:
        """Context manager yielding an async generator of SSE messages."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._clients.add(q)
        try:

            async def stream() -> AsyncGenerator[str, None]:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_INTERVAL)
                        yield msg
                    except TimeoutError:
                        # SSE comment keeps the connection alive.
                        yield ": heartbeat\n\n"

            yield stream()
        finally:
            self._clients.discard(q)


def _build_push_content(event_type: str, data: dict[str, object]) -> tuple[str, str, str]:
    """Extract title, body, and URL from event data for push notifications.

    Returns ("", "", "") if the event should not trigger a notification
    (e.g. a non-failure pipeline update when the category is pipeline_failure).
    """
    # -- Deployments --------------------------------------------------------

    if event_type == "pipeline.state":
        status = data.get("status", "")
        branch = data.get("branch", "unknown")
        if status == "failure":
            return "Pipeline Failed", f"Pipeline failed for {branch}", f"/branch/{branch}"
        if status == "success":
            return "Pipeline Succeeded", f"Pipeline passed for {branch}", f"/branch/{branch}"
        return "", "", ""

    # -- AI -----------------------------------------------------------------

    if event_type == "agent.question":
        branch = data.get("branch", "unknown")
        return "Agent Question", f"An agent needs your input on {branch}", "/inbox"

    if event_type == "agent.run.DONE":
        session_id = data.get("session_id", "")
        error = data.get("error")
        if error:
            return "Agent Failed", f"Agent {session_id} failed: {error}", "/inbox"
        return "Agent Completed", f"Agent {session_id} finished successfully", "/inbox"

    if event_type == "conductor.message":
        branch = data.get("branch", "unknown")
        msg_type = data.get("type", "message")
        return "Conductor Message", f"New {msg_type} on {branch}", f"/branch/{branch}"

    if event_type == "conductor.stop":
        branch = data.get("branch", "unknown")
        return "Conductor Finished", f"Conductor session ended for {branch}", f"/branch/{branch}"

    if event_type == "conductor.crash":
        branch = data.get("branch", "unknown")
        return "Conductor Crashed", f"Conductor crashed on {branch}", f"/branch/{branch}"

    # -- Services -----------------------------------------------------------

    if event_type == "service.state":
        state = data.get("state", "")
        name = data.get("name", data.get("key", "service"))
        # Running with migration errors still deserves a push notification.
        metadata = data.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("migration_error"):
            return "Migrations Failed", f"Migrations failed for {name}", "/"
        # Only notify on state changes to stopped or error.
        if state not in ("stopped", "error", "crashed"):
            return "", "", ""
        return "Service State Change", f"{name} is now {state}", "/"

    # -- Git / Branches -----------------------------------------------------

    if event_type == "branch.switch":
        branch = data.get("branch", "")
        action = data.get("action", "changed")
        return "Branch Activity", f"{branch}: {action}", "/"

    if event_type == "branch.create.DONE":
        branch = data.get("qualified", data.get("branch", ""))
        return "Branch Created", f"New branch: {branch}", "/"

    if event_type == "branch.close.DONE":
        branch = data.get("qualified", "")
        return "Branch Closed", f"Branch archived: {branch}", "/"

    if event_type == "branch.rename.DONE":
        old = data.get("old_qualified", "")
        new = data.get("new_qualified", "")
        return "Branch Renamed", f"{old} -> {new}", "/"

    if event_type == "rebase.DONE":
        branch = data.get("qualified", "unknown")
        outcome = data.get("outcome", "success")
        if outcome == "error":
            msg = data.get("message", "unknown error")
            return "Rebase Failed", f"Rebase failed for {branch}: {msg}", f"/branch/{branch}"
        return "Rebase Completed", f"Rebase succeeded for {branch}", f"/branch/{branch}"

    if event_type == "rebase.conflict":
        branch = data.get("qualified", "unknown")
        return "Rebase Conflict", f"Rebase paused with conflicts on {branch}", f"/branch/{branch}"

    # -- Reviews ------------------------------------------------------------

    if event_type == "review.post.DONE":
        branch = data.get("branch", "unknown")
        count = data.get("comments_posted", 0)
        return "Review Posted", f"Posted {count} comment(s) on {branch}", f"/branch/{branch}"

    if event_type == "review.resolve.DONE":
        branch = data.get("branch", "unknown")
        count = data.get("threads_resolved", 0)
        return "Threads Resolved", f"Resolved {count} thread(s) on {branch}", f"/branch/{branch}"

    # -- Quality ------------------------------------------------------------

    if event_type == "tests.run.DONE":
        run_id = data.get("run_id", "")
        passed = data.get("passed", False)
        status_label = "passed" if passed else "failed"
        return "Test Run " + status_label.title(), f"Test run {run_id} {status_label}", "/"

    return "", "", ""


# Singleton instance used by the server and all modules.
events = EventManager()
