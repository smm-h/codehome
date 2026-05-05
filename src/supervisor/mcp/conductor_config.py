"""Build MCP config for the Conductor process.

Generates the --mcp-config JSON that points to the conductor MCP server,
passing context via environment variables. Mirrors the pattern in
serve/agent_dispatch.py._build_mcp_config.
"""


def build_conductor_mcp_config(
    server_url: str,
    auth_token: str,
    branch: str,
    worktree: str,
) -> dict[str, object]:
    """Build the MCP config dict for the Conductor's claude process.

    Returns a dict suitable for writing to a temp file and passing
    to claude --mcp-config.

    Args:
        server_url: Dashboard server base URL (e.g. http://127.0.0.1:9100).
        auth_token: JWT bearer token for API authentication.
        branch: Qualified branch name (e.g. bag:feature-x).
        worktree: Absolute path to the git worktree.

    """
    return {
        "mcpServers": {
            "conductor": {
                "command": "python3",
                "args": ["-m", "supervisor.mcp.conductor"],
                "env": {
                    "SA_SERVER_URL": server_url,
                    "SA_AUTH_TOKEN": auth_token,
                    "SA_BRANCH": branch,
                    "SA_WORKTREE": worktree,
                },
            }
        }
    }
