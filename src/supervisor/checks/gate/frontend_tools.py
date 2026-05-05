"""Gate checks for frontend tooling: svelte-check, vitest, build, integrity.

``dashboard-build`` must pass before ``build-integrity`` runs (the
latter inspects the build output).  The dependency is declared via
``depends_on``.
"""

from __future__ import annotations

import re

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.checks.runner import run_command


@register_check("svelte-check", group="gate", timeout=120, cwd="dashboard")
async def svelte_check(ctx: CheckContext) -> CheckResult:
    """Run svelte-check via ``npm run check`` in the dashboard."""
    rc, stdout, stderr = await run_command(
        ["npm", "run", "check"],
        cwd=ctx.cwd,
    )
    if rc == 0:
        return CheckResult(name="svelte-check", outcome="pass", duration_ms=0)
    return CheckResult(
        name="svelte-check",
        outcome="fail",
        duration_ms=0,
        message=(stdout + stderr).strip(),
    )


@register_check("vitest", group="gate", timeout=30, cwd="dashboard")
async def vitest_check(ctx: CheckContext) -> CheckResult:
    """Run vitest in the dashboard."""
    rc, stdout, stderr = await run_command(
        ["npx", "vitest", "run"],
        cwd=ctx.cwd,
    )
    if rc == 0:
        return CheckResult(name="vitest", outcome="pass", duration_ms=0)
    return CheckResult(
        name="vitest",
        outcome="fail",
        duration_ms=0,
        message=(stdout + stderr).strip(),
    )


@register_check("dashboard-build", group="gate", timeout=120, cwd="dashboard")
async def dashboard_build(ctx: CheckContext) -> CheckResult:
    """Build the dashboard frontend via ``npm run build``."""
    rc, stdout, stderr = await run_command(
        ["npm", "run", "build"],
        cwd=ctx.cwd,
    )
    if rc == 0:
        return CheckResult(name="dashboard-build", outcome="pass", duration_ms=0)
    return CheckResult(
        name="dashboard-build",
        outcome="fail",
        duration_ms=0,
        message=(stdout + stderr).strip(),
    )


@register_check(
    "build-integrity",
    group="gate",
    depends_on=["dashboard-build"],
    timeout=10,
)
async def build_integrity(ctx: CheckContext) -> CheckResult:
    """Verify every /assets/ reference in the built index.html exists on disk.

    Ported from ``v health build-integrity``.  Reads
    ``src/supervisor/serve/static/index.html`` and checks that every
    ``<link href="/assets/...">`` and ``<script src="/assets/...">``
    points to an existing file.  A broken build silently ships an
    infinite "Loading..." screen because the browser 404s on a hashed
    JS bundle.
    """
    static_dir = ctx.root / "src" / "supervisor" / "serve" / "static"
    index_html = static_dir / "index.html"

    if not index_html.exists():
        return CheckResult(
            name="build-integrity",
            outcome="fail",
            duration_ms=0,
            message=f"index.html not found at {index_html}",
        )

    html = index_html.read_text()

    # Collect every /assets/ URL referenced by link href or script src.
    href_re = re.compile(r"""href=["'](/assets/[^"']+)["']""")
    src_re = re.compile(r"""src=["'](/assets/[^"']+)["']""")

    urls: set[str] = set()
    for pattern in (href_re, src_re):
        for m in pattern.finditer(html):
            urls.add(m.group(1))

    if not urls:
        return CheckResult(
            name="build-integrity",
            outcome="fail",
            duration_ms=0,
            message="no /assets/ references found in index.html",
        )

    missing = [url for url in sorted(urls) if not (static_dir / url.lstrip("/")).exists()]

    if missing:
        detail = ", ".join(missing)
        return CheckResult(
            name="build-integrity",
            outcome="fail",
            duration_ms=0,
            message=f"{len(missing)} referenced file(s) missing: {detail}",
        )

    return CheckResult(
        name="build-integrity",
        outcome="pass",
        duration_ms=0,
        message=f"all {len(urls)} referenced files exist",
    )
