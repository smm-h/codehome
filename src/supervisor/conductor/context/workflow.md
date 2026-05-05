# Workflow: Idea to PR

## Steps

### 1. Understand
Receive the user's idea. If anything is ambiguous, ask clarifying questions (respecting the autonomy level). Identify which repo(s) and area(s) of the codebase are affected. Use `read_file`, `search_files`, and `list_files` to inspect the codebase directly before planning.

### 2. Plan
Create an execution DAG and send it to the user via `ui_message` type "plan". Each node represents one sub-agent task. Nodes declare dependencies so independent work runs in parallel. Wait for user approval before proceeding (skip approval at autonomy >= 3).

### 3. Execute
Use `dispatch_agent` to spawn sub-agents for each node whose dependencies are satisfied. As nodes complete, dispatch newly unblocked nodes. Pass completed node outputs as context to dependent nodes.

### 4. Monitor
Use `check_agents` to poll status or `wait_for_agents` to block until agents finish. Use `get_pending_questions` to discover sub-agent questions and `answer_agent_question` to respond. On failure:
- Transient errors (timeout, flaky test): retry once.
- Implementation errors (wrong approach, type errors): replan the failed node with corrective context.
- Fundamental problems (missing API, unclear requirement): escalate to the user.

Send `ui_message` type "progress" at each meaningful milestone.

### 5. Review
When all implementation nodes complete, dispatch a **reviewer** agent. It reads the full diff and posts review comments. If the review finds issues, dispatch new **implementor** agents to address them, then re-review.

### 6. Test
Dispatch a **test_writer** agent if new tests are needed, then an **auditor** agent to run the test suite. If tests fail, loop back to step 3 for the failing nodes.

### 7. Stage
When implementation, review, and tests all pass, use `dispatch_agent` with the **deployer** role to create the staging PR via `v deploy to staging`.

### 8. Report
Send `ui_message` type "complete" with a summary and the PR link.

## Feature Build Order

Follow bottom-up ordering: database migration first, then Edge Function, then React component, then custom hook. Encode this in your DAG dependencies.
