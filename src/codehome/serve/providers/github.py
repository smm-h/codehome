"""GitHub provider: commits, PRs, reviews, CI, profile, and stats.

Self-registers with the provider registry on import. Uses the gh CLI
for API calls, injecting the user's token via GH_TOKEN.
"""

from __future__ import annotations

import json
from typing import Any

from codehome.serve.logging_config import get_logger
from codehome.serve.providers.registry import register
from codehome.serve.subprocess_utils import run_gh, safe_gh_repo

logger = get_logger(component="provider.github")


class GitHubProvider:
    """GitHub integration provider."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def capabilities(self) -> set[str]:
        return {"commits", "prs", "reviews", "ci", "profile", "stats"}

    def test_connection(self, token: str) -> bool:
        """Validate the token by calling ``gh api user``."""
        stdout, stderr, rc = run_gh("api", "user", timeout=15, gh_token=token)
        if rc != 0:
            logger.warning("github_test_failed", error=stderr)
            return False
        try:
            data = json.loads(stdout)
            return "login" in data
        except (json.JSONDecodeError, TypeError):
            return False

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        """Return open PR info for a branch, if any."""
        owner_repo = safe_gh_repo(repo)
        if not owner_repo:
            return None

        stdout, _, rc = run_gh(
            "pr",
            "list",
            "--repo",
            owner_repo,
            "--head",
            branch,
            "--json",
            "number,title,state,url,reviewDecision,statusCheckRollup",
            "--limit",
            "1",
            timeout=15,
            gh_token=token,
        )
        if rc != 0 or not stdout.strip():
            return None

        try:
            prs = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return None

        if not prs:
            return None

        return prs[0]  # type: ignore[no-any-return]

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        """Delegate to collect_github_stats from team_stats.py.

        Looks up the team member by GitHub username and collects their
        PR/review stats.
        """
        from codehome.serve.roster import resolve
        from codehome.serve.team_stats import collect_github_stats

        member = resolve(github_username)
        if not member:
            return None

        return collect_github_stats(member, gh_token=token)


# Self-register when this module is imported.
register(GitHubProvider())
