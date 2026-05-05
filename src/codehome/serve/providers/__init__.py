"""Provider modules -- auto-registers all providers on import."""

# Import provider modules to trigger self-registration.
from codehome.serve.providers import (
    figma,  # noqa: F401
    github,  # noqa: F401
    linear,  # noqa: F401
    notion,  # noqa: F401
    slack,  # noqa: F401
)
from codehome.serve.providers.base import Provider
from codehome.serve.providers.registry import (
    all_providers,
    available_providers,
    get_provider,
    register,
)

__all__ = [
    "Provider",
    "all_providers",
    "available_providers",
    "get_provider",
    "register",
]
