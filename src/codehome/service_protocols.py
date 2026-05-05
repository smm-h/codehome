"""Protocol definitions for cross-plugin service contracts.

Each Protocol corresponds to a service registered via
``services.register(name, handler, ...)``.  Use with
``services.get_typed(name, ProtocolType)`` to get a typed
reference instead of an untyped ``Callable[..., Any]``.

Protocols are grouped by the plugin that registers them.
Consumers adopt them incrementally -- unused Protocols are
forward-looking definitions, not dead code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# publisher plugin
# ---------------------------------------------------------------------------

class PublisherDispatchArgv(Protocol):
    """publisher.dispatch_argv -- CLI dispatch for publisher commands."""

    def __call__(self, argv: list[str]) -> int: ...


class PublisherCmdVersion(Protocol):  # noqa: dead-code
    """publisher.cmd.version -- streaming version info command."""

    async def __call__(self, args: dict[str, Any]) -> Any: ...


# ---------------------------------------------------------------------------
# supabase plugin
# ---------------------------------------------------------------------------

class SupabaseGetProjectRef(Protocol):  # noqa: dead-code
    """supabase.get_project_ref -- look up project_ref for an env name."""

    def __call__(self, env: str) -> str: ...


class SupabaseRequireToken(Protocol):  # noqa: dead-code
    """supabase.require_token -- load Management API token or exit."""

    def __call__(self) -> str: ...


class SupabaseQueryRemote(Protocol):  # noqa: dead-code
    """supabase.query_remote -- execute SQL against a remote Supabase project."""

    def __call__(self, sql: str, project_ref: str, token: str) -> Any: ...


class SupabaseEnvsFile(Protocol):  # noqa: dead-code
    """supabase.envs_file -- return Path to environments.json."""

    def __call__(self) -> Path: ...


# ---------------------------------------------------------------------------
# design plugin
# ---------------------------------------------------------------------------

class DesignHasMarkers(Protocol):  # noqa: dead-code
    """design.has_markers -- return files with V_DESIGN markers under a path."""

    def __call__(self, path: Path) -> list[str]: ...


class DesignActiveState(Protocol):  # noqa: dead-code
    """design.active_state -- return (active, spec) for a branch."""

    def __call__(self, branch: str) -> tuple[bool, str | None]: ...


# ---------------------------------------------------------------------------
# reveng plugin
# ---------------------------------------------------------------------------

class RevengCoverage(Protocol):  # noqa: dead-code
    """reveng.coverage -- return coverage metrics for a branch."""

    def __call__(self, ctx_or_branch: str | None = None) -> dict[str, Any]: ...


class RevengGaps(Protocol):  # noqa: dead-code
    """reveng.gaps -- return gap report for a branch."""

    def __call__(self, ctx_or_branch: str | None = None) -> dict[str, Any] | None: ...
