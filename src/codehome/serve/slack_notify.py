"""Slack notification dispatcher -- re-exported from supervisor plugin.

The implementation lives in ``codehome.supervisor.ops.slack_notify``.
This stub provides backward compatibility for callers that import from
the original location.
"""

from codehome.supervisor.ops.slack_notify import (
    SlackNotifier,
    send_review_request,
)

__all__ = ["SlackNotifier", "send_review_request"]
