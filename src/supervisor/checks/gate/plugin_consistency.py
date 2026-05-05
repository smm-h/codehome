"""Gate check: plugin manifest consistency.

Validates every ``plugins/*/plugin.toml`` without actually loading the
plugins.  Catches structural problems that would surface as confusing
runtime errors:

1. Unparseable manifests (bad TOML or missing required fields).
2. Commands declared but ``handlers.py`` missing or lacking ``register_cli``.
3. Checks declared but ``checks.py`` missing or lacking the named handler
   functions.
4. Dashboard declared but ``routes.py`` missing or lacking a ``router`` object.
5. Two plugins claiming the same top-level CLI command name.
6. Two plugins claiming the same dashboard route.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult
from supervisor.plugins.manifest import parse_manifest

if TYPE_CHECKING:
    from pathlib import Path

_CHECK_NAME = "plugin-consistency"

# Core CLI top-level command names.  Kept in sync manually; the startup
# pre-check in cli.py catches collisions at runtime regardless.
_CORE_COMMANDS = frozenset(
    {
        "auth",
        "branch",
        "check",
        "docs",
        "git",
        "plugins",
        "repos",
        "services",
        "todo",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_has_name(path: Path, name: str) -> bool:
    """Check whether *path* defines a top-level name (function, class, or assignment).

    Uses AST parsing so we don't need to import the module (which could
    have heavy side-effects or unsatisfied deps).
    """
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return False

    for node in ast.iter_child_nodes(tree):
        # def name(...) or async def name(...)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return True
        # Class definition with the target name.
        if isinstance(node, ast.ClassDef) and node.name == name:
            return True
        # name = ... (simple assignment)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
        # name: Type = ... (annotated assignment)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return True
    return False


# ---------------------------------------------------------------------------
# Gate check
# ---------------------------------------------------------------------------


@register_check(_CHECK_NAME, group="gate", timeout=10)
async def plugin_consistency(ctx: CheckContext) -> CheckResult:
    """Validate structural consistency of all plugin manifests."""
    plugins_dir = ctx.root / "plugins"

    if not plugins_dir.is_dir():
        return CheckResult(
            name=_CHECK_NAME,
            outcome="pass",
            duration_ms=0,
            message="no plugins/ directory found -- nothing to check",
        )

    errors: list[str] = []

    # Collect parsed manifests for cross-plugin conflict detection.
    # Keys: plugin name, values: parsed manifest.
    parsed: dict[str, object] = {}

    # Track command names and dashboard routes across plugins for
    # conflict detection.
    command_owners: dict[str, str] = {}  # command name -> plugin name
    route_owners: dict[str, str] = {}  # dashboard route -> plugin name

    for plugin_dir in sorted(plugins_dir.iterdir()):
        toml_path = plugin_dir / "plugin.toml"
        if not plugin_dir.is_dir() or not toml_path.exists():
            continue

        # 1. Parse manifest.
        try:
            manifest = parse_manifest(plugin_dir)
        except Exception as exc:
            errors.append(f"{plugin_dir.name}: manifest parse error: {exc}")
            continue

        parsed[manifest.name] = manifest

        # 2. Commands -> handlers.py with register_cli.
        if manifest.commands:
            handlers_path = plugin_dir / "handlers.py"
            if not handlers_path.is_file():
                errors.append(f"{manifest.name}: declares commands but handlers.py not found")
            elif not _module_has_name(handlers_path, "register_cli"):
                errors.append(f"{manifest.name}: handlers.py missing 'register_cli' function")

        # 3. Checks -> checks.py with named handler functions.
        if manifest.checks:
            checks_path = plugin_dir / "checks.py"
            if not checks_path.is_file():
                errors.append(f"{manifest.name}: declares checks but checks.py not found")
            else:
                errors.extend(
                    f"{manifest.name}: checks.py missing handler '{decl.handler}' for check '{decl.name}'"
                    for decl in manifest.checks
                    if not _module_has_name(checks_path, decl.handler)
                )

        # 4. Dashboard -> routes.py with router object.
        if manifest.dashboard is not None:
            routes_path = plugin_dir / "routes.py"
            if not routes_path.is_file():
                errors.append(f"{manifest.name}: declares dashboard but routes.py not found")
            elif not _module_has_name(routes_path, "router"):
                errors.append(f"{manifest.name}: routes.py missing 'router' object")

        # 5. Collect command names for cross-plugin conflict detection.
        for cmd in manifest.commands:
            if cmd.name in command_owners:
                errors.append(
                    f"command name conflict: '{cmd.name}' declared by both "
                    f"'{command_owners[cmd.name]}' and '{manifest.name}'"
                )
            else:
                command_owners[cmd.name] = manifest.name

        # 6. Collect dashboard routes for cross-plugin conflict detection.
        if manifest.dashboard is not None:
            route = manifest.dashboard.route
            if route in route_owners:
                errors.append(
                    f"dashboard route conflict: '{route}' declared by both "
                    f"'{route_owners[route]}' and '{manifest.name}'"
                )
            else:
                route_owners[route] = manifest.name

    # 7. Check plugin command names against core CLI commands.
    for cmd_name, owner in command_owners.items():
        if cmd_name in _CORE_COMMANDS:
            errors.append(f"'{owner}' command name '{cmd_name}' conflicts with core CLI command")

    if errors:
        return CheckResult(
            name=_CHECK_NAME,
            outcome="fail",
            duration_ms=0,
            message="\n".join(errors),
        )

    return CheckResult(
        name=_CHECK_NAME,
        outcome="pass",
        duration_ms=0,
        message=f"all {len(parsed)} plugin(s) consistent",
    )
