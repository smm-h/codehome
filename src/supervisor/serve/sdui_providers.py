"""SDUI provider registry.

Plugin-specific SDUI providers.  Each async callable returns
(ui_tree, initial_state).  This is a temporary mechanism until plugins
declare ui.json natively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# TODO(plugin-v2): rename to register_sdui_provider (drop underscore)
# and add to SDK when more plugins adopt SDUI
_SDUI_PROVIDERS: dict[str, Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]]] = {}


def _register_sdui_provider(
    name: str,
    provider: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
) -> None:
    _SDUI_PROVIDERS[name] = provider
