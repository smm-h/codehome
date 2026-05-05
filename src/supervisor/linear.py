"""Linear API client: cache sync, diff, and bulk fetch.

Re-exports all shared symbols from supervisor.linear_shared so that
existing ``from supervisor.linear import X`` statements in plugins
continue to work. Functions defined here (sync_cache, diff_issue, etc.)
are plugin-only -- core modules import from linear_shared directly.

Constants:

- Cache: .supervisor/cache/linear.json (atomic tmpfile + rename)
- sync_cache() diffs against cached version and emits linear.incoming
  events for field changes
"""

from typing import Any

# Re-export everything from linear_shared so plugin code that does
# ``from supervisor.linear import X`` keeps working unchanged.
from supervisor.linear_shared import (  # noqa: F401
    _API_TO_CACHE,
    _ISSUE_FIELDS,
    DEFAULT_TEAM,
    LINEAR_API,
    TEAM_IDS,
    LinearAPIError,
    _normalize_issue,
    _resolve_state_name,
    auto_advance_state,
    create_comment,
    create_issue,
    delete_comment,
    emit_linear_outgoing,
    fetch_comments,
    fetch_issue,
    fetch_issue_by_id,
    fetch_viewer,
    get_linked_issue,
    get_state_id,
    get_viewer_id,
    graphql,
    graphql_with_token,
    issue_file,
    load_cache,
    load_issue_link,
    load_token,
    load_workflows,
    patch_cache,
    save_cache,
    save_issue_link,
    update_issue,
)

# Fields that trigger linear.incoming events when changed.
DIFF_FIELDS = {
    "state",
    "priority",
    "assignee",
    "title",
    "project",
    "labels",
    "description",
    "dueDate",
    "estimate",
}


def diff_issue(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compare two issue dicts on DIFF_FIELDS. Returns {field: {from, to}}."""
    changes = {}
    for field in DIFF_FIELDS:
        old_val = old.get(field)
        new_val = new.get(field)
        if old_val != new_val:
            changes[field] = {"from": old_val, "to": new_val}
    return changes


def fetch_assigned_issues() -> dict[str, dict[str, Any]]:
    """Fetch all issues assigned to the authenticated user. Returns {identifier: issue_dict}."""
    viewer_id = get_viewer_id()
    query = (
        "query($userId: ID!) {"
        "  issues(filter: { assignee: { id: { eq: $userId } } }, first: 100) {"
        "    nodes {" + _ISSUE_FIELDS + "}"
        "  }"
        "}"
    )
    data = graphql(query, {"userId": viewer_id})
    nodes = data.get("issues", {}).get("nodes", [])
    result = {}
    for node in nodes:
        issue = _normalize_issue(node)
        team_key = issue["team"]
        number = issue["number"]
        identifier = f"{team_key}-{number}"
        result[identifier] = issue
    return result


def sync_cache(session_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all issues, diff against cache, emit events, update cache.

    Because outgoing mutations immediately patch the cache (via
    patch_cache), any diffs detected here are genuinely external
    (changed on Linear UI or by automations, not by v commands).

    Returns list of change summaries for display.
    """
    from supervisor.bus import Event, fire_sync

    old_cache = load_cache()
    fresh = fetch_assigned_issues()
    summaries = []

    for identifier, new_issue in fresh.items():
        old_issue = old_cache.get(identifier)
        if old_issue:
            changes = diff_issue(old_issue, new_issue)
            if changes:
                fire_sync(
                    Event(
                        name="linear.sync",
                        payload={
                            "session": session_id,
                            "repo": None,
                            "branch": None,
                            "data": {"issue": identifier, "changes": changes},
                        },
                        audit=True,
                    )
                )
                summaries.append(
                    {
                        "issue": identifier,
                        "changes": changes,
                    }
                )
        else:
            # New issue appeared (assigned to us).
            summaries.append({"issue": identifier, "changes": {"_new": True}})

    # Detect issues removed from assignment.
    summaries.extend(
        {"issue": identifier, "changes": {"_removed": True}} for identifier in old_cache if identifier not in fresh
    )

    # Update cache with fresh data.
    save_cache(fresh)
    return summaries
