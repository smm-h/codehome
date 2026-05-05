"""Session identity: read-only access to the current process ID."""

import os


def get_process_id() -> str | None:
    """Get the active process ID from environment."""
    return os.environ.get("CLAUDE_PROCESS_ID")
