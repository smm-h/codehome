"""Tests for extension integration with v check.

Covers:

- Running a check group with no extensions produces only core checks.
- Running a check group with extensions produces core + extension checks.
- Extension checks with source="repo" show [repo] in formatted output.
- Extension checks with source="branch" show [branch] in formatted output.
- Header shows core + extension breakdown when extensions are present.
- Header shows plain count when only core checks exist.
- source_map is correctly populated by the runner.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType

import pytest

from codehome.checks.formatter import format_report
from codehome.checks.registry import CheckContext, CheckRegistry
from codehome.checks.result import CheckResult, GroupReport
from codehome.checks.runner import run_group


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _ctx() -> CheckContext:
    """Build a minimal CheckContext for testing."""
    return CheckContext(root=Path("/test-root"))


class TestCoreOnlyChecks:
    """v check gate with no extensions runs only core checks."""

    @pytest.mark.asyncio
    async def test_core_only_report(self) -> None:
        reg = CheckRegistry()

        async def check_a(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("a", "gate", 10, ".", (), False, check_a)
        reg.register("b", "gate", 10, ".", (), False, check_b)

        report = await run_group("gate", registry=reg, ctx=_ctx())

        assert report.total == 2
        # All checks are core -- source_map should reflect that.
        assert report.source_map == {"a": "core", "b": "core"}

    @pytest.mark.asyncio
    async def test_core_only_header_no_breakdown(self) -> None:
        """Header shows plain count when only core checks exist."""
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("a", "pass", 100.0),
                CheckResult("b", "pass", 200.0),
            ),
            source_map=MappingProxyType({"a": "core", "b": "core"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        # Should show "2 checks", not "2 core + 0 repo checks".
        assert "2 checks" in plain
        assert "core +" not in plain

    @pytest.mark.asyncio
    async def test_core_checks_no_source_tag(self) -> None:
        """Core checks should not have a [repo] or [branch] suffix."""
        report = GroupReport(
            group="gate",
            results=(CheckResult("ruff-check", "pass", 120.0),),
            source_map=MappingProxyType({"ruff-check": "core"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "[repo]" not in plain
        assert "[branch]" not in plain


class TestExtensionChecks:
    """v check gate with extensions runs core + extension checks."""

    @pytest.mark.asyncio
    async def test_core_plus_extension_report(self) -> None:
        reg = CheckRegistry()

        async def core_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="core-lint", outcome="pass", duration_ms=0.0)

        async def ext_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="ext-lint", outcome="pass", duration_ms=0.0)

        reg.register("core-lint", "gate", 10, ".", (), False, core_check, source="core")
        reg.register("ext-lint", "gate", 10, ".", (), False, ext_check, source="repo")

        report = await run_group("gate", registry=reg, ctx=_ctx())

        assert report.total == 2
        assert report.source_map == {"core-lint": "core", "ext-lint": "repo"}
        assert report.passed == 2

    @pytest.mark.asyncio
    async def test_extension_source_map_branch(self) -> None:
        """Branch extensions get source='branch' in source_map."""
        reg = CheckRegistry()

        async def core_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="core-a", outcome="pass", duration_ms=0.0)

        async def branch_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="branch-a", outcome="pass", duration_ms=0.0)

        reg.register("core-a", "gate", 10, ".", (), False, core_check, source="core")
        reg.register("branch-a", "gate", 10, ".", (), False, branch_check, source="branch")

        report = await run_group("gate", registry=reg, ctx=_ctx())

        assert report.source_map["core-a"] == "core"
        assert report.source_map["branch-a"] == "branch"


class TestFormatterSourceTags:
    """Extension checks show [repo] or [branch] in formatted output."""

    def test_repo_source_shown(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("ruff-check", "pass", 120.0),
                CheckResult("lint-cycles", "pass", 2100.0),
            ),
            source_map=MappingProxyType({"ruff-check": "core", "lint-cycles": "repo"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "[repo]" in plain
        # The [repo] tag should appear on the lint-cycles line.
        assert "lint-cycles [repo]" in plain
        # Core check should not have a tag.
        assert "ruff-check [" not in plain

    def test_branch_source_shown(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("ruff-check", "pass", 120.0),
                CheckResult("branch-lint", "pass", 800.0),
            ),
            source_map=MappingProxyType({"ruff-check": "core", "branch-lint": "branch"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "branch-lint [branch]" in plain

    def test_mixed_sources_shown(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("core-a", "pass", 100.0),
                CheckResult("repo-a", "pass", 200.0),
                CheckResult("branch-a", "pass", 300.0),
            ),
            source_map=MappingProxyType({"core-a": "core", "repo-a": "repo", "branch-a": "branch"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "repo-a [repo]" in plain
        assert "branch-a [branch]" in plain
        assert "core-a [" not in plain


class TestFormatterHeader:
    """Header shows core + extension breakdown when extensions exist."""

    def test_header_with_extensions(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("a", "pass", 100.0),
                CheckResult("b", "pass", 200.0),
                CheckResult("c", "pass", 300.0),
            ),
            source_map=MappingProxyType({"a": "core", "b": "core", "c": "repo"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        # Should show "2 core + 1 repo checks".
        assert "2 core + 1 repo checks" in plain

    def test_header_with_branch_extensions(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("a", "pass", 100.0),
                CheckResult("b", "pass", 200.0),
                CheckResult("c", "pass", 300.0),
            ),
            source_map=MappingProxyType({"a": "core", "b": "repo", "c": "branch"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        # Branch comes before repo alphabetically.
        assert "1 core + 1 branch + 1 repo checks" in plain

    def test_header_without_extensions(self) -> None:
        report = GroupReport(
            group="gate",
            results=(
                CheckResult("a", "pass", 100.0),
                CheckResult("b", "pass", 200.0),
            ),
            source_map=MappingProxyType({"a": "core", "b": "core"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "2 checks" in plain
        assert "core +" not in plain

    def test_header_empty_source_map(self) -> None:
        """Empty source_map (default) shows plain count."""
        report = GroupReport(
            group="gate",
            results=(CheckResult("a", "pass", 100.0),),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "1 checks" in plain
