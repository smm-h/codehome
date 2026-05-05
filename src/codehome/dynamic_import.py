"""Shared dynamic module import utility for plugin and extension loaders.

Provides a single helper that both the extension loader and the plugin
loader delegate to for the core importlib mechanics: create a spec from
a file path, build a module, register it temporarily in sys.modules,
execute it, then clean up.  Callers are responsible for constructing the
module name and for error handling (logging, returning None, etc.).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def import_module_from_path(module_name: str, file_path: Path) -> ModuleType | None:
    """Import a Python module from an absolute file path.

    The module is temporarily registered in :data:`sys.modules` under
    *module_name* during execution (so intra-module imports resolve),
    then removed to avoid polluting the global namespace.

    Returns the loaded module, or ``None`` if ``spec_from_file_location``
    fails (e.g. the path does not exist or is not a valid Python file).
    Raises any exception that occurs during module execution after
    cleaning up sys.modules -- callers decide how to handle import errors.
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.modules.pop(module_name, None)
    return module
