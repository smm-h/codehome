"""Re-export stub: branch command moved to plugins/supervisor/commands/branch.py.

This module exists solely so that serve/ code (which must not be modified)
can continue importing BranchError, create_branch, _sync, rename_branch
from codehome.commands.branch.
"""

from codehome.dynamic_import import import_module_from_path
from codehome.paths import ROOT

_mod = import_module_from_path(
    "_supervisor_cmd_branch",
    ROOT / "plugins" / "supervisor" / "commands" / "branch.py",
)

BranchError = _mod.BranchError
create_branch = _mod.create_branch
_sync = _mod._sync
rename_branch = _mod.rename_branch
