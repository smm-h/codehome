"""SPA static file fallback router.

Serves the built SvelteKit dashboard from the dashboard plugin's
``static/`` directory.  Falls back to the legacy location adjacent to
the server module if the plugin is not loaded.  Acts as a catch-all
so that client-side routing works for non-API paths.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

router = APIRouter()


def _find_dashboard_static() -> Path:
    """Discover the dashboard plugin's static directory.

    Returns the plugin's static/ dir if the plugin is loaded and the
    directory exists, otherwise falls back to the legacy path.
    """
    from codehome.plugins import registry as plugin_registry

    plugin = plugin_registry.get("dashboard")
    if plugin is not None:
        plugin_static = Path(plugin.plugin_dir) / "static"
        if plugin_static.is_dir():
            return plugin_static
    # Fallback: legacy location adjacent to the server module.
    return Path(__file__).parent / "static"


_STATIC_DIR = _find_dashboard_static()

if _STATIC_DIR.exists():

    @router.get("/{path:path}")
    async def serve_spa(path: str) -> FileResponse:
        # Never intercept API or SSE routes.
        if path.startswith("api/") or path == "events":
            raise HTTPException(status_code=404)
        file = _STATIC_DIR / path
        if file.is_file():
            return FileResponse(file)
        # SPA fallback: serve index.html for all non-file routes.
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        raise HTTPException(status_code=404)
