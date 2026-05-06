# codehome

Plugin runtime for dev tools. Install it, point it at a plugin directory, and it loads whatever functionality you need.

## Install

```
uv tool install codehome
```

## What it provides

codehome is a minimal core that provides infrastructure for plugins:

- **Server** -- FastAPI dev server with JWT auth, SSE events, SDUI dashboard, plugin route mounting
- **Plugin system** -- discovery, loading, dependency sorting, namespace mounting, manifest-driven registration
- **Event bus** -- publish/subscribe for async plugin communication
- **Check framework** -- registry, concurrent runner with dependency-aware scheduling, formatter
- **State stores** -- config, file, and state stores with scoping (branch, repo, project, global)
- **SDK** -- lazy re-exports of common utilities for plugin authors
- **CLI** -- plugin management, auth, server lifecycle, project init

Without plugins, codehome is a running server with an empty dashboard. Plugins provide commands, API routes, checks, and event handlers.

## Core commands

| Command | Purpose |
|---------|---------|
| `codehome home` | Show/create the ~/.codehome/ directory |
| `codehome init <name> --remote <url>` | Set up a managed project |
| `codehome plugins list\|enable\|disable\|install` | Manage plugins |
| `codehome auth login\|logout\|setup` | Authenticate with the dev server |
| `codehome server [start\|stop\|status\|restart]` | Run the dev server |
| `codehome migrate` | Migrate state from legacy layout |

## Plugin development

Create a directory with `plugin.toml` + `handlers.py`. Place it anywhere and add the path to `~/.codehome/config.toml`:

```toml
[plugins]
paths = [
    "~/my-plugins",
]
```

Plugins can provide:

- **CLI commands** via `handlers.py` with a `register_cli()` function
- **API routes** via `routes.py` with a FastAPI `router`
- **Checks** via `checks.py` with async check functions
- **Event handlers** via `events.toml` with lazy-loaded subscribers
- **Namespace mounting** via `namespace = "myname"` in `plugin.toml` (makes `codehome.myname.*` resolve to the plugin directory)

See `plugin.toml` manifest format:

```toml
name = "my-plugin"
version = "0.1.0"
description = "What it does"

[[commands]]
name = "mycommand"
handler = "cmd_mycommand"
description = "A CLI command"

[[checks]]
name = "my-check"
group = "gate"
timeout = 30
handler = "check_my_thing"

[dashboard]
group = "root"
route = "/my-plugin"
```
