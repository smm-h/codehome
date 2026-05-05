"""Slack notification dispatcher: maps SSE events to Slack DMs.

Subscribes to server SSE events and delivers targeted Slack
notifications to branch owners. Uses the same push preference system
as web push so users can control which events trigger Slack DMs.

Lifecycle: started in the server lifespan, runs as a background task.
Fails silently when no Slack token is configured or the recipient has
no Slack user ID in the roster.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from codehome.serve.connections import get_token
from codehome.serve.logging_config import get_logger
from codehome.serve.push import DEFAULT_PREFERENCES
from codehome.serve.roster import all_members, resolve

if TYPE_CHECKING:
    from codehome.serve.events import EventManager

logger = get_logger(component="slack_notify")

# Map SSE event types to (push_category, message_builder) pairs.
# The push_category reuses the existing push preference keys so users
# get a single toggle for both web push and Slack notifications.
_SLACK_EVENT_MAP: dict[str, str] = {
    "pipeline.state": "pipeline_failure",
    "agent.question": "agent_question",
    "conductor.stop": "conductor_completion",
}


def _build_slack_message(event_type: str, data: dict[str, object]) -> tuple[str, str]:
    """Build (title, body) for a Slack DM from an SSE event.

    Returns ("", "") if the event should not trigger a notification.
    """
    branch = str(data.get("branch", "unknown"))

    if event_type == "pipeline.state":
        status = data.get("status", "")
        if status != "failure":
            return "", ""
        return "CI Failed", f"Pipeline failed on *{branch}*"

    if event_type == "agent.question":
        question = str(data.get("question", ""))
        preview = (question[:80] + "...") if len(question) > 80 else question
        return "Agent Needs Input", f"Agent needs your input on *{branch}*: {preview}"

    if event_type == "conductor.stop":
        return "Conductor Finished", f"Conductor session ended for *{branch}*"

    return "", ""


def _resolve_branch_owner(data: dict[str, object]) -> str | None:
    """Determine the owner/author handle from event data.

    Events may carry a ``user`` field directly. Otherwise we fall back
    to trying to resolve the branch name to a roster member (for small
    teams where branches map to individuals).
    """
    # Direct user field (e.g. from agent sessions).
    user = data.get("user")
    if user and isinstance(user, str):
        return user
    return None


def _first_slack_id(identifier: str) -> str | None:
    """Look up a Slack user ID from the roster by any identifier."""
    member = resolve(identifier)
    if member and member.slack:
        return member.slack[0]
    return None


def _get_slack_token(jwt_secret: str) -> tuple[str | None, str | None]:
    """Find a Slack bot token from any connected user.

    The Slack bot token is workspace-scoped (not per-user), so we look
    through all roster members and return the first one that has a Slack
    token stored. Returns (token, username) or (None, None).
    """
    for member in all_members():
        token = get_token(member.handle, "slack", jwt_secret)
        if token:
            return token, member.handle
    return None, None


class SlackNotifier:
    """Background listener that dispatches Slack DMs for server events."""

    def __init__(self, event_manager: EventManager, jwt_secret: str) -> None:
        self._events = event_manager
        self._jwt_secret = jwt_secret
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background listener as an asyncio task."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._listen(), name="slack-notifier")
        logger.info("slack_notifier_started")

    def stop(self) -> None:
        """Cancel the background listener."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("slack_notifier_stopped")

    async def _listen(self) -> None:
        """Subscribe to SSE events and dispatch Slack notifications."""
        async with self._events.subscribe() as stream:
            async for raw_msg in stream:
                try:
                    await self._handle_message(raw_msg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("slack_notify_error")

    async def _handle_message(self, raw_msg: str) -> None:
        """Parse an SSE message and send a Slack DM if applicable."""
        # SSE format: "event: <type>\ndata: <json>\n\n"
        # Heartbeat comments start with ":"
        if raw_msg.startswith(":"):
            return

        event_type = ""
        data_str = ""
        for line in raw_msg.strip().splitlines():
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]

        if not event_type or event_type not in _SLACK_EVENT_MAP:
            return

        try:
            data: dict[str, object] = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            return

        push_category = _SLACK_EVENT_MAP[event_type]

        # Refine pipeline.state category based on status.
        if event_type == "pipeline.state":
            status = data.get("status", "")
            if status == "success":
                push_category = "pipeline_success"

        title, body = _build_slack_message(event_type, data)
        if not title:
            return

        # Determine recipient.
        owner = _resolve_branch_owner(data)
        if not owner:
            # No owner in event data -- broadcast to all roster members
            # who have Slack IDs (small team, all care about all branches).
            await self._notify_all(push_category, title, body)
            return

        await self._notify_user(owner, push_category, title, body)

    async def _notify_user(
        self,
        username: str,
        push_category: str,
        title: str,
        body: str,
    ) -> None:
        """Send a Slack DM to a specific user, respecting preferences."""
        from codehome.serve.push import push_manager

        prefs = push_manager.get_preferences(username)
        if not prefs.get(push_category, DEFAULT_PREFERENCES.get(push_category, True)):
            return

        slack_id = _first_slack_id(username)
        if not slack_id:
            return

        await self._send(slack_id, f"*{title}*\n{body}")

    async def _notify_all(
        self,
        push_category: str,
        title: str,
        body: str,
    ) -> None:
        """Send a Slack DM to all roster members with Slack IDs."""
        from codehome.serve.push import push_manager

        for member in all_members():
            if not member.slack:
                continue
            prefs = push_manager.get_preferences(member.handle)
            if not prefs.get(push_category, DEFAULT_PREFERENCES.get(push_category, True)):
                continue
            await self._send(member.slack[0], f"*{title}*\n{body}")

    async def _send(self, slack_user_id: str, text: str) -> None:
        """Send a Slack DM via the provider, running blocking I/O off-loop."""
        from codehome.serve.providers.slack import _provider as slack_provider

        token, _ = _get_slack_token(self._jwt_secret)
        if not token:
            return

        await asyncio.to_thread(slack_provider.send_dm, token, slack_user_id, text)


def send_review_request(
    *,
    requester_handle: str,
    reviewer_handle: str,
    pr_number: int,
    pr_title: str,
    branch: str,
    dashboard_url: str = "",
    jwt_secret: str,
) -> bool:
    """Send a Slack DM requesting a code review.

    Called by the Conductor or manually from the dashboard. Runs
    synchronously (blocking I/O) -- callers in async contexts should
    wrap in ``asyncio.to_thread``.

    Args:
        requester_handle: Handle of the person requesting the review.
        reviewer_handle: Handle of the reviewer (looked up in roster).
        pr_number: Pull request number.
        pr_title: Pull request title.
        branch: Qualified branch name (repo:branch).
        dashboard_url: Base URL of the dashboard for deep links.
        jwt_secret: JWT secret for decrypting the Slack bot token.

    Returns True if the DM was sent, False otherwise.

    """
    from codehome.serve.providers.slack import _provider as slack_provider

    # Resolve reviewer's Slack ID.
    slack_id = _first_slack_id(reviewer_handle)
    if not slack_id:
        logger.info(
            "review_request_no_slack",
            reviewer=reviewer_handle,
        )
        return False

    # Get the Slack bot token.
    token, _ = _get_slack_token(jwt_secret)
    if not token:
        logger.info("review_request_no_token")
        return False

    # Build the message.
    link = ""
    if dashboard_url:
        link = f"  <{dashboard_url}/branch/{branch}|View in dashboard>"
    text = f"{requester_handle} requested your review on PR #{pr_number}: {pr_title}{link}"

    return slack_provider.send_dm(token, slack_id, text)
