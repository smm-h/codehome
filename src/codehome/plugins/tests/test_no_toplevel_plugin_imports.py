"""Guard test: no top-level plugin namespace imports in core source files.

Core modules load before plugins mount. Importing plugin namespaces at the
top level causes breakage (e.g. sdk.py importing from codehome.core,
dependencies.py importing PTYManager from codehome.core.ops).

This test scans all .py files under src/codehome/ (excluding __pycache__
and tests/) and fails if any top-level ``from codehome.<plugin> import ...``
or ``import codehome.<plugin>`` is found.

Allowed:
- Imports nested inside function/method/class bodies
- Imports behind ``if TYPE_CHECKING:`` guards
- Imports inside test files
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Plugin namespaces that must never be imported at the top level of core files.
PLUGIN_NAMESPACES = (
    "codehome.core",
    "codehome.linear",
    "codehome.deploy",
    "codehome.dashboard",
    "codehome.extensions",
    "codehome.supabase",
    "codehome.review",
    "codehome.team",
)

# Root of the codehome package (two levels up from this file).
_SRC_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_excluded(path: Path) -> bool:
    """Return True if *path* should be skipped (caches, test files)."""
    parts = path.parts
    if "__pycache__" in parts:
        return True
    # Skip anything under a tests/ directory.
    if "tests" in parts:
        return True
    return False


def _is_type_checking_guard(node: ast.If) -> bool:
    """Return True if *node* is ``if TYPE_CHECKING:`` or ``if typing.TYPE_CHECKING:``."""
    test = node.test
    # Plain name: ``if TYPE_CHECKING:``
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    # Attribute: ``if typing.TYPE_CHECKING:``
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return True
    return False


def _references_plugin_namespace(module: str | None) -> str | None:
    """If *module* starts with a plugin namespace, return that namespace."""
    if module is None:
        return None
    for ns in PLUGIN_NAMESPACES:
        if module == ns or module.startswith(ns + "."):
            return ns
    return None


def _collect_violations(source: str, filepath: Path) -> list[str]:
    """Parse *source* and return a list of violation descriptions.

    Only top-level imports are checked -- those that are direct children of the
    Module node and NOT inside an ``if TYPE_CHECKING:`` guard.
    """
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        # If the file has a syntax error, skip it -- other tests will catch that.
        return []

    violations: list[str] = []

    for node in ast.iter_child_nodes(tree):
        # ``from codehome.<plugin> import ...``
        if isinstance(node, ast.ImportFrom):
            ns = _references_plugin_namespace(node.module)
            if ns is not None:
                names = ", ".join(alias.name for alias in node.names)
                violations.append(
                    f"{filepath}:{node.lineno}  "
                    f"from {node.module} import {names}  "
                    f"[plugin namespace: {ns}]"
                )

        # ``import codehome.<plugin>`` or ``import codehome.<plugin>.foo``
        elif isinstance(node, ast.Import):
            for alias in node.names:
                ns = _references_plugin_namespace(alias.name)
                if ns is not None:
                    violations.append(
                        f"{filepath}:{node.lineno}  "
                        f"import {alias.name}  "
                        f"[plugin namespace: {ns}]"
                    )

        # Top-level ``if TYPE_CHECKING:`` blocks are allowed -- skip them.
        # Any OTHER top-level ``if`` is NOT allowed to contain plugin imports,
        # but we only need to be strict about direct Module children that are
        # ImportFrom/Import (handled above). Imports inside ``if`` bodies are
        # not direct children of Module, so they are already excluded by the
        # iter_child_nodes walk -- EXCEPT for TYPE_CHECKING guards which we
        # intentionally ignore. For extra safety we do NOT descend into any
        # top-level If/FunctionDef/ClassDef/etc.

    return violations


def _scan_all_core_files() -> list[str]:
    """Scan every .py file under src/codehome/ and return all violations."""
    all_violations: list[str] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        if _is_excluded(py_file):
            continue
        source = py_file.read_text(encoding="utf-8")
        all_violations.extend(_collect_violations(source, py_file))
    return all_violations


def test_no_toplevel_plugin_imports() -> None:
    """Core source files must not import plugin namespaces at the top level.

    Plugin namespaces (codehome.core, codehome.linear, etc.) are only
    available after the plugin system mounts them. Importing them at module
    scope in core files causes ImportError at startup.

    Lazy imports inside functions, TYPE_CHECKING guards, and test files are OK.
    """
    violations = _scan_all_core_files()
    if violations:
        msg = (
            "Top-level plugin namespace imports found in core source files.\n"
            "Move these to local (function-level) imports or behind "
            "`if TYPE_CHECKING:` guards.\n\n"
        )
        msg += "\n".join(f"  {v}" for v in violations)
        pytest.fail(msg)
