"""Linear issue data and mutations for the server API.

Read operations pull from the local cache (.supervisor/cache/linear.json)
and branch issue.json files. Write operations call the Linear GraphQL API
and reuse the same functions the CLI uses.

Per-user token support: ``resolve_linear_token`` tries the encrypted
connection store first, falling back to the machine-wide LINEAR_API_KEY.
"""

from __future__ import annotations

from typing import Any

from codehome.linear_shared import (
    create_comment as _create_comment,
)
from codehome.linear_shared import (
    delete_comment as _delete_comment,
)
from codehome.linear_shared import (
    emit_linear_outgoing,
)
from codehome.linear_shared import (
    fetch_comments as _fetch_comments,
)
from codehome.linear_shared import (
    fetch_issue as _fetch_issue,
)
from codehome.linear_shared import (
    get_linked_issue as _get_linked_issue,
)
from codehome.linear_shared import (
    issue_file as _issue_file,
)
from codehome.linear_shared import (
    load_cache as _load_cache,
)
from codehome.linear_shared import (
    load_issue_link as _load_issue_link,
)
from codehome.linear_shared import (
    load_token as _load_env_token,
)
from codehome.linear_shared import (
    load_workflows as _load_workflows,
)
from codehome.linear_shared import (
    save_issue_link as _save_issue_link,
)
from codehome.linear_shared import (
    update_issue as _update_issue,
)
from codehome.serve.logging_config import get_logger

logger = get_logger(component="linear_ops")


# ---------------------------------------------------------------------------
# Per-user token resolution
# ---------------------------------------------------------------------------


def resolve_linear_token(username: str | None, jwt_secret: str | None) -> str | None:  # noqa: dead-code
    """Return a Linear API token, preferring the per-user connection store.

    Falls back to the machine-wide LINEAR_API_KEY env var / .env file.
    Returns None if no token is available anywhere.
    """
    # Try per-user connection store first.
    if username and jwt_secret:
        from codehome.serve.connections import get_token

        token = get_token(username, "linear", jwt_secret)
        if token:
            return token

    # Fall back to machine-wide env var / .env file.
    try:
        return _load_env_token()
    except SystemExit:
        # load_token calls sys.exit when no token is found; catch it
        # so the server doesn't crash.
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_link(repo: str, branch: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load issue link + cached issue, raise if not linked."""
    link, issue = _get_linked_issue(repo, branch)
    if not link:
        msg = f"no linked issue for '{branch}'"
        raise ValueError(msg)
    # Merge link["id"] as fallback so callers can always access issue["id"]
    # even when the Linear cache is stale/empty.
    merged = {"id": link["id"], **(issue or {})}
    return link, merged


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def get_linked_issue(repo: str, branch: str) -> dict[str, Any] | None:
    """Return issue details for a branch, or None if not linked.

    Reads issue.json from the branch dir, then looks up the full
    issue in the Linear cache. Returns a flat dict with display fields.
    """
    link, issue = _get_linked_issue(repo, branch)
    if not link:
        return None

    # Always return the link identifier/url even if cache is stale.
    result: dict[str, Any] = {
        "identifier": link.get("identifier", ""),
        "url": link.get("url", ""),
    }

    if issue:
        result.update(
            {
                "title": issue.get("title", ""),
                "state": issue.get("state", ""),
                "stateType": issue.get("stateType", ""),
                "priority": issue.get("priority", 0),
                "priorityLabel": issue.get("priorityLabel", ""),
                "url": issue.get("url", "") or result["url"],
                "labels": issue.get("labels", []),
                "assignee": issue.get("assignee", ""),
                "due_date": issue.get("dueDate"),
                "description": issue.get("description", ""),
            },
        )

    return result


def get_issue_comments(repo: str, branch: str) -> list[dict[str, Any]]:
    """Return comments for the branch's linked issue.

    Each comment: {id, author, body, created_at}.
    Fetches live from the Linear API (requires LINEAR_API_KEY).
    Returns [] if no issue is linked or on error.
    """
    link, issue = _get_linked_issue(repo, branch)
    if not link or not issue:
        return []

    issue_id = issue.get("id") or link.get("id")
    if not issue_id:
        return []

    try:
        raw_comments = _fetch_comments(issue_id)
    except Exception:
        return []

    return [
        {
            "id": c.get("id", ""),
            "author": c.get("user", "?"),
            "body": c.get("body", ""),
            "created_at": c.get("createdAt", ""),
        }
        for c in raw_comments
    ]


def get_workflow_states(repo: str, branch: str) -> list[dict[str, Any]]:
    """Return available workflow states for the branch's linked issue's team.

    Each state: {id, name, type}. Returns [] if not linked or no workflows.
    """
    link = _load_issue_link(repo, branch)
    if not link:
        return []

    team = link.get("team", "")
    workflows = _load_workflows()
    team_data = workflows.get(team, {})
    states = team_data.get("states", {})

    return [{"id": info["id"], "name": name, "type": info.get("type", "")} for name, info in states.items()]


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def add_comment(repo: str, branch: str, body: str, session_id: str | None = None) -> dict[str, Any]:
    """Add a comment to the branch's linked issue."""
    link, issue = _require_link(repo, branch)
    success = _create_comment(link["identifier"], issue["id"], body)
    emit_linear_outgoing(session_id, repo, branch, link["identifier"], "add_comment", {"body": body}, success)
    if not success:
        msg = f"failed to add comment to {link['identifier']}"
        raise RuntimeError(msg)
    return {"ok": True}


def remove_comment(repo: str, branch: str, comment_id: str) -> dict[str, Any]:
    """Delete a comment by its UUID."""
    link, _issue = _require_link(repo, branch)
    success = _delete_comment(link["identifier"], comment_id)
    if not success:
        msg = f"failed to delete comment on {link['identifier']}"
        raise RuntimeError(msg)
    return {"ok": True}


def update_description(repo: str, branch: str, body: str, session_id: str | None = None) -> dict[str, Any]:
    """Update the issue description."""
    link, issue = _require_link(repo, branch)
    success = _update_issue(link["identifier"], issue["id"], description=body)
    emit_linear_outgoing(
        session_id,
        repo,
        branch,
        link["identifier"],
        "set_description",
        {"description": body[:200]},
        success,
    )
    if not success:
        msg = f"failed to update description on {link['identifier']}"
        raise RuntimeError(msg)
    return {"ok": True}


def update_state(repo: str, branch: str, state_id: str, session_id: str | None = None) -> dict[str, Any]:
    """Change the issue state by state UUID."""
    link, issue = _require_link(repo, branch)
    success = _update_issue(link["identifier"], issue["id"], stateId=state_id)
    emit_linear_outgoing(session_id, repo, branch, link["identifier"], "set_state", {"stateId": state_id}, success)
    if not success:
        msg = f"failed to update state on {link['identifier']}"
        raise RuntimeError(msg)
    return {"ok": True}


def link_issue(repo: str, branch: str, issue_id: str) -> dict[str, Any]:
    """Link a branch to a Linear issue by identifier (e.g. VELENT-123).

    issue_id here is the human-readable identifier, not the UUID.
    """
    existing = _load_issue_link(repo, branch)
    if existing:
        msg = f"'{branch}' already linked to {existing['identifier']}"
        raise ValueError(msg)

    identifier = issue_id.upper()
    cache = _load_cache()
    issue = cache.get(identifier)
    if not issue:
        issue = _fetch_issue(identifier)
    if not issue:
        msg = f"Linear issue {identifier} not found"
        raise ValueError(msg)

    _save_issue_link(repo, branch, identifier, issue["id"], issue["team"])
    return {"ok": True, "identifier": identifier, "title": issue.get("title", "")}


def unlink_issue(repo: str, branch: str) -> dict[str, Any]:
    """Remove the issue.json link from a branch."""
    path = _issue_file(repo, branch)
    if not path.exists():
        msg = f"'{branch}' has no linked issue"
        raise ValueError(msg)
    path.unlink()
    return {"ok": True}
