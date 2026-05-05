"""superv home: show and bootstrap the global ~/.superv/ directory."""

import argparse
from pathlib import Path

from supervisor.paths import superv_home


# Subdirectories created by `superv home` (the skeleton).
# db.sqlite, config.toml, projects.toml are created on demand by
# their respective features -- not pre-created here.
_SKELETON_DIRS = (
    "credentials",
    "run",
    "projects",
)


def cmd_home(args: argparse.Namespace) -> None:
    """Show the superv home directory, creating the skeleton if needed.

    Prints the home path and the status of each expected subdirectory
    (exists / created / empty).
    """
    home = superv_home()
    created_home = not home.exists()
    home.mkdir(parents=True, exist_ok=True)

    if created_home:
        print(f"superv home: {home}  (created)")
    else:
        print(f"superv home: {home}")

    # Ensure skeleton subdirs exist.
    for name in _SKELETON_DIRS:
        d = home / name
        existed = d.exists()
        d.mkdir(exist_ok=True)
        _print_dir_status(d, name, created=not existed)

    # Lock down credentials dir (owner-only).
    creds = home / "credentials"
    creds.chmod(0o700)

    # Show any extra top-level entries the user may have placed.
    skeleton_set = set(_SKELETON_DIRS)
    for child in sorted(home.iterdir()):
        if child.name not in skeleton_set:
            _print_dir_status(child, child.name, created=False)


def _print_dir_status(path: Path, label: str, *, created: bool) -> None:
    """Print a single line showing a directory/file status."""
    if path.is_dir():
        empty = not any(path.iterdir())
        tag = "created" if created else ("empty" if empty else "ok")
    elif path.is_file():
        tag = "file"
    else:
        tag = "missing"
    print(f"  {label}/  ({tag})" if path.is_dir() else f"  {label}  ({tag})")
