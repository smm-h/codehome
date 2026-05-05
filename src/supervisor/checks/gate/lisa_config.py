"""Gate checks: Lisa SDUI config validation.

Three checks:

**lisa-config-render** -- Runs ``swift run LisaCLI render-screen <config>``
and parses the stats line to detect decode failures or an empty chip set.

**lisa-config-validate** -- Runs ``swift run LisaCLI validate-config <config>``
and parses the summary to detect schema-level errors (missing fields,
unknown types).  Warnings are advisory; only errors cause a failure.

**lisa-vocabulary-coverage** -- Pure-Python check (no Swift build needed).
Loads ``sdui-vocabulary.json`` and verifies every ``componentTypes`` entry
has a matching fixture file at ``test-fixtures/components/<name>.json``.

The first two checks are guarded and pass immediately (skip) when:
- The active branch is not in the ``bag`` repo
- The worktree has no ``lisa/`` directory
- The ``swift`` toolchain is not available on the system

The vocabulary-coverage check uses the same repo/directory guards but
does NOT require the swift toolchain (it only reads JSON and globs files).
"""

from __future__ import annotations

import json
import re
import shutil
from typing import TYPE_CHECKING

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.checks.runner import run_command
from supervisor.paths import worktree_path

if TYPE_CHECKING:
    from pathlib import Path

# Regex to extract the four integers from the stats summary line
# emitted by SpecTreeRenderer (e.g. "N sections visible, N hidden,
# N chips total, N decode failures").
_STATS_RE = re.compile(
    r"(\d+)\s+sections?\s+visible,\s*"
    r"(\d+)\s+hidden,\s*"
    r"(\d+)\s+chips?\s+total,\s*"
    r"(\d+)\s+decode\s+failures?"
)

# Config paths relative to the worktree root.
_CONFIG_REL = "lisa/Lisa/Resources/default-config.json"
_TEST_CONFIG_REL = "lisa/Lisa/Resources/test-config.json"

# Regex to extract the error count from the validate-config summary
# (e.g. "Errors: 3").
_ERRORS_RE = re.compile(r"^Errors:\s*(\d+)", re.MULTILINE)

# Regex to extract the result verdict from the validate-config summary
# (e.g. "Result: FAILED" or "Result: PASSED").
_RESULT_RE = re.compile(r"^Result:\s*(\S+)", re.MULTILINE)


@register_check("lisa-config-render", group="gate", timeout=120)
async def lisa_config_render(ctx: CheckContext) -> CheckResult:
    """Validate the bundled Lisa SDUI config via headless render.

    Runs ``swift run LisaCLI render-screen`` against default-config.json
    and checks for decode failures or zero chips.
    """
    name = "lisa-config-render"

    # Guard: must be a bag branch.
    if ctx.repo != "bag":
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (not bag repo)")

    # Guard: must have a lisa/ directory in the worktree.
    branch = ctx.branch
    if not branch:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no branch context)")

    wt = worktree_path(ctx.repo, branch)
    lisa_dir = wt / "lisa"
    if not lisa_dir.is_dir():
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no lisa/ in worktree)")

    config_path = wt / _CONFIG_REL
    if not config_path.is_file():
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"config file not found: {config_path}",
        )

    # Guard: swift toolchain must be available.
    if shutil.which("swift") is None:
        return CheckResult(
            name=name,
            outcome="pass",
            duration_ms=0,
            message="skipped (swift toolchain not available)",
        )

    # Run the headless renderer.
    rc, stdout, stderr = await run_command(
        ["swift", "run", "LisaCLI", "render-screen", str(config_path)],
        cwd=lisa_dir,
        timeout_s=110,
    )

    if rc != 0:
        # Command itself failed -- surface the error output.
        combined = (stderr + stdout).strip()
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"LisaCLI render-screen exited {rc}\n{combined}",
            fix=f"cd {lisa_dir} && swift run LisaCLI render-screen {config_path}",
        )

    # Parse the stats line from stdout.
    match = _STATS_RE.search(stdout)
    if match is None:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"could not parse stats line from render-screen output:\n{stdout.strip()[-500:]}",
            fix=f"cd {lisa_dir} && swift run LisaCLI render-screen {config_path}",
        )

    visible = int(match.group(1))
    hidden = int(match.group(2))
    chips_total = int(match.group(3))
    decode_failures = int(match.group(4))

    # Fail conditions.
    failures: list[str] = []
    if decode_failures > 0:
        failures.append(f"{decode_failures} decode failure(s)")
    if chips_total == 0:
        failures.append("0 chips total (broken or empty config)")

    if failures:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"config validation failed: {'; '.join(failures)} "
            f"({visible} visible, {hidden} hidden, {chips_total} chips)",
            fix=f"cd {lisa_dir} && swift run LisaCLI render-screen {config_path}",
        )

    return CheckResult(
        name=name,
        outcome="pass",
        duration_ms=0,
        message=f"{visible} sections visible, {chips_total} chips, 0 decode failures",
    )


# ---------------------------------------------------------------------------
# lisa-config-validate
# ---------------------------------------------------------------------------


async def _validate_single_config(
    label: str,
    config_path: Path,
    lisa_dir: Path,
) -> tuple[bool, str]:
    """Run validate-config on one config file.

    Returns ``(ok, message)`` where *ok* is False when errors > 0 or
    the result line says FAILED.
    """
    rc, stdout, stderr = await run_command(
        ["swift", "run", "LisaCLI", "validate-config", str(config_path)],
        cwd=lisa_dir,
        timeout_s=110,
    )

    if rc != 0:
        combined = (stderr + stdout).strip()
        return False, f"[{label}] LisaCLI validate-config exited {rc}\n{combined}"

    # Parse summary from stdout.
    errors_match = _ERRORS_RE.search(stdout)
    result_match = _RESULT_RE.search(stdout)

    if errors_match is None or result_match is None:
        return False, (f"[{label}] could not parse validate-config output:\n{stdout.strip()[-500:]}")

    error_count = int(errors_match.group(1))
    result_verdict = result_match.group(1).upper()

    if error_count > 0 or result_verdict == "FAILED":
        # Include the full output so the user sees the per-field errors.
        return False, f"[{label}] {error_count} error(s), result {result_verdict}\n{stdout.strip()}"

    return True, f"[{label}] {result_verdict}, 0 errors"


@register_check("lisa-config-validate", group="gate", timeout=180)
async def lisa_config_validate(ctx: CheckContext) -> CheckResult:
    """Validate Lisa SDUI config schemas via LisaCLI validate-config.

    Checks default-config.json (required) and test-config.json (if present).
    Fails when errors > 0 or the result line reports FAILED.
    """
    name = "lisa-config-validate"

    # Guard: must be a bag branch.
    if ctx.repo != "bag":
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (not bag repo)")

    # Guard: must have a branch context with a lisa/ directory.
    branch = ctx.branch
    if not branch:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no branch context)")

    wt = worktree_path(ctx.repo, branch)
    lisa_dir = wt / "lisa"
    if not lisa_dir.is_dir():
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no lisa/ in worktree)")

    # Guard: default config must exist.
    default_config = wt / _CONFIG_REL
    if not default_config.is_file():
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"config file not found: {default_config}",
        )

    # Guard: swift toolchain must be available.
    if shutil.which("swift") is None:
        return CheckResult(
            name=name,
            outcome="pass",
            duration_ms=0,
            message="skipped (swift toolchain not available)",
        )

    # Validate each config file that exists.
    configs: list[tuple[str, Path]] = [("default-config", default_config)]

    test_config = wt / _TEST_CONFIG_REL
    if test_config.is_file():
        configs.append(("test-config", test_config))

    failures: list[str] = []
    passes: list[str] = []

    for label, path in configs:
        ok, msg = await _validate_single_config(label, path, lisa_dir)
        if ok:
            passes.append(msg)
        else:
            failures.append(msg)

    if failures:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message="\n".join(failures),
            fix=f"cd {lisa_dir} && swift run LisaCLI validate-config {default_config}",
        )

    return CheckResult(
        name=name,
        outcome="pass",
        duration_ms=0,
        message="; ".join(passes),
    )


# ---------------------------------------------------------------------------
# lisa-vocabulary-coverage
# ---------------------------------------------------------------------------

# Paths relative to the worktree root.
_VOCABULARY_REL = "lisa/Lisa/Resources/sdui-vocabulary.json"
_FIXTURES_DIR_REL = "lisa/Lisa/Resources/test-fixtures/components"


@register_check("lisa-vocabulary-coverage", group="gate", timeout=30)
async def lisa_vocabulary_coverage(ctx: CheckContext) -> CheckResult:
    """Verify every vocabulary componentType has a matching test fixture.

    Loads ``sdui-vocabulary.json``, reads its ``componentTypes`` array,
    and checks that ``test-fixtures/components/<name>.json`` exists for
    each entry.  Pure Python -- no Swift build required.
    """
    name = "lisa-vocabulary-coverage"

    # Guard: must be a bag branch.
    if ctx.repo != "bag":
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (not bag repo)")

    # Guard: must have a branch context with a lisa/ directory.
    branch = ctx.branch
    if not branch:
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no branch context)")

    wt = worktree_path(ctx.repo, branch)
    lisa_dir = wt / "lisa"
    if not lisa_dir.is_dir():
        return CheckResult(name=name, outcome="pass", duration_ms=0, message="skipped (no lisa/ in worktree)")

    # Guard: vocabulary file must exist.
    vocab_path = wt / _VOCABULARY_REL
    if not vocab_path.is_file():
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"vocabulary file not found: {vocab_path}",
        )

    # Load vocabulary JSON and extract componentTypes.
    try:
        vocab_data = json.loads(vocab_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"failed to read vocabulary file: {exc}",
        )

    component_types: list[str] = vocab_data.get("componentTypes", [])
    if not component_types:
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message="vocabulary file has no componentTypes entries",
        )

    # Check that each component type has a fixture file.
    fixtures_dir = wt / _FIXTURES_DIR_REL
    missing: list[str] = []
    for ct in component_types:
        fixture_path = fixtures_dir / f"{ct}.json"
        if not fixture_path.is_file():
            missing.append(ct)

    if missing:
        details = "\n".join(
            f"  component type '{ct}' declared in vocabulary but no fixture at test-fixtures/components/{ct}.json"
            for ct in missing
        )
        return CheckResult(
            name=name,
            outcome="fail",
            duration_ms=0,
            message=f"{len(missing)} vocabulary component(s) lack test fixtures:\n{details}",
        )

    return CheckResult(
        name=name,
        outcome="pass",
        duration_ms=0,
        message=f"all {len(component_types)} vocabulary component types have fixtures",
    )
