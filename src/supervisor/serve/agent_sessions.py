"""Agent session tracking: in-memory state with JSON file persistence.

Each agent session represents one claude -p subprocess dispatched to work
on a task. Sessions are stored in memory (dict) and persisted as individual
JSON files in .supervisor/agent-sessions/.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from supervisor.paths import resolve_global, superv_home
from supervisor.serve.file_lock import write_json_locked

# Read: dual-path fallback. Write: canonical new location.
_SESSIONS_DIR_READ = resolve_global("agent-sessions")
_SESSIONS_DIR_WRITE = superv_home() / "agent-sessions"


@dataclass
class AgentSession:
    id: str
    role: str  # implementor, auditor, reviewer, deployer
    task: str  # task description
    branch: str  # qualified branch name (repo:branch)
    user: str  # who spawned it
    status: str  # pending, running, completed, failed, cancelled
    created_at: str  # ISO timestamp
    ended_at: str | None = None  # ISO timestamp or None
    output: dict[str, Any] | None = None  # structured output from claude
    error: str | None = None  # error message if failed
    events: list[dict[str, Any]] = field(default_factory=list)  # tool call events

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AgentSessionManager:
    """Manages agent sessions in memory with JSON persistence."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        # Map session_id -> asyncio.subprocess.Process for cancellation.
        self._processes: dict[str, object] = {}
        self._load_persisted()

    def _load_persisted(self) -> None:
        """Load previously persisted sessions from disk on startup."""
        if not _SESSIONS_DIR_READ.is_dir():
            return
        for path in _SESSIONS_DIR_READ.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                session = AgentSession(**data)
                # Mark stale running sessions as failed (server restarted).
                if session.status in ("pending", "running"):
                    session.status = "failed"
                    session.error = "Server restarted while agent was active"
                    session.ended_at = datetime.now(UTC).isoformat()
                    self._persist(session)
                self._sessions[session.id] = session
            except (json.JSONDecodeError, TypeError, KeyError):
                pass  # Skip malformed files.

    def _persist(self, session: AgentSession) -> None:
        """Write a session to disk as a JSON file with advisory locking."""
        write_json_locked(
            _SESSIONS_DIR_WRITE / f"{session.id}.json",
            session.to_dict(),
        )

    def create_session(self, role: str, task: str, branch: str, user: str) -> AgentSession:
        """Create a new session in pending state."""
        session = AgentSession(
            id=str(uuid.uuid4()),
            role=role,
            task=task,
            branch=branch,
            user=user,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._sessions[session.id] = session
        self._persist(session)
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def list_sessions(
        self,
        branch: str | None = None,
        user: str | None = None,
    ) -> list[AgentSession]:
        """List sessions, optionally filtered by branch and/or user."""
        result = list(self._sessions.values())
        if branch is not None:
            result = [s for s in result if s.branch == branch]
        if user is not None:
            result = [s for s in result if s.user == user]
        # Most recent first.
        result.sort(key=lambda s: s.created_at, reverse=True)
        return result

    def update_status(
        self,
        session_id: str,
        status: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AgentSession | None:
        """Update session status and optional output/error fields."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        session.status = status
        if output is not None:
            session.output = output
        if error is not None:
            session.error = error
        if status in ("completed", "failed", "cancelled"):
            session.ended_at = datetime.now(UTC).isoformat()
        self._persist(session)
        return session

    def add_event(self, session_id: str, event: dict[str, Any]) -> bool:
        """Append a hook-reported event to a session's event list."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.events.append(event)
        self._persist(session)
        return True

    def register_process(self, session_id: str, process: object) -> None:
        """Associate an asyncio subprocess with a session for cancellation."""
        self._processes[session_id] = process

    def unregister_process(self, session_id: str) -> None:
        """Remove the process reference after it exits."""
        self._processes.pop(session_id, None)

    async def cancel_session(self, session_id: str) -> AgentSession | None:
        """Mark session as cancelled and kill its process if running."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        if session.status not in ("pending", "running"):
            return session  # Already terminal.

        proc = self._processes.get(session_id)
        if proc is not None and hasattr(proc, "terminate"):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

        session.status = "cancelled"
        session.ended_at = datetime.now(UTC).isoformat()
        self._persist(session)
        self._processes.pop(session_id, None)
        return session


# Singleton instance.
agent_sessions = AgentSessionManager()
