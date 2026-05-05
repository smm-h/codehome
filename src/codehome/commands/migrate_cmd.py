"""Migrate state from .supervisor/ to ~/.superv/.

Copies critical configuration files from the legacy project-local
.supervisor/ directory to the new user-global ~/.superv/ home.
Also migrates the legacy ~/.supervisor/token file.

Safe to run multiple times -- existing files at the destination are
not overwritten (skipped with a message).
"""

import argparse
import shutil
from pathlib import Path

from codehome.paths import SUPERVISOR_DIR, superv_home


def cmd_migrate(args: argparse.Namespace) -> None:
    """Copy state files from .supervisor/ to ~/.superv/."""
    home = superv_home()
    home.mkdir(parents=True, exist_ok=True)

    # Items to migrate from project-local .supervisor/ to ~/.superv/.
    items: list[tuple[str, str, bool]] = [
        # (source_rel_to_SUPERVISOR_DIR, dest_rel_to_home, is_directory)
        # Core config
        ("config.json", "config.json", False),
        ("users.json", "users.json", False),
        ("features.json", "features.json", False),
        ("certs", "certs", True),
        # Cache (linear.json, workflows.json, team-stats.json)
        ("cache", "cache", True),
        # Audit logs (JSONL files)
        ("events", "events", True),
        # User preferences
        ("preferences.json", "preferences.json", False),
        ("preferences", "preferences", True),
        # Plugin state
        ("plugins-state.json", "plugins-state.json", False),
        # Check suppressions
        ("suppress-checks.txt", "suppress-checks.txt", False),
        # Web push VAPID keys
        ("vapid_keys.json", "vapid_keys.json", False),
        # Secrets (signing keys, service accounts, keystores)
        ("credentials", "credentials", True),
        # Remote Mac config, ssh_config, logs
        ("telemac", "telemac", True),
    ]

    copied = 0
    skipped = 0
    missing = 0

    for src_rel, dst_rel, is_dir in items:
        src = SUPERVISOR_DIR / src_rel
        dst = home / dst_rel

        if not src.exists():
            print(f"  skip  {src_rel}  (not found in .supervisor/)")
            missing += 1
            continue

        if dst.exists():
            print(f"  skip  {dst_rel}  (already exists in ~/.superv/)")
            skipped += 1
            continue

        if is_dir:
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  copy  {src_rel}  ->  ~/.superv/{dst_rel}")
        copied += 1

    # Migrate legacy ~/.supervisor/token -> ~/.superv/token.
    old_token = Path.home() / ".supervisor" / "token"
    new_token = home / "token"
    if not old_token.exists():
        print("  skip  token  (not found in ~/.supervisor/)")
        missing += 1
    elif new_token.exists():
        print("  skip  token  (already exists in ~/.superv/)")
        skipped += 1
    else:
        shutil.copy2(old_token, new_token)
        print("  copy  ~/.supervisor/token  ->  ~/.superv/token")
        copied += 1

    print()
    print(f"Done: {copied} copied, {skipped} skipped, {missing} not found.")
    if copied > 0:
        print(f"New state directory: {home}")
