"""Centralized SQLite-backed error log for the dev server.

All methods are synchronous. Callers in async contexts should use
``asyncio.to_thread()`` to avoid blocking the event loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ErrorLog"]

_log = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'error',
    message TEXT NOT NULL,
    detail TEXT,
    context TEXT
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_error_log_timestamp ON error_log (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_error_log_category ON error_log (category)",
]


class ErrorLog:
    """SQLite-backed error log with filtering, pruning, and JSON detail storage."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            self._conn.execute(idx_sql)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(
        self,
        source: str,
        category: str,
        message: str,
        *,
        severity: str = "error",
        detail: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Insert an error entry. Never raises -- logs a warning on failure."""
        try:
            ts = datetime.now(UTC).isoformat()
            self._conn.execute(
                "INSERT INTO error_log (timestamp, source, category, severity, message, detail, context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    source,
                    category,
                    severity,
                    message,
                    json.dumps(detail) if detail is not None else None,
                    json.dumps(context) if context is not None else None,
                ),
            )
            self._conn.commit()
        except Exception as exc:
            _log.warning("failed to write error_log entry: %s", exc)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(
        self,
        *,
        since: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return matching entries newest-first as a list of dicts."""
        where, params = self._build_where(since=since, category=category, severity=severity, source=source)
        # _build_where only produces hardcoded column names -- no injection risk
        cols = "id, timestamp, source, category, severity, message, detail, context"
        sql = (
            f"SELECT {cols} FROM error_log{where}"  # noqa: S608
            " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(
        self,
        *,
        since: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        source: str | None = None,
    ) -> int:
        """Return the number of entries matching the given filters."""
        where, params = self._build_where(since=since, category=category, severity=severity, source=source)
        sql = f"SELECT COUNT(*) FROM error_log{where}"  # noqa: S608
        result: int = self._conn.execute(sql, params).fetchone()[0]
        return result

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def clear(self, *, before: str | None = None) -> int:
        """Delete entries. If *before* is given, only entries older than that timestamp."""
        if before is not None:
            cur = self._conn.execute("DELETE FROM error_log WHERE timestamp < ?", (before,))
        else:
            cur = self._conn.execute("DELETE FROM error_log")
        self._conn.commit()
        return cur.rowcount

    def prune(self, days: int = 30) -> int:
        """Delete entries older than *days* days. Returns count deleted."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        return self.clear(before=cutoff)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_where(
        *,
        since: str | None,
        category: str | None,
        severity: str | None,
        source: str | None,
    ) -> tuple[str, list[str | int]]:
        """Build a WHERE clause from non-None filter params."""
        clauses: list[str] = []
        params: list[str | int] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    @staticmethod
    def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert a database row tuple into a dict, parsing JSON columns."""
        return {
            "id": row[0],
            "timestamp": row[1],
            "source": row[2],
            "category": row[3],
            "severity": row[4],
            "message": row[5],
            "detail": json.loads(row[6]) if row[6] is not None else None,
            "context": json.loads(row[7]) if row[7] is not None else None,
        }
