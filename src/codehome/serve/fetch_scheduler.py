"""Re-export stub: fetch_scheduler moved to core plugin.

The implementation lives at ``codehome.core.ops.fetch_scheduler``.
This module re-exports the singleton so existing import paths continue to work
during the migration.
"""

from codehome.core.ops.fetch_scheduler import FetchScheduler, fetch_scheduler

__all__ = ["FetchScheduler", "fetch_scheduler"]
