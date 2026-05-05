"""v todo: list TODO files at context or super level."""

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from supervisor.paths import ROOT, repo_todo
from supervisor.resolution import resolve
from supervisor.utils import (
    bold,
    dim,
    group_by_date,
    render_date_tree,
)

# Subdirs shown with --all, in display order.
_SUBDIRS = [".done", ".defer", ".obsolete"]


def _mtime_date(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()


def _list_md_files(directory: Path) -> list[Path]:
    """List .md files sorted by mtime (most recent first)."""
    if not directory.is_dir():
        return []
    files = [f for f in directory.iterdir() if f.is_file() and f.suffix == ".md"]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def _render_flat(files: list[Path]) -> list[str]:
    """Render todo files as a tree grouped by date."""
    groups = group_by_date(files, _mtime_date)
    return render_date_tree(groups, "  ", name_fn=lambda f: f.stem)


def _render_tree(todo_dir: Path) -> list[str]:
    """Render todo files with --all subdirs as a tree."""
    top_files = _list_md_files(todo_dir)
    # Collect present subdirs (with files) in fixed order.
    present_subdirs = [s for s in _SUBDIRS if (todo_dir / s).is_dir() and _list_md_files(todo_dir / s)]

    groups = group_by_date(top_files, _mtime_date)
    lines = []

    # Date groups.
    lines.extend(
        render_date_tree(
            groups,
            "  ",
            name_fn=lambda f: f.stem,
            is_last_sibling=lambda gi: gi == len(groups) - 1 and not present_subdirs,
        )
    )

    # Subdirs.
    for si, subdir_name in enumerate(present_subdirs):
        is_last = si == len(present_subdirs) - 1
        sub_path = todo_dir / subdir_name
        sub_files = _list_md_files(sub_path)

        connector = "└── " if is_last else "├── "
        label = dim(f"{subdir_name}/ ({len(sub_files)})")
        lines.append(f"  {connector}{label}")

        prefix = "  " + ("    " if is_last else "│   ")
        sub_groups = group_by_date(sub_files, _mtime_date)
        lines.extend(
            render_date_tree(
                sub_groups,
                prefix,
                name_fn=lambda f: dim(f.stem),
            )
        )

    return lines


def cmd_todo(args: argparse.Namespace) -> None:
    """List TODO files at context or super level."""
    show_all = getattr(args, "all", False)
    use_super = getattr(args, "super", False)

    repo_arg = getattr(args, "repo", None)

    if use_super:
        todo_dir = ROOT / "todo"
        header = "super/todo"
    elif repo_arg:
        # Validate repo name via config (dies if unknown).
        from supervisor.config import get_repo

        get_repo(repo_arg)
        todo_dir = repo_todo(repo_arg)
        header = f"{repo_arg}/todo"
    else:
        ctx = resolve(getattr(args, "branch", None))
        todo_dir = ctx.branch_dir / "todo"
        header = f"{ctx.branch}/todo"

    if not todo_dir.is_dir():
        print(bold(header))
        print("  (none)")
        return

    print(bold(header))

    if show_all:
        lines = _render_tree(todo_dir)
    else:
        files = _list_md_files(todo_dir)
        lines = _render_flat(files)

    if lines:
        print("\n".join(lines))
    else:
        print("  (none)")
