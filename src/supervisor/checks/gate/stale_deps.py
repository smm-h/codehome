"""Stale dependency detection: flag pyproject.toml deps with no matching imports.

Parses [project.dependencies] from pyproject.toml, normalizes package
names to importable module names, then scans all .py files under src/
and plugins/ for matching import statements.  Dependencies with zero
matching imports are reported as potentially unused.

Additional advisory diagnostics (informational, never cause failure):
- **Test-only deps**: imported only in test files, should be in [dependency-groups] dev.
- **Undeclared transitive deps**: imported in production code but not declared.
- **Concentrated usage**: deps imported in very few files (1-2).

This is a heuristic check (advisory): packages may be used at runtime
via entry points, CLI tools, or dynamic imports that static scanning
cannot detect.  The hardcoded allowlist covers known name mismatches
in this project.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from typing import TYPE_CHECKING

from supervisor.checks.registry import CheckContext, register_check
from supervisor.checks.result import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

# Packages whose importable name differs from the PyPI package name.
_IMPORT_NAME_MAP: dict[str, str] = {
    "python-dotenv": "dotenv",
    "websocket-client": "websocket",
    "pillow": "PIL",
    "pyjwt": "jwt",
    "psycopg2-binary": "psycopg2",
}

# Dev tools / linters that are invoked as CLI commands, not imported.
_DEV_TOOL_PREFIXES: tuple[str, ...] = (
    "ruff",
    "mypy",
    "pytest",
    "black",
    "pyright",
)

# Undeclared transitive deps that are acceptable (not fragile in practice).
_TRANSITIVE_ALLOWLIST: dict[str, str] = {
    "starlette": "via fastapi",
    "pydantic": "via fastapi",
    "pydantic_core": "via pydantic",
    "anyio": "via starlette",
    "sniffio": "via anyio",
    "typing_extensions": "stdlib backport",
    "incantino": "sibling sub-project (incantino/tooling/)",
    "py_vapid": "via pywebpush",
    "websockets": "via uvicorn[standard], used in vite HMR proxy",
    "yaml": "PyYAML, via incantino plugin handlers",
}

# Deps with concentrated usage that are expected to appear in few files.
_CONCENTRATED_ALLOWLIST: dict[str, str] = {
    "granian": "ASGI server, dashboard only",
    "mcp": "MCP protocol server",
}

# Python stdlib module names (Python 3.10+).
_STDLIB_NAMES: frozenset[str] = frozenset(sys.stdlib_module_names)


def _normalize_package_name(raw: str) -> str:
    """Strip extras and version specifiers, lowercase, replace hyphens with underscores."""
    # "pyjwt[crypto]>=2.8.0" -> "pyjwt"
    name = re.split(r"[\[>=<~!;@]", raw, maxsplit=1)[0].strip()
    return name.lower().replace("-", "_")


def _importable_name(normalized: str, raw_lower: str) -> str:
    """Map a normalized package name to the name used in import statements."""
    if raw_lower in _IMPORT_NAME_MAP:
        return _IMPORT_NAME_MAP[raw_lower]
    return normalized


def _is_dev_tool(raw_lower: str) -> bool:
    """Return True if the package is a dev tool that wouldn't be imported."""
    return any(raw_lower.startswith(prefix) for prefix in _DEV_TOOL_PREFIXES)


def _is_test_file(path: Path) -> bool:
    """Return True if *path* is a test file (test suite, test_*.py, conftest.py)."""
    return "tests" in path.parts or path.name.startswith("test_") or path.name == "conftest.py"


def _parse_dependencies(pyproject_path: Path) -> list[tuple[str, str]]:
    """Parse pyproject.toml and return (raw_lower, importable_name) pairs.

    Skips dev tools that are never imported.
    """
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    raw_deps: list[str] = data.get("project", {}).get("dependencies", [])
    result: list[tuple[str, str]] = []

    for raw in raw_deps:
        normalized = _normalize_package_name(raw)
        # Preserve the hyphenated lowercase form for _IMPORT_NAME_MAP lookups.
        raw_lower = re.split(r"[\[>=<~!;@]", raw, maxsplit=1)[0].strip().lower()

        if _is_dev_tool(raw_lower):
            continue

        importable = _importable_name(normalized, raw_lower)
        result.append((raw_lower, importable))

    return result


def _extract_imports_ast(text: str) -> set[str]:
    """Extract top-level package names from Python source using AST parsing.

    Returns the root module name (first dotted component) for every
    ``import X`` and ``from X import ...`` statement.  Skips files that
    fail to parse (SyntaxError).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _parse_dependency_group_names(pyproject_path: Path) -> set[str]:
    """Parse [dependency-groups] from pyproject.toml and return importable names.

    Deps declared in any dependency group (e.g. dev) should not be flagged
    as undeclared transitive deps.
    """
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    groups: dict[str, list[str]] = data.get("dependency-groups", {})
    names: set[str] = set()
    for deps in groups.values():
        for raw in deps:
            if not isinstance(raw, str):
                continue  # skip non-string entries (e.g. include-group dicts)
            normalized = _normalize_package_name(raw)
            raw_lower = re.split(r"[\[>=<~!;@]", raw, maxsplit=1)[0].strip().lower()
            names.add(_importable_name(normalized, raw_lower))
    return names


def _collect_imports(py_files: list[Path]) -> set[str]:
    """Scan Python files for top-level import names using AST parsing.

    Collects the first component of every ``import X`` and ``from X import ...``
    statement.  Uses ``ast.parse`` to avoid false positives from docstrings or
    comments that happen to contain import-like patterns.
    """
    names: set[str] = set()

    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names.update(_extract_imports_ast(text))

    return names


def _collect_imports_per_file(py_files: list[Path]) -> dict[Path, set[str]]:
    """Scan Python files and return a mapping of file -> set of imported top-level names.

    Uses AST parsing to avoid false positives from docstrings or comments.
    """
    result: dict[Path, set[str]] = {}

    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        result[path] = _extract_imports_ast(text)

    return result


def _find_sub_project_packages(*dirs: Path) -> set[Path]:
    """Find package directories owned by nested sub-projects.

    A sub-project is a directory containing a pyproject.toml that declares
    a different project name than the top-level one.  We parse its
    ``[tool.hatch.build.targets.wheel] packages`` list (Hatchling
    convention) to determine which subdirectories belong to it.  Only
    those subdirectories are excluded from the import scan -- sibling
    library code in the same parent remains included.
    """
    excluded: set[Path] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for toml_path in d.rglob("pyproject.toml"):
            if toml_path.parent == d.parent:
                continue  # skip the root-level pyproject.toml
            try:
                with toml_path.open("rb") as f:
                    data = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            # Only exclude sub-projects (different project name).
            sub_name = data.get("project", {}).get("name", "")
            if sub_name == "supervisor":
                continue
            # Hatch packages list tells us which dirs belong to this sub-project.
            pkgs = (
                data.get("tool", {})
                .get("hatch", {})
                .get("build", {})
                .get("targets", {})
                .get("wheel", {})
                .get("packages", [])
            )
            parent = toml_path.parent
            if pkgs:
                for pkg in pkgs:
                    pkg_dir = parent / pkg
                    if pkg_dir.is_dir():
                        excluded.add(pkg_dir)
            else:
                # No explicit packages list -- conservatively exclude the
                # whole sub-project directory.
                excluded.add(parent)
    return excluded


def _find_py_files(*dirs: Path) -> list[Path]:
    """Collect all .py files under the given directories.

    Excludes __pycache__ and sub-project package directories (those owned
    by a nested pyproject.toml with a different project name), since
    sub-project imports shouldn't count toward the top-level dependency
    budget.
    """
    sub_packages = _find_sub_project_packages(*dirs)
    files: list[Path] = []
    for d in dirs:
        if d.is_dir():
            for p in d.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                if any(p.is_relative_to(sp) for sp in sub_packages):
                    continue
                files.append(p)
    return sorted(files)


def _collect_local_module_names(py_files: list[Path]) -> set[str]:
    """Collect module basenames defined by the scanned source files.

    Any .py file in the scan tree defines a local module name (its stem).
    Bare ``import config`` inside ``telemac/sign.py`` refers to the sibling
    ``telemac/config.py``, not a third-party package.  Collecting these
    names lets us exclude them from the undeclared-transitive check.
    """
    return {p.stem for p in py_files}


def _is_third_party_import(name: str, local_modules: set[str]) -> bool:
    """Return True if *name* looks like a third-party import (not stdlib, not internal)."""
    if name in _STDLIB_NAMES:
        return False
    # Internal package -- not a third-party dep.
    if name == "supervisor" or name.startswith("supervisor."):
        return False
    # Local module defined in the source tree (bare sibling import).
    if name in local_modules:
        return False
    # Common false positives: relative imports captured as bare names, builtins.
    return not name.startswith("_")


def _detect_test_only_deps(
    deps: list[tuple[str, str]],
    prod_imports: set[str],
    test_imports: set[str],
) -> list[str]:
    """Return dep names imported only in test files, not production code."""
    test_only: list[str] = []
    for raw_lower, importable in deps:
        if importable not in prod_imports and importable in test_imports:
            test_only.append(raw_lower)
    return sorted(test_only)


def _detect_undeclared_transitive(
    declared_importables: set[str],
    prod_imports: set[str],
    local_modules: set[str],
) -> list[tuple[str, str | None]]:
    """Return third-party imports in production code that aren't declared deps.

    Returns (import_name, allowlist_reason_or_None) pairs, sorted by name.
    """
    undeclared: list[tuple[str, str | None]] = []
    for name in sorted(prod_imports):
        if not _is_third_party_import(name, local_modules):
            continue
        if name in declared_importables:
            continue
        reason = _TRANSITIVE_ALLOWLIST.get(name)
        undeclared.append((name, reason))
    return undeclared


def _detect_concentrated_usage(
    deps: list[tuple[str, str]],
    per_file_imports: dict[Path, set[str]],
    prod_files: list[Path],
    threshold: int = 2,
) -> list[tuple[str, int, str | None]]:
    """Return deps imported in very few production files (<=threshold).

    Only considers deps that ARE imported somewhere (not stale).
    Returns (raw_lower, file_count, allowlist_reason_or_None) triples.
    """
    concentrated: list[tuple[str, int, str | None]] = []
    for raw_lower, importable in deps:
        count = sum(1 for f in prod_files if f in per_file_imports and importable in per_file_imports[f])
        if 0 < count <= threshold:
            reason = _CONCENTRATED_ALLOWLIST.get(raw_lower)
            concentrated.append((raw_lower, count, reason))
    return sorted(concentrated, key=lambda t: t[0])


# Maximum number of importing files before a dep is no longer "concentrated".
_CONCENTRATED_THRESHOLD = 2


def _format_notes(
    test_only: list[str],
    undeclared: list[tuple[str, str | None]],
    concentrated: list[tuple[str, int, str | None]],
) -> list[str]:
    """Build informational note lines for the advisory diagnostics."""
    notes: list[str] = []

    if test_only:
        dep_str = ", ".join(test_only)
        n = len(test_only)
        notes.append(f"  note: {n} dep{'s' if n != 1 else ''} imported only in test code: {dep_str}")

    if undeclared:
        parts = []
        for name, reason in undeclared:
            if reason:
                parts.append(f"{name} ({reason})")
            else:
                parts.append(name)
        dep_str = ", ".join(parts)
        n = len(undeclared)
        notes.append(f"  note: {n} undeclared transitive dep{'s' if n != 1 else ''} imported: {dep_str}")

    if concentrated:
        parts = []
        for raw_lower, count, reason in concentrated:
            entry = f"{raw_lower} ({count} file{'s' if count != 1 else ''})"
            if reason:
                entry += f" [allowlisted: {reason}]"
            parts.append(entry)
        dep_str = ", ".join(parts)
        n = len(concentrated)
        notes.append(f"  note: {n} dep{'s' if n != 1 else ''} used in <={_CONCENTRATED_THRESHOLD} files: {dep_str}")

    return notes


@register_check("stale-deps", group="gate", timeout=30, advisory=True)
async def stale_deps(ctx: CheckContext) -> CheckResult:
    """Flag pyproject.toml dependencies with no matching imports in src/ and plugins/."""
    pyproject_path = ctx.root / "pyproject.toml"
    if not pyproject_path.is_file():
        return CheckResult(
            name="stale-deps",
            outcome="pass",
            duration_ms=0,
            message="no pyproject.toml found, nothing to check",
        )

    deps = _parse_dependencies(pyproject_path)
    if not deps:
        return CheckResult(
            name="stale-deps",
            outcome="pass",
            duration_ms=0,
            message="no dependencies declared in pyproject.toml",
        )

    all_py_files = _find_py_files(ctx.root / "src", ctx.root / "plugins")

    # Partition files into production and test sets.
    prod_files = [f for f in all_py_files if not _is_test_file(f)]
    test_files = [f for f in all_py_files if _is_test_file(f)]

    # Per-file import map for production files (needed for concentrated usage).
    per_file_imports = _collect_imports_per_file(prod_files)

    # Aggregate import sets.
    prod_imports = set().union(*per_file_imports.values()) if per_file_imports else set()
    test_imports = _collect_imports(test_files)
    all_imports = prod_imports | test_imports

    # Set of importable names for declared deps (used by transitive detection).
    # Include both [project.dependencies] and [dependency-groups] so that
    # dev deps (e.g. httpx) are not flagged as undeclared transitive.
    declared_importables: set[str] = {importable for _, importable in deps}
    declared_importables |= _parse_dependency_group_names(pyproject_path)

    # Primary check: deps whose importable name never appears in any import.
    stale: list[str] = []
    for raw_lower, importable in deps:
        if importable not in all_imports:
            stale.append(raw_lower)

    # Local module basenames (bare sibling imports, not third-party).
    local_modules = _collect_local_module_names(all_py_files)

    # Advisory diagnostics (informational, never cause failure).
    test_only = _detect_test_only_deps(deps, prod_imports, test_imports)
    undeclared = _detect_undeclared_transitive(declared_importables, prod_imports, local_modules)
    concentrated = _detect_concentrated_usage(deps, per_file_imports, prod_files, _CONCENTRATED_THRESHOLD)

    notes = _format_notes(test_only, undeclared, concentrated)
    notes_block = "\n".join(notes) if notes else ""

    if not stale:
        msg = f"all {len(deps)} dependencies have matching imports across {len(all_py_files)} files"
        if notes_block:
            msg += "\n" + notes_block
        return CheckResult(
            name="stale-deps",
            outcome="pass",
            duration_ms=0,
            message=msg,
        )

    listing = "\n".join(f"  - {name}" for name in sorted(stale))
    msg = f"{len(stale)} potentially unused dependenc{'y' if len(stale) == 1 else 'ies'}:\n{listing}"
    if notes_block:
        msg += "\n" + notes_block
    return CheckResult(
        name="stale-deps",
        outcome="fail",
        duration_ms=0,
        message=msg,
    )
