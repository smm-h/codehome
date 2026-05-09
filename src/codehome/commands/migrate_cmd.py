"""State migration command (deprecated).

Migration from .supervisor/ to .codehome/ is no longer supported.
State is stored in ~/.codehome/.
"""

import argparse


def cmd_migrate(args: argparse.Namespace) -> None:
    """Print deprecation notice -- migration is no longer needed."""
    print("Migration from .supervisor/ is no longer supported. State is stored in ~/.codehome/.")
