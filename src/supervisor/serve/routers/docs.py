"""Documentation endpoints: list and serve project docs as markdown."""

from fastapi import APIRouter, HTTPException

from supervisor.serve.docs_ops import get_doc, list_docs

router = APIRouter()


@router.get("/api/docs")
async def api_list_docs() -> list[dict[str, str]]:
    """List all .md files in the project docs/ directory."""
    return list_docs()


@router.get("/api/docs/{name}")
async def api_get_doc(name: str) -> dict[str, str]:
    """Read and return a single markdown doc by stem name."""
    try:
        result = get_doc(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    if result is None:
        raise HTTPException(status_code=400, detail="Invalid document name")
    return result
