"""Design guard: prevent committing design-mode artifacts.

Ported from ``scripts/pre-commit-design-guard`` (bash).  Checks:

1. **Design markers**: Staged files must not contain ephemeral design-mode
   markers (V_DESIGN prefix family).  These are injected by
   ``v design start`` and must be stripped before committing
   (``v design stop``).

2. **Ephemeral design files**: ``__design-seeds.ts`` and
   ``DesignModeSeeder.tsx`` are auto-generated and must not be committed.

3. **Misplaced todo files**: When a branch is selected (``VB`` env var),
   files staged under the project-wide ``todo/`` directory are rejected
   -- branch-specific findings belong in ``branches/<branch>/todo/``.

4. **Broken imports/exports (esbuild)**: Shells out to a bash snippet
   that runs ``npx esbuild --bundle`` per frontend app to catch broken
   imports/exports at commit time.  Kept in bash because the invocation
   involves per-app loader flags, tsconfig paths, and conditional
   skipping that would gain nothing from a Python rewrite (esbuild
   itself is the external dependency either way).  A pure-Python port
   would only make sense if we needed to parse esbuild output
   programmatically or add per-file granularity.
"""

from __future__ import annotations

import os
import re

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.checks.runner import run_command

# Build marker strings dynamically so this file itself doesn't contain
# the literal markers and trigger the pre-commit design-guard pickaxe.
_DESIGN_PREFIX = "V_DESIGN"
_DESIGN_MARKERS = tuple(f"{_DESIGN_PREFIX}_{suffix}" for suffix in ("ORIG", "BLOCK_START", "BLOCK_END"))


async def _check_design_markers(ctx: CheckContext) -> str | None:
    """Return error message if staged files contain design-mode markers."""
    if not ctx.staged_files:
        return None

    for marker in _DESIGN_MARKERS:
        # Use git diff --cached -S to search for the marker in staged content.
        # The -M flag enables rename detection to avoid false positives.
        rc, stdout, _stderr = await run_command(
            [
                "git",
                "diff",
                "--cached",
                "--diff-filter=d",
                "-M",
                "-S",
                marker,
                "--name-only",
            ],
            cwd=ctx.root,
        )
        if rc == 0 and stdout.strip():
            return "staged files contain design markers -- strip them first:\n  v design stop"
    return None


async def _check_ephemeral_files(ctx: CheckContext) -> str | None:
    """Return error message if ephemeral design-mode files are staged."""
    if not ctx.staged_files:
        return None

    pattern = re.compile(r"(__design-seeds\.ts|DesignModeSeeder\.tsx)$")
    offending = [f for f in ctx.staged_files if pattern.search(f)]
    if offending:
        file_list = "\n".join(f"  {f}" for f in offending)
        return f"staged files include design-mode ephemeral files:\n{file_list}\nrun: v design stop"
    return None


def _check_misplaced_todo(ctx: CheckContext) -> str | None:
    """Return error message if branch-specific files are in project-wide todo/."""
    branch = os.environ.get("VB", "")
    if not branch or not ctx.staged_files:
        return None

    todo_files = [f for f in ctx.staged_files if f.startswith("todo/")]
    if todo_files:
        file_list = "\n".join(f"  {f}" for f in todo_files)
        return f"branch '{branch}' is active -- use branches/{branch}/todo/ instead of todo/:\n{file_list}"
    return None


@register_check("design-guard", group="precommit", timeout=10)
async def design_guard(ctx: CheckContext) -> CheckResult:
    """Guard against committing design-mode artifacts and misplaced files."""
    errors: list[str] = []

    # 1. V_DESIGN markers
    marker_err = await _check_design_markers(ctx)
    if marker_err:
        errors.append(marker_err)

    # 2. Ephemeral design files
    ephemeral_err = await _check_ephemeral_files(ctx)
    if ephemeral_err:
        errors.append(ephemeral_err)

    # 3. Misplaced todo files
    todo_err = _check_misplaced_todo(ctx)
    if todo_err:
        errors.append(todo_err)

    # 4. Broken imports/exports (esbuild) -- delegate to the bash script.
    # Only run if there are staged files in any of the frontend app dirs.
    apps = [
        "bag.veliu.com",
        "orders.bag.veliu.com",
        "backoffice.veliu.com",
        "drops.veliu.com",
    ]
    has_frontend_changes = ctx.staged_files and any(f.startswith(f"{app}/") for f in ctx.staged_files for app in apps)
    if has_frontend_changes:
        rc, stdout, stderr = await run_command(
            ["bash", "-c", _esbuild_script()],
            cwd=ctx.root,
        )
        if rc != 0:
            esbuild_msg = (stderr + stdout).strip()
            if esbuild_msg:
                errors.append(esbuild_msg)
            else:
                errors.append("broken imports/exports detected -- fix before committing")

    if errors:
        return CheckResult(
            name="design-guard",
            outcome="fail",
            duration_ms=0,
            message="\n".join(errors),
        )
    return CheckResult(name="design-guard", outcome="pass", duration_ms=0)


def _esbuild_script() -> str:
    """Return the bash snippet for the esbuild bundle check.

    Kept as a shell snippet because the esbuild invocation involves
    complex loader flags and per-app conditional logic that is simpler
    to maintain in the original bash form.
    """
    return r"""
set -euo pipefail
apps=(bag.veliu.com orders.bag.veliu.com backoffice.veliu.com drops.veliu.com)
staged_files=$(git diff --cached --name-only --diff-filter=d)
esbuild_loaders="--loader:.css=empty --loader:.svg=empty --loader:.png=empty"
esbuild_loaders+=" --loader:.jpg=empty --loader:.webp=empty --loader:.woff2=empty"
esbuild_loaders+=" --loader:.woff=empty --loader:.ttf=empty --loader:.webm=empty"
esbuild_loaders+=" --loader:.mp4=empty --loader:.gif=empty"
esbuild_failed=0
for app in "${apps[@]}"; do
    if ! echo "$staged_files" | grep -q "^${app}/"; then
        continue
    fi
    app_dir="$(git rev-parse --show-toplevel)/$app"
    if [ ! -f "$app_dir/src/main.tsx" ]; then
        continue
    fi
    # shellcheck disable=SC2086
    if ! npx --yes esbuild "$app_dir/src/main.tsx" --bundle --outfile=/dev/null \
        --platform=browser --format=esm --jsx=automatic \
        --tsconfig="$app_dir/tsconfig.app.json" \
        --external:'*.json' --packages=external \
        $esbuild_loaders --log-level=error 2>&1; then
        esbuild_failed=1
    fi
done
if [ "$esbuild_failed" -eq 1 ]; then
    echo "error: broken imports/exports detected -- fix before committing" >&2
    exit 1
fi
"""
