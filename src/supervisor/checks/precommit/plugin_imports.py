"""Plugin import guard: prevent bare ``from plugins.*`` imports.

Plugin handler files are loaded via ``importlib.util.spec_from_file_location``
with synthetic module names -- the ``plugins/`` directory is not a Python
package (no ``__init__.py``), nor is it on ``sys.path``.  Any ``from plugins.X``
or ``import plugins.X`` statement in production code will crash at runtime.

The correct pattern is ``load_sibling("module", __file__)`` from
``supervisor.sdk``.

Test files (``tests/``) are excluded because their ``conftest.py`` adds
the project root to ``sys.path``, making ``from plugins.*`` valid in tests.
"""

from __future__ import annotations

import re

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult

_BARE_PLUGIN_IMPORT = re.compile(r"^\s*(?:from|import)\s+plugins\.")


@register_check("plugin-import-guard", group="precommit", timeout=5)
async def plugin_import_guard(ctx: CheckContext) -> CheckResult:
    """Flag bare ``from plugins.*`` imports in staged plugin files."""
    if not ctx.staged_files:
        return CheckResult(
            name="plugin-import-guard",
            outcome="pass",
            duration_ms=0,
            message="no staged files",
        )

    hits: list[str] = []
    for path in ctx.staged_files:
        # Only check .py files under plugins/, excluding tests/.
        if not path.startswith("plugins/") or not path.endswith(".py"):
            continue
        if "/tests/" in path or path.endswith("/conftest.py"):
            continue

        full = ctx.root / path
        if not full.is_file():
            continue

        for lineno, line in enumerate(full.read_text().splitlines(), 1):
            if _BARE_PLUGIN_IMPORT.match(line):
                hits.append(f"  {path}:{lineno}: {line.strip()}")

    if hits:
        return CheckResult(
            name="plugin-import-guard",
            outcome="fail",
            duration_ms=0,
            message=(
                "Bare 'from plugins.*' imports will crash at runtime.\n"
                "Use load_sibling() from supervisor.sdk instead:\n" + "\n".join(hits)
            ),
            fix="from supervisor.sdk import load_sibling; mod = load_sibling('name', __file__)",
        )

    return CheckResult(
        name="plugin-import-guard",
        outcome="pass",
        duration_ms=0,
        message="no bare plugin imports",
    )
