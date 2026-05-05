"""Check registry: kebab-case name -> executable async check function.

Mirrors the decorator-based registration pattern in
:mod:`supervisor.tests.framework.registry`.  Each check module declares
itself with the :func:`register_check` decorator; the registry
auto-populates on import.

Key differences from the test-step registry:

- **Groups.** Checks belong to a named group (e.g. ``precommit``,
  ``gate``) so the runner can execute a whole group at once.
- **Dependencies.** A check may declare ``depends_on`` -- a tuple of
  other check names within the same group.  The runner topo-sorts
  checks into waves and skips downstream checks when a dependency
  fails.
- **Advisory flag.** Advisory checks can fail without breaking the
  overall group verdict (shown as warnings instead of errors).
- **Timeout per check.** Each check carries its own timeout in seconds.

One global registry singleton (``_registry``) holds all registrations.
The module exposes :func:`register_check` (decorator) and the singleton
for direct access by the runner.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from supervisor.checks.result import CheckResult

# Sentinel used by CheckContext to detect when cwd was not provided.
# Using a sentinel instead of None keeps the runtime type as Path,
# which satisfies mypy without requiring callers to narrow.
_CWD_SENTINEL = object()


# -- CheckContext ------------------------------------------------------------


@dataclass
class CheckContext:
    """Runtime context passed to every check function.

    Attributes:
        root: Absolute path to the project root.
        cwd: Resolved working directory for this check.  Set by the
            runner from ``CheckEntry.cwd`` (relative to *root*).
            Check functions should use this instead of computing
            their own working directory.  Defaults to *root*.
        staged_files: List of staged file paths (for precommit checks).
            ``None`` when running gate checks (all files are in scope).
        repo: Repository name (e.g. ``bag``).  Set by callers that
            run repo-specific extension groups (deploy, branch create).
            ``None`` for core checks that don't need repo context.
        branch: Branch name.  Set alongside *repo* for branch-aware
            extension checks.  ``None`` for repo-level-only checks.
        branch_dir: Absolute path to the branch context directory
            (e.g. ``repos/bag/branches/fix-auth/``).  ``None`` when
            not running in a branch context.

    """

    root: Path
    cwd: Path = field(default=_CWD_SENTINEL)  # type: ignore[assignment]
    staged_files: tuple[str, ...] | None = None
    repo: str | None = None
    branch: str | None = None
    branch_dir: Path | None = None

    def __post_init__(self) -> None:
        """Default *cwd* to *root* when not explicitly provided."""
        if self.cwd is _CWD_SENTINEL:
            self.cwd = self.root


# -- CheckEntry --------------------------------------------------------------


@dataclass(frozen=True)
class CheckEntry:
    """Immutable record of a registered check.

    Attributes:
        name: Kebab-case check identifier (e.g. ``ruff-check``).
        group: Group this check belongs to (e.g. ``precommit``).
        timeout: Maximum execution time in seconds.
        cwd: Working directory relative to the project root.
        depends_on: Names of checks that must pass before this one runs.
        advisory: If ``True``, failure is a warning, not an error.
        fn: The async callable that executes the check.
        source: Origin of this check -- ``"core"`` for built-in checks,
            ``"repo"`` or ``"branch"`` for extension-loaded checks.

    """

    name: str
    group: str
    timeout: int
    cwd: str = "."
    depends_on: tuple[str, ...] = ()
    advisory: bool = False
    # Typed as Any at the dataclass level because CheckResult is
    # TYPE_CHECKING-only.  The register() method and decorator enforce
    # the real signature at registration time.
    fn: Any = None
    # Where this check originated: "core" for built-in @register_check
    # checks, "repo" or "branch" for extension-loaded checks.
    source: str = "core"

    def __repr__(self) -> str:  # noqa: D105 -- compact repr
        extras: list[str] = []
        extras.append(f"group={self.group!r}")
        extras.append(f"timeout={self.timeout}")
        if self.depends_on:
            extras.append(f"depends_on={list(self.depends_on)}")
        if self.advisory:
            extras.append("advisory=True")
        if self.source != "core":
            extras.append(f"source={self.source!r}")
        return f"CheckEntry({self.name!r}, {', '.join(extras)})"


# -- CheckRegistry -----------------------------------------------------------


class CheckRegistry:
    """Process-wide registry of check definitions by kebab-case name.

    Populated at import time by the :func:`register_check` decorator.
    The runner reads from this; tests can create isolated instances.

    Duplicate registration raises ``ValueError`` -- check names are a
    namespace, and silent shadowing would hide bugs.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CheckEntry] = {}

    # -- registration --------------------------------------------------------

    def register(
        self,
        name: str,
        group: str,
        timeout: int,
        cwd: str,
        depends_on: tuple[str, ...],
        advisory: bool,
        fn: Callable[..., Awaitable[CheckResult]],
        *,
        source: str = "core",
    ) -> None:
        """Add a check; raise ``ValueError`` if the name is already taken."""
        if name in self._entries:
            existing = self._entries[name]
            msg = f"duplicate check registration for {name!r}: already registered in group {existing.group!r}"
            raise ValueError(msg)
        self._entries[name] = CheckEntry(
            name=name,
            group=group,
            timeout=timeout,
            cwd=cwd,
            depends_on=depends_on,
            advisory=advisory,
            fn=fn,
            source=source,
        )

    # -- lookup --------------------------------------------------------------

    def get(self, name: str) -> CheckEntry | None:
        """Return the entry for *name*, or ``None`` if not registered."""
        return self._entries.get(name)

    def group(self, name: str) -> tuple[CheckEntry, ...]:
        """Return all entries for group *name*, sorted by check name."""
        return tuple(
            sorted(
                (e for e in self._entries.values() if e.group == name),
                key=lambda e: e.name,
            )
        )

    def groups(self) -> tuple[str, ...]:
        """Return all known group names, sorted alphabetically."""
        return tuple(sorted({e.group for e in self._entries.values()}))

    def all(self) -> tuple[CheckEntry, ...]:
        """Return all registered entries, sorted by name."""
        return tuple(sorted(self._entries.values(), key=lambda e: e.name))

    def clear(self) -> None:
        """Drop every registration.  Primarily for test isolation."""
        self._entries.clear()

    def __contains__(self, name: object) -> bool:  # noqa: D105
        return isinstance(name, str) and name in self._entries

    def __len__(self) -> int:  # noqa: D105
        return len(self._entries)


# Module-level singleton.  Checks register into this on import; the
# runner reads from this.  Tests that need isolation create their own
# CheckRegistry instance.
_registry: CheckRegistry = CheckRegistry()


# -- Decorator ---------------------------------------------------------------


def register_check(
    name: str,
    *,
    group: str,
    timeout: int,
    cwd: str = ".",
    depends_on: tuple[str, ...] | list[str] = (),
    advisory: bool = False,
) -> Callable[
    [Callable[..., Awaitable[CheckResult]]],
    Callable[..., Awaitable[CheckResult]],
]:
    """Register an async check function under a kebab-case *name*.

    Usage::

        @register_check("ruff-check", group="precommit", timeout=30)
        async def ruff_check(ctx: CheckContext) -> CheckResult:
            ...

    The decorator returns the original function unchanged -- wrapping
    would break direct-import patterns used by unit tests.  Registration
    is a side-effect of decoration.
    """
    deps = tuple(depends_on)

    def decorator(
        fn: Callable[..., Awaitable[CheckResult]],
    ) -> Callable[..., Awaitable[CheckResult]]:
        if not inspect.iscoroutinefunction(fn):
            msg = (
                f"check {name!r}: @register_check requires an async function "
                f"(got {fn.__qualname__}, defined in {fn.__module__})"
            )
            raise TypeError(msg)

        _registry.register(name, group, timeout, cwd, deps, advisory, fn)
        return fn

    return decorator
