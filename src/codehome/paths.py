"""Core path constants and helpers for the codehome CLI.

This module contains only home/root/global-state paths.
Repo and branch path helpers live in codehome.supervisor.paths.
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
    2. __file__-based computation (development/editable install mode)
    """
    env_root = os.environ.get("CODEHOME_ROOT")
    if env_root:
        p = Path(env_root).resolve()
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent.parent.parent


ROOT = _compute_root()

# Shared infrastructure (not per-repo).
SUPERVISOR_DIR = ROOT / ".supervisor"
EVENTS_DIR = SUPERVISOR_DIR / "events"
CACHE_DIR = SUPERVISOR_DIR / "cache"
LINEAR_CACHE = CACHE_DIR / "linear.json"
WORKFLOWS_CACHE = CACHE_DIR / "workflows.json"
ENV_FILE = ROOT / ".env"

IGNORABLE_FILE = ROOT / "scripts" / "ignorable-worktree-changes"


def resolve_global(rel: str) -> Path:
    """Resolve a global state file: prefer ~/.codehome/, fall back to .supervisor/.

    This enables gradual migration from the legacy .supervisor/ layout
    to the new ~/.codehome/ home directory.
    """
    new = codehome_home() / rel
    if new.exists():
        return new
    return SUPERVISOR_DIR / rel


# Operational state files -- resolved via resolve_global() so they are
# found in either ~/.codehome/ (new layout) or .supervisor/ (legacy).
SUPPRESS_CHECKS = resolve_global("suppress-checks.txt")
STAGING_MERGE_STATE = resolve_global("staging-merge.json")
REBASE_STATE = resolve_global("rebase-state.json")


# Files excluded from diff/summary output (local dev noise).
NOISE_PATTERNS = [".env.local", "*/package-lock.json"]

# Supabase migration file detection.
MIGRATIONS_DIR = "supabase/migrations"

# Branches that are base branches, not feature work.
PROTECTED_BRANCHES = {"production", "staging", "demo", "main"}
