# codehome

A plugin runtime for developer tools. The core provides a CLI, a FastAPI server, an event bus, state stores (scoped to branch/repo/project/global), a check framework, and a Svelte 5 dashboard. Plugins supply the actual functionality: commands, API routes, checks, event handlers, and UI panels. Plugin discovery is manifest-driven with lazy loading and dependency sorting.

- **Backend**: Python 3.11+ (FastAPI, Pydantic, structlog, JWT auth, Docker SDK, MCP support)
- **Frontend**: Svelte 5 + Vite (theming, SDUI rendering, auth)
- **Dual-published**: NPM (dashboard) + PyPI (backend)
- **Version**: 0.1.x (early alpha)

## Release workflow

This project uses [rlsbl](https://github.com/smm-h/rlsbl) for release orchestration.

- Update CHANGELOG.md with a `## X.Y.Z` entry describing changes
- Run `rlsbl release [patch|minor|major]` to bump version and create a GitHub Release
- CI handles publishing automatically via the publish workflow
- Never publish manually — always use `rlsbl release`
- Requires NPM_TOKEN secret on GitHub (Settings > Secrets > Actions)
- Use `rlsbl release --dry-run` to preview a release without making changes

## Configuration

Global config lives at `~/.codehome/config.toml`. See `config.toml.example` for the annotated format.

Key settings:
- `[plugins] paths` -- list of directories to scan for plugins. If unset (or no config.toml exists), falls back to `ROOT/plugins/`. `~/.codehome/plugins/` is always scanned as a second tier; configured paths take priority on name collision.

## Conventions

- No tokens or secrets in command-line arguments (use env vars or config files)
- All file writes to shared state should be atomic (write to tmp, then rename)
- External calls (APIs, CLI tools) must have timeouts and graceful fallbacks
- Use `npm link` (npm) or `uv pip install -e .` (Python) for local development
- CI runs smoke tests on every push; manual testing for UI/UX changes
