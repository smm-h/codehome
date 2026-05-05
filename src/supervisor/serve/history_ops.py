"""Merge history operations for the server API.

Finds PR merge commits on staging/production that reference a branch.
Uses the same git-log approach as the CLI `v git history` command but
enriches results with PR title and author via `gh`.
"""

import json
import re
from pathlib import Path
from typing import Any

from supervisor.config import get_repo
from supervisor.paths import branch_dir, repo_anchor
from supervisor.serve.subprocess_utils import run_gh, run_git, safe_gh_repo


def _find_merges_on_target(
    branch: str,
    anchor: Path,
    target_branch: str,
    target_label: str,
) -> list[dict[str, Any]]:
    """Find PR merge commits on a target branch that reference the given branch.

    Returns list of dicts with pr_number, target, merged_at, merge_commit.
    """
    out, _, rc = run_git(anchor, "log", "--format=%H %aI %s", "--merges", target_branch)
    if rc != 0 or not out.strip():
        return []

    pattern = re.compile(rf"Merge pull request #(\d+) from \S+/{re.escape(branch)}\b")

    merges = []
    for line in out.strip().splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        sha, date, subject = parts
        m = pattern.search(subject)
        if not m:
            continue

        merges.append(
            {
                "pr_number": int(m.group(1)),
                "target": target_label,
                "merged_at": date,
                "merge_commit": sha[:10],
                # pr_title and author are filled in by _enrich_with_gh below.
                "pr_title": "",
                "author": "",
            },
        )

    return merges


def _enrich_with_gh(merges: list[dict[str, Any]], repo: str, *, gh_token: str | None = None) -> None:
    """Fetch PR title and author via `gh` for each merge event, in-place."""
    if not merges:
        return

    gh_r = safe_gh_repo(repo)
    if not gh_r:
        return

    pr_numbers = list({m["pr_number"] for m in merges})

    # Batch-fetch PR info. gh pr view accepts a single PR number, so
    # for small sets we call once per PR. For very large sets this could
    # be optimized, but merge history is typically small.
    pr_info: dict[int, dict[str, Any]] = {}
    for num in pr_numbers:
        stdout, _, rc = run_gh(
            "pr",
            "view",
            str(num),
            "--repo",
            gh_r,
            "--json",
            "title,author",
            timeout=15,
            gh_token=gh_token,
        )
        if rc == 0 and stdout:
            try:
                data = json.loads(stdout)
                pr_info[num] = data
            except json.JSONDecodeError:
                pass

    for m in merges:
        info = pr_info.get(m["pr_number"], {})
        m["pr_title"] = info.get("title", "")
        author = info.get("author", {})
        m["author"] = author.get("login", "") if isinstance(author, dict) else ""


def get_merge_history(repo: str, branch: str, *, gh_token: str | None = None) -> list[dict[str, Any]]:
    """Return merge history for a branch across staging and production.

    Returns a list of merge events sorted by date (most recent first).
    Empty list if no merges found. Raises ValueError if branch not found.
    """
    bd = branch_dir(repo, branch)
    if not bd.is_dir():
        msg = f"branch not found: {repo}:{branch}"
        raise ValueError(msg)

    cfg = get_repo(repo)
    anchor = repo_anchor(repo)

    merges: list[dict[str, Any]] = []

    # Search production (base_branch).
    merges.extend(_find_merges_on_target(branch, anchor, cfg.base_branch, "production"))

    # Search staging if configured.
    if cfg.staging_branch:
        merges.extend(_find_merges_on_target(branch, anchor, cfg.staging_branch, "staging"))

    # Sort by date, most recent first.
    merges.sort(key=lambda m: m["merged_at"], reverse=True)

    # Enrich with PR title and author from GitHub.
    _enrich_with_gh(merges, repo, gh_token=gh_token)

    return merges
