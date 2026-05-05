"""Import smoke test: catch broken imports at commit time.

Ported from ``scripts/check-imports``.  Recursively imports every module
under ``codehome.commands.*``, ``codehome.checks.*``, and
``codehome.extensions.*`` to detect missing imports, stale references,
and typos that static linters miss.

Also scans ``repos/*/extensions/*.py`` via file-based import (using
``importlib.util.spec_from_file_location``) since these are not regular
Python packages -- the same technique the extension loader uses.

Runs the imports in-process since the codehome package is already
loaded.  Any :class:`ImportError` (or other exception) during import
constitutes a check failure.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from typing import TYPE_CHECKING

from codehome.checks.registry import CheckContext, register_check
from codehome.checks.result import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

# Directories to walk.  Each entry is (relative_dir, recursive, exclude_subdirs).
_TARGETS: list[tuple[str, bool, set[str]]] = [
    # commands/: recurse into subpackages (e.g. publisher/)
    ("codehome/commands", True, {"tests"}),
    # checks/: the check framework itself (recurse, skip tests/)
    ("codehome/checks", True, {"tests"}),
    # extensions/: the extension framework (recurse, skip tests/)
    ("codehome/extensions", True, {"tests"}),
]


def _module_name(py_file: Path, src: Path) -> str:
    """Convert a .py path under src/ to its dotted import name."""
    rel = py_file.relative_to(src).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _collect(src: Path) -> list[str]:
    """Collect dotted module names to import, sorted and deduplicated."""
    names: set[str] = set()
    for rel_dir, recursive, exclude in _TARGETS:
        root = src / rel_dir
        if not root.is_dir():
            continue
        iterator = root.rglob("*.py") if recursive else root.glob("*.py")
        for py in iterator:
            if "__pycache__" in py.parts:
                continue
            # Exclude subdirs matched by path component after root.
            rel_to_root = py.relative_to(root)
            if rel_to_root.parts and rel_to_root.parts[0] in exclude:
                continue
            names.add(_module_name(py, src))
    return sorted(names)


def _collect_extension_files(project_root: Path) -> list[Path]:
    """Collect repo extension files (extensions/*.py under each repo dir).

    These are not regular Python packages so they cannot be imported via
    dotted module names.  Returns absolute paths, sorted for determinism.
    Uses load_repos()/repo_dir() to support both legacy and new project layouts.
    """
    from codehome.config import load_repos
    from codehome.paths import repo_dir

    files: list[Path] = []
    for repo_name in sorted(load_repos()):
        ext_dir = repo_dir(repo_name) / "extensions"
        if not ext_dir.is_dir():
            continue
        for py_file in sorted(ext_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            files.append(py_file)
    return files


def _import_extension_file(py_file: Path) -> str | None:
    """Try importing a repo extension file via spec_from_file_location.

    Uses the same technique as the extension loader in
    ``codehome.extensions.loader``.  Returns None on success or an
    error string on failure.
    """
    module_name = f"_ext_check_{py_file.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            return f"could not create import spec for {py_file}"

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)

    except BaseException as exc:
        tb = traceback.format_exc()
        return f"{type(exc).__name__}: {exc}\n{tb}"

    return None


@register_check("check-imports", group="precommit", timeout=10)
async def check_imports(ctx: CheckContext) -> CheckResult:
    """Import all codehome core modules to catch errors."""
    src = ctx.root / "src"
    modules = _collect(src)
    ext_files = _collect_extension_files(ctx.root)

    total = len(modules) + len(ext_files)
    if total == 0:
        return CheckResult(
            name="check-imports",
            outcome="fail",
            duration_ms=0,
            message="no modules found -- layout change?",
        )

    failures: list[tuple[str, str]] = []

    # Standard package imports.
    for name in modules:
        try:
            importlib.import_module(name)
        except BaseException as exc:  # catch SystemExit too
            tb = traceback.format_exc()
            failures.append((name, f"{type(exc).__name__}: {exc}\n{tb}"))

    # File-based extension imports (repos/*/extensions/*.py).
    for ext_file in ext_files:
        err = _import_extension_file(ext_file)
        if err is not None:
            failures.append((str(ext_file), err))

    if failures:
        lines = [f"{len(failures)} of {total} modules failed to import:"]
        for name, err in failures:
            lines.append(f"  {name}: {err.splitlines()[0]}")
        return CheckResult(
            name="check-imports",
            outcome="fail",
            duration_ms=0,
            message="\n".join(lines),
        )

    return CheckResult(
        name="check-imports",
        outcome="pass",
        duration_ms=0,
        message=f"{total} modules imported OK",
    )
