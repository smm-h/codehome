"""Supabase migration staleness detection and remigration."""

import datetime
import re
from pathlib import Path

from codehome.git import git
from codehome.paths import MIGRATIONS_DIR
from codehome.paths import prod_ref as _prod_ref
from codehome.utils import die

MIGRATION_RE = re.compile(r"^(\d{14})_(.+)\.sql$")


def detect_stale_migrations(wt: Path, repo: str = "bag") -> list[str]:
    """Find branch-only migrations with timestamps <= base branch's latest.

    Compares files added on the branch against the base branch's migration
    timestamps. Returns sorted list of stale migration filenames.
    """
    ref = _prod_ref(repo)
    # Get base branch's migration files to find the latest timestamp.
    prod_listing = git(wt, "ls-tree", "--name-only", ref, MIGRATIONS_DIR + "/", check=False)
    if not prod_listing.strip():
        return []

    prod_timestamps = []
    for line in prod_listing.splitlines():
        m = MIGRATION_RE.match(Path(line).name)
        if m:
            prod_timestamps.append(m.group(1))

    if not prod_timestamps:
        return []

    latest_prod = max(prod_timestamps)

    # Get branch-only added migration files.
    added = git(wt, "diff", f"{ref}...HEAD", "--diff-filter=A", "--name-only", "--", MIGRATIONS_DIR + "/")
    if not added.strip():
        return []

    stale = []
    for line in added.splitlines():
        m = MIGRATION_RE.match(Path(line).name)
        if m and m.group(1) <= latest_prod:
            stale.append(Path(line).name)

    return sorted(stale)


def remigrate_file(wt: Path, filename: str, *, dry_run: bool = False, timestamp: str | None = None) -> tuple[str, str]:
    """Rename a migration file with a fresh UTC timestamp.

    Returns (old_filename, new_filename).
    """
    m = MIGRATION_RE.match(filename)
    if not m:
        die(f"invalid migration filename: {filename}\n  expected: YYYYMMDDHHMMSS_name.sql")

    name = m.group(2)
    ts = timestamp or datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    new_filename = f"{ts}_{name}.sql"

    if not dry_run:
        old_path = wt / MIGRATIONS_DIR / filename
        new_path = wt / MIGRATIONS_DIR / new_filename
        if not old_path.exists():
            die(f"file not found: {old_path}")
        old_path.rename(new_path)

    return (filename, new_filename)
