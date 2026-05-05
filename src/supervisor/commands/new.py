"""v branch create: create a new branch + worktree + Linear issue."""

import argparse

from supervisor.resolution import parse_qualified
from supervisor.utils import die


def cmd_new(args: argparse.Namespace) -> None:
    """Create a new branch with worktree and Linear issue.

    First arg is a qualified name (repo:branch). Does not auto-switch
    -- prints the qualified name for the user to `v branch select` into manually.
    """
    name = getattr(args, "name", None)
    desc = getattr(args, "description", None)
    if not name:
        die('branch name required: v branch create <repo:branch> "description"')
    if not desc:
        die('description required: v branch create <repo:branch> "description"')

    repo, branch = parse_qualified(name)

    from supervisor.commands.branch import BranchError, create_branch

    try:
        result = create_branch(
            branch,
            desc,
            repo=repo,
            no_issue=getattr(args, "no_issue", False),
            remote=getattr(args, "remote", False),
        )
    except BranchError as e:
        die(str(e))

    qualified = result["qualified"]
    if result.get("issue_id"):
        print(f"  issue: {result['issue_id']}")
    print(f"created: {qualified}")
    print(f"run: v branch select {qualified}")
