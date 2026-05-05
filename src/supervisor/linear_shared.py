"""Shared Linear utilities needed by core modules.

Extracted from supervisor.linear so that core never imports the linear
plugin directly. The linear plugin (plugins/linear/) imports from here
via supervisor.linear, which re-exports everything.

Contains: token/HTTP helpers, cache management, GraphQL queries,
mutations, branch-to-issue linking, and outgoing event helpers.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from supervisor.paths import (
    ENV_FILE,
    branch_dir,
    resolve_global,
    superv_home,
)
from supervisor.session import get_process_id
from supervisor.utils import atomic_json_write, load_json

LINEAR_API = "https://api.linear.app/graphql"
DEFAULT_TEAM = "VELENT"
TEAM_IDS = {
    "PDC": "dfc5a1ef-7cd0-4df6-b410-cfefddd0652c",
    "VELENT": "15b29fc6-ab57-4cd8-a8ed-4922c7e4f402",
    "VELPD": "0518bdb9-a5ad-41b7-af89-4d957145e212",
}


class LinearAPIError(Exception):
    """Raised by graphql_with_token on HTTP or GraphQL errors."""


# ---------------------------------------------------------------------------
# Token + HTTP
# ---------------------------------------------------------------------------


def load_token() -> str:
    token = os.environ.get("LINEAR_API_KEY")
    if token:
        return token
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text().splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("LINEAR_API_KEY=") and not stripped.startswith("#"):
                val = stripped.split("=", 1)[1].strip()
                if val:
                    return val
    sys.stderr.write("error: LINEAR_API_KEY not set (check super/.env or env var)\n")
    sys.exit(1)


def graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST a GraphQL query to Linear. Returns the `data` dict.

    Uses the machine-wide token from LINEAR_API_KEY / .env. For per-user
    tokens, use ``graphql_with_token`` instead.
    """
    token = load_token()
    return graphql_with_token(query, token, variables)


def graphql_with_token(query: str, token: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST a GraphQL query using an explicit token. Returns the `data` dict.

    Raises ``LinearAPIError`` on HTTP or GraphQL errors instead of calling
    sys.exit, making it safe for use in long-running server processes.
    """
    body: dict[str, Any] = {"query": query}
    if variables:
        body["variables"] = variables
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        LINEAR_API,
        data=data,
        method="POST",
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        msg = f"Linear API -> {e.code}: {err_body[:500]}"
        raise LinearAPIError(msg) from e
    except urllib.error.URLError as e:
        raise LinearAPIError(f"Linear API network error: {e.reason}", 0) from e
    if "errors" in result:
        msg = f"Linear GraphQL: {result['errors'][0]['message']}"
        raise LinearAPIError(msg)
    return dict(result.get("data", {}))


def fetch_viewer(token: str) -> dict[str, Any] | None:
    """Query the authenticated user via ``{ viewer { id name email } }``.

    Returns the viewer dict or None on failure. Uses the provided token
    directly (no env var fallback).
    """
    try:
        data = graphql_with_token("{ viewer { id name email } }", token)
        return data.get("viewer")
    except LinearAPIError:
        return None


# Module-level cache so we only hit the viewer API once per process.
_cached_viewer_id: str | None = None


def get_viewer_id(token: str | None = None) -> str:
    """Return the authenticated user's Linear ID, cached per-process.

    Resolves the viewer via the Linear API on first call, then caches
    the result for subsequent calls. Falls back to ``load_token()`` if
    no explicit token is provided.

    Raises ``LinearAPIError`` if the viewer query fails (e.g. bad token).
    """
    global _cached_viewer_id
    if _cached_viewer_id is not None:
        return _cached_viewer_id

    if not token:
        token = load_token()

    viewer = fetch_viewer(token)
    if not viewer or not viewer.get("id"):
        raise LinearAPIError("Failed to resolve Linear viewer ID -- check that LINEAR_API_KEY is valid")

    _cached_viewer_id = viewer["id"]
    return _cached_viewer_id


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def load_cache() -> dict[str, Any]:
    result: dict[str, Any] = load_json(resolve_global("cache/linear.json"), {})
    return result


def save_cache(cache: dict[str, Any]) -> None:
    """Atomic write to ~/.superv/cache/linear.json."""
    dest = superv_home() / "cache" / "linear.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(dest, cache)


def load_workflows() -> dict[str, Any]:
    result: dict[str, Any] = load_json(resolve_global("cache/workflows.json"), {})
    return result


def get_state_id(team: str, state_name: str) -> str | None:
    """Look up a workflow state ID from the workflows cache."""
    workflows = load_workflows()
    team_data = workflows.get(team, {})
    states = team_data.get("states", {})
    state = states.get(state_name, {})
    result: str | None = state.get("id")
    return result


# ---------------------------------------------------------------------------
# API queries
# ---------------------------------------------------------------------------

_ISSUE_FIELDS = """
    id title number
    state { name type }
    priority priorityLabel
    team { id key name }
    project { id name }
    assignee { id name }
    labels { nodes { name } }
    branchName description dueDate estimate
    url createdAt updatedAt
"""


def _normalize_issue(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize a GraphQL issue node to the flat cache schema."""
    state = node.get("state") or {}
    team = node.get("team") or {}
    project = node.get("project") or {}
    assignee = node.get("assignee") or {}
    labels_nodes = (node.get("labels") or {}).get("nodes", [])
    return {
        "id": node["id"],
        "title": node.get("title", ""),
        "number": node.get("number"),
        "state": state.get("name", ""),
        "stateType": state.get("type", ""),
        "stateId": "",  # not returned by this query shape
        "priority": node.get("priority", 0),
        "priorityLabel": node.get("priorityLabel", ""),
        "team": team.get("key", ""),
        "teamId": team.get("id", ""),
        "teamName": team.get("name", ""),
        "project": project.get("name"),
        "projectId": project.get("id"),
        "assignee": assignee.get("name", ""),
        "assigneeId": assignee.get("id", ""),
        "labels": [label["name"] for label in labels_nodes],
        "branchName": node.get("branchName", ""),
        "description": node.get("description", ""),
        "dueDate": node.get("dueDate"),
        "estimate": node.get("estimate"),
        "url": node.get("url", ""),
        "createdAt": node.get("createdAt", ""),
        "updatedAt": node.get("updatedAt", ""),
        "lastSyncedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def fetch_issue(identifier: str) -> dict[str, Any] | None:
    """Fetch a single issue by identifier (e.g. VELENT-455)."""
    parts = identifier.split("-", 1)
    if len(parts) != 2:
        return None
    team_key, number_str = parts
    try:
        number = int(number_str)
    except ValueError:
        return None
    # Linear filter uses String for team key, Float for issue number.
    query = (
        "query($teamKey: String!, $number: Float!) {"
        "  issues(filter: {"
        "    team: { key: { eq: $teamKey } }"
        "    number: { eq: $number }"
        "  }, first: 1) {"
        "    nodes {" + _ISSUE_FIELDS + "}"
        "  }"
        "}"
    )
    data = graphql(query, {"teamKey": team_key, "number": number})
    nodes = data.get("issues", {}).get("nodes", [])
    if not nodes:
        return None
    return _normalize_issue(nodes[0])


def fetch_issue_by_id(issue_id: str) -> dict[str, Any] | None:
    """Fetch a single issue by its UUID."""
    query = "query($id: String!) { issue(id: $id) {" + _ISSUE_FIELDS + "} }"
    data = graphql(query, {"id": issue_id})
    node = data.get("issue")
    if not node:
        return None
    return _normalize_issue(node)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _resolve_state_name(state_id: str) -> str | None:
    """Reverse-lookup a state UUID to its name via the workflows cache."""
    for team_data in load_workflows().values():
        for name, info in team_data.get("states", {}).items():
            if info.get("id") == state_id:
                return str(name)
    return None


# Map Linear API input fields to their cache field names.
# Fields where the API value can be stored directly in the cache.
# stateId, assigneeId, projectId, labelIds need ID->name resolution
# and are handled specially in update_issue.
_API_TO_CACHE = {
    "title": "title",
    "description": "description",
    "priority": "priority",
    "dueDate": "dueDate",
    "estimate": "estimate",
}


def update_issue(identifier: str, issue_id: str, **kwargs: Any) -> bool:
    """Issue mutation + automatic cache patch.

    identifier is required so the cache is always updated on success.
    kwargs are Linear IssueUpdateInput fields (e.g. stateId, description).
    """
    query = """
    mutation($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) { success }
    }
    """
    data = graphql(query, {"id": issue_id, "input": kwargs})
    success: bool = data.get("issueUpdate", {}).get("success", False)
    if success:
        # Build cache patch from the API fields we just set.
        cache_fields = {}
        for api_key, cache_key in _API_TO_CACHE.items():
            if api_key in kwargs:
                cache_fields[cache_key] = kwargs[api_key]
        if "stateId" in kwargs:
            name = _resolve_state_name(kwargs["stateId"])
            if name:
                cache_fields["state"] = name
        if cache_fields:
            patch_cache(identifier, **cache_fields)
    return success


def create_comment(identifier: str, issue_id: str, body: str) -> bool:
    """CommentCreate mutation."""
    query = """
    mutation($input: CommentCreateInput!) {
      commentCreate(input: $input) { success }
    }
    """
    data = graphql(query, {"input": {"issueId": issue_id, "body": body}})
    result: bool = data.get("commentCreate", {}).get("success", False)
    return result


def delete_comment(identifier: str, comment_id: str) -> bool:
    """CommentDelete mutation."""
    query = """
    mutation($id: String!) {
      commentDelete(id: $id) { success }
    }
    """
    data = graphql(query, {"id": comment_id})
    result: bool = data.get("commentDelete", {}).get("success", False)
    return result


def fetch_comments(issue_id: str) -> list[dict[str, Any]]:
    """Fetch all comments on an issue. Returns [{id, body, createdAt, user}]."""
    query = """
    query($id: String!) {
      issue(id: $id) {
        comments(orderBy: createdAt) {
          nodes { id body createdAt user { name } }
        }
      }
    }
    """
    data = graphql(query, {"id": issue_id})
    nodes = data.get("issue", {}).get("comments", {}).get("nodes", [])
    return [
        {
            "id": n["id"],
            "body": n["body"],
            "createdAt": n["createdAt"],
            "user": (n.get("user") or {}).get("name", "?"),
        }
        for n in nodes
    ]


def create_issue(title: str, team: str = DEFAULT_TEAM, description: str | None = None) -> dict[str, Any] | None:
    """Create a Linear issue, add it to the cache, return link dict.

    Returns {identifier, id, team} or None. Fetches the full issue
    after creation so the cache has all fields and auto_advance_state
    works immediately.
    """
    team_id = TEAM_IDS.get(team)
    if not team_id:
        sys.stderr.write(f"unknown team: {team}\n")
        return None
    query = """
    mutation($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id number team { key } }
      }
    }
    """
    inp = {"title": title, "teamId": team_id, "assigneeId": get_viewer_id()}
    if description:
        inp["description"] = description
    data = graphql(query, {"input": inp})
    result = data.get("issueCreate", {})
    if not result.get("success"):
        return None
    issue = result["issue"]
    team_key = issue["team"]["key"]
    identifier = f"{team_key}-{issue['number']}"

    # Fetch the full issue and add it to the cache so that
    # auto_advance_state and update_issue work immediately.
    try:
        full_issue = fetch_issue(identifier)
        if full_issue:
            cache = load_cache()
            cache[identifier] = full_issue
            save_cache(cache)
    except Exception:
        pass  # cache miss is non-fatal; `v linear sync` will pick it up

    return {
        "identifier": identifier,
        "id": issue["id"],
        "team": team_key,
    }


def patch_cache(identifier: str, **fields: Any) -> None:
    """Update specific fields of a cached issue after a successful mutation.

    Called after outgoing API calls so that `v linear sync` sees no diff for
    changes we caused. Only genuinely external changes produce
    linear.incoming events.
    """
    cache = load_cache()
    if identifier not in cache:
        return
    cache[identifier].update(fields)
    cache[identifier]["lastSyncedAt"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_cache(cache)


def emit_linear_outgoing(
    session_id: str | None, repo: str, branch: str, identifier: str, action: str, payload: dict[str, Any], success: bool
) -> None:
    """Emit a linear.mutation event for an outgoing mutation."""
    from supervisor.bus import Event, fire_sync

    fire_sync(
        Event(
            name="linear.mutation",
            payload={
                "session": session_id,
                "repo": repo,
                "branch": branch,
                "data": {"action": action, "issue": identifier, "payload": payload, "success": success},
            },
            audit=True,
        )
    )


def auto_advance_state(repo: str, branch: str, state_name: str, session_id: str | None = None) -> None:
    """Set a branch's Linear issue to the given state. Graceful degradation.

    Skips if: no issue linked, already at/past target state (for
    "In Progress" only), or API fails. Patches the local cache so
    `v linear sync` won't re-report this as an external change.
    """
    if session_id is None:
        session_id = get_process_id()

    try:
        link, issue = get_linked_issue(repo, branch)
        if not link or not issue:
            return

        # Don't regress: skip if already started or completed.
        if state_name == "In Progress" and issue.get("stateType") in (
            "started",
            "completed",
        ):
            return

        state_id = get_state_id(link["team"], state_name)
        if not state_id:
            return

        success = update_issue(link["identifier"], issue["id"], stateId=state_id)
        emit_linear_outgoing(session_id, repo, branch, link["identifier"], "set_state", {"stateId": state_id}, success)
        if success:
            sys.stderr.write(f"  {link['identifier']}: set to {state_name}\n")
    except Exception:
        pass  # graceful degradation


# ---------------------------------------------------------------------------
# Branch-to-issue linking (issue.json in branch dir)
# ---------------------------------------------------------------------------


def issue_file(repo: str, branch: str) -> Path:
    return branch_dir(repo, branch) / "issue.json"


def load_issue_link(repo: str, branch: str) -> dict[str, Any] | None:
    """Read issue.json for a branch. Returns None if missing."""
    result: dict[str, Any] | None = load_json(issue_file(repo, branch))
    return result


def get_linked_issue(repo: str, branch: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load the issue link and its cached data for a branch.

    Returns (link, issue) where either may be None.
    """
    link = load_issue_link(repo, branch)
    if not link:
        return None, None
    cache = load_cache()
    issue = cache.get(link["identifier"])
    return link, issue


def save_issue_link(repo: str, branch: str, identifier: str, issue_id: str, team: str) -> None:
    """Write issue.json for a branch."""
    path = issue_file(repo, branch)
    atomic_json_write(
        path,
        {
            "identifier": identifier,
            "id": issue_id,
            "team": team,
        },
    )
