"""Gate checks ported from ``v health``: linear, raw-components, routes.

Utility scanning logic lives in sibling modules:

- :mod:`._raw_components` -- raw HTML element scanning
- :mod:`._routes` -- route reachability scanning (custom client-side router)
- ``_check_linear_vs_git`` is inlined below (depends on supervisor core)

Check semantics:

- **health-linear**: advisory (API failures should not block pushes).
- **health-raw-components**: non-advisory (fails when raw HTML findings are found).
- **health-routes**: non-advisory (unreachable routes are bugs).
"""

from __future__ import annotations

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.git import iter_all_branches
from supervisor.paths import (
    PROTECTED_BRANCHES,
    prod_ref,
    resolve_global,
    worktree_path,
)
from supervisor.utils import warn

# ---------------------------------------------------------------------------
# Suppression helpers (relocated from commands/check/__init__.py)
# ---------------------------------------------------------------------------


def _load_suppressed() -> list[str]:
    """Load suppression patterns from suppress-checks.txt (substring match)."""
    suppress_file = resolve_global("suppress-checks.txt")
    if suppress_file.exists():
        return [
            line.strip()
            for line in suppress_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return []


# ---------------------------------------------------------------------------
# Linear vs git cross-check (relocated from commands/check.__init__.py)
# ---------------------------------------------------------------------------


def _check_linear_vs_git() -> None:
    """Cross-reference Linear state against git merge state.

    Slower than the fast pre-command checks (~200ms per branch for
    merge-base), so only runs on explicit health checks.
    """
    suppressed = _load_suppressed()
    warnings = []
    try:
        from supervisor.git import (
            git,
            is_merged_to_production,
            is_merged_to_staging,
            is_worktree_locked,
            lock_worktree,
        )
        from supervisor.linear_shared import load_cache, load_issue_link

        branch_pairs = list(iter_all_branches())

        cache = load_cache()
        for repo, name in branch_pairs:
            if name in PROTECTED_BRANCHES:
                continue
            if not worktree_path(repo, name).exists():
                continue
            link = load_issue_link(repo, name)
            if not link:
                continue
            issue = cache.get(link["identifier"])
            if not issue:
                continue
            state = issue.get("state", "")
            state_type = issue.get("stateType", "")

            qualified = f"{repo}:{name}"

            # Check production merge.
            merged_prod = is_merged_to_production(name, repo)
            if merged_prod:
                if state_type != "completed":
                    warnings.append(
                        f"{qualified} is merged to production but Linear says '{state}'"
                        f" -- run: v branch finalize -B {qualified}"
                    )
                # Auto-lock worktree if merged to production.
                if not is_worktree_locked(repo, name):
                    lock_worktree(repo, name)
                    warnings.append(f"{qualified} worktree locked (merged to production)")
                continue

            # Check staging merge.
            merged_staging = is_merged_to_staging(name, repo)
            if merged_staging and state_type in ("backlog", "unstarted", "triage"):
                warnings.append(
                    f"{qualified} is merged to staging but Linear says '{state}' -- should be at least 'In Review'"
                )

            # Linear says done but code isn't merged.
            if state_type == "completed":
                warnings.append(
                    f"{qualified} is '{state}' in Linear but has unmerged commits -- deploy or fix Linear state"
                )
            else:
                wt = worktree_path(repo, name)
                behind = int(git(wt, "rev-list", f"{name}..{prod_ref(repo)}", "--count"))
                if behind > 200:
                    warnings.append(
                        f"{qualified} is {behind} commits behind production"
                        " -- consider: v git rebase or v branch finalize"
                    )
    except Exception:
        pass

    for w in warnings:
        if not any(p in w for p in suppressed):
            warn(w)


# ---------------------------------------------------------------------------
# Gate checks
# ---------------------------------------------------------------------------


@register_check("health-linear", group="gate", timeout=30, advisory=True)
async def health_linear(ctx: CheckContext) -> CheckResult:
    """Cross-reference Linear issue state vs git merge state.

    Advisory because Linear API failures or stale caches should not
    block a push -- the check is informational.
    """
    import io
    import sys

    try:
        # Capture warnings emitted to stderr by _check_linear_vs_git.
        old_stderr = sys.stderr
        sys.stderr = captured = io.StringIO()
        try:
            _check_linear_vs_git()
        finally:
            sys.stderr = old_stderr

        output = captured.getvalue().strip()
        if output:
            return CheckResult(
                name="health-linear",
                outcome="fail",
                duration_ms=0,
                message=output,
            )
        return CheckResult(name="health-linear", outcome="pass", duration_ms=0)

    except Exception as exc:
        return CheckResult(
            name="health-linear",
            outcome="fail",
            duration_ms=0,
            message=f"linear check error: {exc}",
        )


@register_check("health-raw-components", group="gate", timeout=10)
async def health_raw_components(ctx: CheckContext) -> CheckResult:
    """Find raw HTML elements in Svelte files that should use components.

    Returns fail when raw HTML findings are found, pass when clean.
    """
    try:
        from supervisor.checks.gate._raw_components import (
            _EXCLUDED_FILES,
            DASHBOARD_SRC,
            scan_file,
        )

        if not DASHBOARD_SRC.is_dir():
            return CheckResult(
                name="health-raw-components",
                outcome="fail",
                duration_ms=0,
                message=f"dashboard src not found: {DASHBOARD_SRC}",
            )

        svelte_files = sorted(DASHBOARD_SRC.rglob("*.svelte"))
        all_findings = []
        for path in svelte_files:
            if path.name in _EXCLUDED_FILES:
                continue
            all_findings.extend(scan_file(path))

        if not all_findings:
            return CheckResult(
                name="health-raw-components",
                outcome="pass",
                duration_ms=0,
                message="no raw-element findings",
            )

        # Group by element for a compact summary.
        by_element: dict[str, int] = {}
        for f in all_findings:
            by_element[f.element] = by_element.get(f.element, 0) + 1
        summary_parts = [f"{el}: {count}" for el, count in sorted(by_element.items())]
        summary = f"{len(all_findings)} finding(s) ({', '.join(summary_parts)})"

        return CheckResult(
            name="health-raw-components",
            outcome="fail",
            duration_ms=0,
            message=summary,
        )

    except Exception as exc:
        return CheckResult(
            name="health-raw-components",
            outcome="fail",
            duration_ms=0,
            message=f"raw-components check error: {exc}",
        )


@register_check("health-routes", group="gate", timeout=10)
async def health_routes(ctx: CheckContext) -> CheckResult:
    """Find unreachable dashboard routes.

    Parses the route table from the custom client-side router
    (dashboard/src/lib/router/routes.ts) and checks that every
    declared route is reachable via navigation targets in the UI.
    """
    try:
        from supervisor.checks.gate._routes import (
            _ROUTES_FILE,
            _SRC_DIR,
            _discover_routes,
            _grep_branch_url_calls,
            _grep_gotos,
            _grep_hrefs,
            _grep_window_location,
            _is_route_reachable,
            _parse_tabs,
        )

        if not _ROUTES_FILE.exists():
            return CheckResult(
                name="health-routes",
                outcome="fail",
                duration_ms=0,
                message=f"routes file not found: {_ROUTES_FILE}",
            )

        routes = _discover_routes()
        svelte_files = list(_SRC_DIR.rglob("*.svelte"))

        targets: set[str] = set()
        targets |= _parse_tabs()
        targets |= _grep_hrefs(svelte_files)
        targets |= _grep_gotos(svelte_files)
        targets |= _grep_window_location(svelte_files)
        targets |= _grep_branch_url_calls(svelte_files)

        unreachable = [r for r in routes if not _is_route_reachable(r, targets)]

        if unreachable:
            detail = ", ".join(unreachable)
            return CheckResult(
                name="health-routes",
                outcome="fail",
                duration_ms=0,
                message=f"{len(unreachable)} unreachable route(s): {detail}",
            )

        return CheckResult(
            name="health-routes",
            outcome="pass",
            duration_ms=0,
            message=f"all {len(routes)} routes reachable",
        )

    except Exception as exc:
        return CheckResult(
            name="health-routes",
            outcome="fail",
            duration_ms=0,
            message=f"routes check error: {exc}",
        )
