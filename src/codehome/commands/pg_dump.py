"""Re-export stub: pg_dump command moved to plugins/core/commands/pg_dump.py.

This module exists solely so that serve/ and supabase plugin code can continue
importing from codehome.commands.pg_dump.
"""

from codehome.dynamic_import import import_module_from_path
from codehome.paths import ROOT

_mod = import_module_from_path(
    "_core_cmd_pg_dump",
    ROOT / "plugins" / "core" / "commands" / "pg_dump.py",
)

_generate_full_md = _mod._generate_full_md
cmd_pgdump = _mod.cmd_pgdump
DB_CONTAINER = _mod.DB_CONTAINER
DB_URL_INTERNAL = _mod.DB_URL_INTERNAL
