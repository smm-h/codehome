"""MCP server for per-role agent tool provisioning.

Spawned as a subprocess by claude -p via --mcp-config. Reads role and
context from environment variables, then exposes only the tools that
role is allowed to use.

Environment variables:
    SA_ROLE          -- agent role (implementor, auditor, reviewer, deployer)
    SA_TASK_ID       -- unique task identifier
    SA_SESSION_ID    -- parent session identifier
    SA_WORKTREE      -- absolute path to the git worktree
    SA_PROJECT_ROOT  -- absolute path to the project root
    SA_SERVER_URL    -- codehome server base URL (e.g. http://127.0.0.1:9100)
    SA_AUTH_TOKEN    -- bearer token for server API calls
"""

import os
import sys
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from wesktop.mcp import DEFAULT_ROLE, ROLES
from wesktop.mcp_tools import ask_user as ask_user_mod
from wesktop.mcp_tools import deployment, filesystem, git, review, testing

# -- Read environment -----------------------------------------------------------

role = os.environ.get("SA_ROLE", DEFAULT_ROLE)
worktree = os.environ.get("SA_WORKTREE", "")
server_url = os.environ.get("SA_SERVER_URL", "http://127.0.0.1:9100")
auth_token = os.environ.get("SA_AUTH_TOKEN", "")
task_id = os.environ.get("SA_TASK_ID", "")
session_id = os.environ.get("SA_SESSION_ID", "")
project_root = os.environ.get("SA_PROJECT_ROOT", "")

# Resolve qualified branch name from task_id (format: repo:branch).
qualified_branch = task_id  # task_id doubles as the qualified branch name

# -- Build the server -----------------------------------------------------------

app = FastMCP("codehome-agent")

# -- Tool wrappers --------------------------------------------------------------
# Each wrapper closes over the environment variables and delegates to the
# implementation module. We define all possible tools here, then register
# only those allowed for the current role.


# Filesystem tools


def _read_file(path: str) -> str:
    """Read a file from the worktree. Path is relative to worktree root."""
    return filesystem.read_file(worktree, path)


def _write_file(path: str, content: str) -> str:
    """Write content to a file in the worktree. Creates parent dirs as needed."""
    return filesystem.write_file(worktree, path, content)


def _edit_file(path: str, old_text: str, new_text: str) -> str:
    """Find and replace text in a file. Replaces the first occurrence."""
    return filesystem.edit_file(worktree, path, old_text, new_text)


def _list_files(path: str = "") -> str:
    """List directory contents. Path is relative to worktree root (empty = root)."""
    return filesystem.list_files(worktree, path)


def _search_files(pattern: str, path: str = "") -> str:
    """Search file contents using ripgrep. Returns matching lines."""
    return filesystem.search_files(worktree, pattern, path)


# Git tools


def _git_status() -> str:
    """Show working tree status (porcelain format)."""
    return git.git_status(worktree)


def _git_diff(path: str = "") -> str:
    """Show unstaged changes, optionally for a specific file."""
    return git.git_diff(worktree, path)


def _git_commit(message: str) -> str:
    """Stage all changes and commit with the given message."""
    return git.git_commit(worktree, message)


def _git_log(count: int = 20) -> str:
    """Show recent commit log (oneline format)."""
    return git.git_log(worktree, count)


# Testing tools


def _run_tests(suite: str = "", pattern: str = "") -> str:
    """Run tests via the codehome server."""
    return testing.run_tests(server_url, auth_token, suite, pattern)


# Deployment tools


def _stage_branch(message: str) -> str:
    """Trigger staging merge for the current branch."""
    return deployment.stage_branch(server_url, auth_token, qualified_branch, message)


def _create_prod_pr(message: str) -> str:
    """Create a production PR for the current branch."""
    return deployment.create_prod_pr(server_url, auth_token, qualified_branch, message)


def _check_pipeline() -> str:
    """Check CI/CD pipeline status for the current branch."""
    return deployment.check_pipeline(server_url, auth_token, qualified_branch)


# Review tools


def _post_review_comment(file: str, line: int, body: str) -> str:
    """Post a review comment on a specific file and line."""
    return review.post_review_comment(server_url, auth_token, file, line, body)


# Ask-user tool


def _ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask the user a question and wait for their answer.

    Use this when you need clarification or a decision from the user. The
    question will appear in the dashboard inbox. Blocks until the user
    responds (up to 10 minutes).
    """
    return ask_user_mod.ask_user(
        server_url,
        auth_token,
        session_id,
        qualified_branch,
        role,
        question,
        options,
    )


# -- Tool registry (name -> wrapper function) ----------------------------------

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "read_file": _read_file,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "list_files": _list_files,
    "search_files": _search_files,
    "git_status": _git_status,
    "git_diff": _git_diff,
    "git_commit": _git_commit,
    "git_log": _git_log,
    "run_tests": _run_tests,
    "stage_branch": _stage_branch,
    "create_prod_pr": _create_prod_pr,
    "check_pipeline": _check_pipeline,
    "post_review_comment": _post_review_comment,
    "ask_user": _ask_user,
}

# -- Register tools for the active role ----------------------------------------

role_config = ROLES.get(role, ROLES[DEFAULT_ROLE])

for tool_name in role_config["tools"]:
    fn = TOOL_REGISTRY.get(tool_name)
    if fn:
        app.add_tool(fn, name=tool_name)


# -- Entry point ---------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    if not worktree:
        # CLI entry-point: print to stderr before exiting is idiomatic.
        print("Error: SA_WORKTREE environment variable is required", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
