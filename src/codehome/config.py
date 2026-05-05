"""Repo configuration: load repos.toml and server config.

Each repo defines a remote URL, base branch, and optional staging branch.
Repo identity is encoded in the directory structure (repos/<repo>/branches/),
not in metadata files.

Repos come from two sources:
  - ROOT/.supervisor/repos.toml (legacy, paths under ROOT/repos/)
  - ~/.superv/projects.toml (new, paths under ~/.superv/projects/<name>/)

Both are merged by load_repos(). The project_dir field on RepoConfig
distinguishes them: None = legacy layout, set = new layout.

Server config lives at .supervisor/config.json, created by `v auth setup`.
"""

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codehome.paths import ROOT, SUPERVISOR_DIR, resolve_global, superv_home

REPOS_FILE = ROOT / ".supervisor" / "repos.toml"
SERVER_CONFIG_FILE = resolve_global("config.json")
DEFAULT_REPO = "bag"


# ---------------------------------------------------------------------------
# Server config (created by `v auth setup`)
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    port: int
    jwt_secret: str
    data_dir: str
    sentry_dsn: str = ""  # Optional Sentry DSN; empty means disabled.


def server_config_exists() -> bool:
    """Check whether the server config file exists."""
    return SERVER_CONFIG_FILE.is_file()


def load_server_config() -> ServerConfig | None:
    """Load server config from .supervisor/config.json.

    Returns None if the file doesn't exist (caller decides what to do).
    Raises ValueError if the file contains malformed JSON.
    """
    if not SERVER_CONFIG_FILE.is_file():
        return None
    text = SERVER_CONFIG_FILE.read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in {SERVER_CONFIG_FILE}: {exc}") from exc
    return ServerConfig(
        port=int(data["port"]),
        jwt_secret=str(data["jwt_secret"]),
        data_dir=str(data["data_dir"]),
        sentry_dsn=str(data.get("sentry_dsn", "")),
    )


# ---------------------------------------------------------------------------
# Repo config (repos.toml)
# ---------------------------------------------------------------------------


@dataclass
class RepoConfig:
    name: str
    remote: str
    base_branch: str
    staging_branch: str | None = None
    demo_branch: str | None = None
    default_linear_team: str | None = None
    # When set, paths derive from this directory instead of ROOT/repos/<name>.
    # Set for projects loaded from ~/.superv/projects.toml.
    project_dir: Path | None = None


def _load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file, returning {} if missing or malformed."""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def load_repos() -> dict[str, RepoConfig]:
    """Load all repo configs from repos.toml AND ~/.superv/projects.toml.

    Legacy repos (repos.toml) have project_dir=None; paths derive from
    ROOT/repos/<name>/.  New projects (projects.toml) have project_dir
    set to ~/.superv/projects/<name>/.

    On name collision, legacy repos.toml wins (it's the local install).
    """
    repos: dict[str, RepoConfig] = {}

    # --- New projects from ~/.superv/projects.toml ---
    home = superv_home()
    projects_file = home / "projects.toml"
    for name, data in _load_toml(projects_file).items():
        remote = data.get("remote", "")
        base = data.get("base_branch", "main")
        staging = data.get("staging_branch")
        demo = data.get("demo_branch")
        linear_team = data.get("default_linear_team")
        # project_dir: explicit path from projects.toml, or default location.
        pdir_str = data.get("path")
        pdir = Path(pdir_str) if pdir_str else home / "projects" / name
        repos[name] = RepoConfig(
            name=name,
            remote=remote,
            base_branch=base,
            staging_branch=staging,
            demo_branch=demo,
            default_linear_team=linear_team,
            project_dir=pdir,
        )

    # --- Legacy repos from ROOT/.supervisor/repos.toml (wins on collision) ---
    for name, data in _load_toml(REPOS_FILE).items():
        remote = data.get("remote", "")
        base = data.get("base_branch", "main")
        staging = data.get("staging_branch")
        demo = data.get("demo_branch")
        linear_team = data.get("default_linear_team")
        repos[name] = RepoConfig(
            name=name,
            remote=remote,
            base_branch=base,
            staging_branch=staging,
            demo_branch=demo,
            default_linear_team=linear_team,
            # project_dir=None: paths derive from REPOS_DIR / name.
        )

    return repos


def get_repo(name: str) -> RepoConfig:
    """Get a repo config by name. Dies if not found."""
    repos = load_repos()
    if name not in repos:
        available = ", ".join(sorted(repos)) if repos else "(none configured)"
        sys.stderr.write(f"error: unknown repo '{name}'\n")
        sys.stderr.write(f"  available: {available}\n")
        sys.stderr.write(f"  config: {REPOS_FILE}\n")
        sys.exit(1)
    return repos[name]


def list_repos() -> list[RepoConfig]:
    """Return all configured repos, sorted by name."""
    return sorted(load_repos().values(), key=lambda r: r.name)
