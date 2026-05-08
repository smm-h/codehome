"""Re-export stub: pg_dump command moved to plugins/core/commands/pg_dump.py.

Uses lazy __getattr__ so the core plugin directory is resolved on first
access, not at import time.
"""

_EXPORTS = ("_generate_full_md", "cmd_pgdump", "DB_CONTAINER", "DB_URL_INTERNAL")


def __getattr__(name: str):
    if name in _EXPORTS:
        from codehome.commands import _load_core_command

        return getattr(_load_core_command("_core_cmd_pg_dump", "pg_dump.py"), name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
