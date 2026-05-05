"""Gate checks for Python tooling: mypy and pytest.

Each check shells out via :func:`run_command` and maps exit code to
pass/fail.  On failure, stdout+stderr is included in the message.
"""

from __future__ import annotations

from codehome.checks.registry import CheckContext, register_check
from codehome.checks.result import CheckResult
from codehome.checks.runner import run_command


@register_check("mypy", group="gate", timeout=60)
async def mypy_check(ctx: CheckContext) -> CheckResult:
    """Run mypy type-checking on the codehome Python package."""
    rc, stdout, stderr = await run_command(
        ["uv", "run", "mypy", "src/codehome/"],
        cwd=ctx.root,
    )
    if rc == 0:
        return CheckResult(name="mypy", outcome="pass", duration_ms=0)
    return CheckResult(
        name="mypy",
        outcome="fail",
        duration_ms=0,
        message=(stdout + stderr).strip(),
    )


@register_check("pytest", group="gate", timeout=60)
async def pytest_check(ctx: CheckContext) -> CheckResult:
    """Run the codehome test suite via pytest."""
    rc, stdout, stderr = await run_command(
        ["uv", "run", "pytest", "src/codehome/", "--tb=short"],
        cwd=ctx.root,
    )
    if rc == 0:
        return CheckResult(name="pytest", outcome="pass", duration_ms=0)
    return CheckResult(
        name="pytest",
        outcome="fail",
        duration_ms=0,
        message=(stdout + stderr).strip(),
    )
