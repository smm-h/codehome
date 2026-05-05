"""Pipeline endpoints: staging, production, PR status, actions, merge history."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from codehome.serve.dependencies import get_gh_token
from codehome.serve.history_ops import get_merge_history
from codehome.serve.pipeline import (
    get_actions_status,
    get_pipeline_status,
    trigger_prod,
    trigger_stage,
)

router = APIRouter()


@router.get("/api/branches/{qualified}/pipeline")
async def api_pipeline_status(qualified: str, gh_token: str | None = Depends(get_gh_token)) -> object:
    """Get pipeline status (staging + production PRs) for a branch."""
    repo, branch = qualified.split(":", 1)
    return await asyncio.to_thread(get_pipeline_status, repo, branch, gh_token=gh_token)


class PipelineActionRequest(BaseModel):
    message: str = ""


@router.post("/api/branches/{qualified}/pipeline/stage")
async def api_pipeline_stage(
    qualified: str,
    req: PipelineActionRequest,
    gh_token: str | None = Depends(get_gh_token),
) -> object:
    """Trigger staging: push branch and create a staging PR."""
    repo, branch = qualified.split(":", 1)
    result = await asyncio.to_thread(trigger_stage, repo, branch, req.message, gh_token=gh_token)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.post("/api/branches/{qualified}/pipeline/prod")
async def api_pipeline_prod(
    qualified: str,
    req: PipelineActionRequest,
    gh_token: str | None = Depends(get_gh_token),
) -> object:
    """Trigger production: push branch and create a production PR."""
    repo, branch = qualified.split(":", 1)
    result = await asyncio.to_thread(trigger_prod, repo, branch, req.message, gh_token=gh_token)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.get("/api/branches/{qualified}/pipeline/actions")
async def api_pipeline_actions(qualified: str, gh_token: str | None = Depends(get_gh_token)) -> object:
    """Get the latest GitHub Actions workflow run for a branch."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(get_actions_status, repo, branch, gh_token=gh_token)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Branch not found: {qualified}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None


@router.get("/api/branches/{qualified}/history")
async def api_merge_history(qualified: str, gh_token: str | None = Depends(get_gh_token)) -> object:
    """Get merge history (staging + production PRs) for a branch."""
    repo, branch = qualified.split(":", 1)
    try:
        return await asyncio.to_thread(get_merge_history, repo, branch, gh_token=gh_token)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Branch not found: {qualified}") from None
