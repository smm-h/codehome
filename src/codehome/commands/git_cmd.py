"""Re-export stub: git_cmd command moved to plugins/core/commands/git_cmd.py.

Uses lazy __getattr__ so the core plugin directory is resolved on first
access, not at import time.
"""

_EXPORTS = (
    "_conflict_file_list",
    "_detect_skip_worktree",
    "_is_rebase_in_progress",
    "_remigrate",
    "_restore_skip_worktree",
    "_save_rebase_state",
)


def __getattr__(name: str):
    if name in _EXPORTS:
        from codehome.commands import _load_core_command

        return getattr(_load_core_command("_core_cmd_git_cmd", "git_cmd.py"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
