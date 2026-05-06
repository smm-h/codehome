"""Execution plan data model for the Strategist agent system.

An ExecutionPlan is a DAG of PlanNodes representing tasks, conditions,
gates (human approval), and loops that AI agents execute to accomplish
a user's goal.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codehome.paths import resolve_global, codehome_home

# Read: dual-path fallback. Write: canonical new location.
_PLANS_DIR_READ = resolve_global("plans")
_PLANS_DIR_WRITE = codehome_home() / "plans"


@dataclass
class PlanNode:
    """A single node in an execution plan DAG."""

    id: str  # unique node ID within the plan
    type: str  # "task", "condition", "gate", "loop"

    # For task nodes:
    role: str = ""  # agent role (implementor, auditor, reviewer, deployer)
    description: str = ""  # what this task does

    # Edges: node IDs this depends on (must complete before this runs).
    depends_on: list[str] = field(default_factory=list)

    # For condition nodes:
    condition: str = ""  # expression to evaluate against prior output
    if_true: str = ""  # node ID to execute if condition is true
    if_false: str = ""  # node ID to execute if condition is false

    # For gate nodes:
    prompt: str = ""  # question to ask the human

    # For loop nodes:
    body: list[str] = field(default_factory=list)  # node IDs in the loop body
    until: str = ""  # condition to break the loop

    # Runtime state (not part of the plan definition, updated during execution):
    status: str = "pending"  # pending, running, completed, failed, skipped, waiting
    agent_session_id: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPlan:
    """A complete execution plan: a goal decomposed into a DAG of nodes."""

    id: str  # plan UUID
    goal: str  # original user goal
    branch: str  # qualified branch name (repo:branch)
    user: str  # who created it
    status: str = "pending"  # pending, running, paused, completed, failed
    nodes: list[PlanNode] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get_node(self, node_id: str) -> PlanNode | None:
        """Look up a node by ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def ready_nodes(self) -> list[PlanNode]:
        """Return nodes whose dependencies are all completed and that are pending."""
        completed_ids = {n.id for n in self.nodes if n.status == "completed"}
        return [n for n in self.nodes if n.status == "pending" and all(dep in completed_ids for dep in n.depends_on)]

    def is_terminal(self) -> bool:
        """Check whether the plan has reached a terminal state."""
        return self.status in ("completed", "failed")


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _plan_path_read(plan_id: str) -> Path:
    return _PLANS_DIR_READ / f"{plan_id}.json"


def _plan_path_write(plan_id: str) -> Path:
    return _PLANS_DIR_WRITE / f"{plan_id}.json"


def save_plan(plan: ExecutionPlan) -> None:
    """Persist a plan to disk as JSON (canonical location)."""
    plan.updated_at = datetime.now(UTC).isoformat()
    _PLANS_DIR_WRITE.mkdir(parents=True, exist_ok=True)
    _plan_path_write(plan.id).write_text(json.dumps(plan.to_dict(), indent=2) + "\n")


def load_plan(plan_id: str) -> ExecutionPlan | None:
    """Load a single plan from disk (dual-path fallback), or None if not found."""
    path = _plan_path_read(plan_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        nodes = [PlanNode(**n) for n in data.pop("nodes", [])]
        return ExecutionPlan(**data, nodes=nodes)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def load_all_plans() -> list[ExecutionPlan]:
    """Load all persisted plans from disk (dual-path fallback)."""
    if not _PLANS_DIR_READ.is_dir():
        return []
    plans: list[ExecutionPlan] = []
    for path in _PLANS_DIR_READ.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            nodes = [PlanNode(**n) for n in data.pop("nodes", [])]
            plans.append(ExecutionPlan(**data, nodes=nodes))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    # Most recent first.
    plans.sort(key=lambda p: p.created_at, reverse=True)
    return plans


def delete_plan_file(plan_id: str) -> bool:
    """Remove a plan's JSON file from disk. Checks both locations."""
    # Try canonical location first, then legacy.
    for path in (_plan_path_write(plan_id), _plan_path_read(plan_id)):
        if path.is_file():
            path.unlink()
            return True
    return False


def new_plan(goal: str, branch: str, user: str) -> ExecutionPlan:
    """Create a new empty plan with a fresh UUID and timestamp."""
    return ExecutionPlan(
        id=str(uuid.uuid4()),
        goal=goal,
        branch=branch,
        user=user,
        status="pending",
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )
