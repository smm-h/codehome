"""Provider protocol: interface for external service providers.

Each provider declares a name, a set of capabilities, and methods for
testing connections, enriching branch data, and fetching user stats.
Concrete providers live in sibling modules and self-register via the
registry at import time.
"""

from __future__ import annotations

from typing import Any, Protocol


class Provider(Protocol):
    """Interface for external service providers."""

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> set[str]: ...

    def test_connection(self, token: str) -> bool:
        """Validate the token works. Returns True if connection is valid."""
        ...

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        """Return extra data for a branch, or None if not applicable."""
        ...

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        """Return stats for a user, or None if not applicable."""
        ...
