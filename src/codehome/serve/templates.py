"""Service template resolution.

Read .services.template.json / .services.json, resolve placeholders,
and produce ServiceInstance registrations.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from codehome.paths import branch_dir, repo_dir, worktree_path

if TYPE_CHECKING:
    from pathlib import Path


def _services_json_path(repo: str, branch: str) -> Path:
    """Path to the branch-level service config."""
    return branch_dir(repo, branch) / ".services.json"


def _template_path(repo: str) -> Path:
    """Path to the repo-level service template."""
    return repo_dir(repo) / ".services.template.json"


def load_services_config(repo: str, branch: str) -> list[dict[str, Any]] | None:
    """Load .services.json for a branch, returning the service list or None.

    If the branch has no .services.json but the repo has a template,
    copies the template on-the-fly (backfill for existing branches).
    """
    cfg_path = _services_json_path(repo, branch)

    # Backfill: copy template if branch config is missing.
    if not cfg_path.exists():
        tpl_path = _template_path(repo)
        if not tpl_path.exists():
            return None
        shutil.copy2(tpl_path, cfg_path)

    try:
        data = json.loads(cfg_path.read_text())
        return data.get("services", [])  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def resolve_placeholders(
    service_defs: list[dict[str, Any]],
    qualified: str,
    repo: str,
    branch: str,
) -> list[dict[str, Any]]:
    """Resolve {worktree} and {branch} placeholders in service definitions.

    Returns a new list of dicts with resolved values and computed keys.
    """
    wt = str(worktree_path(repo, branch))
    resolved = []
    for svc in service_defs:
        entry = _deep_resolve(svc, qualified, wt)
        # Compute the full service key from qualified name + key_suffix.
        entry["key"] = f"{qualified}/{entry['key_suffix']}"
        resolved.append(entry)
    return resolved


def _deep_resolve(obj: Any, qualified: str, worktree: str) -> Any:
    """Recursively resolve {branch} and {worktree} in strings."""
    if isinstance(obj, str):
        return obj.replace("{branch}", qualified).replace("{worktree}", worktree)
    if isinstance(obj, dict):
        return {k: _deep_resolve(v, qualified, worktree) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(item, qualified, worktree) for item in obj]
    return obj
