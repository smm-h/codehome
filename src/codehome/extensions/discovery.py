"""Extension discovery: scan repo/branch directories for extension files.

Scans ``repos/{repo}/extensions/*.py`` (repo-level) and optionally
``repos/{repo}/branches/{branch}/extensions/*.py`` (branch-level) for
Python files containing ``@extension``-decorated functions.  Each file
is imported via ``importlib.util`` (file-based, not package-based) so
extensions can live at arbitrary repo paths outside the normal Python
package tree.

Branch-level extensions with the same name as repo-level extensions
take precedence (branch wins).
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from codehome.extensions.types import ExtensionEntry

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class DiscoveryResult:
    """Result of scanning extension directories.

    Attributes:
        extensions: Successfully discovered extension entries.
        errors: Human-readable error messages from failed imports
            or validation failures.

    """

    extensions: list[ExtensionEntry]
    errors: list[str]


def discover_extensions(
    repo: str,
    branch: str | None,
) -> DiscoveryResult:
    """Scan repo and branch extension directories for decorated functions.

    Args:
        repo: Repository name (e.g. ``bag``).
        branch: Branch name, or ``None`` for repo-level only.

    Returns:
        DiscoveryResult with discovered entries and any errors.

    """
    from codehome.paths import repo_branches, repo_dir

    repo_base = repo_dir(repo)
    repo_ext_dir = repo_base / "extensions"
    all_entries: dict[str, ExtensionEntry] = {}
    all_errors: list[str] = []

    # Scan repo-level extensions first.
    if repo_ext_dir.is_dir():
        entries, errors = _scan_directory(repo_ext_dir, source="repo")
        all_errors.extend(errors)
        for entry in entries:
            all_entries[entry.name] = entry

    # Scan branch-level extensions (overrides repo-level on name collision).
    if branch is not None:
        branch_ext_dir = repo_branches(repo) / branch / "extensions"
        if branch_ext_dir.is_dir():
            entries, errors = _scan_directory(branch_ext_dir, source="branch")
            all_errors.extend(errors)
            for entry in entries:
                all_entries[entry.name] = entry

    return DiscoveryResult(
        extensions=list(all_entries.values()),
        errors=all_errors,
    )


def _scan_directory(
    directory: Path,
    *,
    source: Literal["repo", "branch"],
) -> tuple[list[ExtensionEntry], list[str]]:
    """Import all .py files in *directory* and extract decorated functions.

    Returns (entries, errors) where errors are human-readable strings
    for files that failed to import or contained invalid extensions.
    """
    entries: list[ExtensionEntry] = []
    errors: list[str] = []

    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        file_entries, file_errors = _import_and_extract(py_file, source=source)
        entries.extend(file_entries)
        errors.extend(file_errors)

    return entries, errors


def _import_and_extract(
    py_file: Path,
    *,
    source: Literal["repo", "branch"],
) -> tuple[list[ExtensionEntry], list[str]]:
    """Import a single .py file and extract @extension-decorated functions.

    Uses importlib.util.spec_from_file_location for file-based import
    so the file doesn't need to be part of an installed package.
    """
    entries: list[ExtensionEntry] = []
    errors: list[str] = []

    # Unique module name to avoid collisions in sys.modules.
    module_name = f"_ext_{source}_{py_file.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            errors.append(f"{py_file}: could not create import spec")
            return entries, errors

        module = importlib.util.module_from_spec(spec)
        # Temporarily add to sys.modules so relative imports within
        # the extension file can resolve, then remove after exec.
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)

    except Exception as exc:
        errors.append(f"{py_file}: import failed: {exc}")
        return entries, errors

    # Walk module attributes looking for _extension_meta.
    for attr_name in dir(module):
        obj = getattr(module, attr_name, None)
        if obj is None or not callable(obj):
            continue

        meta = getattr(obj, "_extension_meta", None)
        if meta is None:
            continue

        # Validate: must be async (decorator already enforces this,
        # but belt-and-suspenders for manually crafted metadata).
        if not inspect.iscoroutinefunction(obj):
            errors.append(f"{py_file}: {attr_name} has _extension_meta but is not async")
            continue

        entry = ExtensionEntry(
            name=meta["name"],
            group=meta["group"],
            timeout=meta["timeout"],
            cwd=meta.get("cwd", "."),
            depends_on=tuple(meta.get("depends_on", ())),
            advisory=meta.get("advisory", False),
            fn=obj,
            description=meta.get("description", ""),
            source=source,
            file=str(py_file),
            enabled=True,
        )
        entries.append(entry)

    return entries, errors
