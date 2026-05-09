"""Tests for the check runner.

Covers:

- Wave execution order (dependency resolution).
- Cycle detection.
- Timeout handling.
- Skip on dependency failure.
- Advisory dependency failure does not skip downstream.
- Exception in check function -> fail outcome.
- Invalid depends_on reference -> ValueError.
- Empty group -> empty report.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from codehome.checks.registry import CheckContext, CheckRegistry
from codehome.checks.result import CheckResult
from codehome.checks.runner import run_group


def _ctx() -> CheckContext:
    """Build a minimal CheckContext for testing."""
    return CheckContext(root=Path("/test-root"))


class TestRunGroupBasic:
    @pytest.mark.asyncio
    async def test_empty_group(self) -> None:
        reg = CheckRegistry()
        report = await run_group("empty", registry=reg, ctx=_ctx())
        assert report.group == "empty"
        assert report.total == 0

    @pytest.mark.asyncio
    async def test_single_passing_check(self) -> None:
        reg = CheckRegistry()

        async def pass_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, ".", (), False, pass_check)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.total == 1
        assert report.passed == 1
        assert report.results[0].outcome == "pass"

    @pytest.mark.asyncio
    async def test_failing_check(self) -> None:
        reg = CheckRegistry()

        async def fail_check(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="fail", duration_ms=0.0, message="lint error")

        reg.register("a", "g", 10, ".", (), False, fail_check)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.failed == 1
        assert report.results[0].message == "lint error"

    @pytest.mark.asyncio
    async def test_exception_in_check(self) -> None:
        reg = CheckRegistry()

        async def boom(ctx: CheckContext) -> CheckResult:
            msg = "unexpected error"
            raise RuntimeError(msg)

        reg.register("a", "g", 10, ".", (), False, boom)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.failed == 1
        assert "unexpected error" in (report.results[0].message or "")


class TestRunGroupDependencies:
    @pytest.mark.asyncio
    async def test_dependency_order(self) -> None:
        """Checks with dependencies run after their deps."""
        reg = CheckRegistry()
        order: list[str] = []

        async def check_a(ctx: CheckContext) -> CheckResult:
            order.append("a")
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            order.append("b")
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("b", "g", 10, ".", ("a",), False, check_b)
        reg.register("a", "g", 10, ".", (), False, check_a)
        await run_group("g", registry=reg, ctx=_ctx())
        assert order == ["a", "b"]

    @pytest.mark.asyncio
    async def test_skip_on_dependency_failure(self) -> None:
        """A check is skipped if a required dependency failed."""
        reg = CheckRegistry()

        async def fail_a(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="fail", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, ".", (), False, fail_a)
        reg.register("b", "g", 10, ".", ("a",), False, check_b)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.results[0].outcome == "fail"  # a
        assert report.results[1].outcome == "skip"  # b

    @pytest.mark.asyncio
    async def test_advisory_dep_failure_does_not_skip(self) -> None:
        """An advisory dependency failure does not skip downstream checks."""
        reg = CheckRegistry()

        async def fail_a(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="fail", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        # 'a' is advisory -- its failure should not block 'b'.
        reg.register("a", "g", 10, ".", (), True, fail_a)
        reg.register("b", "g", 10, ".", ("a",), False, check_b)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.results[0].outcome == "fail"  # a
        assert report.results[1].outcome == "pass"  # b

    @pytest.mark.asyncio
    async def test_invalid_dependency_raises(self) -> None:
        """Referencing a non-existent dependency raises ValueError."""
        reg = CheckRegistry()

        async def check_a(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, ".", ("nonexistent",), False, check_a)
        with pytest.raises(ValueError, match="not registered"):
            await run_group("g", registry=reg, ctx=_ctx())


class TestRunGroupCycleDetection:
    @pytest.mark.asyncio
    async def test_cycle_raises(self) -> None:
        """Circular dependencies raise RuntimeError."""
        reg = CheckRegistry()

        async def check_a(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, ".", ("b",), False, check_a)
        reg.register("b", "g", 10, ".", ("a",), False, check_b)
        with pytest.raises(RuntimeError, match="cycle"):
            await run_group("g", registry=reg, ctx=_ctx())


class TestRunGroupTimeout:
    @pytest.mark.asyncio
    async def test_timeout_produces_timeout_outcome(self) -> None:
        """A check that exceeds its timeout gets a 'timeout' outcome."""
        reg = CheckRegistry()

        async def slow_check(ctx: CheckContext) -> CheckResult:
            await asyncio.sleep(10)
            return CheckResult(name="slow", outcome="pass", duration_ms=0.0)

        # 1-second timeout; the check sleeps for 10s.
        reg.register("slow", "g", 1, ".", (), False, slow_check)
        report = await run_group("g", registry=reg, ctx=_ctx())
        assert report.results[0].outcome == "timeout"
        assert "timeout" in (report.results[0].message or "").lower()


class TestRunGroupCwd:
    @pytest.mark.asyncio
    async def test_cwd_resolved_from_entry(self) -> None:
        """The runner resolves entry.cwd relative to ctx.root and sets ctx.cwd."""
        reg = CheckRegistry()
        observed_cwd: list[Path] = []

        async def capture_cwd(ctx: CheckContext) -> CheckResult:
            observed_cwd.append(ctx.cwd)
            return CheckResult(name="sub", outcome="pass", duration_ms=0.0)

        reg.register("sub", "g", 10, "subdir", (), False, capture_cwd)
        await run_group("g", registry=reg, ctx=_ctx())
        assert observed_cwd == [Path("/test-root/subdir")]

    @pytest.mark.asyncio
    async def test_cwd_dot_resolves_to_root(self) -> None:
        """entry.cwd='.' resolves to ctx.root (the default)."""
        reg = CheckRegistry()
        observed_cwd: list[Path] = []

        async def capture_cwd(ctx: CheckContext) -> CheckResult:
            observed_cwd.append(ctx.cwd)
            return CheckResult(name="root", outcome="pass", duration_ms=0.0)

        reg.register("root", "g", 10, ".", (), False, capture_cwd)
        await run_group("g", registry=reg, ctx=_ctx())
        assert observed_cwd == [Path("/test-root")]

    @pytest.mark.asyncio
    async def test_parallel_checks_get_independent_cwd(self) -> None:
        """Checks in the same wave get their own ctx with independent cwd."""
        reg = CheckRegistry()
        observed: dict[str, Path] = {}

        async def check_a(ctx: CheckContext) -> CheckResult:
            observed["a"] = ctx.cwd
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        async def check_b(ctx: CheckContext) -> CheckResult:
            observed["b"] = ctx.cwd
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, "dir-a", (), False, check_a)
        reg.register("b", "g", 10, "dir-b", (), False, check_b)
        await run_group("g", registry=reg, ctx=_ctx())
        assert observed["a"] == Path("/test-root/dir-a")
        assert observed["b"] == Path("/test-root/dir-b")


class TestRunGroupConcurrency:
    @pytest.mark.asyncio
    async def test_independent_checks_run_concurrently(self) -> None:
        """Checks in the same wave (no deps) should overlap in time."""
        reg = CheckRegistry()

        async def slow_a(ctx: CheckContext) -> CheckResult:
            await asyncio.sleep(0.1)
            return CheckResult(name="a", outcome="pass", duration_ms=0.0)

        async def slow_b(ctx: CheckContext) -> CheckResult:
            await asyncio.sleep(0.1)
            return CheckResult(name="b", outcome="pass", duration_ms=0.0)

        reg.register("a", "g", 10, ".", (), False, slow_a)
        reg.register("b", "g", 10, ".", (), False, slow_b)

        import time

        start = time.perf_counter()
        report = await run_group("g", registry=reg, ctx=_ctx())
        elapsed = time.perf_counter() - start

        assert report.passed == 2
        # If run sequentially, would take ~0.2s. Concurrent should be ~0.1s.
        # Use 0.18s as the upper bound to allow for some overhead.
        assert elapsed < 0.18, f"expected concurrent execution, took {elapsed:.3f}s"
