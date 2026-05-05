"""Check result types: per-check outcome and per-group aggregate report.

Two frozen dataclasses capture the output of the check framework:

- :class:`CheckResult` -- the outcome of a single check (pass/fail/skip/timeout).
- :class:`GroupReport` -- aggregates results for a whole group and provides
  summary counts.  The :attr:`ok` property uses the embedded
  ``advisory_names`` set so advisory failures don't break the overall
  verdict.

Mirrors the ``StepResult`` / ``RunReport`` pattern in
:mod:`codehome.tests.framework.runner` but tailored for the lighter
check semantics (no FlowState, no step sequences -- just independent
checks with optional dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

# -- Per-check result --------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """Immutable outcome of a single check execution.

    Attributes:
        name: Kebab-case check identifier (e.g. ``ruff-check``).
        outcome: One of ``pass``, ``fail``, ``skip``, ``timeout``.
        duration_ms: Wall-clock time spent executing the check.
        message: Human-readable error output; populated on ``fail``/``timeout``.
        fix: Suggested fix command shown to the user (optional).

    """

    name: str
    outcome: Literal["pass", "fail", "skip", "timeout"]
    duration_ms: float
    message: str | None = None
    fix: str | None = None


# -- Group-level aggregate ---------------------------------------------------


@dataclass(frozen=True)
class GroupReport:
    """Aggregate results for all checks in a group.

    Attributes:
        group: Group name (e.g. ``precommit``, ``gate``).
        results: Ordered tuple of :class:`CheckResult` instances.

    """

    group: str
    results: tuple[CheckResult, ...]
    # Set by the runner from registry entries so callers don't need to
    # thread advisory info through separately.
    advisory_names: frozenset[str] = frozenset()
    # Maps check name -> source ("core", "repo", "branch").  Populated by
    # the runner from CheckEntry.source so the formatter can distinguish
    # extension checks without needing the registry.  Wrapped in
    # MappingProxyType to enforce immutability on this frozen dataclass.
    source_map: MappingProxyType[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Freeze source_map if passed as a plain dict."""
        if isinstance(self.source_map, dict):
            object.__setattr__(self, "source_map", MappingProxyType(self.source_map))

    # -- verdict -------------------------------------------------------------

    @property
    def ok(self) -> bool:
        """Return ``True`` if no non-advisory check failed or timed out.

        Advisory checks (identified by name in :attr:`advisory_names`)
        are allowed to fail without breaking the overall verdict -- they
        surface warnings but don't block commits or gates.
        """
        return all(r.outcome not in ("fail", "timeout") or r.name in self.advisory_names for r in self.results)

    # -- summary counts ------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.outcome == "skip")

    @property
    def timed_out(self) -> int:
        return sum(1 for r in self.results if r.outcome == "timeout")
