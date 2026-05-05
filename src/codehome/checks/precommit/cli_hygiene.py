"""CLI hygiene: detect stale command references and orphaned handlers.

The CLI dispatcher uses ``set_defaults(_cmd=("module.path", "handler"))``
tuples.  If a module is renamed/deleted or a handler function is removed,
the CLI silently breaks at runtime.

Two checks:

1. **command-file-mismatch** -- parse ``cli.py`` for all ``_cmd=(...)``
   tuples, verify each referenced module file exists on disk and each
   referenced handler function exists in that module (via ``importlib``).

2. **orphaned-cmd-functions** -- scan all ``cmd_*`` function definitions
   in ``src/codehome/commands/`` and flag any that are not wired into
   ``cli.py``'s dispatch table and are not referenced by a wired handler
   in the same module.  Catches dead command handlers that are defined
   but never dispatched.
"""

from __future__ import annotations

import ast
import importlib
import re
from typing import TYPE_CHECKING

from codehome.checks.registry import CheckContext, register_check
from codehome.checks.result import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

# Regex to extract _cmd=("module.path", "handler") from cli.py.
# Handles both single-quoted and double-quoted strings, and allows
# whitespace around the tuple elements.
_CMD_PATTERN = re.compile(
    r"""_cmd\s*=\s*\(\s*"""
    r"""["']([^"']+)["']"""  # group 1: module path
    r"""\s*,\s*"""
    r"""["']([^"']+)["']"""  # group 2: handler name
    r"""\s*\)""",
)


def _module_to_path(module_path: str, src: Path) -> Path:
    """Convert a dotted module path to its expected file path.

    Handles both flat modules (codehome.commands.branch -> branch.py)
    and packages (codehome.commands.publisher -> publisher/__init__.py).
    """
    parts = module_path.split(".")
    base = src / "/".join(parts)
    # Could be a flat module (foo.py) or a package (foo/__init__.py).
    flat = base.with_suffix(".py")
    package = base / "__init__.py"
    return flat if flat.exists() else package


def _parse_cli_cmd_refs(cli_py: Path) -> set[tuple[str, str]]:
    """Parse all ``_cmd=("module", "handler")`` tuples from cli.py.

    Returns a set of (module_path, handler_name) pairs, deduplicated.
    """
    text = cli_py.read_text()
    refs: set[tuple[str, str]] = set()
    for match in _CMD_PATTERN.finditer(text):
        refs.add((match.group(1), match.group(2)))
    return refs


@register_check("command-file-mismatch", group="precommit", timeout=5)
async def command_file_mismatch(ctx: CheckContext) -> CheckResult:
    """Verify all _cmd=(module, handler) references in cli.py are valid."""
    cli_py = ctx.root / "src" / "codehome" / "cli.py"
    if not cli_py.exists():
        return CheckResult(
            name="command-file-mismatch",
            outcome="fail",
            duration_ms=0,
            message=f"cli.py not found at {cli_py}",
        )

    src = ctx.root / "src"
    refs = _parse_cli_cmd_refs(cli_py)

    if not refs:
        return CheckResult(
            name="command-file-mismatch",
            outcome="fail",
            duration_ms=0,
            message="no _cmd=(...) patterns found in cli.py -- parser changed?",
        )

    failures: list[str] = []

    for module_path, handler_name in sorted(refs):
        # Check 1: does the module file exist on disk?
        file_path = _module_to_path(module_path, src)
        if not file_path.exists():
            failures.append(f"{module_path}: module file not found ({file_path})")
            continue

        # Check 2: does the handler function exist in the module?
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            failures.append(f"{module_path}: import failed ({type(exc).__name__}: {exc})")
            continue

        if not hasattr(mod, handler_name):
            failures.append(f"{module_path}.{handler_name}: handler not found in module")

    if failures:
        detail = "\n".join(f"  {f}" for f in failures)
        return CheckResult(
            name="command-file-mismatch",
            outcome="fail",
            duration_ms=0,
            message=f"{len(failures)} command reference(s) broken:\n{detail}",
        )

    return CheckResult(
        name="command-file-mismatch",
        outcome="pass",
        duration_ms=0,
        message=f"all {len(refs)} command references valid",
    )


# ---------------------------------------------------------------------------
# Orphaned cmd_* detection
# ---------------------------------------------------------------------------


def _collect_cmd_definitions(commands_dir: Path) -> list[tuple[str, str]]:
    """Find all top-level ``cmd_*`` function defs in commands/ .py files.

    Returns (module_dotted_path, function_name) pairs.  Only scans
    top-level function definitions (col_offset == 0) to avoid picking
    up nested helpers.
    """
    results: list[tuple[str, str]] = []
    for py_file in sorted(commands_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        # Derive the dotted module path from the file path.
        # e.g. .../src/codehome/commands/branch.py -> codehome.commands.branch
        # e.g. .../src/codehome/commands/publisher/__init__.py -> codehome.commands.publisher
        rel = py_file.relative_to(commands_dir.parent.parent)  # relative to src/
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        module_path = ".".join(parts)

        results.extend(
            (module_path, node.name)
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("cmd_")
            and node.col_offset == 0
        )
    return results


def _collect_per_function_refs(py_file: Path) -> dict[str, set[str]]:
    """For each top-level function in *py_file*, collect names it references.

    Returns ``{func_name: {referenced_names, ...}}``.  Includes a
    synthetic ``__module_level__`` entry for names referenced at module
    scope (e.g. in dict literals that map subcommand names to handlers).
    Used to build a call graph for reachability analysis from wired handlers.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return {}

    result: dict[str, set[str]] = {}
    # Collect module-level references (outside any function body).
    # These cover dispatch dicts like _STAGING_SUBCOMMANDS that reference
    # cmd_* functions at module scope.
    func_nodes: set[int] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.add(id(node))
            refs: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name):
                    refs.add(child.id)
                elif isinstance(child, ast.Attribute):
                    refs.add(child.attr)
            result[node.name] = refs

    # Module-level names: walk all non-function top-level nodes.
    module_refs: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if id(node) in func_nodes:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                module_refs.add(child.id)
            elif isinstance(child, ast.Attribute):
                module_refs.add(child.attr)
    result["__module_level__"] = module_refs
    return result


def _package_key(module_path: str) -> str:
    """Return the top-level package under codehome.commands for grouping.

    ``codehome.commands.telemac.transport`` -> ``codehome.commands.telemac``
    ``codehome.commands.branch``            -> ``codehome.commands.branch``

    Sub-module cmd_* handlers are called by their parent package's wired
    handler, so reachability must span the entire package.
    """
    prefix = "codehome.commands."
    if not module_path.startswith(prefix):
        return module_path
    rest = module_path[len(prefix) :]
    top = rest.split(".")[0]
    return prefix + top


def _collect_plugin_wired_refs(plugins_dir: Path) -> set[tuple[str, str]]:
    """Scan plugin handlers.py files for imports from codehome.commands.

    Plugin handlers that delegate to ``codehome.commands.*`` functions
    make those functions reachable.  This returns (module, func) pairs
    for any ``cmd_*`` function imported or referenced in plugin handler
    files, so the orphan check treats them as wired.
    """
    refs: set[tuple[str, str]] = set()
    if not plugins_dir.is_dir():
        return refs

    for handler_file in sorted(plugins_dir.glob("*/handlers.py")):
        try:
            source = handler_file.read_text()
            tree = ast.parse(source, filename=str(handler_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            # Match: from codehome.commands.foo import bar
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("codehome.commands."):
                for alias in node.names:
                    real_name = alias.name
                    refs.add((node.module, real_name))
    return refs


@register_check("orphaned-cmd-functions", group="precommit", timeout=5)
async def orphaned_cmd_functions(ctx: CheckContext) -> CheckResult:
    """Detect cmd_* functions in commands/ not wired into cli.py's dispatch table.

    A cmd_* function is orphaned if it is not reachable from any handler
    wired into cli.py or delegated by a plugin handler.  Reachability is
    computed transitively across all files in the same package: if a wired
    handler in ``__init__.py`` imports and calls ``cmd_foo`` from a
    sub-module, ``cmd_foo`` is reachable.  Plugin handlers that import
    from ``codehome.commands.*`` also count as wiring.
    """
    cli_py = ctx.root / "src" / "codehome" / "cli.py"
    commands_dir = ctx.root / "src" / "codehome" / "commands"

    if not cli_py.exists() or not commands_dir.is_dir():
        return CheckResult(
            name="orphaned-cmd-functions",
            outcome="fail",
            duration_ms=0,
            message="cli.py or commands/ directory not found",
        )

    # All (module, handler) pairs wired in cli.py.
    wired_refs = _parse_cli_cmd_refs(cli_py)
    # Also include references from plugin handlers that delegate to
    # codehome.commands.* functions (these are not orphans).
    plugin_refs = _collect_plugin_wired_refs(ctx.root / "plugins")
    wired_refs |= plugin_refs
    wired_set: set[tuple[str, str]] = set(wired_refs)

    # All cmd_* definitions in commands/.
    all_defs = _collect_cmd_definitions(commands_dir)

    # Group definitions by package so sub-module handlers are analysed
    # together with their parent's wired handler.
    defs_by_package: dict[str, list[tuple[str, str]]] = {}
    for mod, func in all_defs:
        pkg = _package_key(mod)
        defs_by_package.setdefault(pkg, []).append((mod, func))

    # Build per-function call graphs for all command files.
    call_graph_cache: dict[str, dict[str, set[str]]] = {}
    src = ctx.root / "src"

    orphans: list[tuple[str, str]] = []

    for pkg, pkg_defs in sorted(defs_by_package.items()):
        # Merge call graphs from all modules in this package.
        merged_graph: dict[str, set[str]] = {}
        modules_in_pkg = {mod for mod, _ in pkg_defs}
        # Also include the package itself if it has a wired handler
        # (e.g. codehome.commands.telemac wired, sub-modules are not).
        for mod_path, _handler in wired_set:
            if _package_key(mod_path) == pkg:
                modules_in_pkg.add(mod_path)

        for mod in modules_in_pkg:
            file_path = _module_to_path(mod, src)
            file_key = str(file_path)
            if file_key not in call_graph_cache:
                call_graph_cache[file_key] = _collect_per_function_refs(file_path)
            merged_graph.update(call_graph_cache[file_key])

        # Seed reachable set with wired handlers in this package.
        # Also seed __module_level__ (module-scope references like dispatch
        # dicts) for modules that have wired handlers, since those modules
        # are imported by cli.py and their module-level code runs.
        reachable: set[str] = set()
        for mod_path, handler in wired_set:
            if _package_key(mod_path) == pkg:
                reachable.add(handler)
        if reachable:
            reachable.add("__module_level__")

        # Expand reachability transitively through ALL functions in the
        # merged call graph (not just cmd_*).  A wired handler may call
        # a private wrapper like _call_build which in turn calls cmd_build.
        all_func_names = set(merged_graph.keys())
        changed = True
        while changed:
            changed = False
            for reached_fn in list(reachable):
                refs = merged_graph.get(reached_fn, set())
                for candidate in all_func_names - reachable:
                    if candidate in refs:
                        reachable.add(candidate)
                        changed = True

        for mod, func in pkg_defs:
            if func not in reachable:
                orphans.append((mod, func))

    if not orphans:
        return CheckResult(
            name="orphaned-cmd-functions",
            outcome="pass",
            duration_ms=0,
            message=f"all {len(all_defs)} cmd_* functions are wired or internally referenced",
        )

    lines = [f"{mod}.{func}" for mod, func in sorted(orphans)]
    detail = "\n".join(f"  {line}" for line in lines)
    return CheckResult(
        name="orphaned-cmd-functions",
        outcome="fail",
        duration_ms=0,
        message=f"{len(orphans)} orphaned cmd_* function(s) not wired into cli.py:\n{detail}",
    )
