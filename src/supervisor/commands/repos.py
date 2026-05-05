"""v repos: list configured repositories."""

import argparse

from supervisor.config import list_repos
from supervisor.paths import repo_anchor
from supervisor.utils import render_box_table


def cmd_repos(args: argparse.Namespace) -> None:
    """List all configured repos with branch counts.

    Shows repo name, remote URL, base branch, and number of active
    branches (worktrees) for each repo.
    """
    repos = list_repos()
    if not repos:
        print("no repos configured")
        print("  add repos to .supervisor/repos.toml")
        return

    rows = []
    for cfg in repos:
        anchor = repo_anchor(cfg.name)
        if anchor.exists():
            from supervisor.git import list_worktrees
            from supervisor.paths import PROTECTED_BRANCHES

            names = [n for n in list_worktrees(cfg.name) if n not in PROTECTED_BRANCHES]
            branch_count = str(len(names))
        else:
            branch_count = "(not cloned)"
        rows.append(
            [
                cfg.name,
                cfg.remote,
                cfg.base_branch,
                branch_count,
            ]
        )

    print(
        render_box_table(
            ["Repo", "Remote", "Base", "Branches"],
            rows,
            aligns=["l", "l", "l", "r"],
        )
    )
