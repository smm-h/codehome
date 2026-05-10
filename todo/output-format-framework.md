# Output format framework for CLI commands

## Context

CLI commands currently print output in ad-hoc formats — some use `--json` flags, some print plain text, some print markdown. There's no standard way for a command to declare what output formats it supports, or for the framework to render structured data consistently.

This was discussed as Phase 2 of the declarative CLI rework (Phase 1 shipped: declarative arguments, templates, lazy loading). The user wants to formalize endpoints that can return multiple output types, enabling content negotiation.

## Design decisions (already made)

- **Handlers return structured data**, the framework renders it (not the handler)
- **Typed result objects**: define a `CommandResult` class with fields like rows, columns, metadata
- **Formats**: json, csv, md, html (and text as a rare escape hatch)
- **text is an escape hatch** — allowed but should be used infrequently. Most commands should return structured data
- **Manifest-level declaration**: commands declare supported output formats in plugin.toml
- **Framework adds `--output`/`-o` flag** automatically based on declared formats
- Connects to a broader goal of content negotiation for API endpoints (not just CLI)

## Open questions

- What fields does `CommandResult` need? Options discussed:
  - `rows: list[dict]` + `columns: list[str]` + `metadata: dict` (tabular)
  - Single `data: Any` field with shape detection (flexible but unpredictable)
  - Multiple result types: `TableResult`, `TreeResult`, `ScalarResult` (most precise)
- How does the framework detect whether a handler returned structured data vs. printed directly (escape hatch)?
- Should the `--output` flag be added globally or per-command?
- How does this interact with the server API? Same result objects rendered differently for CLI vs. HTTP?
- CSV rendering: who decides column order? The handler (via columns field) or alphabetical?
- HTML rendering: what template? Minimal table? Full page with styles?

## Affected files

- `src/codehome/plugins/manifest.py` — add output format declaration to CommandDecl
- `src/codehome/plugins/cli_builder.py` — auto-add --output flag when formats declared
- New module: `src/codehome/output/` — renderers for json, csv, md, html, text
- New module: `src/codehome/output/result.py` — CommandResult types
- All plugins' plugin.toml — add output format declarations to commands
- All plugins' handlers — refactor to return CommandResult instead of printing

## Effort

Medium-large. Framework changes are moderate (result types, renderers, CLI integration). Plugin migration is the bulk: every handler that currently prints needs to be refactored to return structured data. Can be done incrementally — commands without output declarations keep working as-is (escape hatch).
