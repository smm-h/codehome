"""Notification dispatcher: shared vocabulary and channel registry.

Defines the preference categories and event-to-category mapping that all
notification channels (web push, Slack, etc.) share.  The ``NotificationChannel``
protocol and ``NotificationRegistry`` provide the abstraction that Phase 3 will
wire existing channels into.

Phase 2 -- interfaces and shared data only.  Existing push.py and
slack_notify.py continue to work as-is; they are not modified here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codehome.serve.logging_config import get_logger

log = get_logger(component="notifications")

# Default notification preferences -- all enabled.
# Grouped by domain for UI display; the keys are the preference categories
# referenced in EVENT_TO_CATEGORY below.
#
# Canonical source of truth: channels consult these defaults when a user
# has no stored preference for a category.
DEFAULT_PREFERENCES: dict[str, bool] = {
    # Deployments
    "pipeline_failure": True,
    "pipeline_success": True,
    # AI
    "agent_question": True,
    "agent_completion": True,
    "conductor_message": True,
    "conductor_completion": True,
    # Services
    "service_state": True,
    # Git
    "branch_activity": True,
    "review_activity": True,
    "rebase_result": True,
    # Quality
    "test_results": True,
}

# Map SSE event types to preference categories.
# Only events in this map trigger notifications.  High-frequency / streaming
# events (service.log, agent.output, tdd.output, tests.output, metrics,
# ports, rebase.progress, conductor.autonomy, design.status) are excluded.
#
# NOTE: conductor.crash maps to a category not in DEFAULT_PREFERENCES.
# It is always delivered (no user toggle) -- channels should treat unknown
# categories as enabled.
EVENT_TO_CATEGORY: dict[str, str] = {
    # Deployments
    "pipeline.state": "pipeline_failure",  # refined at delivery time by status
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


@runtime_checkable
class NotificationChannel(Protocol):
    """Protocol that notification channels must satisfy.

    Each channel (web push, Slack, email, etc.) implements this protocol
    and registers with the module-level ``notification_registry``.
    """

    name: str

    async def send(
        self,
        user: str,
        category: str,
        title: str,
        body: str,
        data: dict[str, object] | None = None,
    ) -> None:
        """Deliver a notification to a single user.

        Args:
            user: User identifier (handle or user_id).
            category: Preference category key (from DEFAULT_PREFERENCES).
            title: Short notification title.
            body: Notification body text.
            data: Optional extra data (URL, tag, event payload, etc.).
        """
        ...


class NotificationRegistry:
    """Registry of notification channels.

    Channels register at startup; the dispatcher (Phase 3) iterates
    registered channels to fan out each notification.
    """

    def __init__(self) -> None:
        self._channels: dict[str, NotificationChannel] = {}

    def register_channel(self, channel: NotificationChannel) -> None:
        """Register a notification channel.

        Raises ValueError if a channel with the same name is already
        registered.
        """
        if channel.name in self._channels:
            msg = f"Notification channel {channel.name!r} is already registered"
            raise ValueError(msg)
        self._channels[channel.name] = channel
        log.debug("channel_registered", name=channel.name)

    def get_channels(self) -> list[NotificationChannel]:
        """Return all registered channels (stable insertion order)."""
        return list(self._channels.values())

    def get_channel(self, name: str) -> NotificationChannel | None:
        """Return a specific channel by name, or None."""
        return self._channels.get(name)


# Module-level singleton: channels register into this at startup,
# the dispatcher fans out notifications through it.
notification_registry = NotificationRegistry()
