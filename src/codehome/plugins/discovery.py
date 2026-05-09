"""Plugin discovery: scan plugin directories for plugin manifests.

Scans configured directories for subdirectories containing a
``plugin.toml`` manifest file.  Plugin paths come from:

1. ``[plugins] paths`` in ``~/.codehome/config.toml`` (config-based)
2. ``~/.codehome/plugins/`` (user-level plugins installed via
   ``v plugins install``)

If no ``config.toml`` exists or ``[plugins] paths`` is unset, falls
back to scanning ``ROOT / "plugins"`` for backward compatibility.

Each valid manifest is parsed via
:func:`codehome.plugins.manifest.parse_manifest` and collected into
a :class:`DiscoveryResult`.  Directories starting with ``_`` or ``.``
are skipped.  If multiple locations contain a plugin with the same
name, the first-listed path wins (later duplicates are skipped with
a warning).

This mirrors the pattern in :mod:`codehome.extensions.discovery` but
is simpler: no file-based import is needed, just manifest parsing.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from codehome.plugins.manifest import PluginManifest, parse_manifest


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


def _read_plugin_paths_from_config() -> list[Path] | None:
    """Read [plugins] paths from ~/.codehome/config.toml.

    Returns None if the config file doesn't exist or doesn't have the key,
    signaling the caller should use the fallback behavior.
    """
    from codehome.paths import codehome_home

    config_file = codehome_home() / "config.toml"
    if not config_file.is_file():
        return None

    try:
        data = tomllib.loads(config_file.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None

    raw_paths = data.get("plugins", {}).get("paths")
    if raw_paths is None:
        return None

    # Expand ~ in each path entry.
    return [Path(p).expanduser() for p in raw_paths]


def discover_plugins(root: Path | None = None) -> DiscoveryResult:
    """Scan plugin directories for subdirectories with ``plugin.toml``.

    Plugin directories are determined by (in priority order):

    1. If *root* is passed explicitly (e.g. from tests), scan
       ``root / "plugins"`` only.
    2. Otherwise, read ``[plugins] paths`` from
       ``~/.codehome/config.toml``.
    3. If neither is available, fall back to ``ROOT / "plugins"``
       (backward compat for editable installs without config).

    In all cases, ``~/.codehome/plugins/`` is also scanned for
    user-installed plugins.  Earlier directories take priority: if
    multiple locations declare a plugin with the same name, only
    the first-seen one is kept.

    Args:
        root: Optional project root override (used by tests).
            When None, plugin paths come from config or ROOT fallback.

    Returns:
        DiscoveryResult with parsed manifests and any errors.

    """
    from codehome.paths import codehome_home

    result = DiscoveryResult()

    # Determine which directories to scan for plugins.
    if root is not None:
        # Explicit root provided (tests, backward compat callers).
        scan_dirs = [root / "plugins"]
    else:
        config_paths = _read_plugin_paths_from_config()
        if config_paths is not None:
            scan_dirs = config_paths
        else:
            # Fallback: use ROOT / "plugins" (old behavior).
            from codehome.paths import ROOT
            scan_dirs = [ROOT / "plugins"]

    # 1. Scan configured plugin directories.
    for plugins_dir in scan_dirs:
        _scan_plugins_dir(plugins_dir, result)

    # Track names found so far so user-level duplicates are skipped.
    seen_names = {m.name for _, m in result.plugins}

    # 2. Scan user-level ~/.codehome/plugins/ directory.
    user_plugins_dir = codehome_home() / "plugins"
    if user_plugins_dir.is_dir():
        user_result = DiscoveryResult()
        _scan_plugins_dir(user_plugins_dir, user_result)

        # Merge: skip user plugins that collide with already-found names.
        for path, manifest in user_result.plugins:
            if manifest.name in seen_names:
                result.errors.append(
                    f"User plugin '{manifest.name}' at {path} skipped: "
                    f"overridden by higher-priority plugin"
                )
            else:
                result.plugins.append((path, manifest))
                seen_names.add(manifest.name)

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
