"""Scope resolution -- maps (plugin, scope, repo?, branch?) to a filesystem path."""

from enum import StrEnum
from pathlib import Path

from codehome.paths import STATE_DIR, codehome_home


class Scope(StrEnum):
    GLOBAL = "global"  # ~/.codehome/plugin-data/<plugin>/
    REPO = "repo"  # <repo_dir>/.codehome/plugins/<plugin>/
    BRANCH = "branch"  # <repo_dir>/branches/<branch>/.codehome/plugins/<plugin>/
    SECRET = "secret"  # ~/.codehome/credentials/<plugin>/


def _require_layout() -> object:
    """Resolve the ProjectLayout service, raising if unavailable."""
    from codehome.service_protocols import ProjectLayout
    from codehome.state.service_registry import services

    layout = services.get_typed("core.layout", ProjectLayout)
    if layout is None:
        raise RuntimeError("ProjectLayout not registered (core plugin not loaded)")
    return layout


def resolve_path(
    plugin: str,
    scope: Scope,
    *,
    repo: str | None = None,
    branch: str | None = None,
) -> Path:
    """Resolve the storage directory for a plugin at a given scope.

    GLOBAL and SECRET scopes prefer ~/.codehome/ (new layout), falling back
    to .codehome/ (project-local) when the new directory does not exist yet.
    """
    match scope:
        case Scope.GLOBAL:
            new = codehome_home() / "plugin-data" / plugin
            if new.exists():
                return new
            legacy = STATE_DIR / "plugins" / plugin
            if legacy.exists():
                return legacy
            return new  # default to new location
        case Scope.REPO:
            if not repo:
                raise ValueError("repo required for REPO scope")
            return _require_layout().repo_dir(repo) / ".codehome" / "plugins" / plugin
        case Scope.BRANCH:
            if not repo or not branch:
                raise ValueError("repo and branch required for BRANCH scope")
            return _require_layout().branch_dir(repo, branch) / ".codehome" / "plugins" / plugin
        case Scope.SECRET:
            new = codehome_home() / "credentials" / plugin
            if new.exists():
                return new
            legacy = STATE_DIR / "credentials" / plugin
            if legacy.exists():
                return legacy
            return new  # default to new location
        case _:
            raise ValueError(f"Unknown scope: {scope}")
