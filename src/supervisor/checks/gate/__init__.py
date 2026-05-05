"""Gate checks: thorough checks run before push (pre-push hook).

Each submodule registers its checks via ``@register_check`` on import.
Importing this package triggers all submodule imports so every gate
check is registered in the global registry.
"""

from __future__ import annotations

# Import all gate check submodules so their @register_check decorators
# fire and populate the registry.
from supervisor.checks.gate import (
    dead_code,
    frontend_tools,
    health,
    incantino_analyzer,
    lisa_config,
    plugin_consistency,
    python_tools,
    stale_deps,
    supabase_config,
)

__all__ = [
    "dead_code",
    "frontend_tools",
    "health",
    "incantino_analyzer",
    "lisa_config",
    "plugin_consistency",
    "python_tools",
    "stale_deps",
    "supabase_config",
]
