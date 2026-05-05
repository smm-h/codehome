"""TODO file operations for the server API.

Lists, reads, creates, updates, and moves TODO markdown files across three
scope levels (branch, repo, super). Each level has optional subdirectories
for status: .done/, .defer/, .obsolete/ (files outside these are 'active').
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any

from supervisor.paths import ROOT, branch_dir, repo_dir

# Status subdirectory names mapped to their status label.
_STATUS_DIRS = {
    ".done": "done",
    ".defer": "defer",
    ".obsolete": "obsolete",
}

# Reverse map: status label -> subdirectory name.
_STATUS_TO_DIR = {v: k for k, v in _STATUS_DIRS.items()}


def _todo_dir_for_level(repo: str, branch: str, level: str) -> Path | None:
    """Return the todo/ directory for a given scope level."""
    if level == "branch":
        return branch_dir(repo, branch) / "todo"
    if level == "repo":
        return repo_dir(repo) / "todo"
    if level == "super":
        return ROOT / "todo"
    return None


def _classify_status(todo_root: Path, file_path: Path) -> str:
    """Determine status from the file's parent relative to the todo root."""
    try:
        rel = file_path.parent.relative_to(todo_root)
    except ValueError:
        return "active"
    # Check if the first path component is a status subdirectory.
    parts = rel.parts
    if parts and parts[0] in _STATUS_DIRS:
        return _STATUS_DIRS[parts[0]]
    return "active"


def _scan_todo_dir(todo_root: Path, level: str) -> list[dict[str, Any]]:
    """Scan a todo directory and return file metadata entries."""
    if not todo_root.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for dirpath, _dirnames, filenames in os.walk(todo_root):
        dp = Path(dirpath)
        for fname in sorted(filenames):
            if not fname.endswith(".md"):
                continue
            fpath = dp / fname
            status = _classify_status(todo_root, fpath)
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            results.append(
                {
                    "path": str(fpath),
                    "filename": fname,
                    "level": level,
                    "status": status,
                    "size_bytes": size,
                },
            )
    return results


def list_todos(repo: str, branch: str) -> list[dict[str, Any]]:
    """List all TODO files at branch, repo, and super levels."""
    items: list[dict[str, Any]] = []
    for level in ("branch", "repo", "super"):
        todo_root = _todo_dir_for_level(repo, branch, level)
        if todo_root:
            items.extend(_scan_todo_dir(todo_root, level))
    return items


def read_todo(repo: str, branch: str, path: str) -> dict[str, Any]:
    """Read a TODO file's content and metadata.

    Validates that the path is within one of the allowed todo directories
    to prevent path traversal.
    """
    file_path = Path(path).resolve()
    _validate_todo_path(repo, branch, file_path)

    if not file_path.is_file():
        msg = f"TODO file not found: {path}"
        raise FileNotFoundError(msg)

    # Determine level and status from the resolved path.
    level, todo_root = _detect_level(repo, branch, file_path)
    status = _classify_status(todo_root, file_path)

    content = file_path.read_text(encoding="utf-8")
    return {
        "path": str(file_path),
        "content": content,
        "level": level,
        "status": status,
    }


def create_todo(repo: str, branch: str, level: str, filename: str, content: str) -> dict[str, Any]:
    """Create a new TODO markdown file at the specified level."""
    _validate_filename(filename)

    todo_root = _todo_dir_for_level(repo, branch, level)
    if not todo_root:
        msg = f"Invalid level: {level}"
        raise ValueError(msg)

    todo_root.mkdir(parents=True, exist_ok=True)
    file_path = todo_root / filename
    if file_path.exists():
        msg = f"File already exists: {filename}"
        raise FileExistsError(msg)

    file_path.write_text(content, encoding="utf-8")
    return {"path": str(file_path), "level": level}


def update_todo(repo: str, branch: str, path: str, content: str) -> dict[str, Any]:
    """Update a TODO file's content."""
    file_path = Path(path).resolve()
    _validate_todo_path(repo, branch, file_path)

    if not file_path.is_file():
        msg = f"TODO file not found: {path}"
        raise FileNotFoundError(msg)

    file_path.write_text(content, encoding="utf-8")
    return {"ok": True}


def move_todo(repo: str, branch: str, path: str, target: str) -> dict[str, Any]:
    """Move a TODO file between status subdirectories.

    Target must be one of: 'active', 'done', 'defer', 'obsolete'.
    Moving to 'active' moves the file back to the todo root.
    """
    if target not in ("active", "done", "defer", "obsolete"):
        msg = f"Invalid target status: {target}"
        raise ValueError(msg)

    file_path = Path(path).resolve()
    _validate_todo_path(repo, branch, file_path)

    if not file_path.is_file():
        msg = f"TODO file not found: {path}"
        raise FileNotFoundError(msg)

    _level, todo_root = _detect_level(repo, branch, file_path)

    # Determine destination directory.
    dest_dir = todo_root if target == "active" else todo_root / _STATUS_TO_DIR[target]

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_path.name

    if dest_path == file_path:
        return {"path": str(file_path), "status": target}

    if dest_path.exists():
        msg = f"A file named {file_path.name} already exists in {target}"
        raise FileExistsError(msg)

    shutil.move(str(file_path), str(dest_path))
    return {"path": str(dest_path), "status": target}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_filename(filename: str) -> None:
    """Ensure filename is safe: no slashes, no traversal, must end in .md."""
    if not filename.endswith(".md"):
        msg = "Filename must end with .md"
        raise ValueError(msg)
    if "/" in filename or "\\" in filename:
        msg = "Filename must not contain path separators"
        raise ValueError(msg)
    if ".." in filename:
        msg = "Filename must not contain '..'"
        raise ValueError(msg)
    # Only allow alphanumeric, hyphens, underscores, dots.
    stem = filename[:-3]  # strip .md
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", stem):
        msg = "Filename must start with alphanumeric and contain only letters, digits, hyphens, underscores, dots"
        raise ValueError(
            msg,
        )


def _validate_todo_path(repo: str, branch: str, file_path: Path) -> None:
    """Ensure the resolved path is inside one of the allowed todo dirs."""
    allowed_roots = []
    for level in ("branch", "repo", "super"):
        root = _todo_dir_for_level(repo, branch, level)
        if root:
            allowed_roots.append(root.resolve())

    resolved = file_path.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue

    msg = "Path is outside allowed TODO directories"
    raise PermissionError(msg)


def _detect_level(repo: str, branch: str, file_path: Path) -> tuple[str, Path]:
    """Detect which level a file belongs to and return (level, todo_root)."""
    resolved = file_path.resolve()
    # Check in order of specificity: branch > repo > super.
    for level in ("branch", "repo", "super"):
        root = _todo_dir_for_level(repo, branch, level)
        if root:
            root_resolved = root.resolve()
            try:
                resolved.relative_to(root_resolved)
                return level, root_resolved
            except ValueError:
                continue
    msg = "File is not in any known TODO directory"
    raise PermissionError(msg)
