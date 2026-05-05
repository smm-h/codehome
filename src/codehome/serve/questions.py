"""Question persistence: tracks agent and gate questions as JSON files.

Each question is stored as a JSON file in .supervisor/questions/. This
allows the dashboard inbox to fetch pending questions on page load instead
of relying solely on ephemeral SSE events.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from codehome.paths import resolve_global, superv_home
from codehome.serve.file_lock import write_json_locked

# Read: dual-path fallback. Write: canonical new location.
_QUESTIONS_DIR_READ = resolve_global("questions")
_QUESTIONS_DIR_WRITE = superv_home() / "questions"


@dataclass
class Question:
    id: str
    source: str  # "agent" or "gate"
    branch: str  # qualified branch name (repo:branch)
    role: str
    question: str
    status: str  # "pending" or "answered"
    created_at: str  # ISO timestamp
    session_id: str | None = None  # for agent questions
    plan_id: str | None = None  # for gate questions
    node_id: str | None = None  # for gate questions
    options: list[str] | None = None
    answer: str | None = None
    auto_decided: bool = False
    answered_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class QuestionStore:
    """Manages questions in memory with JSON persistence."""

    def __init__(self) -> None:
        self._questions: dict[str, Question] = {}
        self._load_persisted()

    def _load_persisted(self) -> None:
        if not _QUESTIONS_DIR_READ.is_dir():
            return
        for path in _QUESTIONS_DIR_READ.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                self._questions[data["id"]] = Question(**data)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

    def _persist(self, q: Question) -> None:
        """Write a question to disk as a JSON file with advisory locking."""
        write_json_locked(
            _QUESTIONS_DIR_WRITE / f"{q.id}.json",
            q.to_dict(),
        )

    def create(
        self,
        *,
        source: str,
        branch: str,
        role: str,
        question: str,
        session_id: str | None = None,
        plan_id: str | None = None,
        node_id: str | None = None,
        options: list[str] | None = None,
    ) -> Question:
        q = Question(
            id=str(uuid.uuid4()),
            source=source,
            branch=branch,
            role=role,
            question=question,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
            session_id=session_id,
            plan_id=plan_id,
            node_id=node_id,
            options=options,
        )
        self._questions[q.id] = q
        self._persist(q)
        return q

    def get(self, question_id: str) -> Question | None:
        return self._questions.get(question_id)

    def list(
        self,
        branch: str | None = None,
        status: str | None = None,
    ) -> list[Question]:
        result = list(self._questions.values())
        if branch is not None:
            result = [q for q in result if q.branch == branch]
        if status is not None:
            result = [q for q in result if q.status == status]
        result.sort(key=lambda q: q.created_at, reverse=True)
        return result

    def mark_answered(
        self,
        question_id: str,
        answer: str,
        auto_decided: bool = False,
    ) -> Question | None:
        q = self._questions.get(question_id)
        if not q:
            return None
        q.status = "answered"
        q.answer = answer
        q.auto_decided = auto_decided
        q.answered_at = datetime.now(UTC).isoformat()
        self._persist(q)
        return q

    def find_pending_by_session(self, session_id: str) -> Question | None:
        """Find the pending question for an agent session."""
        for q in self._questions.values():
            if q.source == "agent" and q.session_id == session_id and q.status == "pending":
                return q
        return None

    def find_pending_by_gate(self, plan_id: str, node_id: str) -> Question | None:
        """Find the pending question for a gate node."""
        for q in self._questions.values():
            if q.source == "gate" and q.plan_id == plan_id and q.node_id == node_id and q.status == "pending":
                return q
        return None

    def mark_session_questions_answered(self, session_id: str) -> None:
        """Mark all pending questions for a session as answered (agent completed/cancelled)."""
        for q in self._questions.values():
            if q.source == "agent" and q.session_id == session_id and q.status == "pending":
                q.status = "answered"
                q.answer = "(session ended)"
                q.auto_decided = True
                q.answered_at = datetime.now(UTC).isoformat()
                self._persist(q)


question_store = QuestionStore()
