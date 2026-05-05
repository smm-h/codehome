"""Remote branch listing with PR enrichment and ahead/behind counts.

Fetches remote branches from all configured repos, parses git metadata,
and optionally enriches with GitHub PR data. All git/subprocess calls
are blocking and should be wrapped in asyncio.to_thread() by callers.
"""

import json
import time
from typing import Any

from supervisor.config import load_repos
from supervisor.git import git
from supervisor.paths import repo_anchor, worktree_path
from supervisor.serve.logging_config import get_logger
from supervisor.serve.roster import resolve
from supervisor.serve.subprocess_utils import run_gh, safe_gh_repo

log = get_logger(component="remote_branches")

# -- Module-level caches (simple dicts, no external dependencies) -----------

# {repo: (timestamp, pr_list)} -- PR data cache per repo
_pr_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_PR_CACHE_TTL_SECS = 300

# Branches to exclude from listing and notifications (infrastructure refs).
# Also imported by fetch_scheduler.py to avoid duplication.
EXCLUDE_BRANCHES = frozenset({"HEAD", "production", "staging", "demo", "main"})


def _get_pr_list(repo: str) -> list[dict[str, Any]]:
    """Fetch PRs (all states) for a repo from GitHub, with caching."""
    now = time.monotonic()
    cached = _pr_cache.get(repo)
    if cached:
        ts, data = cached
        if now - ts < _PR_CACHE_TTL_SECS:
            return data

    owner_repo = safe_gh_repo(repo)
    if not owner_repo:
        return cached[1] if cached else []

    stdout, _, rc = run_gh(
        "pr",
        "list",
        "--repo",
        owner_repo,
        "--state",
        "all",
        "--json",
        "headRefName,number,url,state",
        "--limit",
        "100",
    )
    if rc != 0 or not stdout.strip():
        return cached[1] if cached else []

    try:
        prs: list[dict[str, Any]] = json.loads(stdout)
    except json.JSONDecodeError:
        return cached[1] if cached else []

    _pr_cache[repo] = (now, prs)
    return prs


def list_remote_branches(repo_filter: str | None = None) -> list[dict[str, Any]]:
    """List remote branches across all repos (or a single repo).

    Returns a list of dicts matching the RemoteBranch response shape.
    Runs blocking git and gh subprocess calls.
    """
    repos = load_repos()
    if repo_filter:
        if repo_filter not in repos:
            return []
        repos = {repo_filter: repos[repo_filter]}

    results: list[dict[str, Any]] = []

    for repo_name, repo_cfg in repos.items():
        anchor = repo_anchor(repo_name)
        if not anchor.exists():
            continue

        # Determine the production ref for ahead/behind computation.
        prod = f"origin/{repo_cfg.base_branch}"

        # List all remote branches with metadata including ahead/behind.
        # Tab-separated fields: ref, hash, date, name, email, subject, ahead-behind.
        # %(ahead-behind:REF) outputs "ahead behind" (space-separated) where
        # ahead = commits on this branch not on REF, behind = commits on REF
        # not on this branch.
        fmt = (
            "%(refname:short)%09%(objectname:short)%09%(committerdate:iso)"
            "%09%(committername)%09%(committeremail:trim)%09%(subject)"
            f"%09%(ahead-behind:{prod})"
        )
        output = git(
            anchor,
            "for-each-ref",
            f"--format={fmt}",
            "refs/remotes/origin/",
            "--sort=-committerdate",
            check=False,
        )
        if not output.strip():
            continue

        # Fetch PR data for enrichment (cached).
        prs = _get_pr_list(repo_name)
        pr_by_branch: dict[str, dict[str, Any]] = {}
        for pr in prs:
            head = pr.get("headRefName", "")
            if head:
                pr_by_branch[head] = pr

        for line in output.splitlines():
            parts = line.split("\t", 6)
            if len(parts) < 7:
                continue

            ref_short, commit_hash, commit_date, committer_name, committer_email, subject, ab_str = parts

            # Strip the "origin/" prefix to get the bare branch name.
            if ref_short.startswith("origin/"):
                branch_name = ref_short[len("origin/") :]
            else:
                branch_name = ref_short

            # Skip infrastructure branches.
            if branch_name in EXCLUDE_BRANCHES:
                continue

            # Parse ahead/behind from the for-each-ref output.
            # Format: "ahead behind" (space-separated integers).
            ab_parts = ab_str.strip().split()
            if len(ab_parts) == 2:
                try:
                    ahead = int(ab_parts[0])
                    behind = int(ab_parts[1])
                except ValueError:
                    ahead, behind = 0, 0
            else:
                ahead, behind = 0, 0

            # PR enrichment.
            pr_data = pr_by_branch.get(branch_name)
            pr_number = pr_data["number"] if pr_data else None
            pr_url = pr_data["url"] if pr_data else None
            pr_state = pr_data["state"] if pr_data else None

            # Resolve committer to a team member for display name.
            member = resolve(committer_email) or resolve(committer_name)
            display_name = member.name if member else committer_name
            handle = member.handle if member else None

            # Check if a local worktree exists.
            wt = worktree_path(repo_name, branch_name)
            has_local = wt.exists()

            results.append(
                {
                    "branch": branch_name,
                    "repo": repo_name,
                    "commit_hash": commit_hash,
                    "committer_name": committer_name,
                    "committer_email": committer_email,
                    "display_name": display_name,
                    "handle": handle,
                    "commit_date": commit_date.strip(),
                    "commit_subject": subject,
                    "ahead": ahead,
                    "behind": behind,
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "pr_state": pr_state,
                    "has_local_worktree": has_local,
                }
            )

    return results
