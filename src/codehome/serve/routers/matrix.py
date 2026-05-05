"""Matrix endpoint: aggregate (branch x tab) summary grid for the home page."""

import asyncio

from fastapi import APIRouter, Depends

from codehome.serve.auth_deps import get_current_user
from codehome.serve.matrix import get_matrix
from codehome.serve.preferences import get_preference

router = APIRouter()


@router.get("/api/matrix")
async def api_matrix(user: dict[str, str] = Depends(get_current_user)) -> object:
    """Return the branch/tab matrix for the home page.

    Sort order: branches the user has recently accessed come first, then by
    most-recent commit. Each branch row includes one summary cell per tab
    (or null for tabs with no data).
    """
    # The recent-branches preference is the dashboard's source of truth for
    # "which branch did I last visit". It's set by BranchLayout on mount.
    recent = await asyncio.to_thread(get_preference, user["sub"], "recent-branches")
    if not isinstance(recent, list):
        recent = None
    return await asyncio.to_thread(get_matrix, recent)
