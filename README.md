# codehome

Plugin-based worktree manager and dev tool orchestrator.

## Install

```
uv tool install codehome
```

## What it does

codehome is a minimal core that loads plugins to provide functionality:
- Branch and worktree management (git worktrees)
- Plugin discovery and loading
- Check system (precommit, gate)
- Dev server infrastructure

Without plugins, codehome is a framework. Plugins provide the actual commands.

## Plugin Development

Create a directory with `plugin.toml` + `handlers.py`. Place it in `~/.codehome/plugins/` or in your project's `codehome/plugins/`.
