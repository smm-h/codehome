"""superv init: create a managed project under ~/.superv/projects/.

Clones the repo as the anchor worktree, creates the project directory
skeleton (branches/, state/, cache/, run/), and registers the project
in ~/.superv/projects.toml.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

if TYPE_CHECKING:
    import argparse

from codehome.paths import superv_home
from codehome.utils import die, green


def _load_projects_toml(path: Path) -> dict[str, Any]:
    """Read projects.toml, returning an empty dict if missing or malformed."""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _save_projects_toml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write projects.toml via tomli_w."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling tmp file, then rename for atomicity.
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(tomli_w.dumps(data).encode())
        tmp.rename(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new managed project under ~/.superv/projects/.

    Creates the directory skeleton, clones the repo as the anchor
    worktree, and registers the project in projects.toml.
    """
    name: str = args.name
    remote: str = args.remote
    base: str = args.base or "production"

    # Sanitize: project name is used as a directory name, so restrict to safe chars.
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        die(f"invalid project name '{name}': must match [a-zA-Z0-9_-]+")

    home = superv_home()
    project_dir = home / "projects" / name

    if project_dir.exists():
        die(f"project '{name}' already exists at {project_dir}")

    # Validate: projects.toml shouldn't already have this name either.
    projects_toml = home / "projects.toml"
    existing = _load_projects_toml(projects_toml)
    if name in existing:
        die(f"project '{name}' already registered in {projects_toml}")

    # Create project structure.
    branches_dir = project_dir / "branches"
    worktree_dir = branches_dir / base / "worktree"

    print(f"Initializing project '{name}'...")
    project_dir.mkdir(parents=True)
    branches_dir.mkdir()
    (project_dir / "state").mkdir()
    (project_dir / "cache").mkdir()
    (project_dir / "run").mkdir()

    # Clone the repo as the anchor worktree.
    print(f"Cloning {remote} into {worktree_dir}...")
    result = subprocess.run(
        ["git", "clone", "--branch", base, remote, str(worktree_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Clean up the partially-created directory on failure.
        sys.stderr.write(f"git clone failed:\n{result.stderr}")
        shutil.rmtree(project_dir, ignore_errors=True)
        die("aborted -- project directory cleaned up")

    # Register in projects.toml.
    existing[name] = {
        "remote": remote,
        "base_branch": base,
        "path": str(project_dir),
    }
    _save_projects_toml(projects_toml, existing)

    print(green(f"Project '{name}' initialized at {project_dir}"))
    print(f"  anchor: {worktree_dir}")
    print(f"  base branch: {base}")
    print(f"\nNext: superv branch select {name}:{base}")
