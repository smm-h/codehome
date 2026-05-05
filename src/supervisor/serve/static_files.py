"""SPA static file fallback router.

Serves the built SvelteKit dashboard from the ``static/`` directory
adjacent to the server module.  Acts as a catch-all so that
client-side routing works for non-API paths.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

router = APIRouter()

_STATIC_DIR = Path(__file__).parent / "static"

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
