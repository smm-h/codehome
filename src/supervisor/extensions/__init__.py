"""Extension framework: repo/branch-specific checks loaded at runtime.

Extensions are user-defined async check functions that live in
``repos/<repo>/extensions/`` (repo-level) or
``repos/<repo>/branches/<branch>/extensions/`` (branch-level).  They
integrate with the existing check system but are managed independently:
discovered via file scanning, persisted in a state file, and loaded into
the CheckRegistry on demand.

Pipeline: discover -> merge state -> load into registry.
"""

from __future__ import annotations

from supervisor.extensions.decorator import extension
from supervisor.extensions.types import ExtensionEntry

__all__ = [
    "ExtensionEntry",
    "extension",
]
