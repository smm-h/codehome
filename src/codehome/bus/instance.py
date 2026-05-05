"""Application-level bus singleton.

Import ``bus`` from here to fire events. The bus is created with the
canonical registry. Subscribers are installed lazily via install_*
helpers -- the server installs SSE + audit, the CLI installs audit only.
"""

from codehome.bus.dispatch import EventBus
from codehome.bus.events import registry

bus = EventBus(registry)
