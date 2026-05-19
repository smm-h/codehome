# Migrate plugin CLI builder to strictcli

## Context

codehome's plugin system builds CLI commands dynamically from TOML manifests via `cli_builder.py`, which targets argparse. strictcli now supports all the features needed for this migration.

## What's available in strictcli

- Programmatic (non-decorator) registration: `app.command("name", help="...")(handler_func)` works
- type=float for manifest `type = "float"` fields
- Recursive nesting for manifest `[[commands.subcommands]]`
- MutexGroup for manifest `mutex_group` fields
- Choices validation for manifest `choices` fields
- Repeatable flags for manifest `action = "append"`
- Passthrough commands for manifest `passthrough = true`
- Tags for argument templates (manifest `includes`)

## What's NOT in strictcli (handle in codehome)

- `type = "path"` -- use `Flag(type=str, validate=...)` with Path conversion in handler
- `hidden = true` -- filter hidden flags from help output in codehome's builder
- `nargs` variants -- no actual plugin manifest uses nargs (can drop from manifest schema)
- `action = "count"` -- no actual plugin manifest uses count
- `action = "store_false"` -- map to `Flag(type=bool)` with inverted logic

## Migration approach

Replace `cli_builder.py`'s argparse backend with strictcli calls. The `build_commands()` function would call `app.command()(handler)` or `group.command()(handler)` instead of creating argparse subparsers.

## Effort

Medium. The builder is ~200 lines. The TOML manifest format stays the same -- only the backend changes.
