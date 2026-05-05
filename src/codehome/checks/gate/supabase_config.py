"""Gate check: supabase/config.toml must not contain local dev patches."""

from __future__ import annotations

import re
import time

from codehome.checks.registry import CheckContext, register_check
from codehome.checks.result import CheckResult
from codehome.checks.runner import run_command
from codehome.paths import worktree_path

_PROJECT_ID_RE = re.compile(r'^project_id\s*=\s*"([^"]*)"', re.MULTILINE)
_PORT_RE = re.compile(
    r'^\s*(port|shadow_port|smtp_port|pop3_port|inspector_port)\s*=\s*"?(\d+)"?',
    re.MULTILINE,
)


def _extract_project_id(text: str) -> str | None:
    m = _PROJECT_ID_RE.search(text)
    return m.group(1) if m else None


def _extract_ports(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _PORT_RE.finditer(text)}


@register_check("supabase-config", group="gate", timeout=5)
async def supabase_config(ctx: CheckContext) -> CheckResult:
    """Block push if supabase/config.toml has local dev patches (project_id or ports)."""
    name = "supabase-config"
    t0 = time.monotonic()

    if not ctx.repo or not ctx.branch:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no branch context)")

    wt = worktree_path(ctx.repo, ctx.branch)

    # Read canonical version from origin/production.
    rc_prod, prod_text, _ = await run_command(
        ["git", "show", "origin/production:supabase/config.toml"],
        cwd=wt,
        timeout_s=4,
    )
    if rc_prod != 0:
        ms = int((time.monotonic() - t0) * 1000)
        return CheckResult(name=name, outcome="pass", duration_ms=ms, message="skipped (origin/production ref unavailable)")

    # Read the committed version on the current branch.
    rc_head, head_text, _ = await run_command(
        ["git", "show", "HEAD:supabase/config.toml"],
        cwd=wt,
        timeout_s=4,
    )
    if rc_head != 0:
        ms = int((time.monotonic() - t0) * 1000)
        return CheckResult(name=name, outcome="pass", duration_ms=ms, message="skipped (no supabase/config.toml on HEAD)")

    # Also check staged version -- if something is staged, that takes priority.
    rc_staged, staged_text, _ = await run_command(
        ["git", "show", ":supabase/config.toml"],
        cwd=wt,
        timeout_s=4,
    )
    branch_text = staged_text if rc_staged == 0 else head_text

    # Compare project_id.
    prod_pid = _extract_project_id(prod_text)
    branch_pid = _extract_project_id(branch_text)

    # Compare ports.
    prod_ports = _extract_ports(prod_text)
    branch_ports = _extract_ports(branch_text)

    diffs: list[str] = []

    if prod_pid and branch_pid and prod_pid != branch_pid:
        diffs.append(f"project_id: production={prod_pid!r}, branch={branch_pid!r}")

    for key in sorted(set(prod_ports) | set(branch_ports)):
        pv = prod_ports.get(key)
        bv = branch_ports.get(key)
        if pv != bv:
            diffs.append(f"{key}: production={pv!r}, branch={bv!r}")

    ms = int((time.monotonic() - t0) * 1000)

    if diffs:
        detail = "\n".join(f"  {d}" for d in diffs)
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=ms,
            message=f"supabase/config.toml has local dev patches vs origin/production:\n{detail}",
            fix="git checkout origin/production -- supabase/config.toml",
        )

    return CheckResult(name=name, outcome="pass", duration_ms=ms, message="supabase/config.toml matches production")
