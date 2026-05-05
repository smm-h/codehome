"""Plan executor: runs an ExecutionPlan as a background asyncio task.

Walks the DAG of PlanNodes, dispatching task agents in parallel where
dependencies allow, evaluating conditions, and pausing at gate nodes
for human approval.
"""

import asyncio
import json
import logging
from typing import Any

from supervisor.bus import Event
from supervisor.bus import fire as bus_fire
from supervisor.serve.agent_dispatch import _build_system_prompt, dispatch_task_agent
from supervisor.serve.plan_schema import ExecutionPlan, PlanNode, save_plan
from supervisor.serve.questions import question_store

log = logging.getLogger(__name__)

# Map of plan_id -> asyncio.Task for running executors.
_running_tasks: dict[str, asyncio.Task[None]] = {}

# Map of plan_id -> asyncio.Event for gate node answers.
# When a gate node blocks, it waits on this event. The server endpoint
# sets the answer on the plan node and triggers the event.
_gate_events: dict[str, asyncio.Event] = {}


async def execute_plan(
    plan: ExecutionPlan,
    server_url: str,
    auth_token: str,
    worktree: str,
) -> None:
    """Execute a plan as a background task.

    Finds ready nodes (pending with all deps completed), dispatches them
    concurrently, and repeats until every node is done or a failure halts
    progress.

    This function is meant to be wrapped in asyncio.create_task() by the
    server endpoint.
    """
    plan.status = "running"
    save_plan(plan)
    await _broadcast_plan_state(plan)

    try:
        while True:
            # Check for pause/cancel.
            if plan.status == "paused":
                log.info("Plan %s paused, stopping executor", plan.id)
                return
            if plan.status == "failed":
                log.info("Plan %s failed, stopping executor", plan.id)
                return

            ready = plan.ready_nodes()
            if not ready:
                # No ready nodes -- either everything is done or we're stuck.
                pending = [n for n in plan.nodes if n.status == "pending"]
                waiting = [n for n in plan.nodes if n.status == "waiting"]
                running = [n for n in plan.nodes if n.status == "running"]

                if running:
                    # Still have running nodes; wait for them to complete.
                    # This shouldn't happen in the main loop since we await
                    # below, but guard against edge cases.
                    await asyncio.sleep(1)
                    continue

                if waiting:
                    # Gate nodes waiting for human input -- sleep and retry.
                    await asyncio.sleep(2)
                    continue

                if pending:
                    # Pending nodes exist but none are ready -- blocked by
                    # failed dependencies. Mark plan as failed.
                    plan.status = "failed"
                    save_plan(plan)
                    await _broadcast_plan_state(plan)
                    log.warning(
                        "Plan %s stuck: %d pending nodes with unmet deps",
                        plan.id,
                        len(pending),
                    )
                    return

                # All nodes are completed, failed, or skipped. Plan is done.
                failed_nodes = [n for n in plan.nodes if n.status == "failed"]
                plan.status = "failed" if failed_nodes else "completed"
                save_plan(plan)
                await _broadcast_plan_state(plan)
                return

            # Dispatch all ready nodes concurrently.
            tasks = []
            for node in ready:
                if node.type == "task":
                    tasks.append(_run_task_node(plan, node, server_url, auth_token, worktree))
                elif node.type == "condition":
                    tasks.append(_run_condition_node(plan, node))
                elif node.type == "gate":
                    tasks.append(_run_gate_node(plan, node))
                else:
                    # Unknown node type -- skip it.
                    node.status = "skipped"
                    save_plan(plan)
                    await _broadcast_node_state(plan, node)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    except asyncio.CancelledError:
        plan.status = "paused"
        save_plan(plan)
        await _broadcast_plan_state(plan)
        raise
    except Exception:
        log.exception("Plan %s executor crashed", plan.id)
        plan.status = "failed"
        save_plan(plan)
        await _broadcast_plan_state(plan)


MAX_DEP_OUTPUT_CHARS = 2000


def _extract_output_summary(output: dict[str, Any] | None) -> str:
    """Extract a concise summary string from a node's output dict."""
    if not output:
        return "(no output)"
    for key in ("summary", "result"):
        if key in output and isinstance(output[key], str):
            text = output[key]
            if len(text) > MAX_DEP_OUTPUT_CHARS:
                return text[:MAX_DEP_OUTPUT_CHARS] + "..."  # type: ignore[no-any-return]
            return text  # type: ignore[no-any-return]
    raw = json.dumps(output, indent=2, default=str)
    if len(raw) > MAX_DEP_OUTPUT_CHARS:
        return raw[:MAX_DEP_OUTPUT_CHARS] + "..."
    return raw


def _format_dependency_context(plan: ExecutionPlan, node: PlanNode) -> str:
    """Build a context block summarizing outputs from completed dependency nodes."""
    if not node.depends_on:
        return ""

    sections: list[str] = []
    for dep_id in node.depends_on:
        dep = plan.get_node(dep_id)
        if not dep or dep.status != "completed":
            continue
        label = dep.description or dep.id
        role = dep.role or dep.type
        summary = _extract_output_summary(dep.output)
        sections.append(f'### Task: "{label}" ({role}, completed)\nOutput: {summary}')

    if not sections:
        return ""

    return "## Context from prior tasks\n\n" + "\n\n".join(sections)


async def _run_task_node(
    plan: ExecutionPlan,
    node: PlanNode,
    server_url: str,
    auth_token: str,
    worktree: str,
) -> None:
    """Dispatch an agent for a task node and update its status on completion."""
    node.status = "running"
    save_plan(plan)
    await _broadcast_node_state(plan, node)

    dep_context = _format_dependency_context(plan, node)

    try:
        result = await dispatch_task_agent(
            role=node.role or "implementor",
            task=node.description,
            branch=plan.branch,
            user=plan.user,
            worktree=worktree,
            server_url=server_url,
            auth_token=auth_token,
            system_prompt=_build_system_prompt(
                node.role or "implementor",
                node.description,
                plan.branch,
                dep_context,
            )
            if dep_context
            else "",
        )

        if result.get("ok"):
            node.status = "completed"
            node.output = result.get("output")
            node.agent_session_id = result.get("session_id")
        else:
            node.status = "failed"
            node.error = result.get("error", "Agent failed")
            node.agent_session_id = result.get("session_id")

    except Exception as exc:
        node.status = "failed"
        node.error = str(exc)

    save_plan(plan)
    await _broadcast_node_state(plan, node)


async def _run_condition_node(plan: ExecutionPlan, node: PlanNode) -> None:
    """Evaluate a condition node based on previous task outputs.

    The condition string is matched against the output of dependency nodes.
    Simple heuristic: if the condition contains keywords like "pass", "success",
    "ok", check if any dependency output indicates success/failure.
    """
    node.status = "running"
    save_plan(plan)
    await _broadcast_node_state(plan, node)

    try:
        result = _evaluate_condition(plan, node)
        node.status = "completed"
        node.output = {"result": result}

        # Activate the appropriate branch and skip the other.
        target = node.if_true if result else node.if_false
        skipped = node.if_false if result else node.if_true

        if skipped:
            skipped_node = plan.get_node(skipped)
            if skipped_node and skipped_node.status == "pending":
                skipped_node.status = "skipped"
                await _broadcast_node_state(plan, skipped_node)

        # The target node will be picked up naturally by ready_nodes()
        # since this condition node is now completed.
        node.output = {"result": result, "target": target, "skipped": skipped}

    except Exception as exc:
        node.status = "failed"
        node.error = str(exc)

    save_plan(plan)
    await _broadcast_node_state(plan, node)


async def _run_gate_node(plan: ExecutionPlan, node: PlanNode) -> None:
    """Block execution until a human answers the gate's prompt.

    Broadcasts an agent.question SSE event and waits for the answer to be
    set on the node (via the /plans/{id}/gate/{node_id}/answer endpoint).
    """
    node.status = "waiting"
    save_plan(plan)
    await _broadcast_node_state(plan, node)

    # Broadcast the question to the dashboard.
    await bus_fire(
        Event(
            name="agent.question",
            payload={
                "source": "gate",
                "plan_id": plan.id,
                "node_id": node.id,
                "prompt": node.prompt,
                "branch": plan.branch,
            },
        )
    )

    # Persist the gate question so it survives page refreshes.
    question_store.create(
        source="gate",
        branch=plan.branch,
        role="gate",
        question=node.prompt or "",
        plan_id=plan.id,
        node_id=node.id,
    )

    # Create an event that the answer endpoint will set.
    gate_key = f"{plan.id}:{node.id}"
    gate_event = asyncio.Event()
    _gate_events[gate_key] = gate_event

    try:
        # Wait up to 24 hours for a human answer.
        await asyncio.wait_for(gate_event.wait(), timeout=86400)
        node.status = "completed"
    except TimeoutError:
        node.status = "failed"
        node.error = "Gate timed out waiting for human answer"
    except asyncio.CancelledError:
        node.status = "skipped"
        raise
    finally:
        _gate_events.pop(gate_key, None)

    save_plan(plan)
    await _broadcast_node_state(plan, node)


def answer_gate(plan_id: str, node_id: str, answer: str) -> bool:
    """Provide a human answer for a gate node, unblocking execution.

    Called by the server endpoint. Returns True if the gate was found
    and unblocked, False otherwise.
    """
    gate_key = f"{plan_id}:{node_id}"
    gate_event = _gate_events.get(gate_key)
    if not gate_event:
        return False

    gate_event.set()
    return True


def _evaluate_condition(plan: ExecutionPlan, node: PlanNode) -> bool:
    """Evaluate a condition string against dependency node outputs.

    Uses simple heuristics since conditions are natural-language strings
    written by the strategist agent:
    - Looks for "pass", "success", "ok" -> checks if deps succeeded
    - Looks for "fail", "error" -> checks if deps failed
    - Default: True if all dependencies completed successfully
    """
    condition = node.condition.lower()
    dep_outputs = []
    for dep_id in node.depends_on:
        dep = plan.get_node(dep_id)
        if dep:
            dep_outputs.append(dep)

    # Check if any dependency failed.
    any_failed = any(d.status == "failed" for d in dep_outputs)
    all_ok = all(d.status == "completed" for d in dep_outputs)

    # Keywords indicating the condition checks for success.
    success_keywords = ("pass", "success", "ok", "green", "true")
    failure_keywords = ("fail", "error", "red", "false")

    if any(kw in condition for kw in failure_keywords):
        return any_failed
    if any(kw in condition for kw in success_keywords):
        return all_ok

    # Default: True if all deps completed.
    return all_ok


# ---------------------------------------------------------------------------
# Lifecycle management (called by server endpoints)
# ---------------------------------------------------------------------------


def start_plan(plan: ExecutionPlan, server_url: str, auth_token: str, worktree: str) -> bool:
    """Start executing a plan as a background task.

    Returns False if a plan is already running on this branch.
    """
    # Guard: no concurrent plans on the same branch.
    for pid, task in list(_running_tasks.items()):
        if task.done():
            _running_tasks.pop(pid, None)
            continue
        # Check if this running plan is on the same branch.
        # We store branch in plan metadata, but the task itself doesn't
        # carry it. Use a simple approach: check our plan store.
        from supervisor.serve.plan_schema import load_plan as _load

        other = _load(pid)
        if other and other.branch == plan.branch and other.status == "running":
            return False

    task = asyncio.create_task(
        execute_plan(plan, server_url, auth_token, worktree),
        name=f"plan-executor-{plan.id}",
    )
    _running_tasks[plan.id] = task
    return True


def pause_plan(plan_id: str) -> bool:
    """Pause a running plan by setting its status (executor checks on next loop)."""
    from supervisor.serve.plan_schema import load_plan as _load
    from supervisor.serve.plan_schema import save_plan as _save

    plan = _load(plan_id)
    if not plan or plan.status != "running":
        return False
    plan.status = "paused"
    _save(plan)
    return True


def resume_plan(plan: ExecutionPlan, server_url: str, auth_token: str, worktree: str) -> bool:
    """Resume a paused plan by restarting the executor."""
    if plan.status != "paused":
        return False
    return start_plan(plan, server_url, auth_token, worktree)


def cancel_plan(plan_id: str) -> bool:
    """Cancel a plan: kill the executor task and mark all active nodes as failed."""
    task = _running_tasks.pop(plan_id, None)
    if task and not task.done():
        task.cancel()

    from supervisor.serve.plan_schema import load_plan as _load
    from supervisor.serve.plan_schema import save_plan as _save

    plan = _load(plan_id)
    if not plan:
        return False

    for node in plan.nodes:
        if node.status in ("pending", "running", "waiting"):
            node.status = "skipped"

    plan.status = "failed"
    _save(plan)
    return True


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


async def _broadcast_plan_state(plan: ExecutionPlan) -> None:
    """Broadcast plan-level status change via SSE."""
    await bus_fire(
        Event(
            name="plan.state",
            payload={
                "plan_id": plan.id,
                "status": plan.status,
                "branch": plan.branch,
            },
        )
    )


async def _broadcast_node_state(plan: ExecutionPlan, node: PlanNode) -> None:
    """Broadcast node-level status change via SSE."""
    await bus_fire(
        Event(
            name="plan.state",
            payload={
                "plan_id": plan.id,
                "status": plan.status,
                "node_id": node.id,
                "node_status": node.status,
                "branch": plan.branch,
            },
        )
    )
