"""Plugin SDK -- the stable API surface for plugins.

Plugins should import ONLY from this module. Direct imports from
codehome.* internals are deprecated and will be enforced by lint
in Phase 4.

Provides: paths, resolution, git, config, formatting, events (bus),
state (Config/State/Files), services (RPC), locking, CLI helpers,
and FastAPI auth dependencies.
"""

from __future__ import annotations

import types

# -- CLI defaults (module re-export: plugins use `cli_defaults.get(key)`) --
from codehome import cli_defaults

# -- Bus (event system) --
from codehome.bus import Event, fire, fire_sync, on

# -- CLI construction & helpers --
from codehome.cli import build_parser
from codehome.cli_helpers import add_branch_flag, add_deploy_flags, dispatch_subcommand, resolve_optional
from codehome.config import RepoConfig, get_repo, load_server_config
from codehome.config import (  # Original name is load_repos; renamed to list_repos for SDK
    load_repos as list_repos,
)
from codehome.dispatch import dispatched, server_running, server_url

# -- Git operations --
from codehome.git import (
    delete_local_branch,
    delete_remote_branch,
    gh_api,
    gh_repo,
    git,
    git_passthrough,
    is_worktree_locked,
    list_worktrees,
    require_fresh,
    require_unlocked,
)
from codehome.http_client import TOKEN_FILE

# -- Paths --
from codehome.paths import (
    PROTECTED_BRANCHES,
    REPOS_DIR,
    ROOT,
    STAGING_MERGE_STATE,
    SUPERVISOR_DIR,
    base_ref,
    branch_dir,
    branch_name_from_wt,
    prod_ref,
    repo_anchor,
    repo_branches,
    repo_dir,
    resolve_global,
    staging_worktree,
    superv_home,
    tests_file,
    worktree_path,
)
from codehome.resolution import (
    BranchContext,
    active_context,
    parse_qualified,
    resolve,
)
from codehome.serve import read_server_url

# -- FastAPI auth dependencies (for plugin routes) --
from codehome.serve.auth_deps import get_current_user
from codehome.serve.dependencies import get_gh_token

# -- SDUI streaming command types + CLI runner --
from codehome.serve.sdui.cli_runner import run_command
from codehome.serve.sdui.commands import CommandError, CommandProgress, CommandResult
from codehome.session import get_process_id

# -- State (storage APIs) --
from codehome.state import ConfigStore, FileStore, Scope, StateStore, lock, services
from codehome.utils import (
    USE_COLOR,
    atomic_json_write,
    blue,
    bold,
    cyan,
    die,
    dim,
    green,
    load_json,
    magenta,
    open_url,
    red,
    render_box_table,
    warn,
    yellow,
)


def load_sibling(name: str, caller_file: str, *, cache: bool = False) -> types.ModuleType:
    """Dynamically import a sibling module from the same directory as *caller_file*.

    Plugins consist of loose .py files (not packages), so normal relative
    imports don't work.  This helper replaces the per-plugin ``_sibling()``
    pattern with a single SDK function.

    *cache*: if True, store the module in ``sys.modules`` so subsequent
    calls return the same object (useful for singletons like orchestrators).
    """
    import importlib.util
    import sys
    from pathlib import Path

    directory = Path(caller_file).resolve().parent
    path = directory / f"{name}.py"
    module_name = f"_plugin_{directory.name}_{name}"
    if cache and module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load sibling module {path}")
    mod = importlib.util.module_from_spec(spec)
    # Always register before exec_module: Python 3.13's @dataclass (and other
    # metaclass-based decorators) calls sys.modules.get(cls.__module__) during
    # class creation, which fails with AttributeError if the module isn't
    # registered yet.
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # On failure, don't leave a broken module in sys.modules.
        sys.modules.pop(module_name, None)
        raise
    if not cache:
        # Non-cached modules can still be found by name if needed, but
        # remove them to avoid accumulating stale refs on repeated loads.
        sys.modules.pop(module_name, None)
    return mod


__all__ = [
    "PROTECTED_BRANCHES",
    "REPOS_DIR",
    "ROOT",
    "STAGING_MERGE_STATE",
    "SUPERVISOR_DIR",
    "TOKEN_FILE",
    "USE_COLOR",
    "BranchContext",
    "CommandError",
    "CommandProgress",
    "CommandResult",
    "ConfigStore",
    "Event",
    "FileStore",
    "RepoConfig",
    "Scope",
    "StateStore",
    "active_context",
    "add_branch_flag",
    "add_deploy_flags",
    "atomic_json_write",
    "base_ref",
    "blue",
    "bold",
    "branch_dir",
    "branch_name_from_wt",
    "build_parser",
    "cli_defaults",
    "cyan",
    "delete_local_branch",
    "delete_remote_branch",
    "die",
    "dim",
    "dispatch_subcommand",
    "dispatched",
    "fire",
    "fire_sync",
    "get_current_user",
    "get_gh_token",
    "get_process_id",
    "get_repo",
    "gh_api",
    "gh_repo",
    "git",
    "git_passthrough",
    "green",
    "is_worktree_locked",
    "list_repos",
    "list_worktrees",
    "load_json",
    "load_server_config",
    "load_sibling",
    "lock",
    "magenta",
    "on",
    "open_url",
    "parse_qualified",
    "prod_ref",
    "read_server_url",
    "red",
    "render_box_table",
    "repo_anchor",
    "repo_branches",
    "repo_dir",
    "require_fresh",
    "require_unlocked",
    "resolve",
    "resolve_global",
    "resolve_optional",
    "run_command",
    "server_running",
    "server_url",
    "services",
    "staging_worktree",
    "superv_home",
    "tests_file",
    "warn",
    "worktree_path",
    "yellow",
]
