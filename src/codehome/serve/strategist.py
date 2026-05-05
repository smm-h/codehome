"""Strategist agent: generates execution plans via claude -p.

The strategist is itself a claude -p agent (auditor role) that receives a
user's goal, reads the codebase, and returns a structured ExecutionPlan
as a DAG of PlanNodes.
"""

import json
from typing import Any

from codehome.serve.agent_dispatch import dispatch_task_agent
from codehome.serve.plan_schema import (
    ExecutionPlan,
    PlanNode,
    new_plan,
    save_plan,
)

# JSON schema describing the expected output from the strategist agent.
_OUTPUT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["task", "condition", "gate"],
                        },
                        "role": {"type": "string"},
                        "description": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "condition": {"type": "string"},
                        "if_true": {"type": "string"},
                        "if_false": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["id", "type"],
                },
            },
        },
        "required": ["nodes"],
    },
    indent=2,
)

_SYSTEM_PROMPT = f"""\
You are a software development strategist. Given a goal, you create an execution plan
as a DAG of tasks for AI agents to execute.

Available roles:
- implementor (read-write): can read/write files, commit, run tests
- auditor (read-only): can read files, run tests, analyze code
- reviewer (read-only): can read diffs, post review comments
- deployer (everything): can stage, create PRs, deploy

Each task node should have:
- A clear, specific description of what the agent should do
- The appropriate role
- Dependencies on other tasks (by node ID)

Use condition nodes to branch based on task results (e.g., "tests pass?").
Use gate nodes when human approval is needed before proceeding.

You MUST respond with ONLY a JSON object matching this schema (no markdown, no explanation):

{_OUTPUT_SCHEMA}

Use the MCP tools available to you to read the codebase, understand the project
structure, and create an informed plan. Then output the JSON plan."""


async def create_plan(
    goal: str,
    branch: str,
    user: str,
    worktree: str,
    server_url: str,
    auth_token: str,
) -> ExecutionPlan:
    """Dispatch a strategist agent to generate an execution plan.

    The strategist runs as an auditor (read-only) agent that analyzes the
    codebase and returns a JSON array of plan nodes. Those nodes are parsed
    into a full ExecutionPlan, persisted, and returned.

    Raises ValueError if the strategist fails or returns invalid output.
    """
    plan = new_plan(goal, branch, user)

    # The task prompt tells the strategist what goal to plan for.
    task_description = (
        f"Analyze the codebase and create an execution plan for this goal:\n\n"
        f"{goal}\n\n"
        f"Respond with ONLY a JSON object matching the schema described in "
        f"your system prompt. No markdown fences, no explanation -- just JSON."
    )

    result = await dispatch_task_agent(
        role="auditor",
        task=task_description,
        branch=branch,
        user=user,
        worktree=worktree,
        server_url=server_url,
        auth_token=auth_token,
        system_prompt=_SYSTEM_PROMPT,
        model="opus",
        max_budget_usd=5.0,
        timeout_seconds=600,
    )

    if not result.get("ok"):
        error = result.get("error", "Strategist agent failed")
        msg = f"Strategist failed: {error}"
        raise ValueError(msg)

    # Parse the agent's output into PlanNodes.
    output = result.get("output", {})
    nodes_data = _extract_nodes(output)
    if not nodes_data:
        msg = f"Strategist returned no plan nodes. Raw output: {json.dumps(output)[:500]}"
        raise ValueError(msg)

    # Convert raw dicts to PlanNode dataclasses.
    for node_dict in nodes_data:
        node = PlanNode(
            id=node_dict["id"],
            type=node_dict["type"],
            role=node_dict.get("role", ""),
            description=node_dict.get("description", ""),
            depends_on=node_dict.get("depends_on", []),
            condition=node_dict.get("condition", ""),
            if_true=node_dict.get("if_true", ""),
            if_false=node_dict.get("if_false", ""),
            prompt=node_dict.get("prompt", ""),
        )
        plan.nodes.append(node)

    # Validate the DAG: all depends_on references must exist.
    node_ids = {n.id for n in plan.nodes}
    for node in plan.nodes:
        for dep in node.depends_on:
            if dep not in node_ids:
                msg = f"Node '{node.id}' depends on unknown node '{dep}'"
                raise ValueError(msg)

    save_plan(plan)
    return plan


def _extract_nodes(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the nodes array from the strategist's parsed output.

    The output may be nested in various ways depending on how claude -p
    wraps the JSON response. This tries several common shapes.
    """
    # Direct "nodes" key.
    if isinstance(output, dict) and "nodes" in output:
        nodes = output["nodes"]
        if isinstance(nodes, list):
            return nodes

    # The output itself might be the nodes array (wrapped in a result key).
    if isinstance(output, dict) and "result" in output:
        result = output["result"]
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "nodes" in parsed:
                    return parsed["nodes"]  # type: ignore[no-any-return]
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        if isinstance(result, dict) and "nodes" in result:
            return result["nodes"]  # type: ignore[no-any-return]

    # Raw text that might be JSON.
    if isinstance(output, dict) and "raw" in output:
        raw = output["raw"]
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "nodes" in parsed:
                    return parsed["nodes"]  # type: ignore[no-any-return]
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass

    return []
