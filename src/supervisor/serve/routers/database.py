"""Database endpoints: schema introspection, query execution, export, query store."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from supervisor.serve.auth_deps import get_current_user
from supervisor.serve.database import (
    execute_query as db_execute_query,
)
from supervisor.serve.database import (
    export_schema as db_export_schema,
)
from supervisor.serve.database import (
    get_schema as db_get_schema,
)
from supervisor.serve.database import (
    get_studio_url as db_get_studio_url,
)
from supervisor.serve.query_store import (
    get_query_sql as qs_get_sql,
)
from supervisor.serve.query_store import (
    load_graph as qs_load_graph,
)
from supervisor.serve.query_store import (
    store_query as qs_store,
)

router = APIRouter()


@router.get("/api/branches/{qualified}/database/schema")
async def api_database_schema(qualified: str) -> object:
    """Return public schema tables and columns for the branch's Supabase DB."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(db_get_schema, repo, branch)


class QueryRequest(BaseModel):
    sql: str
    parent_hash: str | None = None
    composition: str | None = None


@router.post("/api/branches/{qualified}/database/query")
async def api_database_query(
    qualified: str,
    req: QueryRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> object:
    """Execute a read-only SQL query and store it in the query DAG."""
    repo, branch = qualified.split(":", 1)
    result = await asyncio.to_thread(db_execute_query, repo, branch, req.sql)

    # Store in query DAG regardless of success (the SQL itself is valuable).
    query_hash = await asyncio.to_thread(
        qs_store,
        repo,
        req.sql,
        user["sub"],
        qualified,
        req.parent_hash,
        req.composition,
    )
    result["hash"] = query_hash
    return result


@router.get("/api/branches/{qualified}/database/studio-url")
async def api_database_studio_url(qualified: str) -> object:
    """Return the Supabase Studio URL for this branch."""
    repo, branch = qualified.split(":", 1)
    url = await asyncio.to_thread(db_get_studio_url, repo, branch)
    return {"url": url}


class ExportRequest(BaseModel):
    format: str  # "sql" | "markdown" | "both"


@router.post("/api/branches/{qualified}/database/export")
async def api_database_export(qualified: str, req: ExportRequest) -> object:
    """Export database schema as SQL DDL and/or Markdown summary."""
    if req.format not in ("sql", "markdown", "both"):
        raise HTTPException(status_code=400, detail="format must be sql, markdown, or both")
    repo, branch = qualified.split(":", 1)
    try:
        result = await asyncio.to_thread(db_export_schema, repo, branch, req.format)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return result


@router.get("/api/repos/{repo}/queries")
async def api_query_graph(repo: str) -> object:
    """Return the full query DAG for a repo."""
    return await asyncio.to_thread(qs_load_graph, repo)


@router.get("/api/repos/{repo}/queries/{query_hash}")
async def api_query_sql(repo: str, query_hash: str) -> object:
    """Return the SQL content for a stored query."""
    sql = await asyncio.to_thread(qs_get_sql, repo, query_hash)
    if sql is None:
        raise HTTPException(status_code=404, detail=f"Query not found: {query_hash}")
    return {"hash": query_hash, "sql": sql}
