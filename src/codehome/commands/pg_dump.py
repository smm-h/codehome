"""Database schema dump: v supabase db dump."""

import argparse
import datetime
import json
import subprocess
from typing import Any

from codehome.paths import ROOT
from codehome.resolution import resolve
from codehome.utils import die

# Local Supabase DB connection.
DB_PROJECT_ID = "khfauevdifdargbbynzf"
DB_CONTAINER = f"supabase_db_{DB_PROJECT_ID}"
DB_URL_INTERNAL = "postgresql://postgres:postgres@localhost:5432/postgres"
PG_DUMP_DIR = ROOT / "pg_dump"


def _psql_json(query: str) -> list[Any] | None:
    """Run a SQL query via psql inside the Supabase container, return parsed JSON."""
    result = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", DB_URL_INTERNAL, "-t", "-A", "-c", query],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"psql failed: {result.stderr.strip()}")
    raw = result.stdout.strip()
    if not raw or raw == "null":
        return None
    parsed: list[Any] | None = json.loads(raw)
    return parsed


def _db_reachable() -> bool:
    """Check if the local Supabase DB container is running and responsive."""
    result = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", DB_URL_INTERNAL, "-c", "SELECT 1"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _truncate_fn_args(args: str, max_args: int = 3) -> str:
    """Shorten function argument lists with many parameters."""
    if not args:
        return ""
    parts = args.split(", ")
    if len(parts) <= max_args:
        return args
    shown = ", ".join(parts[:max_args])
    return f"{shown}, ... +{len(parts) - max_args} more"


def _query_schema_data() -> dict[str, Any]:
    """Query all schema metadata from the local DB. Returns a structured dict."""
    data: dict[str, Any] = {}

    data["stats"] = _psql_json("""
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
    """)

    data["enums"] = (
        _psql_json("""
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
    """)
        or []
    )

    raw_triggers = (
        _psql_json("""
        SELECT COALESCE(json_agg(json_build_object(
            'name', trigger_name,
            'table', event_object_table,
            'timing', action_timing,
            'event', event_manipulation
        ) ORDER BY event_object_table, trigger_name), '[]')
        FROM information_schema.triggers
        WHERE trigger_schema = 'public'
    """)
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
        _psql_json("""
        SELECT COALESCE(json_agg(json_build_object(
            'table', tablename,
            'command', cmd
        ) ORDER BY tablename), '[]')
        FROM pg_policies
        WHERE schemaname = 'public'
    """)
        or []
    )
    policies_by_table: dict[str, dict[str, int]] = {}
    for pol in raw_policies:
        tbl_cmds = policies_by_table.setdefault(pol["table"], {})
        tbl_cmds[pol["command"]] = tbl_cmds.get(pol["command"], 0) + 1
    data["policies_by_table"] = policies_by_table

    all_functions = (
        _psql_json("""
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
    """)
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
        _psql_json("""
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
    """)
        or []
    )

    data["views"] = (
        _psql_json("""
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
    """)
        or []
    )

    referenced_by: dict[str, list[str]] = {}
    for tbl in data["tables"]:
        for fk in tbl.get("fks") or []:
            referenced_by.setdefault(fk["ref_table"], []).append(tbl["name"])
    data["referenced_by"] = referenced_by

    return data


def _render_header(branch: str, lines: list[str]) -> None:
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.extend([f"- Branch: {branch}", f"- Generated: {now}", ""])


def _render_overview(lines: list[str], data: dict[str, Any]) -> None:
    stats = data["stats"]
    if not stats:
        return
    lines.append("| Object | Count |")
    lines.append("|--------|------:|")
    lines.extend(
        f"| {key.title()} | {stats[key]} |" for key in ("tables", "views", "enums", "functions", "triggers", "policies")
    )
    lines.append("")


def _render_enums(lines: list[str], data: dict[str, Any]) -> None:
    for enum in data["enums"]:
        vals = ", ".join(f"`{v}`" for v in enum["values"])
        lines.append(f"- **{enum['name']}**: {vals}")
    if data["enums"]:
        lines.append("")


def _render_relationships(lines: list[str], data: dict[str, Any]) -> None:
    referenced_by = data["referenced_by"]
    if not referenced_by:
        return
    for target in sorted(referenced_by, key=lambda t: -len(referenced_by[t])):
        sources = sorted(set(referenced_by[target]))
        lines.append(f"- **{target}** <- {', '.join(sources)}")
    lines.append("")


def _table_extras(tbl: dict[str, Any], data: dict[str, Any]) -> str:
    tbl_name = tbl["name"]
    extras = []
    idx_count = tbl.get("index_count", 0)
    if idx_count:
        extras.append(f"{idx_count} idx")
    tbl_policies = data["policies_by_table"].get(tbl_name)
    if tbl_policies:
        extras.append(f"{sum(tbl_policies.values())} RLS")
    tbl_triggers = data["triggers_by_table"].get(tbl_name)
    if tbl_triggers:
        extras.append(f"{len(tbl_triggers)} triggers")
    row_est = tbl.get("row_estimate", 0)
    if row_est and row_est > 0:
        extras.append(f"~{row_est} rows")
    return f"  |  {', '.join(extras)}" if extras else ""


def _generate_full_md(branch: str, data: dict[str, Any]) -> str:
    lines = ["# Database Schema -- Full Detail", ""]
    _render_header(branch, lines)
    _render_overview(lines, data)

    if data["enums"]:
        lines.append("## Enums")
        lines.append("")
        _render_enums(lines, data)

    tables = data["tables"]
    if tables:
        lines.append("## Tables")
        lines.append("")
        for tbl in tables:
            tbl_name = tbl["name"]
            cols = tbl["columns"] or []
            extra_str = _table_extras(tbl, data)
            lines.append(f"### {tbl_name} ({len(cols)} columns{extra_str})")
            lines.append("")

            for col in cols:
                parts = [f"`{col['name']}` {col['type']}"]
                if col["nullable"] == "YES":
                    parts.append("NULL")
                default = col["default"] or ""
                if default:
                    if len(default) > 40:
                        default = default[:37] + "..."
                    parts.append(f"default={default}")
                lines.append(f"- {' '.join(parts)}")

            pk = tbl.get("pk")
            if pk:
                lines.append(f"- **PK**: {', '.join(pk)}")
            fks = tbl.get("fks")
            if fks:
                fk_parts = [f"{fk['column']} -> {fk['ref_table']}({fk['ref_column']})" for fk in fks]
                lines.append(f"- **FKs**: {'; '.join(fk_parts)}")

            tbl_policies = data["policies_by_table"].get(tbl_name)
            if tbl_policies:
                cmd_parts = [f"{cmd}({cnt})" for cmd, cnt in sorted(tbl_policies.items())]
                lines.append(f"- **RLS**: {', '.join(cmd_parts)}")

            tbl_triggers = data["triggers_by_table"].get(tbl_name)
            if tbl_triggers:
                for trig in tbl_triggers:
                    ev_str = "/".join(trig["events"])
                    lines.append(f"- **Trigger**: {trig['name']} {trig['timing']} {ev_str}")

            lines.append("")

    if data["views"]:
        lines.append("## Views")
        lines.append("")
        for view in data["views"]:
            cols = view.get("columns") or []
            lines.append(f"- **{view['name']}**: {', '.join(cols)}")
        lines.append("")

    if data["referenced_by"]:
        lines.append("## Relationships")
        lines.append("")
        _render_relationships(lines, data)

    regular = data["regular_functions"]
    if regular:
        lines.append(f"## Functions ({len(regular)} non-trigger)")
        lines.append("")
        for fn in regular:
            args = _truncate_fn_args(fn["args"] or "")
            ret = fn["returns"] or "void"
            lang = fn["language"]
            lines.append(f"- `{fn['name']}({args})` -> {ret} [{lang}]")
        lines.append("")

    trig_fns = data["trigger_fn_names"]
    if trig_fns:
        lines.append(f"## Trigger Functions ({len(trig_fns)})")
        lines.append("")
        lines.extend(f"- `{name}`" for name in sorted(trig_fns))
        lines.append("")

    return "\n".join(lines)


def _generate_overview_md(branch: str, data: dict[str, Any]) -> str:
    lines = ["# Database Schema -- Overview", ""]
    _render_header(branch, lines)
    _render_overview(lines, data)

    if data["enums"]:
        lines.append("## Enums")
        lines.append("")
        _render_enums(lines, data)

    tables = data["tables"]
    if tables:
        lines.append("## Tables")
        lines.append("")
        for tbl in tables:
            tbl_name = tbl["name"]
            cols = tbl["columns"] or []
            n_cols = len(cols)
            fks = tbl.get("fks") or []
            fk_targets = sorted({fk["ref_table"] for fk in fks})
            extra_str = _table_extras(tbl, data)
            col_names = ", ".join(c["name"] for c in cols)
            fk_str = f"  -> {', '.join(fk_targets)}" if fk_targets else ""
            lines.append(f"- **{tbl_name}** ({n_cols} cols{extra_str}){fk_str}")
            lines.append(f"  {col_names}")
        lines.append("")

    if data["views"]:
        lines.append("## Views")
        lines.append("")
        for view in data["views"]:
            cols = view.get("columns") or []
            lines.append(f"- **{view['name']}**: {', '.join(cols)}")
        lines.append("")

    if data["referenced_by"]:
        lines.append("## Relationships")
        lines.append("")
        _render_relationships(lines, data)

    regular = data["regular_functions"]
    if regular:
        groups: dict[str, list[dict[str, Any]]] = {}
        for fn in regular:
            name = fn["name"]
            prefix = name.split("_")[0] if "_" in name else name
            groups.setdefault(prefix, []).append(fn)

        collapsed: list[str] = []
        ungrouped: list[dict[str, Any]] = []
        for prefix in sorted(groups):
            fns = groups[prefix]
            if len(fns) >= 3:
                names = [fn["name"] for fn in fns]
                lcp = names[0]
                for n in names[1:]:
                    while not n.startswith(lcp):
                        lcp = lcp[:-1]
                lcp = lcp.rstrip("_")
                suffixes = [n[len(lcp) :].lstrip("_") for n in names]
                collapsed.append(f"- **{lcp}_\\*** ({len(fns)}): {', '.join(s for s in suffixes)}")
            else:
                ungrouped.extend(fns)

        lines.append(f"## Functions ({len(regular)} non-trigger)")
        lines.append("")
        lines.extend(collapsed)
        if ungrouped:
            if collapsed:
                lines.append("")
            for fn in ungrouped:
                args = _truncate_fn_args(fn["args"] or "")
                ret = fn["returns"] or "void"
                lines.append(f"- `{fn['name']}({args})` -> {ret}")
        lines.append("")

    trig_fns = data["trigger_fn_names"]
    if trig_fns:
        lines.append(f"## Trigger Functions ({len(trig_fns)})")
        lines.append("")
        lines.append(", ".join(f"`{n}`" for n in sorted(trig_fns)))
        lines.append("")

    return "\n".join(lines)


def cmd_pgdump(args: argparse.Namespace) -> None:
    """Dump the local database schema to SQL + markdown summaries.

    Runs pg_dump in the Supabase Docker container. Outputs to
    pg_dump/<branch>/<timestamp>/: FULL.sql, TABLES.md (columns,
    types, constraints), FUNCTIONS.md (signatures). Requires `make up`.
    """
    if not _db_reachable():
        die("local Supabase DB not reachable at localhost:54322\n  run: make up")

    ctx = resolve(getattr(args, "branch", None))
    name = ctx.branch
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = PG_DUMP_DIR / name / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    sql_file = out_dir / "FULL.sql"
    full_md = out_dir / "full.md"
    overview_md = out_dir / "overview.md"

    print(f"[{name}] dumping schema...")
    result = subprocess.run(
        [
            "docker",
            "exec",
            DB_CONTAINER,
            "pg_dump",
            DB_URL_INTERNAL,
            "--schema-only",
            "--no-owner",
            "--no-acl",
            "--schema=public",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"pg_dump failed: {result.stderr.strip()}")
    sql_file.write_text(result.stdout)
    print(f"  {sql_file.relative_to(ROOT)} ({result.stdout.count(chr(10))} lines)")

    data = _query_schema_data()

    full_content = _generate_full_md(name, data)
    full_md.write_text(full_content)
    print(f"  {full_md.relative_to(ROOT)} ({full_content.count(chr(10))} lines)")

    overview_content = _generate_overview_md(name, data)
    overview_md.write_text(overview_content)
    print(f"  {overview_md.relative_to(ROOT)} ({overview_content.count(chr(10))} lines)")
