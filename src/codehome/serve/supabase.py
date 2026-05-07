"""Supabase CLI wrapper: start, stop, status, config patching."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from typing import TYPE_CHECKING, Any

from codehome.utils import warn

if TYPE_CHECKING:
    from pathlib import Path

    from codehome.subprocesses import OutputCallback

_DEFAULT_SUPABASE_VERSION = "2.90.1"

# Services excluded from `supabase start` (unused locally, save resources).
ALWAYS_EXCLUDE = ["mailpit", "imgproxy", "realtime", "logflare", "vector", "supavisor"]

# Subprocess timeouts (seconds) — configurable.
TIMEOUT_SB_STATUS = 30
TIMEOUT_SB_START = 300
TIMEOUT_SB_STOP = 60
TIMEOUT_SB_MIGRATE = 120


def read_supabase_version(wt: Path) -> str:
    """Read pinned version from .supabase-version in the worktree root."""
    version_file = wt / ".supabase-version"
    if version_file.exists():
        return version_file.read_text().strip()
    return _DEFAULT_SUPABASE_VERSION


def supabase_cmd(wt: Path) -> list[str]:
    """Return the supabase command, preferring the local binary."""
    local_bin = wt / "node_modules" / ".bin" / "supabase"
    if local_bin.exists():
        return [str(local_bin)]
    return ["npx", f"supabase@{read_supabase_version(wt)}"]


def project_id_for(branch: str) -> str:
    """Generate a deterministic project_id from a branch name."""
    return hashlib.sha256(branch.encode()).hexdigest()[:20]


def status(wt: Path) -> dict[str, str] | None:
    """Run supabase status and return parsed dict, or None if not running."""
    try:
        result = subprocess.run(
            [*supabase_cmd(wt), "status", "-o", "json", "--workdir", str(wt)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SB_STATUS,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        return None


def start(wt: Path, exclude: list[str] | None = None) -> tuple[bool, str]:
    """Start Supabase. Returns (success, message)."""
    exc = list(ALWAYS_EXCLUDE) + (exclude or [])
    cmd = [*supabase_cmd(wt), "start", "--workdir", str(wt), "-x", ",".join(exc)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SB_START)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SB_START}s"
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return False, msg
    return True, "Supabase started."


def stop(wt: Path) -> tuple[bool, str]:
    """Stop Supabase. Returns (success, message)."""
    cmd = [*supabase_cmd(wt), "stop", "--workdir", str(wt)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SB_STOP)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SB_STOP}s"
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return False, msg
    return True, "Supabase stopped."


def start_streaming(
    wt: Path,
    callback: OutputCallback,
    exclude: list[str] | None = None,
) -> tuple[bool, str]:
    """Start Supabase with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    exc = list(ALWAYS_EXCLUDE) + (exclude or [])
    cmd = [*supabase_cmd(wt), "start", "--workdir", str(wt), "-x", ",".join(exc)]
    ok, msg = run_streaming(cmd, callback, timeout=TIMEOUT_SB_START)
    if ok:
        return True, "Supabase started."
    return False, msg


def stop_streaming(wt: Path, callback: OutputCallback) -> tuple[bool, str]:
    """Stop Supabase with line-by-line output streaming."""
    from codehome.subprocesses import run_streaming

    cmd = [*supabase_cmd(wt), "stop", "--workdir", str(wt)]
    ok, msg = run_streaming(cmd, callback, timeout=TIMEOUT_SB_STOP)
    if ok:
        return True, "Supabase stopped."
    return False, msg


def apply_migrations(wt: Path) -> tuple[bool, str]:
    """Apply pending migrations. Returns (success, message).

    Uses --include-all because supabase start restores from a production
    backup whose migration history may reference versions that don't exist
    in the feature branch's local migrations/ directory.
    """
    cmd = [*supabase_cmd(wt), "migration", "up", "--include-all", "--workdir", str(wt)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SB_MIGRATE)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SB_MIGRATE}s"
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "(no output)"
        return False, f"Migration failed: {msg}"
    return True, "Migrations applied."


def parse_migration_output(raw: str) -> dict[str, str | None]:
    """Parse raw supabase migration stderr into a structured error dict.

    Filters noise (privilege warnings), extracts the failing migration file,
    SQLSTATE code, error line, and classifies the error with a human hint.
    Always returns a dict with all keys; optional fields are None on miss.
    """
    # Filter out the hundreds of pg_dump privilege warnings.
    filtered_lines = [line for line in raw.splitlines() if not re.search(r"WARNING.*no privileges were granted", line)]
    filtered = "\n".join(filtered_lines)

    # Extract the last "Applying migration <file>.sql..." before the error.
    migration_file: str | None = None
    migration_matches = re.findall(r"Applying migration (\S+\.sql)", filtered)
    if migration_matches:
        migration_file = migration_matches[-1]

    # Extract SQLSTATE code (5 alphanumeric chars).
    sqlstate: str | None = None
    sqlstate_match = re.search(r"\(SQLSTATE (\w{5})\)", filtered)
    if sqlstate_match:
        sqlstate = sqlstate_match.group(1)

    # Extract the ERROR: line content.
    error_line: str | None = None
    error_match = re.search(r"^\s*ERROR:\s*(.+)$", filtered, re.MULTILINE)
    if error_match:
        error_line = error_match.group(1).strip()

    # Classify by SQLSTATE, then by text patterns.
    category: str | None = None
    hint: str | None = None

    sqlstate_map: dict[str, tuple[str, str]] = {
        "42P01": (
            "missing_object",
            "Migration ordering issue -- a referenced table or column does not exist."
            " Check that CREATE migrations have earlier timestamps than ALTERs that reference them.",
        ),
        "42703": (
            "missing_object",
            "Migration ordering issue -- a referenced table or column does not exist."
            " Check that CREATE migrations have earlier timestamps than ALTERs that reference them.",
        ),
        "42501": (
            "permission",
            "Permission denied -- check role grants and RLS policies.",
        ),
        "42601": (
            "syntax",
            "SQL syntax error in the migration file.",
        ),
        "42710": (
            "duplicate",
            "Object already exists -- the migration may have been partially applied. Consider adding IF NOT EXISTS.",
        ),
        "42P07": (
            "duplicate",
            "Object already exists -- the migration may have been partially applied. Consider adding IF NOT EXISTS.",
        ),
    }

    if sqlstate and sqlstate in sqlstate_map:
        category, hint = sqlstate_map[sqlstate]
    elif not sqlstate and re.search(r"timeout", raw, re.IGNORECASE):
        category = "timeout"
        hint = "Migration timed out -- the database may be under heavy load."

    # Build a human-readable summary.
    if migration_file and error_line:
        summary = f"Migration {migration_file} failed: {error_line}"
    elif error_line:
        summary = f"Migration failed: {error_line}"
    elif filtered.strip():
        summary = filtered.strip()[:200]
    else:
        summary = "Migration failed (no details available)"

    return {
        "summary": summary,
        "migration_file": migration_file,
        "sqlstate": sqlstate,
        "error_line": error_line,
        "category": category,
        "hint": hint,
        "raw": raw,
    }


def patch_config(wt: Path, slot_ports: dict[str, int], project_id: str) -> bool:
    """Patch supabase/config.toml with custom ports and project_id.

    Uses section-aware replacement: finds each [section] header, then replaces
    the target key only within that section. Returns False if config not found.
    """
    config_path = wt / "supabase" / "config.toml"
    if not config_path.exists():
        return False

    content = config_path.read_text()

    # Map our port names to (TOML section header, key name) pairs.
    toml_mappings = {
        "api_port": ("api", "port"),
        "db_port": ("db", "port"),
        "shadow_port": ("db", "shadow_port"),
        "pooler_port": ("db.pooler", "port"),
        "studio_port": ("studio", "port"),
        "inbucket_port": ("inbucket", "port"),
        "inbucket_smtp": ("inbucket", "smtp_port"),
        "inbucket_pop3": ("inbucket", "pop3_port"),
        "analytics_port": ("analytics", "port"),
        "inspector_port": ("edge_runtime", "inspector_port"),
    }

    # Replace project_id (top-level, not in a section).
    content = re.sub(
        r'^project_id\s*=\s*"[^"]*"',
        f'project_id = "{project_id}"',
        content,
        flags=re.MULTILINE,
    )

    # Section-aware port replacement: match key = <number> only within the
    # correct [section]. Find the section header, then replace the first
    # matching key after it (before the next section header).
    for port_name, (section, key) in toml_mappings.items():
        if port_name not in slot_ports:
            continue
        port_val = slot_ports[port_name]
        # Match [section] header, then content up to the next section header,
        # then the key = value line.  Value can be bare or quoted integer.
        section_pattern = (
            rf"(\[{re.escape(section)}\]"  # [section] header
            rf"(?:(?!\n\[)[^\n]*\n)*?)"  # lines that don't start a new section
            rf"(^(\s*){re.escape(key)}\s*=\s*)"  # key = (with indentation)
            rf'"?\d+"?'  # bare 5432 or quoted "5432"
        )
        replacement = rf"\g<1>\g<2>{port_val}"
        content = re.sub(section_pattern, replacement, content, count=1, flags=re.MULTILINE)

    config_path.write_text(content)
    return True


def skip_worktree_config(wt: Path) -> None:
    """Set --skip-worktree on config.toml so patched ports don't dirty git status."""
    subprocess.run(
        ["git", "update-index", "--skip-worktree", "supabase/config.toml"],
        cwd=wt,
        check=False,
        capture_output=True,
    )


def restore_config(wt: Path) -> None:
    """Restore config.toml to its committed state and clear the skip-worktree flag."""
    result = subprocess.run(
        ["git", "show", "HEAD:supabase/config.toml"],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        (wt / "supabase" / "config.toml").write_text(result.stdout)
    else:
        warn(f"Could not read committed config.toml: {result.stderr.strip()}")
    subprocess.run(
        ["git", "update-index", "--no-skip-worktree", "supabase/config.toml"],
        cwd=wt,
        check=False,
        capture_output=True,
    )


def extract_connection_details(status_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract connection details from supabase status output."""
    return {
        "api_url": status_dict.get("API_URL", ""),
        "db_url": status_dict.get("DB_URL", ""),
        "studio_url": status_dict.get("STUDIO_URL", ""),
        "graphql_url": status_dict.get("GRAPHQL_URL", ""),
        "anon_key": status_dict.get("ANON_KEY", ""),
        "service_role_key": status_dict.get("SERVICE_ROLE_KEY", ""),
    }
