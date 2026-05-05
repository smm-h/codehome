"""Dead code detection: find unreferenced functions and classes.

Best-effort heuristic using the ``ast`` module to find Python
functions and classes in ``src/supervisor/`` and ``repos/*/extensions/``
that are defined but never referenced anywhere in the scanned files.
False positives can be suppressed with a ``# noqa: dead-code`` comment
on the definition line.

Limitations (acceptable):
- Dynamic dispatch (getattr, __import__) won't be detected as references.
- String-based references (e.g. in JSON configs) are invisible.
- Cross-package usage outside the scanned directories is not checked.

These are inherent to static analysis without type information. The
check is advisory-grade: it surfaces candidates for cleanup, not a
definitive dead-code list.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

# Decorators whose presence means the function is called by a framework,
# not by direct reference in source code.
_FRAMEWORK_DECORATORS: frozenset[str] = frozenset(
    {
        "register_check",
        "register_step",
        "extension",
        "pytest.fixture",
        "fixture",
        "app.tool",
    }
)

# HTTP method suffixes that indicate a route handler regardless of the
# router variable name (router, public_router, authed_router, app, etc.).
_ROUTE_SUFFIXES: frozenset[str] = frozenset(
    {".get", ".post", ".put", ".patch", ".delete", ".websocket", ".api_route", ".on_event"}
)


def _decorator_name(node: ast.expr) -> str:
    """Extract a human-readable name from a decorator AST node.

    Handles plain names (``@foo``), attribute access (``@app.get``),
    and call forms (``@register_check(...)``).
    """
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        value_name = _decorator_name(node.value)
        return f"{value_name}.{node.attr}" if value_name else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _has_framework_decorator(decorators: list[ast.expr]) -> bool:
    """Return True if any decorator matches a known framework pattern."""
    for dec in decorators:
        name = _decorator_name(dec)
        if name in _FRAMEWORK_DECORATORS:
            return True
        # Match any *.get, *.post, etc. regardless of router variable name.
        dot = name.rfind(".")
        if dot > 0 and name[dot:] in _ROUTE_SUFFIXES:
            return True
    return False


def _line_has_noqa(source_lines: list[str], lineno: int) -> bool:
    """Check if the definition line has a ``# noqa: dead-code`` comment."""
    if lineno < 1 or lineno > len(source_lines):
        return False
    return "# noqa: dead-code" in source_lines[lineno - 1]


def _is_init_reexport(filepath: Path, name: str, source: str) -> bool:
    """Check if *name* in an __init__.py is a re-export (imported then in __all__)."""
    if filepath.name != "__init__.py":
        return False
    # If the name appears in __all__, it's a public re-export.
    return f'"{name}"' in source or f"'{name}'" in source


def _collect_definitions(py_files: list[Path], src_root: Path) -> list[tuple[str, str, int, str]]:
    """Walk all files and collect (name, relative_path, lineno, kind) tuples.

    Applies exclusion rules to skip framework-registered, dunder,
    test_, cmd_*, and noqa-suppressed definitions.
    """
    defs: list[tuple[str, str, int, str]] = []

    for filepath in py_files:
        try:
            source = filepath.read_text()
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            continue

        source_lines = source.splitlines()
        rel = str(filepath.relative_to(src_root))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                kind = "function"
                decorators = node.decorator_list
            elif isinstance(node, ast.ClassDef):
                name = node.name
                kind = "class"
                decorators = node.decorator_list
            else:
                continue

            # Exclusion: dunder methods (magic methods).
            if name.startswith("__") and name.endswith("__"):
                continue

            # Exclusion: test functions and test classes.
            if name.startswith("test_") or (kind == "class" and name.startswith("Test")):
                continue

            # Exclusion: CLI command handlers (called via dispatch).
            if name.startswith("cmd_"):
                continue

            # Exclusion: framework-registered (decorators).
            if _has_framework_decorator(decorators):
                continue

            # Exclusion: noqa suppression.
            if _line_has_noqa(source_lines, node.lineno):
                continue

            # Exclusion: __init__.py re-exports.
            if _is_init_reexport(filepath, name, source):
                continue

            # Exclusion: skip methods (they're referenced via self.method()).
            # Only track top-level and class-level definitions that are
            # not methods -- but since ast.walk flattens, we check if
            # the node is nested inside a ClassDef by looking at col_offset.
            # A more reliable approach: skip functions defined inside classes.
            # We detect this by checking if any ClassDef in the tree
            # contains this node's line range.
            # For simplicity, skip anything with col_offset > 0 that is
            # a function (likely a method).
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset > 0:
                continue

            defs.append((name, rel, node.lineno, kind))

    return defs


def _collect_references(py_files: list[Path]) -> set[str]:
    """Collect all name references across all files.

    Gathers: bare names, attribute targets, import names, and
    string arguments to common dynamic-dispatch patterns.
    """
    refs: set[str] = set()

    for filepath in py_files:
        try:
            source = filepath.read_text()
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            # Direct name references (function calls, variable access).
            if isinstance(node, ast.Name):
                refs.add(node.id)

            # Attribute access (obj.method -- record 'method').
            elif isinstance(node, ast.Attribute):
                refs.add(node.attr)

            # Import statements: from X import name1, name2.
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    refs.add(alias.name)
                    if alias.asname:
                        refs.add(alias.asname)

            # import X.Y.Z -- record each component.
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for part in alias.name.split("."):
                        refs.add(part)
                    if alias.asname:
                        refs.add(alias.asname)

            # String literals in getattr/hasattr calls.
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr")
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                refs.add(node.args[1].value)

            # set_defaults(_cmd=("module", "func_name")) dispatch pattern.
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_defaults":
                for kw in node.keywords:
                    if kw.arg == "_cmd" and isinstance(kw.value, ast.Tuple) and len(kw.value.elts) == 2:
                        func_elt = kw.value.elts[1]
                        if isinstance(func_elt, ast.Constant) and isinstance(func_elt.value, str):
                            refs.add(func_elt.value)

    return refs


def _find_py_files(supervisor_dir: Path) -> list[Path]:
    """Collect all .py files under the supervisor package, excluding __pycache__."""
    return sorted(p for p in supervisor_dir.rglob("*.py") if "__pycache__" not in p.parts)


def _find_extension_files() -> list[Path]:
    """Collect .py files from each repo's extensions/ dir, excluding __pycache__.

    Uses load_repos()/repo_dir() to support both legacy and new project layouts.
    """
    from supervisor.config import load_repos
    from supervisor.paths import repo_dir

    results: list[Path] = []
    for repo_name in sorted(load_repos()):
        ext_dir = repo_dir(repo_name) / "extensions"
        if ext_dir.is_dir():
            results.extend(p for p in ext_dir.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(results)


@register_check("dead-code", group="gate", timeout=30)
async def dead_code(ctx: CheckContext) -> CheckResult:
    """Find Python functions/classes with zero references in src/supervisor/ and repos/*/extensions/."""
    supervisor_dir = ctx.root / "src" / "supervisor"
    if not supervisor_dir.is_dir():
        return CheckResult(
            name="dead-code",
            outcome="fail",
            duration_ms=0,
            message=f"supervisor package not found at {supervisor_dir}",
        )

    py_files = _find_py_files(supervisor_dir)
    # Also include repo extension files for both definition and reference scanning.
    py_files.extend(_find_extension_files())
    # Include plugin files for reference scanning (so calls from plugins
    # to core functions are detected). Plugins are scanned for references
    # only -- definitions in plugins are not flagged as dead.
    plugins_dir = ctx.root / "plugins"
    plugin_ref_files: list[Path] = []
    if plugins_dir.is_dir():
        plugin_ref_files = sorted(
            p for p in plugins_dir.rglob("*.py")
            if "__pycache__" not in p.parts and "node_modules" not in p.parts
        )
    if not py_files:
        return CheckResult(
            name="dead-code",
            outcome="fail",
            duration_ms=0,
            message="no .py files found in src/supervisor/",
        )

    # src_root is the parent of src/supervisor/ so relative paths
    # start with src/supervisor/...
    src_root = ctx.root

    definitions = _collect_definitions(py_files, src_root)
    # Scan both core files and plugin files for references.
    all_ref_files = py_files + plugin_ref_files
    references = _collect_references(all_ref_files)

    # Find definitions with zero references anywhere.
    unreferenced = [(name, path, lineno, kind) for name, path, lineno, kind in definitions if name not in references]

    if not unreferenced:
        return CheckResult(
            name="dead-code",
            outcome="pass",
            duration_ms=0,
            message=f"scanned {len(definitions)} definitions across {len(py_files)} files, all referenced",
        )

    # Build human-readable report sorted by file path then line number.
    unreferenced.sort(key=lambda x: (x[1], x[2]))
    lines = [f"{path}:{lineno}: {name} (0 references)" for name, path, lineno, _kind in unreferenced]
    detail = "\n".join(lines)

    return CheckResult(
        name="dead-code",
        outcome="fail",
        duration_ms=0,
        message=f"{len(unreferenced)} potentially dead definition(s):\n{detail}",
        fix=None,
    )
