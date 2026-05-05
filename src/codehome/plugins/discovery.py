"""Plugin discovery: scan plugin directories for plugin manifests.

Scans two locations for subdirectories containing a ``plugin.toml``
manifest file:

1. ``plugins/`` at the project root (project-level plugins)
2. ``~/.superv/plugins/`` (user-level plugins installed via
   ``v plugins install``)

Each valid manifest is parsed via
:func:`codehome.plugins.manifest.parse_manifest` and collected into
a :class:`DiscoveryResult`.  Directories starting with ``_`` or ``.``
are skipped.  If both locations contain a plugin with the same name,
the project-level one wins (user-level is skipped with a warning).

This mirrors the pattern in :mod:`codehome.extensions.discovery` but
is simpler: no file-based import is needed, just manifest parsing.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from codehome.plugins.manifest import PluginManifest, parse_manifest

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class DiscoveryResult:
    """Result of scanning the plugins directory.

    Attributes:
        plugins: Successfully discovered (plugin_dir, manifest) pairs.
        errors: Human-readable error messages from manifests that
            failed to parse or validate.

    """

    plugins: list[tuple[Path, PluginManifest]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _scan_plugins_dir(plugins_dir: Path, result: DiscoveryResult) -> None:
    """Scan a single plugins directory and append findings to *result*.

    Skips hidden/private directories and entries without ``plugin.toml``.
    """
    if not plugins_dir.is_dir():
        return

    for entry in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue

        # Skip hidden and private directories.
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue

        manifest_path = entry / "plugin.toml"
        if not manifest_path.is_file():
            continue

        try:
            manifest = parse_manifest(entry)
        except (FileNotFoundError, ValueError) as exc:
            result.errors.append(str(exc))
            continue

        result.plugins.append((entry, manifest))


def discover_plugins(root: Path) -> DiscoveryResult:
    """Scan plugin directories for subdirectories with ``plugin.toml``.

    Scans two locations:
    1. ``root / "plugins"``     -- project-level plugins
    2. ``~/.superv/plugins/``   -- user-level installed plugins

    Project-level plugins take priority: if both locations declare
    a plugin with the same name, only the project-level one is kept.

    Args:
        root: Absolute path to the super/ project root.

    Returns:
        DiscoveryResult with parsed manifests and any errors.

    """
    from codehome.paths import superv_home

    result = DiscoveryResult()

    # 1. Scan project-level plugins/ directory.
    _scan_plugins_dir(root / "plugins", result)

    # Track names found at project level so user-level duplicates are skipped.
    project_names = {m.name for _, m in result.plugins}

    # 2. Scan user-level ~/.superv/plugins/ directory.
    user_plugins_dir = superv_home() / "plugins"
    if user_plugins_dir.is_dir():
        user_result = DiscoveryResult()
        _scan_plugins_dir(user_plugins_dir, user_result)

        # Merge: skip user plugins that collide with project-level names.
        for path, manifest in user_result.plugins:
            if manifest.name in project_names:
                result.errors.append(
                    f"User plugin '{manifest.name}' at {path} skipped: "
                    f"overridden by project-level plugin"
                )
            else:
                result.plugins.append((path, manifest))

        result.errors.extend(user_result.errors)

    # Detect duplicate plugin names across different directories.
    seen: dict[str, list[Path]] = {}
    for path, manifest in result.plugins:
        seen.setdefault(manifest.name, []).append(path)

    duplicated_names = {name for name, paths in seen.items() if len(paths) > 1}
    if duplicated_names:
        for name in sorted(duplicated_names):
            dirs = ", ".join(str(p) for p in seen[name])
            result.errors.append(f"Duplicate plugin name '{name}' declared by: {dirs}")
        # Remove all entries with a duplicated name (keep neither).
        result.plugins = [(p, m) for p, m in result.plugins if m.name not in duplicated_names]

    return result


def check_system_deps(manifest: PluginManifest) -> list[str]:
    """Check whether system-level dependencies declared in *manifest* exist.

    Args:
        manifest: A parsed plugin manifest whose ``requires`` field
            lists program names expected on ``$PATH``.

    Returns:
        List of missing dependency names.  Empty if all are satisfied.

    """
    return [dep for dep in manifest.requires if shutil.which(dep) is None]
