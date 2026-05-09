"""Core path constants and helpers for the codehome CLI.

This module contains only home/root/global-state paths.
Repo and branch path helpers live in codehome.core.paths.
"""

import os
from pathlib import Path


def codehome_home() -> Path:
    """Return the global codehome home directory (~/.codehome/).

    Respects CODEHOME_HOME env var for testing and custom layouts.
    Does NOT create the directory -- callers create on demand.
    """
    env = os.environ.get("CODEHOME_HOME")
    if env:
        return Path(env).resolve()
    return Path.home() / ".codehome"


def _compute_root() -> Path:
    """Compute the project root directory.

    Priority:
    1. CODEHOME_ROOT env var (explicit override)
    2. Walk up from CWD looking for .codehome/repos.toml (project marker)
    3. __file__-based computation (development/editable install mode)

    Step 2 handles the repo-split scenario: the codehome package lives
    in a separate repo (~/Projects/codehome/) while the project root
    with repos.toml and plugins/ lives elsewhere (e.g. ~/Work/super/).
    Walking from CWD mirrors how git discovers .git/.
    """
    env_root = os.environ.get("CODEHOME_ROOT")
    if env_root:
        p = Path(env_root).resolve()
        if p.is_dir():
            return p

    # Walk up from CWD looking for the project-local state directory
    # containing repos.toml -- the definitive project marker.
    try:
        cwd = Path.cwd().resolve()
        for parent in [cwd, *cwd.parents]:
            if (parent / ".codehome" / "repos.toml").is_file():
                return parent
    except OSError:
        pass

    return Path(__file__).resolve().parent.parent.parent


ROOT = _compute_root()

# Shared infrastructure (not per-repo).
STATE_DIR = ROOT / ".codehome"
EVENTS_DIR = STATE_DIR / "events"
CACHE_DIR = STATE_DIR / "cache"
LINEAR_CACHE = CACHE_DIR / "linear.json"
WORKFLOWS_CACHE = CACHE_DIR / "workflows.json"
ENV_FILE = ROOT / ".env"

IGNORABLE_FILE = ROOT / "scripts" / "ignorable-worktree-changes"


def resolve_global(rel: str) -> Path:
    """Resolve a global state file: prefer ~/.codehome/, fall back to .codehome/.

    This enables gradual migration from the project-local .codehome/ layout
    to the user-global ~/.codehome/ home directory.
    """
    new = codehome_home() / rel
    if new.exists():
        return new
    return STATE_DIR / rel


# Operational state files -- resolved via resolve_global() so they are
# found in either ~/.codehome/ (new layout) or .codehome/ (project-local).
SUPPRESS_CHECKS = resolve_global("suppress-checks.txt")
STAGING_MERGE_STATE = resolve_global("staging-merge.json")
REBASE_STATE = resolve_global("rebase-state.json")


# Files excluded from diff/summary output (local dev noise).
NOISE_PATTERNS = [".env.local", "*/package-lock.json"]

# Supabase migration file detection.
MIGRATIONS_DIR = "supabase/migrations"

# Branches that are base branches, not feature work.
PROTECTED_BRANCHES = {"production", "staging", "demo", "main"}
