"""Extension loader: import enabled extensions and register into CheckRegistry.

Reads the persisted state file to determine which extensions are enabled,
then imports each enabled extension's source file, locates the decorated
function, and registers it as a CheckEntry in the provided CheckRegistry.

This is the bridge between the extension system and the check system:
extensions are discovered and managed independently, but once loaded
they become regular checks in the registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from pathlib import Path

    from supervisor.checks.registry import CheckRegistry

from supervisor.dynamic_import import import_module_from_path
from supervisor.extensions.state import load_state


def load_extensions(
    repo: str,
    branch: str | None,
    root: Path,
    registry: CheckRegistry,
) -> int:
    """Load enabled extensions into the check registry.

    Args:
        repo: Repository name (e.g. ``bag``).
        branch: Branch name, or ``None`` for repo-level only.
        root: Absolute path to the super/ project root.
        registry: CheckRegistry instance to register extensions into.

    Returns:
        Number of extensions successfully loaded.

    """
    state = load_state(repo, root)
    extensions = state.get("extensions", {})

    loaded = 0
    for ext_name, ext_info in extensions.items():
        if not ext_info.get("enabled", True):
            continue

        fn = _import_extension_fn(ext_info["file"], ext_name)
        if fn is None:
            continue

        source = ext_info.get("source", "repo")
        registry.register(
            name=ext_name,
            group=ext_info["group"],
            timeout=ext_info["timeout"],
            cwd=ext_info.get("cwd", "."),
            depends_on=tuple(ext_info.get("depends_on", ())),
            advisory=ext_info.get("advisory", False),
            fn=fn,
            source=source,
        )
        loaded += 1

    return loaded


def _import_extension_fn(file_path: str, ext_name: str) -> Callable[..., Any] | None:
    """Import the extension file and find the function decorated with *ext_name*.

    Returns the async callable, or None if the file can't be imported or
    the named extension isn't found.
    """
    from pathlib import Path

    module_name = f"_ext_load_{ext_name}"

    try:
        module = import_module_from_path(module_name, Path(file_path))
    except Exception:
        return None

    if module is None:
        return None

    # Find the function whose _extension_meta["name"] matches ext_name.
    for attr_name in dir(module):
        obj = getattr(module, attr_name, None)
        if obj is None or not callable(obj):
            continue
        meta = getattr(obj, "_extension_meta", None)
        if meta is not None and meta.get("name") == ext_name:
            fn: Callable[..., Any] = obj
            return fn

    return None
