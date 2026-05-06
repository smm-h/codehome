"""Scope resolution -- maps (plugin, scope, repo?, branch?) to a filesystem path."""

from enum import StrEnum
from pathlib import Path

from codehome.paths import SUPERVISOR_DIR, codehome_home


class Scope(StrEnum):
    GLOBAL = "global"  # ~/.codehome/plugin-data/<plugin>/
    REPO = "repo"  # <repo_dir>/.supervisor/plugins/<plugin>/
    BRANCH = "branch"  # <repo_dir>/branches/<branch>/.supervisor/plugins/<plugin>/
    SECRET = "secret"  # ~/.codehome/credentials/<plugin>/


def resolve_path(
    plugin: str,
    scope: Scope,
    *,
    repo: str | None = None,
    branch: str | None = None,
) -> Path:
    """Resolve the storage directory for a plugin at a given scope.

    GLOBAL and SECRET scopes prefer ~/.codehome/ (new layout), falling back
    to .supervisor/ (legacy) when the new directory does not exist yet.
    """
    match scope:
        case Scope.GLOBAL:
            new = codehome_home() / "plugin-data" / plugin
            if new.exists():
                return new
            legacy = SUPERVISOR_DIR / "plugins" / plugin
            if legacy.exists():
                return legacy
            return new  # default to new location
        case Scope.REPO:
            if not repo:
                raise ValueError("repo required for REPO scope")
            from codehome.supervisor.paths import repo_dir

            return repo_dir(repo) / ".supervisor" / "plugins" / plugin
        case Scope.BRANCH:
            if not repo or not branch:
                raise ValueError("repo and branch required for BRANCH scope")
            from codehome.supervisor.paths import branch_dir

            return branch_dir(repo, branch) / ".supervisor" / "plugins" / plugin
        case Scope.SECRET:
            new = codehome_home() / "credentials" / plugin
            if new.exists():
                return new
            legacy = SUPERVISOR_DIR / "credentials" / plugin
            if legacy.exists():
                return legacy
            return new  # default to new location
        case _:
            raise ValueError(f"Unknown scope: {scope}")
