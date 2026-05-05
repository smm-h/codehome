"""Extension entry type: metadata for a discovered extension check.

ExtensionEntry mirrors CheckEntry from the check framework but adds
fields for extension-specific metadata (description, source, file path,
enabled state).  The loader converts enabled ExtensionEntries into
CheckEntries for registration into the CheckRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ExtensionEntry:
    """Immutable record of a discovered extension check.

    Attributes:
        name: Kebab-case check identifier (e.g. ``lint-cycles``).
        group: Check group this extension belongs to (e.g. ``gate``).
        timeout: Maximum execution time in seconds.
        cwd: Working directory relative to the project root.
        depends_on: Names of checks that must pass before this one runs.
        advisory: If ``True``, failure is a warning, not an error.
        fn: The async callable that executes the check.
        description: Human-readable description for ``v extensions list``.
        source: Where this extension was discovered (``repo`` or ``branch``).
        file: Absolute file path where the extension was defined.
        enabled: Whether this extension is currently enabled.

    """

    name: str
    group: str
    timeout: int
    cwd: str = "."
    depends_on: tuple[str, ...] = ()
    advisory: bool = False
    # Typed as Any because the real signature (CheckContext -> CheckResult)
    # uses TYPE_CHECKING-only imports.  Validation happens at decoration time.
    fn: Any = None
    description: str = ""
    source: Literal["repo", "branch"] = "repo"
    file: str = ""
    enabled: bool = True

    def __repr__(self) -> str:  # noqa: D105 -- compact repr
        state = "on" if self.enabled else "off"
        return f"ExtensionEntry({self.name!r}, group={self.group!r}, [{state}])"
