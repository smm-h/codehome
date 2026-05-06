"""Re-export stub: fin command moved to plugins/supervisor/commands/fin.py.

This module exists solely so that serve/ code can continue importing
close_branch from codehome.commands.fin.
"""

from codehome.dynamic_import import import_module_from_path
from codehome.paths import ROOT

_mod = import_module_from_path(
    "_supervisor_cmd_fin",
    ROOT / "plugins" / "supervisor" / "commands" / "fin.py",
)

close_branch = _mod.close_branch
