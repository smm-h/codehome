"""Documentation operations: list and read project docs.

Extracted from routers/docs.py so route handlers stay thin.
"""

from __future__ import annotations

from pathlib import Path

# docs/ lives at the repository root, four levels above the serve/ package.
_DOCS_DIR = Path(__file__).parent.parent.parent.parent / "docs"


def list_docs() -> list[dict[str, str]]:
    """List all .md files in the project docs/ directory."""
    if not _DOCS_DIR.is_dir():
        return []
    return sorted(
        [{"name": f.stem, "filename": f.name} for f in _DOCS_DIR.glob("*.md")],
        key=lambda d: d["name"],
    )


def get_doc(name: str) -> dict[str, str] | None:
    """Read a single markdown doc by stem name.

    Validates against path traversal. Returns the doc dict or None if invalid,
    or raises FileNotFoundError if the document doesn't exist.
    """
    if "/" in name or "\\" in name or ".." in name:
        return None  # Invalid name -- caller should return 400

    filename = name if name.endswith(".md") else f"{name}.md"
    doc_path = (_DOCS_DIR / filename).resolve()

    # Guard against path traversal.
    if not str(doc_path).startswith(str(_DOCS_DIR.resolve())):
        return None

    if not doc_path.is_file():
        raise FileNotFoundError(f"Document '{name}' not found")

    content = doc_path.read_text(encoding="utf-8")
    return {"name": doc_path.stem, "filename": doc_path.name, "content": content}
