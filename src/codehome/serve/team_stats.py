"""Team stats collector: per-person git statistics across all repos.

Collects commit counts, first/last commit dates, and per-repo breakdowns
for each team member by running git log in local worktrees. Optionally
collects GitHub PR/review stats via the gh CLI.

Usage::

    from codehome.serve.team_stats import collect_all_stats, save_stats

    stats = collect_all_stats()
    save_stats(stats)
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from codehome.paths import resolve_global, codehome_home
from codehome.serve.logging_config import get_logger
from codehome.serve.roster import TeamMember, all_members
from codehome.serve.subprocess_utils import run_gh, run_git, safe_gh_repo

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = get_logger(component="team_stats")

_STATS_CACHE_REL = "cache/team-stats.json"


def _find_worktree(repo: str) -> Path | None:
    """Find a usable worktree for a repo.

    Prefers the production/base branch worktree (the anchor), falls back
    to any existing worktree directory under the repo's branches/.
    """
    from codehome.supervisor.paths import repo_anchor, repo_branches

    anchor = repo_anchor(repo)
    if anchor.exists():
        return anchor

    branches_dir = repo_branches(repo)
    if not branches_dir.is_dir():
        return None

    for entry in sorted(branches_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        wt = entry / "worktree"
        if wt.is_dir():
            return wt

    return None


def _all_repo_names() -> list[str]:
    """Return names of all configured repos."""
    from codehome.supervisor.repo_config import load_repos as _load_repos

    return sorted(_load_repos().keys())


def collect_git_stats(member: TeamMember) -> dict[str, Any]:
    """Collect git stats for one team member across all their repos.

    Returns::

        {
            "total_commits": int,
            "first_commit": "2025-03-15" or None,
            "last_commit": "2026-04-03" or None,
            "repos": {"bag": 45, "chat": 12}
        }
    """
    target_repos = member.repos or _all_repo_names()

    all_first: list[str] = []
    all_last: list[str] = []
    repos_commits: dict[str, int] = {}
    total = 0

    for repo in target_repos:
        wt = _find_worktree(repo)
        if wt is None:
            continue

        # Count unique commits across all emails using commit hashes.
        commit_hashes: set[str] = set()
        for email in member.emails:
            stdout, _, rc = run_git(
                wt,
                "log",
                "--all",
                "--format=%H",
                f"--author={email}",
                timeout=60,
            )
            if rc == 0 and stdout.strip():
                commit_hashes.update(stdout.strip().splitlines())

        count = len(commit_hashes)
        if count > 0:
            repos_commits[repo] = count
            total += count

        # First and last commit dates across all emails for this repo.
        for email in member.emails:
            # First commit (oldest).
            stdout, _, rc = run_git(
                wt,
                "log",
                "--all",
                "--format=%aI",
                f"--author={email}",
                "--reverse",
                timeout=60,
            )
            if rc == 0 and stdout.strip():
                first_line = stdout.strip().splitlines()[0]
                # Extract date portion from ISO timestamp.
                date_str = first_line[:10]
                all_first.append(date_str)

            # Last commit (newest).
            stdout, _, rc = run_git(
                wt,
                "log",
                "--all",
                "--format=%aI",
                f"--author={email}",
                "-1",
                timeout=60,
            )
            if rc == 0 and stdout.strip():
                date_str = stdout.strip()[:10]
                all_last.append(date_str)

    first_commit = min(all_first) if all_first else None
    last_commit = max(all_last) if all_last else None

    return {
        "total_commits": total,
        "first_commit": first_commit,
        "last_commit": last_commit,
        "repos": repos_commits,
    }


def collect_github_stats(member: TeamMember, gh_token: str | None = None) -> dict[str, int] | None:
    """Collect GitHub stats for one team member.

    Returns None if no GitHub username or no token. Otherwise returns::

        {"open_prs": N, "merged_prs_30d": N, "reviews_30d": N}
    """
    if not member.github or not gh_token:
        return None

    gh_user = member.github[0]
    target_repos = member.repos or _all_repo_names()
    since_date = (datetime.now(tz=UTC) - timedelta(days=30)).strftime("%Y-%m-%d")

    open_prs = 0
    merged_prs = 0
    reviews = 0

    for repo in target_repos:
        owner_repo = safe_gh_repo(repo)
        if not owner_repo:
            continue

        # Open PRs authored by this user.
        stdout, _, rc = run_gh(
            "api",
            f"search/issues?q=author:{gh_user}+type:pr+is:open+repo:{owner_repo}",
            timeout=30,
            gh_token=gh_token,
        )
        if rc == 0:
            try:
                open_prs += json.loads(stdout).get("total_count", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        time.sleep(2)  # GitHub Search API rate limit: 30 requests/min.

        # Merged PRs in last 30 days.
        stdout, _, rc = run_gh(
            "api",
            f"search/issues?q=author:{gh_user}+type:pr+is:merged+merged:>={since_date}+repo:{owner_repo}",
            timeout=30,
            gh_token=gh_token,
        )
        if rc == 0:
            try:
                merged_prs += json.loads(stdout).get("total_count", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        time.sleep(2)

        # Reviews in last 30 days.
        stdout, _, rc = run_gh(
            "api",
            f"search/issues?q=reviewed-by:{gh_user}+type:pr+updated:>={since_date}+repo:{owner_repo}",
            timeout=30,
            gh_token=gh_token,
        )
        if rc == 0:
            try:
                reviews += json.loads(stdout).get("total_count", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        time.sleep(2)

    return {"open_prs": open_prs, "merged_prs_30d": merged_prs, "reviews_30d": reviews}


def collect_linear_stats(member: TeamMember, linear_token: str | None = None) -> dict[str, int] | None:
    """Collect Linear issue stats for one team member.

    Queries the Linear API for issues assigned to the member, grouped by
    team. Uses the member's email addresses to find their Linear user ID
    (via the viewer query if possible, or by searching assignees).

    Returns ``{"assigned_issues": N, "completed_30d": N}`` or None if no
    token or no data is available.
    """
    if not linear_token:
        return None

    from codehome.linear.linear_shared import LinearAPIError, graphql_with_token

    # Look up the Linear user ID for this member. First try the viewer
    # endpoint (works when the token belongs to this member), then fall
    # back to searching by email.
    viewer_id = _resolve_linear_user_id(member, linear_token)
    if not viewer_id:
        return None

    # Linear doesn't expose totalCount, so fetch nodes and count locally.
    count_query = """
    query($userId: ID!) {
      issues(filter: { assignee: { id: { eq: $userId } } }, first: 100) {
        nodes { id state { type } updatedAt }
      }
    }
    """
    try:
        data = graphql_with_token(count_query, linear_token, {"userId": viewer_id})
    except LinearAPIError:
        logger.warning("linear_stats_failed", member=member.handle)
        return None

    nodes = data.get("issues", {}).get("nodes", [])
    assigned_issues = len(nodes)

    # Count issues completed in the last 30 days.
    since = (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()
    completed_30d = 0
    for node in nodes:
        state = node.get("state") or {}
        if state.get("type") == "completed":
            updated = node.get("updatedAt", "")
            if updated >= since:
                completed_30d += 1

    return {"assigned_issues": assigned_issues, "completed_30d": completed_30d}


def _resolve_linear_user_id(member: TeamMember, token: str) -> str | None:
    """Find the Linear user ID for a team member.

    Strategy: query the Linear viewer (authenticated user). If the
    viewer's email matches one of the member's emails, use that ID.
    Otherwise, search Linear users by the member's emails.
    """
    from codehome.linear.linear_shared import LinearAPIError, graphql_with_token

    # Try viewer first (cheapest API call).
    try:
        viewer_data = graphql_with_token("{ viewer { id email } }", token)
        viewer = viewer_data.get("viewer") or {}
        viewer_email = (viewer.get("email") or "").lower()
        if viewer_email and viewer_email in [e.lower() for e in member.emails]:
            return viewer.get("id")
    except LinearAPIError:
        pass

    # Search by email in the organization's users.
    for email in member.emails:
        try:
            data = graphql_with_token(
                """
                query($email: String!) {
                  users(filter: { email: { eq: $email } }, first: 1) {
                    nodes { id }
                  }
                }
                """,
                token,
                {"email": email},
            )
            nodes = data.get("users", {}).get("nodes", [])
            if nodes:
                uid: str | None = nodes[0].get("id")
                return uid
        except LinearAPIError:
            continue

    return None


def collect_all_stats(
    *,
    on_progress: Callable[[str, str], None] | None = None,
    gh_token: str | None = None,
    linear_token: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Collect stats for all team members.

    Returns ``{handle: stats_dict}``. Each stats_dict contains git commit
    data and optionally ``"github"`` and/or ``"linear"`` keys with
    provider-specific counts when tokens are provided.

    Calls ``on_progress(handle, name)`` before processing each member if
    provided.
    """
    members = all_members()
    result: dict[str, dict[str, Any]] = {}

    for member in members:
        if on_progress:
            on_progress(member.handle, member.name)
        stats = collect_git_stats(member)

        if gh_token:
            gh_stats = collect_github_stats(member, gh_token=gh_token)
            if gh_stats is not None:
                stats["github"] = gh_stats

        if linear_token:
            lin_stats = collect_linear_stats(member, linear_token=linear_token)
            if lin_stats is not None:
                stats["linear"] = lin_stats

        result[member.handle] = stats

    return result


def save_stats(stats: dict[str, dict[str, Any]]) -> None:
    """Save stats to ~/.codehome/cache/team-stats.json."""
    payload = {
        "_refreshed_at": datetime.now(tz=UTC).isoformat(),
        **stats,
    }
    dest = codehome_home() / _STATS_CACHE_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n")


def load_cached_stats() -> dict[str, dict[str, Any]] | None:
    """Load stats from cache (prefers ~/.codehome/, falls back to .supervisor/)."""
    cache_path = resolve_global(_STATS_CACHE_REL)
    if not cache_path.is_file():
        return None
    try:
        result: dict[str, dict[str, Any]] = json.loads(cache_path.read_text())
        return result
    except (json.JSONDecodeError, OSError):
        return None
