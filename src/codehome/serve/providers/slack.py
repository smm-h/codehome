"""Slack provider: notification delivery via the Slack Web API.

Self-registers with the provider registry on import. Uses the Slack
Web API to validate tokens and send messages.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from codehome.serve.logging_config import get_logger
from codehome.serve.providers.registry import register

logger = get_logger(component="provider.slack")

_SLACK_API_BASE = "https://slack.com/api"


class SlackProvider:
    """Slack notification provider."""

    @property
    def name(self) -> str:
        return "slack"

    @property
    def capabilities(self) -> set[str]:
        return {"notify"}

    def test_connection(self, token: str) -> bool:
        """Validate the token by calling auth.test."""
        req = urllib.request.Request(
            f"{_SLACK_API_BASE}/auth.test",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("ok", False) is True
        except Exception:
            logger.warning("slack_test_failed")
            return False

    def send_message(
        self,
        token: str,
        channel_or_user_id: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Send a message via chat.postMessage.

        Args:
            token: Slack bot token.
            channel_or_user_id: Channel ID or user ID to post to.
            text: Fallback text (shown in notifications / non-block clients).
            blocks: Optional Block Kit blocks for rich formatting.

        Returns True on success, False on error.

        """
        payload: dict[str, Any] = {
            "channel": channel_or_user_id,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{_SLACK_API_BASE}/chat.postMessage",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                if not data.get("ok"):
                    logger.warning(
                        "slack_send_failed",
                        channel=channel_or_user_id,
                        error=data.get("error", "unknown"),
                    )
                    return False
                return True
        except Exception:
            logger.warning("slack_send_error", channel=channel_or_user_id, exc_info=True)
            return False

    def send_dm(self, token: str, slack_user_id: str, text: str) -> bool:
        """Send a direct message to a Slack user.

        Convenience wrapper around send_message -- DMs in Slack are sent
        by posting to the user's ID as the channel.
        """
        return self.send_message(token, slack_user_id, text)

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        return None

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        return None


# Self-register when this module is imported.
_provider = SlackProvider()
register(_provider)
