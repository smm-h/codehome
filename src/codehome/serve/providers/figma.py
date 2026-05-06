"""Figma provider: design file access.

Self-registers with the provider registry on import. Uses the Figma
REST API to validate tokens and fetch file metadata.

Branch enrichment reads a `.figma` link file from the branch context
directory. The file contains a single line: the Figma file URL. The
provider parses the file key from the URL and fetches metadata via
``GET /v1/files/{key}?depth=1``.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from codehome.serve.logging_config import get_logger
from codehome.serve.providers.registry import register

logger = get_logger(component="provider.figma")

_FIGMA_API = "https://api.figma.com/v1/me"
_FIGMA_FILES_API = "https://api.figma.com/v1/files"

# Matches Figma URLs like:
#   https://www.figma.com/file/abc123/My-Design
#   https://www.figma.com/design/abc123/My-Design
#   https://www.figma.com/proto/abc123/My-Prototype
#   https://www.figma.com/board/abc123/My-Board
#   https://www.figma.com/slides/abc123/My-Slides
# The file key is the segment after the path type.
_FIGMA_URL_RE = re.compile(r"figma\.com/(?:file|design|proto|board|slides)/([A-Za-z0-9]+)")


def _parse_file_key(url: str) -> str | None:
    """Extract the Figma file key from a URL. Returns None if unparseable."""
    m = _FIGMA_URL_RE.search(url)
    return m.group(1) if m else None


def _read_figma_link(branch: str, repo: str) -> str | None:
    """Read the .figma link file from a branch context dir.

    Returns the raw URL string, or None if the file doesn't exist or
    is empty.
    """
    from codehome.supervisor.paths import branch_dir

    link_file = branch_dir(repo, branch) / ".figma"
    if not link_file.is_file():
        return None
    content = link_file.read_text().strip()
    return content or None


class FigmaProvider:
    """Figma design provider."""

    @property
    def name(self) -> str:
        return "figma"

    @property
    def capabilities(self) -> set[str]:
        return {"designs"}

    def test_connection(self, token: str) -> bool:
        """Validate the token by fetching the current user."""
        req = urllib.request.Request(
            _FIGMA_API,
            headers={"X-Figma-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return "id" in data
        except Exception:
            logger.warning("figma_test_failed")
            return False

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        """Fetch Figma file metadata if the branch has a .figma link file.

        Returns a dict with figma_url, file_name, last_modified, and
        thumbnail_url, or None if no .figma file exists or the API call
        fails.
        """
        figma_url = _read_figma_link(branch, repo)
        if not figma_url:
            return None

        file_key = _parse_file_key(figma_url)
        if not file_key:
            logger.warning("figma_bad_url", url=figma_url)
            return None

        # Fetch file metadata (depth=1 to keep response small).
        api_url = f"{_FIGMA_FILES_API}/{file_key}?depth=1"
        req = urllib.request.Request(
            api_url,
            headers={"X-Figma-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception:
            logger.warning("figma_enrich_failed", file_key=file_key)
            return None

        return {
            "figma_url": figma_url,
            "file_name": data.get("name", ""),
            "last_modified": data.get("lastModified", ""),
            "thumbnail_url": data.get("thumbnailUrl", ""),
        }

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        """Not available for Figma (no per-user stats API outside enterprise)."""
        return None


# Self-register when this module is imported.
register(FigmaProvider())
