"""Linear provider: issue tracking integration.

Self-registers with the provider registry on import. Uses the Linear
GraphQL API to validate tokens and enrich branch/user data.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from supervisor.serve.logging_config import get_logger
from supervisor.serve.providers.registry import register

logger = get_logger(component="provider.linear")

_LINEAR_API = "https://api.linear.app/graphql"


class LinearProvider:
    """Linear issue-tracking provider."""

    @property
    def name(self) -> str:
        return "linear"

    @property
    def capabilities(self) -> set[str]:
        return {"issues", "stats"}

    def test_connection(self, token: str) -> bool:
        """Validate the token by querying the Linear viewer."""
        query = '{"query": "{ viewer { id name } }"}'
        req = urllib.request.Request(
            _LINEAR_API,
            data=query.encode(),
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return "data" in data and "viewer" in data["data"]
        except Exception:
            logger.warning("linear_test_failed")
            return False

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        """Look up the linked Linear issue for a branch.

        Reads issue.json from the branch dir, then fetches current issue
        status from the Linear API using the per-user token. Returns
        enriched data or None if no issue is linked.
        """
        from supervisor.linear_shared import (
            _ISSUE_FIELDS,
            LinearAPIError,
            _normalize_issue,
            graphql_with_token,
            load_issue_link,
        )

        link = load_issue_link(repo, branch)
        if not link:
            return None

        identifier = link.get("identifier", "")
        parts = identifier.split("-", 1)
        if len(parts) != 2:
            return None

        team_key, number_str = parts
        try:
            number = int(number_str)
        except ValueError:
            return None

        # Reuse the canonical field list from the Linear client.
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
        try:
            data = graphql_with_token(query, token, {"teamKey": team_key, "number": number})
        except LinearAPIError:
            logger.warning("linear_enrich_failed", identifier=identifier)
            return None

        nodes = data.get("issues", {}).get("nodes", [])
        if not nodes:
            return None

        issue = _normalize_issue(nodes[0])
        # Include the link identifier for convenience.
        issue["identifier"] = identifier
        return issue

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        """Collect Linear issue stats for a team member.

        Resolves the member by github_username via the roster, then
        delegates to collect_linear_stats in team_stats.py.
        """
        from supervisor.serve.roster import resolve
        from supervisor.serve.team_stats import collect_linear_stats

        member = resolve(github_username)
        if not member:
            return None

        return collect_linear_stats(member, linear_token=token)


# Self-register when this module is imported.
register(LinearProvider())
