"""Precommit check group: lint, format, design-guard, import-smoke.

Importing this package triggers registration of all precommit checks
via the ``@register_check`` decorator in each submodule.
"""

from __future__ import annotations

# Import submodules to trigger @register_check side effects.
from codehome.checks.precommit import (
    cli_hygiene,
    design_guard,
    imports,
    install_mode,
    plugin_imports,
    tools,
)

__all__ = ["cli_hygiene", "design_guard", "imports", "install_mode", "plugin_imports", "tools"]
