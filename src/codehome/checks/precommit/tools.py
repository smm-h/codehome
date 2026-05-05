"""Shell-out checks for external linting/formatting tools.

Registers ruff (lint + format), eslint, and prettier as precommit
checks.  Each check invokes the tool via :func:`run_command` and maps
the exit code to a :class:`CheckResult`.

When ``ctx.staged_files`` is populated (pre-commit hook), each check
filters to its relevant file extensions and passes only those files
to the tool.  When ``ctx.staged_files`` is ``None`` (manual run),
falls back to scanning ``.`` (entire directory).
"""

from __future__ import annotations

from codehome.checks.registry import CheckContext, register_check
from codehome.checks.result import CheckResult
from codehome.checks.runner import run_command

# File extensions each tool cares about.
_PYTHON_EXTS = (".py",)
_FRONTEND_EXTS = (".ts", ".tsx", ".js", ".jsx", ".svelte")

# Prefix stripped from staged paths for tools that run in dashboard/.
_DASHBOARD_PREFIX = "dashboard/"


def _filter_staged(
    ctx: CheckContext,
    extensions: tuple[str, ...],
    *,
    strip_prefix: str | None = None,
) -> list[str] | None:
    """Return staged files matching *extensions*, or None to scan everything.

    - Returns ``None`` when ``ctx.staged_files`` is ``None`` (manual run,
      no git context) -- caller should fall back to ``.``.
    - Returns a (possibly empty) list when staged_files is set.
    - When *strip_prefix* is given, only files under that prefix are kept
      and the prefix is removed (e.g. ``dashboard/src/App.svelte`` ->
      ``src/App.svelte``).
    """
    if ctx.staged_files is None:
        return None

    files: list[str] = []
    for path in ctx.staged_files:
        resolved = path
        if strip_prefix:
            if not path.startswith(strip_prefix):
                continue
            resolved = path[len(strip_prefix) :]
        if resolved.endswith(extensions):
            files.append(resolved)
    return files


@register_check("ruff-check", group="precommit", timeout=10)
async def ruff_check(ctx: CheckContext) -> CheckResult:
    """Run ruff linter on the Python codebase."""
    files = _filter_staged(ctx, _PYTHON_EXTS)
    # staged_files set but no .py files -- nothing to check.
    if files is not None and not files:
        return CheckResult(name="ruff-check", outcome="pass", duration_ms=0, message="no staged .py files")

    target = files or ["."]
    rc, stdout, stderr = await run_command(
        ["uv", "run", "ruff", "check", *target],
        cwd=ctx.root,
    )
    if rc == 0:
        return CheckResult(name="ruff-check", outcome="pass", duration_ms=0)
    return CheckResult(
        name="ruff-check",
        outcome="fail",
        duration_ms=0,
        message=(stderr + stdout).strip(),
        fix="uv run ruff check --fix",
    )


@register_check("ruff-format", group="precommit", timeout=10)
async def ruff_format(ctx: CheckContext) -> CheckResult:
    """Verify Python formatting with ruff."""
    files = _filter_staged(ctx, _PYTHON_EXTS)
    if files is not None and not files:
        return CheckResult(name="ruff-format", outcome="pass", duration_ms=0, message="no staged .py files")

    target = files or ["."]
    rc, stdout, stderr = await run_command(
        ["uv", "run", "ruff", "format", "--check", *target],
        cwd=ctx.root,
    )
    if rc == 0:
        return CheckResult(name="ruff-format", outcome="pass", duration_ms=0)
    return CheckResult(
        name="ruff-format",
        outcome="fail",
        duration_ms=0,
        message=(stderr + stdout).strip(),
        fix="uv run ruff format",
    )


@register_check("eslint", group="precommit", timeout=30, cwd="dashboard")
async def eslint(ctx: CheckContext) -> CheckResult:
    """Run eslint on the dashboard frontend."""
    files = _filter_staged(ctx, _FRONTEND_EXTS, strip_prefix=_DASHBOARD_PREFIX)
    if files is not None and not files:
        return CheckResult(name="eslint", outcome="pass", duration_ms=0, message="no staged frontend files")

    target = files or ["."]
    rc, stdout, stderr = await run_command(
        ["npx", "eslint", "--max-warnings", "0", *target],
        cwd=ctx.cwd,
    )
    if rc == 0:
        return CheckResult(name="eslint", outcome="pass", duration_ms=0)
    return CheckResult(
        name="eslint",
        outcome="fail",
        duration_ms=0,
        message=(stderr + stdout).strip(),
        fix="cd dashboard && npx eslint --fix .",
    )


@register_check("prettier", group="precommit", timeout=30, cwd="dashboard")
async def prettier(ctx: CheckContext) -> CheckResult:
    """Verify frontend formatting with prettier."""
    files = _filter_staged(ctx, _FRONTEND_EXTS, strip_prefix=_DASHBOARD_PREFIX)
    if files is not None and not files:
        return CheckResult(name="prettier", outcome="pass", duration_ms=0, message="no staged frontend files")

    target = files or ["."]
    rc, stdout, stderr = await run_command(
        ["npx", "prettier", "--check", *target],
        cwd=ctx.cwd,
    )
    if rc == 0:
        return CheckResult(name="prettier", outcome="pass", duration_ms=0)
    return CheckResult(
        name="prettier",
        outcome="fail",
        duration_ms=0,
        message=(stderr + stdout).strip(),
        fix="cd dashboard && npx prettier --write .",
    )
