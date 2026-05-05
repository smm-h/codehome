"""Content-addressable query storage with DAG tracking.

Queries are stored as files keyed by SHA-256 hash prefix, with a
graph.json tracking relationships between queries (parent -> child).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import TYPE_CHECKING, Any

from supervisor.paths import repo_dir

if TYPE_CHECKING:
    from pathlib import Path

# Only alphanumeric chars allowed in hash lookups (path traversal guard).
_HASH_RE = re.compile(r"^[a-f0-9]+$")


def _queries_dir(repo: str) -> Path:
    """Return the queries directory for a repo, creating it if needed."""
    d = repo_dir(repo) / "queries"
    d.mkdir(parents=True, exist_ok=True)
    (d / "objects").mkdir(exist_ok=True)
    return d


def _hash_sql(sql: str) -> str:
    """Compute 12-char SHA-256 hex prefix of the SQL text."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]


def _load_graph_file(queries_dir: Path) -> dict[str, Any]:
    """Load graph.json or return empty structure."""
    graph_file = queries_dir / "graph.json"
    if graph_file.exists():
        try:
            return json.loads(graph_file.read_text())  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass
    return {"nodes": {}, "edges": []}


def _save_graph_file(queries_dir: Path, graph: dict[str, Any]) -> None:
    """Persist graph.json to disk."""
    graph_file = queries_dir / "graph.json"
    graph_file.write_text(json.dumps(graph, indent=2))


def store_query(
    repo: str,
    sql: str,
    user: str,
    branch: str,
    parent_hash: str | None = None,
    composition: str | None = None,
) -> str:
    """Store a query and record it in the graph.

    Args:
        repo: Repository shorthand (e.g. "bag").
        sql: The SQL text.
        user: Who executed the query.
        branch: Qualified branch name (e.g. "bag:feature").
        parent_hash: Hash of the parent query in the DAG, if any.
        composition: How this query relates to its parent (e.g. "refined", "filtered").

    Returns:
        The 12-char hash of the stored query.

    """
    qdir = _queries_dir(repo)
    h = _hash_sql(sql)

    # Write SQL file if it doesn't already exist (content-addressable).
    sql_file = qdir / "objects" / f"{h}.sql"
    if not sql_file.exists():
        sql_file.write_text(sql)

    # Update graph.
    graph = _load_graph_file(qdir)

    # Add or update node (always update timestamp for re-executions).
    graph["nodes"][h] = {
        "created": time.time(),
        "user": user,
        "branch": branch,
        "description": sql.strip().split("\n")[0][:120],
    }

    # Add edge if parent is specified.
    if parent_hash and _HASH_RE.match(parent_hash):
        edge = {"from": parent_hash, "to": h}
        if composition:
            edge["composition"] = composition
        # Avoid duplicate edges.
        if edge not in graph["edges"]:
            graph["edges"].append(edge)

    _save_graph_file(qdir, graph)
    return h


def load_graph(repo: str) -> dict[str, Any]:
    """Load the full query DAG for a repo.

    Returns {nodes: {hash: {created, user, branch, description}}, edges: [...]}.
    """
    qdir = repo_dir(repo) / "queries"
    if not qdir.exists():
        return {"nodes": {}, "edges": []}
    return _load_graph_file(qdir)


def get_query_sql(repo: str, query_hash: str) -> str | None:
    """Read the SQL content for a query by hash.

    Returns None if not found. Validates hash is alphanumeric to
    prevent path traversal.
    """
    if not _HASH_RE.match(query_hash):
        return None
    sql_file = repo_dir(repo) / "queries" / "objects" / f"{query_hash}.sql"
    if not sql_file.exists():
        return None
    return sql_file.read_text()
