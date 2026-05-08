"""Re-export stub: git_cmd command moved to plugins/core/commands/git_cmd.py.

This module exists solely so that serve/ code can continue importing
rebase internals from codehome.commands.git_cmd.
"""

from codehome.dynamic_import import import_module_from_path
from codehome.paths import ROOT

_mod = import_module_from_path(
    "_core_cmd_git_cmd",
    ROOT / "plugins" / "core" / "commands" / "git_cmd.py",
)

_conflict_file_list = _mod._conflict_file_list
_detect_skip_worktree = _mod._detect_skip_worktree
_is_rebase_in_progress = _mod._is_rebase_in_progress
_remigrate = _mod._remigrate
_restore_skip_worktree = _mod._restore_skip_worktree
_save_rebase_state = _mod._save_rebase_state
