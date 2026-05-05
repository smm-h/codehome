"""Gate check: SwiftUI static analyzer.

Runs the Incantino SwiftUI static analyzer on Lisa Swift source files
in the active branch's worktree.  The analyzer detects view lifecycle
issues, anti-patterns, and potential deadlocks.

Only runs when:
- The active branch's repo is ``bag``
- The worktree contains a ``lisa/`` directory with Swift files

Gate fails if the analyzer reports errors.  Warnings and infos are
advisory -- they appear in the output but do not fail the check.

Registered in the ``gate`` group so it runs on every push.
"""

from __future__ import annotations

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.paths import worktree_path


@register_check("incantino-swift-analyzer", group="gate", timeout=120)
async def incantino_swift_analyzer(ctx: CheckContext) -> CheckResult:
    """Run the SwiftUI static analyzer on Lisa Swift files."""
    name = "incantino-swift-analyzer"

    # Guard: must be a bag branch.
    if ctx.repo != "bag":
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (not bag repo)")

    # Guard: must have a branch context.
    branch = ctx.branch
    if not branch:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no branch context)")

    # Guard: must have a lisa/ directory with Swift files in the worktree.
    wt = worktree_path(ctx.repo, branch)
    lisa_dir = wt / "lisa"
    if not lisa_dir.is_dir():
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no lisa/ in worktree)")

    swift_files = list(lisa_dir.rglob("*.swift"))
    if not swift_files:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no Swift files in lisa/)")

    # Lazy import to avoid loading analyzer tooling at CLI startup.
    try:
        from incantino.swift_analyzer.analyzer import analyze
    except ImportError as exc:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"failed to import swift analyzer module: {exc}",
        )

    try:
        result = analyze(str(lisa_dir), None)
    except Exception as exc:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"analyzer raised an exception: {exc}",
        )

    views_analyzed = result.get("views_analyzed", 0)
    total_files = result.get("total_files", 0)
    parse_failures = result.get("parse_failures", 0)
    errors = result.get("errors", [])
    warnings = result.get("warnings", [])
    infos = result.get("infos", [])

    # Too many parse failures indicate an infrastructure problem
    # (>5% of files failed to parse).
    if total_files > 0 and parse_failures > total_files * 0.05:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=(
                f"too many parse failures: {parse_failures}/{total_files} files failed to parse (>{5}% threshold)"
            ),
            fix=f"uv run --project incantino/tooling incantino analyze check {lisa_dir}",
        )

    if errors:
        # Build a detailed error summary.
        error_lines = [f"  {e['file']}:{e['line']} [{e['rule']}] {e['message']}" for e in errors]

        advisory_parts: list[str] = []
        if warnings:
            advisory_parts.append(f"{len(warnings)} warning(s)")
        if infos:
            advisory_parts.append(f"{len(infos)} info(s)")
        advisory_suffix = f" (also: {', '.join(advisory_parts)})" if advisory_parts else ""

        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=(
                f"{len(errors)} error(s) in {views_analyzed} views across "
                f"{total_files} files{advisory_suffix}\n" + "\n".join(error_lines)
            ),
            fix=f"uv run --project incantino/tooling incantino analyze check {lisa_dir}",
        )

    # No errors -- pass.  Mention warnings/infos if present.
    parts: list[str] = [f"{views_analyzed} views analyzed across {total_files} files, 0 errors"]
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    if infos:
        parts.append(f"{len(infos)} info(s)")

    return CheckResult(
        name=name,
        outcome="pass",
        duration_ms=0,
        message=", ".join(parts),
    )
