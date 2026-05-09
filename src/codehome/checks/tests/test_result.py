"""Tests for CheckResult and GroupReport dataclasses.

Covers:

- CheckResult construction and frozen immutability.
- GroupReport summary counts (passed, failed, skipped, timed_out).
- GroupReport.ok property with and without advisory names.
- Edge cases: empty report, all outcomes mixed.
"""

from __future__ import annotations

import pytest

from codehome.checks.result import CheckResult, GroupReport

# -- CheckResult -------------------------------------------------------------


class TestCheckResult:
    def test_basic_construction(self) -> None:
        r = CheckResult(name="ruff-check", outcome="pass", duration_ms=120.0)
        assert r.name == "ruff-check"
        assert r.outcome == "pass"
        assert r.duration_ms == 120.0
        assert r.message is None
        assert r.fix is None

    def test_with_message_and_fix(self) -> None:
        r = CheckResult(
            name="design-guard",
            outcome="fail",
            duration_ms=300.0,
            message="BLOCKED: design marker found in src/serve/app.py",
            fix="remove the marker before committing",
        )
        assert r.message is not None
        assert r.fix is not None

    def test_frozen(self) -> None:
        r = CheckResult(name="a", outcome="pass", duration_ms=0.0)
        with pytest.raises(AttributeError):
            r.name = "b"  # type: ignore[misc]


# -- GroupReport -------------------------------------------------------------


class TestGroupReport:
    def _make_report(self, outcomes: list[str]) -> GroupReport:
        """Build a GroupReport with checks named c-0, c-1, ..."""
        results = tuple(
            CheckResult(name=f"c-{i}", outcome=o, duration_ms=float(i * 100))  # type: ignore[arg-type]
            for i, o in enumerate(outcomes)
        )
        return GroupReport(group="test-group", results=results)

    def test_empty_report(self) -> None:
        report = GroupReport(group="empty", results=())
        assert report.total == 0
        assert report.passed == 0
        assert report.failed == 0
        assert report.skipped == 0
        assert report.timed_out == 0
        assert report.ok

    def test_all_pass(self) -> None:
        report = self._make_report(["pass", "pass", "pass"])
        assert report.total == 3
        assert report.passed == 3
        assert report.failed == 0
        assert report.ok

    def test_mixed_outcomes(self) -> None:
        report = self._make_report(["pass", "fail", "skip", "timeout"])
        assert report.total == 4
        assert report.passed == 1
        assert report.failed == 1
        assert report.skipped == 1
        assert report.timed_out == 1
        assert not report.ok

    def test_ok_with_advisory(self) -> None:
        """Advisory failures should not break ok."""
        report = self._make_report(["pass", "fail"])
        # Without advisory -- not ok.
        assert not report.ok
        # Mark c-1 as advisory -- now ok.
        advisory_report = GroupReport(
            group="test-group",
            results=report.results,
            advisory_names=frozenset({"c-1"}),
        )
        assert advisory_report.ok

    def test_ok_with_advisory_timeout(self) -> None:
        """Advisory timeouts should not break ok."""
        report = self._make_report(["pass", "timeout"])
        assert not report.ok
        advisory_report = GroupReport(
            group="test-group",
            results=report.results,
            advisory_names=frozenset({"c-1"}),
        )
        assert advisory_report.ok

    def test_ok_non_advisory_failure_still_fails(self) -> None:
        """Non-advisory failure blocks ok even when advisory names exist."""
        report = self._make_report(["fail", "fail"])
        # Only c-1 is advisory; c-0 is not.
        advisory_report = GroupReport(
            group="test-group",
            results=report.results,
            advisory_names=frozenset({"c-1"}),
        )
        assert not advisory_report.ok

    def test_frozen(self) -> None:
        report = self._make_report(["pass"])
        with pytest.raises(AttributeError):
            report.group = "other"  # type: ignore[misc]
