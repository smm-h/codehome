"""Unified check framework: registry, runner, formatter.

The check framework provides a decorator-based registry for defining
checks (lint, type-check, build verification, etc.), a concurrent runner
with dependency-aware wave scheduling, and a terminal formatter for
human-readable output.

Nothing in this package imports a concrete check definition. Check
modules live elsewhere and register themselves via :func:`register_check`
on import -- mirroring the step registration pattern in
:mod:`supervisor.tests.framework`.
"""

from __future__ import annotations

from supervisor.checks.formatter import format_report
from supervisor.checks.registry import (
    CheckContext,
    CheckEntry,
    CheckRegistry,
    _registry,
    register_check,
)
from supervisor.checks.result import CheckResult, GroupReport
from supervisor.checks.runner import run_command, run_group

__all__ = [
    "CheckContext",
    "CheckEntry",
    "CheckRegistry",
    "CheckResult",
    "GroupReport",
    "_registry",
    "format_report",
    "register_check",
    "run_command",
    "run_group",
]
