"""Tests for the check report formatter.

Covers:

- Output contains check names and durations.
- Color tags for each outcome (pass, fail, skip, timeout).
- Advisory failures shown as [warn] with [advisory] tag.
- Fix suggestion rendered on separate line.
- Summary line counts.
- Duration formatting (ms vs s).
"""

from __future__ import annotations

import re

from codehome.checks.formatter import format_report
from codehome.checks.result import CheckResult, GroupReport


def _report(*results: CheckResult) -> GroupReport:
    return GroupReport(group="precommit", results=results)


class TestFormatReportBasic:
    def test_header_contains_group_name(self) -> None:
        report = _report(CheckResult("a", "pass", 100.0))
        output = format_report(report)
        assert "precommit" in output
        assert "1 checks" in output

    def test_check_name_in_output(self) -> None:
        report = _report(CheckResult("ruff-check", "pass", 120.0))
        output = format_report(report)
        assert "ruff-check" in output
        assert "120ms" in output

    def test_all_outcomes_present(self) -> None:
        report = _report(
            CheckResult("a", "pass", 10.0),
            CheckResult("b", "fail", 20.0, message="error"),
            CheckResult("c", "skip", 0.0, message="dep failed"),
            CheckResult("d", "timeout", 30000.0, message="killed"),
        )
        output = format_report(report)
        # Strip ANSI to check text content.
        plain = _strip_ansi(output)
        assert "[pass]" in plain
        assert "[FAIL]" in plain
        assert "[skip]" in plain
        assert "[TIME]" in plain


class TestFormatReportAdvisory:
    def test_advisory_failure_shown_as_warn(self) -> None:
        report = GroupReport(
            group="precommit",
            results=(CheckResult("health-linear", "fail", 3000.0, message="API timeout"),),
            advisory_names=frozenset({"health-linear"}),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "[warn]" in plain
        assert "[advisory]" in plain

    def test_non_advisory_failure_shown_as_fail(self) -> None:
        report = _report(CheckResult("design-guard", "fail", 300.0, message="blocked"))
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "[FAIL]" in plain
        assert "[advisory]" not in plain


class TestFormatReportFix:
    def test_fix_shown_on_separate_line(self) -> None:
        report = _report(
            CheckResult("design-guard", "fail", 300.0, message="blocked", fix="remove the marker"),
        )
        output = format_report(report)
        assert "Fix: remove the marker" in output


class TestFormatReportSummary:
    def test_summary_counts(self) -> None:
        report = _report(
            CheckResult("a", "pass", 10.0),
            CheckResult("b", "pass", 20.0),
            CheckResult("c", "fail", 30.0),
            CheckResult("d", "skip", 0.0),
            CheckResult("e", "timeout", 5000.0),
        )
        output = format_report(report)
        plain = _strip_ansi(output)
        assert "2 passed" in plain
        assert "1 failed" in plain
        assert "1 skipped" in plain
        assert "1 timed out" in plain


class TestFormatReportDuration:
    def test_sub_second_duration(self) -> None:
        report = _report(CheckResult("a", "pass", 120.0))
        output = format_report(report)
        assert "120ms" in output

    def test_multi_second_duration(self) -> None:
        report = _report(CheckResult("a", "pass", 3500.0))
        output = format_report(report)
        assert "3.5s" in output

    def test_skip_shows_dash_duration(self) -> None:
        report = _report(CheckResult("a", "skip", 0.0, message="dep failed"))
        output = format_report(report)
        assert "(-)" in output


# -- Helpers -----------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)
