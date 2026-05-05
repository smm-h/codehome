"""v branch status: dashboard showing all active branches with state."""

import argparse
import json
import urllib.error
import urllib.request

from codehome.git import commits_behind, git, iter_all_branches
from codehome.linear_shared import load_cache, load_issue_link
from codehome.paths import PROTECTED_BRANCHES, worktree_path
from codehome.resolution import active_context


def _print_server_status() -> None:
    """If v server is running, fetch and print a service summary table."""
    from codehome.serve import read_server_url
    from codehome.utils import dim, render_box_table

    server_url = read_server_url()
    if not server_url:
        return

    try:
        req = urllib.request.Request(f"{server_url}/api/services", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            services = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        print(f"v server: {server_url} (could not fetch services)")
        print()
        return

    if not services:
        print(f"v server: {server_url} (no services registered)")
        print()
        return

    headers = ["Service", "State", "Port", "Branch"]
    rows = [
        [
            svc.get("name", svc.get("key", "?")),
            svc.get("state", "?"),
            str(svc.get("port", "")) if svc.get("port") else dim("-"),
            svc.get("branch", "?"),
        ]
        for svc in services
    ]

    print(f"v server: {server_url}")
    print(render_box_table(headers, rows, aligns=["l", "l", "r", "l"]))
    print()


def cmd_status(args: argparse.Namespace) -> None:
    """Show a status dashboard for all active branches.

    For each non-protected worktree: Linear state, commits behind
    production, uncommitted file count, and whether it's the current
    stack top. If v server is running, also shows a server service table.
    """
    # Show server status at the top if available.
    _print_server_status()

    # Gather (repo, name) pairs from all repos.
    branch_pairs = [(r, n) for r, n in iter_all_branches() if n not in PROTECTED_BRANCHES]
    if not branch_pairs:
        print("no active branches")
        return

    cache = load_cache()
    ctx = active_context()
    current_qualified = ctx.qualified if ctx else ""

    rows = []
    for repo, name in sorted(branch_pairs, key=lambda p: p[1]):
        qualified = f"{repo}:{name}"

        # Linear state.
        link = load_issue_link(repo, name)
        linear_state = ""
        identifier = ""
        if link:
            identifier = link["identifier"]
            issue = cache.get(identifier)
            if issue:
                linear_state = issue.get("state", "?")

        # Staleness.
        try:
            behind = commits_behind(name, repo)
        except Exception:
            behind = 0

        # Dirty files.
        wt = worktree_path(repo, name)
        try:
            dirty_output = git(wt, "status", "--porcelain", check=False)
            dirty_count = len([line for line in dirty_output.splitlines() if line.strip()])
        except Exception:
            dirty_count = 0

        marker = "*" if qualified == current_qualified else " "
        rows.append((marker, qualified, identifier, linear_state, behind, dirty_count))

    # Render table.
    from codehome.utils import render_box_table

    headers = ["", "Branch", "Issue", "State", "Behind", "Dirty"]
    str_rows = [[str(c) for c in r] for r in rows]
    print(render_box_table(headers, str_rows, aligns=["l", "l", "l", "l", "r", "r"]))
