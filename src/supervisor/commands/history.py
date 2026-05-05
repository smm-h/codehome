"""Branch merge history: v history."""

import argparse
import re
from pathlib import Path
from typing import Any

from supervisor.git import git
from supervisor.paths import repo_anchor
from supervisor.resolution import resolve
from supervisor.utils import bold, dim, green


def _find_pr_merges(branch: str, anchor: Path, base_branch: str = "production") -> list[dict[str, Any]]:
    """Find all PR merge commits on production that reference a branch.

    Returns list of {sha, date, pr_number, branch_commits} dicts,
    most recent first.
    """
    # Search production history for merge commits mentioning this branch.
    log = git(anchor, "log", "--format=%H %aI %s", "--merges", base_branch)
    pattern = re.compile(rf"Merge pull request #(\d+) from \S+/{re.escape(branch)}\b")

    merges = []
    for line in log.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        sha, date, subject = parts
        m = pattern.search(subject)
        if not m:
            continue

        pr_number = int(m.group(1))

        # Get commits from the branch side of the merge (second parent).
        # sha^2 = branch tip, sha^1 = production before merge.
        commits_out = git(
            anchor,
            "log",
            "--oneline",
            "--no-merges",
            f"{sha}^2",
            "--not",
            f"{sha}^1",
            check=False,
        )
        branch_commits = []
        for cline in commits_out.splitlines():
            if cline.strip():
                c_sha, c_msg = cline.split(" ", 1)
                branch_commits.append({"sha": c_sha, "message": c_msg})

        merges.append(
            {
                "sha": sha[:10],
                "date": date[:10],
                "pr": pr_number,
                "commits": branch_commits,
            }
        )

    return merges


def cmd_history(args: argparse.Namespace) -> None:
    """Show merge history for a branch on production.

    Searches production log for PR merge commits referencing the
    branch. Shows PR number, date, and branch-side commits.
    """
    ctx = resolve(getattr(args, "branch", None))
    anchor = repo_anchor(ctx.repo)
    merges = _find_pr_merges(ctx.branch, anchor, ctx.config.base_branch)

    if not merges:
        print(f"no PR merges found for '{ctx.branch}' on production")
        return

    total_commits = sum(len(m["commits"]) for m in merges)
    print(bold(f"# {ctx.branch}"))
    print(f"  {len(merges)} PR merge(s), {total_commits} commit(s) total")
    print()

    for m in merges:
        header = f"PR #{m['pr']}  {m['date']}  {dim(m['sha'])}"
        print(green(header))
        if m["commits"]:
            for c in m["commits"]:
                print(f"  {dim(c['sha'])} {c['message']}")
        else:
            print(f"  {dim('(no non-merge commits)')}")
        print()
