"""Path constants and helpers for the codehome CLI.

Multi-repo layout: repos/<repo>/branches/<branch>/worktree/.
Each repo has its own anchor clone, archive, todo, and aliases.
"""

import os
import shutil
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

REPOS_DIR = ROOT / "repos"

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

# Items that make up a branch's metadata (everything outside the worktree/).
# Used by transfer_metadata() for archiving (fin) and renaming (branch).
BRANCH_METADATA_ITEMS = (
    "docs",
    "scripts",
    "progress.md",
    "todo",
    "tests.json",
    "issue.json",
    "review.md",
    "prs.json",
    "push.json",
    "context.md",
    "branch.json",
    ".services.json",
    ".git",
    ".gitignore",
)


def transfer_metadata(src_dir: Path, dst_dir: Path, *, move: bool = False, verbose: bool = False) -> None:
    """Copy or move branch metadata items from src_dir to dst_dir.

    When move=False (default): copies items (used by v branch finalize to archive).
    When move=True: moves items via rename for files, copytree+rmtree
    for directories (used by v branch rename).
    """
    for item in BRANCH_METADATA_ITEMS:
        src = src_dir / item
        dst = dst_dir / item
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                if move:
                    # Merge into existing dst, then remove src.
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    shutil.rmtree(src)
                else:
                    shutil.rmtree(dst)
                    shutil.copytree(src, dst)
            else:
                if move:
                    src.rename(dst)
                else:
                    shutil.copytree(src, dst)
        else:
            if move:
                src.rename(dst)
            else:
                shutil.copy2(src, dst)
        if verbose:
            action = "moved" if move else "copied"
            print(f"  {item}: {action}")


# ---------------------------------------------------------------------------
# Repo-level paths
# ---------------------------------------------------------------------------


def repo_dir(repo: str) -> Path:
    """Top-level directory for a repo.

    For legacy repos (repos.toml): ROOT/repos/<repo>/
    For new projects (projects.toml): ~/.codehome/projects/<name>/
    """
    # Lazy import to avoid circular dependency (paths -> config -> paths).
    from codehome.config import get_repo

    cfg = get_repo(repo)
    if cfg.project_dir:
        return cfg.project_dir
    return REPOS_DIR / repo


def repo_branches(repo: str) -> Path:
    """Branch directory for a repo: <repo_dir>/branches/."""
    return repo_dir(repo) / "branches"


def repo_archive(repo: str) -> Path:
    """Archive directory for a repo: <repo_dir>/archive/."""
    return repo_dir(repo) / "archive"


def repo_todo(repo: str) -> Path:
    """Repo-level TODO directory: <repo_dir>/todo/."""
    return repo_dir(repo) / "todo"


def repo_aliases_file(repo: str) -> Path:
    """Alias file for a repo: <repo_dir>/aliases.txt."""
    return repo_dir(repo) / "aliases.txt"


# ---------------------------------------------------------------------------
# Anchor clone and staging paths
# ---------------------------------------------------------------------------


def repo_anchor(repo: str) -> Path:
    """Anchor clone (base branch worktree) for a repo.

    Reads the repo's base_branch from config to find the right directory.
    e.g. repos/bag/branches/production/worktree/ for bag,
         repos/chat/branches/main/worktree/ for chat.
    """
    from codehome.config import get_repo

    cfg = get_repo(repo)
    return repo_branches(repo) / cfg.base_branch / "worktree"


def staging_worktree(repo: str) -> Path:
    """Ephemeral staging worktree: repos/<repo>/branches/.staging/worktree/."""
    return repo_branches(repo) / ".staging" / "worktree"


def prod_ref(repo: str) -> str:
    """Remote-tracking ref for the repo's base branch."""
    from codehome.config import get_repo

    cfg = get_repo(repo)
    return f"origin/{cfg.base_branch}"


def base_ref(repo: str, branch: str) -> str:
    """Return the diff base ref for a branch (production ref)."""
    return prod_ref(repo)


def staging_ref(repo: str) -> str:
    """Remote-tracking ref for the repo's staging branch."""
    from codehome.config import get_repo

    cfg = get_repo(repo)
    if not cfg.staging_branch:
        return ""
    return f"origin/{cfg.staging_branch}"


def demo_ref(repo: str) -> str:  # noqa: dead-code
    """Remote ref for the demo branch."""
    from codehome.config import get_repo

    cfg = get_repo(repo)
    if not cfg.demo_branch:
        return ""
    return f"origin/{cfg.demo_branch}"


# ---------------------------------------------------------------------------
# Branch-level paths (require both repo and branch)
# ---------------------------------------------------------------------------


def branch_dir(repo: str, branch: str) -> Path:
    """Context directory for a branch: repos/<repo>/branches/<branch>/."""
    return repo_branches(repo) / branch


def worktree_path(repo: str, branch: str) -> Path:
    """Git worktree: repos/<repo>/branches/<branch>/worktree/."""
    return repo_branches(repo) / branch / "worktree"


def docs_path(repo: str, branch: str) -> Path:
    return repo_branches(repo) / branch / "docs"


def progress_file(repo: str, branch: str) -> Path:
    return repo_branches(repo) / branch / "progress.md"


def context_file(repo: str, branch: str) -> Path:
    return repo_branches(repo) / branch / "context.md"


def tests_file(repo: str, branch: str) -> Path:
    return repo_branches(repo) / branch / "tests.json"


def branch_name_from_wt(wt_path: Path) -> str:
    """Extract branch name from a worktree path.

    All worktrees: repos/<repo>/branches/<name>/worktree/ -> <name>
    """
    return wt_path.parent.name if wt_path.name == "worktree" else wt_path.name


# ---------------------------------------------------------------------------
# Area maps (topical grouping for summaries, per-repo)
# ---------------------------------------------------------------------------


def area_map(repo: str) -> list[tuple[str, str]]:
    """Area mapping for topical grouping in summaries. First match wins.

    Used by `v git diff` and `v git changes` to group files by area for readability.
    Maps are per-repo; paths not matching any prefix fall into "Other".
    """
    if repo == "bag":
        return [
            ("bag.veliu.com/", "bag.veliu.com"),
            ("orders.bag.veliu.com/", "orders.bag.veliu.com"),
            ("backoffice.veliu.com/", "backoffice.veliu.com"),
            ("drops.veliu.com/", "drops.veliu.com"),
            ("supabase/migrations/", "Migrations"),
            ("supabase/functions/", "Edge Functions"),
            ("supabase/", "Supabase config"),
            (".github/", "CI/CD"),
        ]
    if repo == "chat":
        return [
            ("bot/", "Bot"),
            ("tests/", "Tests"),
            (".github/", "CI/CD"),
        ]
    if repo == "infra":
        return [
            ("terraform/", "Terraform"),
            (".github/", "CI/CD"),
        ]
    return []
