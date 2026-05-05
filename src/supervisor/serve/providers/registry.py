"""Provider registry: register and look up providers by name.

Providers self-register at module level when their module is imported.
The ``__init__.py`` imports all provider modules to trigger registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from supervisor.serve.connections import has_token

if TYPE_CHECKING:
    from supervisor.serve.providers.base import Provider

_providers: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    """Register a provider."""
    _providers[provider.name] = provider


def get_provider(name: str) -> Provider | None:
    """Get a registered provider by name."""
    return _providers.get(name)


def all_providers() -> list[Provider]:
    """Return all registered providers."""
    return list(_providers.values())


def available_providers(username: str) -> list[dict[str, Any]]:
    """Return provider info with connection status for a user.

    Returns a list of dicts, each with:
      - name: provider name
      - capabilities: list of capability strings
      - connected: whether the user has a token stored
    """
    return [
        {
            "name": p.name,
            "capabilities": sorted(p.capabilities),
            "connected": has_token(username, p.name),
        }
        for p in _providers.values()
    ]
