"""MCP server for the Conductor process.

The Conductor is a claude process that orchestrates sub-agents. It has no
built-in Claude Code tools (--tools "") and uses only this MCP server.
It talks to the user via structured UI messages and delegates work to
specialized sub-agents via the server API.

Environment variables:
    SA_SERVER_URL  -- server base URL (default http://127.0.0.1:9100)
    SA_AUTH_TOKEN  -- bearer token for server API calls
    SA_BRANCH      -- qualified branch name (e.g. bag:feature-x)
    SA_WORKTREE    -- absolute path to the git worktree
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# -- Read environment -----------------------------------------------------------

server_url = os.environ.get("SA_SERVER_URL", "http://127.0.0.1:9100")
auth_token = os.environ.get("SA_AUTH_TOKEN", "")
branch = os.environ.get("SA_BRANCH", "")
worktree = os.environ.get("SA_WORKTREE", "")

# -- HTTP helpers ---------------------------------------------------------------


def _api(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> str:
    """Make an authenticated JSON request to the server.

    Returns the response body as a string, or an error message prefixed
    with 'Error:'.
    """
    url = f"{server_url.rstrip('/')}{path}"
    body = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Authorization": f"Bearer {auth_token}"}
    if body:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data: bytes = resp.read()
            return data.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} -- {e.read().decode('utf-8', errors='replace')}"
    except urllib.error.URLError as e:
        return f"Error: could not reach server: {e.reason}"
    except TimeoutError:
        return f"Error: request timed out after {timeout}s"


def _api_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Like _api but parses the response as JSON. Returns a dict."""
    raw = _api(method, path, payload, timeout)
    if raw.startswith("Error:"):
        return {"error": raw}
    try:
        result: dict[str, Any] = json.loads(raw)
        return result
    except json.JSONDecodeError:
        return {"raw": raw}


# -- Path guard -----------------------------------------------------------------


def _guard_path(relative: str) -> Path:
    """Resolve a relative path and ensure it stays under the worktree."""
    wt = Path(worktree).resolve()
    resolved = (wt / relative).resolve()
    if not str(resolved).startswith(str(wt) + "/") and resolved != wt:
        raise ValueError(f"Path traversal blocked: {relative}")
    return resolved


# -- Build the server -----------------------------------------------------------

app = FastMCP("conductor")

# Max file size for read_file (100 KB).
_MAX_READ_SIZE = 102_400


# -- Tool definitions ----------------------------------------------------------


@app.tool()
def ui_message(
    type: str,  # noqa: A002 -- public MCP tool schema; renaming breaks dispatcher contract.
    content: str,
    options: list[str] | None = None,
) -> str:
    """Send a structured message to the user via the dashboard UI.

    Use this to communicate plans, progress updates, questions, and results
    to the user. The message appears in the conductor panel of the dashboard.

    Args:
        type: Message type -- one of "text", "plan", "progress", "question", "complete".
        content: The message body. Plain text for text/progress/complete; JSON string for plan.
        options: Optional list of choices (only used when type is "question").

    """
    valid_types = ("text", "plan", "progress", "question", "complete")
    if type not in valid_types:
        return f"Error: type must be one of {valid_types}, got '{type}'"

    payload: dict[str, Any] = {"type": type, "content": content, "branch": branch}
    if options:
        payload["options"] = options

    return _api("POST", "/api/conductor/ui", payload)


@app.tool()
def dispatch_agent(
    role: str,
    task: str,
    system_prompt: str = "",
) -> str:
    """Spawn a sub-agent to perform a specific task.

    The agent runs asynchronously. Use check_agents or wait_for_agents to
    monitor its progress.

    Args:
        role: Agent role -- one of "implementor", "auditor", "reviewer", "test_writer", "deployer".
        task: Description of the work the agent should perform.
        system_prompt: Optional custom system prompt (overrides the default role-based prompt).

    """
    valid_roles = ("implementor", "auditor", "reviewer", "test_writer", "deployer")
    if role not in valid_roles:
        return f"Error: role must be one of {valid_roles}, got '{role}'"

    payload: dict[str, str] = {"role": role, "task": task}
    if system_prompt:
        payload["system_prompt"] = system_prompt

    return _api("POST", f"/api/branches/{branch}/agents", payload)


@app.tool()
def check_agents(session_ids: list[str]) -> str:
    """Check the current status of one or more sub-agents.

    Returns the status, output, and any errors for each agent session.

    Args:
        session_ids: List of agent session IDs to check.

    """
    results = []
    for sid in session_ids:
        data = _api_json("GET", f"/api/branches/{branch}/agents/{sid}")
        results.append(
            {
                "session_id": sid,
                "status": data.get("status", "unknown"),
                "output": data.get("output"),
                "error": data.get("error"),
            }
        )
    return json.dumps(results, indent=2)


@app.tool()
def wait_for_agents(
    session_ids: list[str],
    timeout_seconds: int = 1800,
) -> str:
    """Block until all listed agents reach a terminal status.

    Polls every 5 seconds until all agents are completed, failed, or cancelled,
    or until the timeout is reached.

    Args:
        session_ids: List of agent session IDs to wait for.
        timeout_seconds: Maximum wait time in seconds (default 1800 = 30 min).

    """
    terminal = {"completed", "failed", "cancelled"}
    deadline = time.monotonic() + timeout_seconds
    pending = set(session_ids)
    final: dict[str, dict[str, Any]] = {}

    while pending and time.monotonic() < deadline:
        for sid in list(pending):
            data = _api_json("GET", f"/api/branches/{branch}/agents/{sid}")
            status = data.get("status", "unknown")
            if status in terminal:
                final[sid] = {
                    "session_id": sid,
                    "status": status,
                    "output": data.get("output"),
                    "error": data.get("error"),
                }
                pending.discard(sid)

        if pending:
            time.sleep(5)

    # Report timed-out agents.
    for sid in pending:
        final[sid] = {
            "session_id": sid,
            "status": "timeout",
            "output": None,
            "error": f"Timed out after {timeout_seconds}s",
        }

    # Return in the original order.
    ordered = [final.get(sid, {"session_id": sid, "status": "unknown"}) for sid in session_ids]
    return json.dumps(ordered, indent=2)


@app.tool()
def answer_agent_question(session_id: str, answer: str) -> str:
    """Answer a pending question from a sub-agent.

    Sub-agents can ask questions via the ask_user tool. Use get_pending_questions
    to see unanswered questions, then use this tool to respond.

    Args:
        session_id: The agent session ID that asked the question.
        answer: Your answer to the agent's question.

    """
    return _api(
        "POST",
        f"/api/branches/{branch}/agents/{session_id}/answer",
        {"answer": answer},
    )


@app.tool()
def get_branch_info() -> str:
    """Get current branch state including git status, services, and metadata."""
    return _api("GET", f"/api/branches/{branch}")


@app.tool()
def read_file(path: str) -> str:
    """Read a file from the worktree.

    Args:
        path: File path relative to the worktree root.

    """
    try:
        target = _guard_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.is_file():
        return f"Error: file not found: {path}"

    size = target.stat().st_size
    if size > _MAX_READ_SIZE:
        # Read the first 100KB and warn about truncation.
        try:
            with target.open(encoding="utf-8", errors="strict") as f:
                content = f.read(_MAX_READ_SIZE)
        except (UnicodeDecodeError, ValueError):
            return f"Error: binary file cannot be read as text: {path}"
        return content + f"\n\n[TRUNCATED: file is {size} bytes, showing first {_MAX_READ_SIZE}]"

    try:
        return target.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return f"Error: binary file cannot be read as text: {path}"


@app.tool()
def search_files(pattern: str, path: str = "") -> str:
    """Search file contents in the worktree using ripgrep.

    Args:
        pattern: Regex pattern to search for.
        path: Optional directory path relative to worktree root to restrict the search.

    """
    wt = Path(worktree)
    try:
        search_dir = _guard_path(path or ".")
    except ValueError as e:
        return f"Error: {e}"

    if not search_dir.is_dir():
        return f"Error: not a directory: {path}"

    try:
        result = subprocess.run(
            [
                "rg",
                "--no-heading",
                "--line-number",
                "--color=never",
                "--max-count=50",
                "--max-filesize=1M",
                pattern,
                str(search_dir),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(wt),
        )
    except FileNotFoundError:
        return "Error: ripgrep (rg) not found on PATH"
    except subprocess.TimeoutExpired:
        return "Error: search timed out after 15s"

    if result.returncode == 1:
        return "No matches found."
    if result.returncode != 0:
        return f"Error: rg exited with code {result.returncode}: {result.stderr}"

    # Make paths relative to worktree.
    wt_str = str(wt.resolve())
    output = result.stdout
    if wt_str in output:
        output = output.replace(wt_str + "/", "")

    lines = output.splitlines()
    if len(lines) > 200:
        return "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"
    return output.rstrip()


@app.tool()
def list_files(path: str = "") -> str:
    """List directory contents in the worktree.

    Returns one entry per line: 'd <name>/' for directories,
    'f <name> (<size>b)' for files.

    Args:
        path: Directory path relative to worktree root (default: root).

    """
    wt = Path(worktree)
    try:
        target = _guard_path(path or ".")
    except ValueError as e:
        return f"Error: {e}"

    if not target.is_dir():
        return f"Error: not a directory: {path}"

    lines = []
    try:
        entries = sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return "Error: permission denied"

    for entry in entries:
        name = entry.name
        if name.startswith(".") and entry.is_dir():
            continue

        rel = str(entry.relative_to(wt.resolve()))
        if entry.is_dir():
            lines.append(f"d {rel}/")
        elif entry.is_file():
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"f {rel} ({size}b)")

    return "\n".join(lines) if lines else "(empty directory)"


@app.tool()
def get_pending_questions() -> str:
    """Check for unanswered questions from sub-agents.

    Returns a list of pending questions that need the Conductor's attention.
    """
    return _api("GET", f"/api/questions?branch={branch}&status=pending")


# -- Entry point ---------------------------------------------------------------


def main() -> None:
    """Run the Conductor MCP server over stdio."""
    # CLI entry-point: print to stderr before exiting is idiomatic.
    if not branch:
        print("Error: SA_BRANCH environment variable is required", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    if not worktree:
        print("Error: SA_WORKTREE environment variable is required", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
