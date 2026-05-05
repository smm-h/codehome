"""Format a :class:`GroupReport` for terminal output.

Produces colored, human-readable output showing the outcome of each
check in a group.  Advisory failures are rendered as warnings rather
than hard failures.

Color scheme:

- green: pass
- red: FAIL
- yellow: warn (advisory failure)
- gray: skip
- magenta: TIME (timeout)

Mirrors the formatting approach in
:mod:`supervisor.tests.framework.formatter` but adapted for the
check-level granularity (no step-within-test nesting).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supervisor.utils import bold, gray, green, magenta, red, yellow

if TYPE_CHECKING:
    from supervisor.checks.result import CheckResult, GroupReport


def _fmt_duration(ms: float) -> str:
    """Format milliseconds as a human-friendly string.

    Sub-second durations render as milliseconds (e.g. ``120ms``);
    durations >= 1s render with one decimal (e.g. ``3.0s``).
    """
    if ms < 1:
        return "0ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _outcome_tag(result: CheckResult, *, advisory: bool) -> str:
    """Build a colorized outcome tag like ``[pass]``, ``[FAIL]``, etc."""
    if result.outcome == "pass":
        return green("[pass]")
    if result.outcome == "fail":
        if advisory:
            return yellow("[warn]")
        return red("[FAIL]")
    if result.outcome == "skip":
        return gray("[skip]")
    # timeout
    return magenta("[TIME]")


def _format_check(
    result: CheckResult,
    *,
    advisory: bool,
    source: str = "core",
    indent: str = "  ",
) -> list[str]:
    """Format a single check result as one or more output lines."""
    tag = _outcome_tag(result, advisory=advisory)
    duration = _fmt_duration(result.duration_ms) if result.outcome != "skip" else "-"
    # Show [repo] or [branch] suffix for extension checks so the output
    # distinguishes core checks from extension-loaded ones.
    source_tag = f" [{source}]" if source != "core" else ""
    suffix = " [advisory]" if advisory and result.outcome == "fail" else ""
    lines = [f"{indent}{tag} {result.name}{source_tag} ({duration}){suffix}"]

    # Message line (error detail, skip reason, timeout message).
    if result.message:
        lines.append(f"{indent}       {result.message}")

    # Fix suggestion on a separate line.
    if result.fix:
        lines.append(f"{indent}       Fix: {result.fix}")

    return lines


def _build_header(report: GroupReport, total_ms: float) -> str:
    """Build the header line with core/extension breakdown when extensions exist."""
    source_map = report.source_map
    ext_count = sum(1 for s in source_map.values() if s != "core")
    if ext_count:
        core_count = report.total - ext_count
        # Group extension counts by source type (repo, branch).
        by_source: dict[str, int] = {}
        for s in source_map.values():
            if s != "core":
                by_source[s] = by_source.get(s, 0) + 1
        ext_parts = [f"{by_source[src]} {src}" for src in sorted(by_source)]
        breakdown = f"{core_count} core + {' + '.join(ext_parts)} checks"
    else:
        breakdown = f"{report.total} checks"
    return f"v check {report.group} ({breakdown}, {_fmt_duration(total_ms)})"


def format_report(report: GroupReport) -> str:
    """Format a full :class:`GroupReport` for terminal display.

    Advisory names are read from ``report.advisory_names`` -- the runner
    embeds them at construction time so callers don't need to thread
    them through separately.

    Returns:
        A multi-line string ready for ``print()``.

    """
    total_ms = sum(r.duration_ms for r in report.results)
    header = _build_header(report, total_ms)
    lines: list[str] = [bold(header), ""]

    for result in report.results:
        is_advisory = result.name in report.advisory_names
        source = report.source_map.get(result.name, "core")
        lines.extend(_format_check(result, advisory=is_advisory, source=source))

    # Summary line.
    lines.append("")
    parts: list[str] = []
    if report.passed:
        parts.append(green(f"{report.passed} passed"))
    if report.failed:
        parts.append(red(f"{report.failed} failed"))
    if report.skipped:
        parts.append(gray(f"{report.skipped} skipped"))
    if report.timed_out:
        parts.append(magenta(f"{report.timed_out} timed out"))
    summary = ", ".join(parts) if parts else "no checks"
    lines.append(f"{summary} ({_fmt_duration(total_ms)} total)")

    return "\n".join(lines)
