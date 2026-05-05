"""v branch select: change active branch within a session.

With --machine, prints machine-readable key=value output instead of
human-readable text (replaces the old ``v activate`` command).
"""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

from codehome.activation import activate_branch
from codehome.aliases import resolve_alias
from codehome.resolution import parse_qualified
from codehome.utils import die


def _notify_server_switch(qualified_branch: str) -> None:
    """Best-effort notification to v server about a branch switch."""
    from codehome.serve import read_server_url

    server_url = read_server_url()
    if not server_url:
        return
    try:
        data = json.dumps({"branch": qualified_branch}).encode()
        req = urllib.request.Request(
            f"{server_url}/api/branch/switch",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # Server notification is best-effort.


def cmd_switch(args: argparse.Namespace) -> None:
    """Switch the active branch.

    Activation, Linear advancement, and server notification
    all proceed regardless of whether a session is active.

    With --machine, prints machine-readable key=value pairs to stdout
    (for scripts and AI agents) instead of human-readable output.
    """
    machine = getattr(args, "machine", False)

    name = getattr(args, "name", None)
    if not name:
        die("usage: v branch select <repo:branch>")

    repo, branch = parse_qualified(name)
    # Resolve alias early so the no-op check uses the canonical branch name.
    # activate_branch() will call resolve_alias again but it's a no-op on
    # an already-resolved name (no duplicate stderr output).
    branch = resolve_alias(repo, branch)

    result = activate_branch(repo, branch, commit_prefix="v branch select")

    # Notify the server about the branch switch.
    _notify_server_switch(f"{repo}:{branch}")

    if machine:
        # Machine-readable output for scripts and AI agents.
        print(f"worktree={result['worktree']}")
        print(f"context={result['context'] or ''}")
        print(f"linear={result['linear'] or ''}")
        return

    wt = result["worktree"]
    bd = Path(wt).parent
    print(f"Switched to '{repo}:{branch}'. Worktree: {wt}")
    print(f"cd {bd} && pwd")
    print("Read progress.md for task history.")

    # Print context.md so the agent sees branch context as command output.
    ctx_path = result["context"]
    if ctx_path:
        content = Path(ctx_path).read_text().strip()
        if content:
            print()
            print(content)
