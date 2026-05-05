# Sub-Agent Roles

Each sub-agent is a `claude` process launched with a specific role. The role determines its permissions and specialization.

## Available Roles

### implementor
- **Access**: Read-write to the worktree.
- **Can do**: Modify files, create files, run git commands, run build/lint commands.
- **Use for**: Writing application code, creating database migrations, modifying configs, fixing bugs.

### auditor
- **Access**: Read-only to the worktree. Can run tests and build commands.
- **Can do**: Read files, run test suites, run type checking, run builds.
- **Use for**: Verifying implementations, checking for bugs, security review, validating migrations.

### reviewer
- **Access**: Read-only plus can post review comments.
- **Can do**: Read files, read diffs, post inline review comments.
- **Use for**: Code review of completed work. Check correctness, style, edge cases, and consistency with project patterns.

### test_writer
- **Access**: Read-write, specialized for test files.
- **Can do**: Create and modify test files, read source files for context, run tests.
- **Use for**: Writing unit tests, integration tests, and test fixtures.

### deployer
- **Access**: Full access including staging/production commands.
- **Can do**: Run `v deploy to staging`, `v deploy to production`, create PRs, push branches.
- **Use for**: Creating staging PRs and deploying to production.

## Context Passing

Each agent receives:
- The project's coding standards and architecture summary.
- Its specific task description from the plan node.
- The outputs of all completed dependency nodes.
- The current branch and worktree path.

## Agent Questions

Sub-agents can ask questions via the `ask_user` MCP tool. These questions flow to you (the Conductor) first -- not directly to the user. You decide whether to answer them yourself or forward them based on the autonomy level.
