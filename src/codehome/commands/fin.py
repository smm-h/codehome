"""Re-export stub: fin command moved to plugins/core/commands/fin.py.

Uses lazy __getattr__ so the core plugin directory is resolved on first
access, not at import time.
"""

_EXPORTS = ("close_branch",)


def __getattr__(name: str):
    if name in _EXPORTS:
        from codehome.commands import _load_core_command

        return getattr(_load_core_command("_core_cmd_fin", "fin.py"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
