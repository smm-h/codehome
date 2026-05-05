"""Database introspection and query execution for local Supabase instances."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

# Re-use the Markdown rendering from the CLI pgdump module.  These functions
# accept a pre-built data dict and a branch name -- they do not call the
# database themselves.
from supervisor.commands.pg_dump import _generate_full_md
from supervisor.serve.ports import ports


def _db_connection_string(branch: str) -> str | None:
    """Build a psql connection string from the port allocator.

    Returns postgresql://postgres:postgres@127.0.0.1:{db_port}/postgres
    or None if no Supabase slot is allocated for the branch.
    """
    slot_result = ports.get_supabase_slot(branch)
    if not slot_result:
        return None
    _slot, slot_ports = slot_result
    db_port = slot_ports.get("db_port")
    if not db_port:
        return None
    return f"postgresql://postgres:postgres@127.0.0.1:{db_port}/postgres"


def _run_psql(conn_str: str, sql: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a SQL statement via psql subprocess."""
    return subprocess.run(
        ["psql", conn_str, "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_schema(repo: str, branch: str) -> dict[str, Any]:
    """Query information_schema for the public schema tables and columns.

    Returns {tables: [{name, columns: [{name, type, nullable, default}]}]}.
    """
    qualified = f"{repo}:{branch}"
    conn = _db_connection_string(qualified)
    if not conn:
        return {"tables": [], "error": "No Supabase instance running for this branch"}

    # Single query: aggregate tables with their columns as JSON.
    sql = """
    SELECT COALESCE(json_agg(tbl ORDER BY tbl->>'name'), '[]'::json)
    FROM (
        SELECT json_build_object(
            'name', t.table_name,
            'columns', (
                SELECT COALESCE(json_agg(json_build_object(
                    'name', c.column_name,
                    'type', CASE
                        WHEN c.data_type = 'USER-DEFINED' THEN c.udt_name
                        WHEN c.data_type = 'ARRAY' THEN c.udt_name
                        WHEN c.character_maximum_length IS NOT NULL
                            THEN c.data_type || '(' || c.character_maximum_length || ')'
                        ELSE c.data_type
                    END,
                    'nullable', (c.is_nullable = 'YES'),
                    'default', c.column_default
                ) ORDER BY c.ordinal_position), '[]'::json)
                FROM information_schema.columns c
                WHERE c.table_schema = 'public' AND c.table_name = t.table_name
            )
        ) AS tbl
        FROM information_schema.tables t
        WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
    ) sub;
    """

    try:
        result = _run_psql(conn, sql)
    except subprocess.TimeoutExpired:
        return {"tables": [], "error": "Query timed out"}

    if result.returncode != 0:
        return {"tables": [], "error": result.stderr.strip()}

    raw = result.stdout.strip()
    if not raw or raw == "null":
        return {"tables": []}

    try:
        tables = json.loads(raw)
    except json.JSONDecodeError:
        return {"tables": [], "error": "Failed to parse schema JSON"}

    return {"tables": tables}


def execute_query(repo: str, branch: str, sql: str) -> dict[str, Any]:
    """Execute a read-only SQL query against the local Supabase DB.

    Wraps the query in a read-only transaction to prevent mutations.
    Returns {columns, rows, row_count, duration_ms} on success,
    or {error, duration_ms} on failure.
    """
    qualified = f"{repo}:{branch}"
    conn = _db_connection_string(qualified)
    if not conn:
        return {"error": "No Supabase instance running for this branch", "duration_ms": 0}

    # Wrap in a read-only transaction. The BEGIN...COMMIT ensures
    # that even if the user tries INSERT/UPDATE/DELETE, it will fail.
    wrapped_sql = f"BEGIN TRANSACTION READ ONLY; {sql.rstrip(';')}; COMMIT;"

    start = time.monotonic()
    try:
        # Use psql with CSV header output for easy parsing.
        result = subprocess.run(
            ["psql", conn, "--csv", "-c", wrapped_sql],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - start) * 1000
        return {"error": "Query timed out (30s limit)", "duration_ms": round(elapsed, 1)}

    elapsed = (time.monotonic() - start) * 1000

    if result.returncode != 0:
        error_msg = result.stderr.strip()
        # Clean up common psql noise.
        if "ERROR:" in error_msg:
            error_msg = error_msg[error_msg.index("ERROR:") :]
        return {"error": error_msg, "duration_ms": round(elapsed, 1)}

    # Parse CSV output: first line is headers, rest are data rows.
    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

    # psql --csv with a transaction outputs BEGIN/COMMIT noise; filter those.
    # The actual data starts after "BEGIN" line and before "COMMIT" line.
    data_lines = []
    in_data = False
    for line in lines:
        if line.strip() == "BEGIN":
            in_data = True
            continue
        if line.strip() == "COMMIT":
            break
        if in_data:
            data_lines.append(line)

    if not data_lines:
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "duration_ms": round(elapsed, 1),
        }

    # Parse CSV manually (simple: split on comma, respecting quotes).
    import csv
    import io

    reader = csv.reader(io.StringIO("\n".join(data_lines)))
    all_rows = list(reader)

    if not all_rows:
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "duration_ms": round(elapsed, 1),
        }

    columns = all_rows[0]
    rows = all_rows[1:]

    # Enforce max 1000 rows.
    truncated = len(rows) > 1000
    if truncated:
        rows = rows[:1000]

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "duration_ms": round(elapsed, 1),
    }


def get_studio_url(repo: str, branch: str) -> str | None:
    """Return the Supabase Studio URL for a branch, or None."""
    qualified = f"{repo}:{branch}"
    slot_result = ports.get_supabase_slot(qualified)
    if not slot_result:
        return None
    _slot, slot_ports = slot_result
    studio_port = slot_ports.get("studio_port")
    if not studio_port:
        return None
    return f"http://127.0.0.1:{studio_port}"


# -- Schema export (pgdump) ------------------------------------------------


def _psql_json(conn_str: str, query: str) -> list[Any] | None:
    """Run a SQL query via psql and parse JSON output.

    Server-side counterpart to pg_dump._psql_json -- uses a connection
    string from the port allocator instead of docker exec.
    """
    result = subprocess.run(
        ["psql", conn_str, "-t", "-A", "-c", query],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        msg = f"psql failed: {result.stderr.strip()}"
        raise RuntimeError(msg)
    raw = result.stdout.strip()
    if not raw or raw == "null":
        return None
    return json.loads(raw)  # type: ignore[no-any-return]


def _query_schema_data(conn_str: str) -> dict[str, Any]:
    """Query all schema metadata using the same queries as pg_dump.

    Returns a dict matching the shape expected by _generate_full_md and
    _generate_overview_md from the pgdump module.
    """
    data: dict[str, Any] = {}

    data["stats"] = _psql_json(
        conn_str,
        """
        SELECT json_build_object(
            'tables', (SELECT count(*) FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_type = 'BASE TABLE'),
            'views', (SELECT count(*) FROM information_schema.views
                      WHERE table_schema = 'public'),
            'functions', (SELECT count(*) FROM information_schema.routines
                          WHERE routine_schema = 'public'),
            'triggers', (SELECT count(DISTINCT trigger_name) FROM information_schema.triggers
                         WHERE trigger_schema = 'public'),
            'enums', (SELECT count(*) FROM pg_type t
                      JOIN pg_namespace n ON t.typnamespace = n.oid
                      WHERE n.nspname = 'public' AND t.typtype = 'e'),
            'policies', (SELECT count(*) FROM pg_policies
                         WHERE schemaname = 'public')
        )
    """,
    )

    data["enums"] = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(row ORDER BY row->>'name'), '[]')
        FROM (
            SELECT json_build_object(
                'name', t.typname,
                'values', (SELECT json_agg(e.enumlabel ORDER BY e.enumsortorder)
                           FROM pg_enum e WHERE e.enumtypid = t.oid)
            ) AS row
            FROM pg_type t
            JOIN pg_namespace n ON t.typnamespace = n.oid
            WHERE n.nspname = 'public' AND t.typtype = 'e'
        ) sub
    """,
        )
        or []
    )

    raw_triggers = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(json_build_object(
            'name', trigger_name,
            'table', event_object_table,
            'timing', action_timing,
            'event', event_manipulation
        ) ORDER BY event_object_table, trigger_name), '[]')
        FROM information_schema.triggers
        WHERE trigger_schema = 'public'
    """,
        )
        or []
    )
    triggers_by_table: dict[str, list[dict[str, Any]]] = {}
    _trig_seen: dict[tuple[str, str], dict[str, Any]] = {}
    for trig in raw_triggers:
        key = (trig["name"], trig["table"])
        if key not in _trig_seen:
            entry = {"name": trig["name"], "timing": trig["timing"], "events": [trig["event"]]}
            _trig_seen[key] = entry
            triggers_by_table.setdefault(trig["table"], []).append(entry)
        else:
            _trig_seen[key]["events"].append(trig["event"])
    data["triggers_by_table"] = triggers_by_table

    raw_policies = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(json_build_object(
            'table', tablename,
            'command', cmd
        ) ORDER BY tablename), '[]')
        FROM pg_policies
        WHERE schemaname = 'public'
    """,
        )
        or []
    )
    policies_by_table: dict[str, dict[str, int]] = {}
    for pol in raw_policies:
        tbl_cmds = policies_by_table.setdefault(pol["table"], {})
        tbl_cmds[pol["command"]] = tbl_cmds.get(pol["command"], 0) + 1
    data["policies_by_table"] = policies_by_table

    all_functions = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(fn ORDER BY fn->>'name'), '[]')
        FROM (
            SELECT json_build_object(
                'name', p.proname,
                'args', pg_get_function_arguments(p.oid),
                'returns', pg_get_function_result(p.oid),
                'language', l.lanname
            ) AS fn
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            JOIN pg_language l ON p.prolang = l.oid
            WHERE n.nspname = 'public'
                AND p.prokind IN ('f', 'p')
        ) sub
    """,
        )
        or []
    )
    trigger_fn_names: set[str] = set()
    regular_functions: list[dict[str, Any]] = []
    for fn in all_functions:
        if fn.get("returns") == "trigger":
            trigger_fn_names.add(fn["name"])
        else:
            regular_functions.append(fn)
    data["trigger_fn_names"] = trigger_fn_names
    data["regular_functions"] = regular_functions

    data["tables"] = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(tbl ORDER BY tbl->>'name'), '[]')
        FROM (
            SELECT json_build_object(
                'name', t.table_name,
                'columns', (
                    SELECT json_agg(json_build_object(
                        'name', c.column_name,
                        'type', CASE
                            WHEN c.data_type = 'USER-DEFINED' THEN c.udt_name
                            WHEN c.data_type = 'ARRAY' THEN c.udt_name
                            WHEN c.character_maximum_length IS NOT NULL
                                THEN c.data_type || '(' || c.character_maximum_length || ')'
                            ELSE c.data_type
                        END,
                        'nullable', c.is_nullable,
                        'default', c.column_default
                    ) ORDER BY c.ordinal_position)
                    FROM information_schema.columns c
                    WHERE c.table_schema = 'public' AND c.table_name = t.table_name
                ),
                'pk', (
                    SELECT json_agg(kcu.column_name ORDER BY kcu.ordinal_position)
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.table_schema = 'public'
                        AND tc.table_name = t.table_name
                        AND tc.constraint_type = 'PRIMARY KEY'
                ),
                'fks', (
                    SELECT json_agg(json_build_object(
                        'column', kcu.column_name,
                        'ref_table', ccu.table_name,
                        'ref_column', ccu.column_name
                    ))
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                        AND tc.table_schema = ccu.table_schema
                    WHERE tc.table_schema = 'public'
                        AND tc.table_name = t.table_name
                        AND tc.constraint_type = 'FOREIGN KEY'
                ),
                'index_count', (
                    SELECT count(*) FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = t.table_name
                ),
                'row_estimate', (
                    SELECT reltuples::bigint FROM pg_class
                    WHERE relname = t.table_name AND relnamespace = 'public'::regnamespace
                )
            ) AS tbl
            FROM information_schema.tables t
            WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ) sub
    """,
        )
        or []
    )

    data["views"] = (
        _psql_json(
            conn_str,
            """
        SELECT COALESCE(json_agg(json_build_object(
            'name', table_name,
            'columns', (
                SELECT json_agg(c.column_name ORDER BY c.ordinal_position)
                FROM information_schema.columns c
                WHERE c.table_schema = 'public' AND c.table_name = v.table_name
            )
        ) ORDER BY table_name), '[]')
        FROM information_schema.views v
        WHERE table_schema = 'public'
    """,
        )
        or []
    )

    referenced_by: dict[str, list[str]] = {}
    for tbl in data["tables"]:
        for fk in tbl.get("fks") or []:
            referenced_by.setdefault(fk["ref_table"], []).append(tbl["name"])
    data["referenced_by"] = referenced_by

    return data


def export_schema(repo: str, branch: str, fmt: str) -> dict[str, Any]:
    """Export the database schema as SQL DDL and/or Markdown.

    Args:
        repo: Repository shorthand (e.g. "bag").
        branch: Branch name.
        fmt: One of "sql", "markdown", or "both".

    Returns:
        {"sql": str|None, "markdown": str|None, "filename": str}

    Raises RuntimeError on connection or pg_dump failures.

    """
    qualified = f"{repo}:{branch}"
    conn = _db_connection_string(qualified)
    if not conn:
        msg = "No Supabase instance running for this branch"
        raise RuntimeError(msg)

    sql_content: str | None = None
    md_content: str | None = None

    if fmt in ("sql", "both"):
        # Run pg_dump for DDL export (uses psql connection string directly).
        result = subprocess.run(
            ["pg_dump", conn, "--schema-only", "--no-owner", "--no-acl", "--schema=public"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            msg = f"pg_dump failed: {result.stderr.strip()}"
            raise RuntimeError(msg)
        sql_content = result.stdout

    if fmt in ("markdown", "both"):
        data = _query_schema_data(conn)
        md_content = _generate_full_md(branch, data)

    # Build a human-friendly base filename.
    safe_branch = branch.replace("/", "-").replace(":", "-")
    filename = f"{safe_branch}-schema"

    return {"sql": sql_content, "markdown": md_content, "filename": filename}
