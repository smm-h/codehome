"""Notion provider: documentation and knowledge-base access.

Self-registers with the provider registry on import. Uses the Notion
API to validate tokens and fetch page metadata.

Branch enrichment reads a ``.notion`` link file from the branch context
directory. The file contains a single line: the Notion page URL. The
provider parses the page ID from the URL and fetches metadata via
``GET /v1/pages/{page_id}``.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from codehome.paths import branch_dir
from codehome.serve.logging_config import get_logger
from codehome.serve.providers.registry import register

logger = get_logger(component="provider.notion")

_NOTION_API = "https://api.notion.com/v1/users/me"
_NOTION_PAGES_API = "https://api.notion.com/v1/pages"
_NOTION_VERSION = "2022-06-28"

# Matches a 32-hex-char ID (with or without dashes) at the end of a Notion URL,
# or after the final dash in a slug like "My-Page-abc123def456".
# Notion page IDs are 32 hex chars, optionally hyphenated as a UUID.
_NOTION_PAGE_ID_RE = re.compile(
    r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)

# Fallback: extract the hex suffix after the last dash in a slug URL segment.
# e.g. "My-Page-abc123def456" -> "abc123def456" (which we zero-pad or validate).
_NOTION_SLUG_SUFFIX_RE = re.compile(r"-([0-9a-f]{12,})$", re.IGNORECASE)


def _parse_page_id(url_or_id: str) -> str | None:
    """Extract the Notion page ID from a URL or raw ID string.

    Handles formats:
    - Full UUID: 12345678-1234-1234-1234-123456789abc
    - Bare 32-hex ID: 12345678123412341234123456789abc
    - URL with UUID or 32-hex at the end of the path
    - Slug URL: https://www.notion.so/My-Page-abc123def456789012345678abcdef12
    """
    text = url_or_id.strip().rstrip("/")

    # If it's a URL, take the last path segment for matching.
    if "/" in text:
        text = text.rsplit("/", 1)[-1]

    # Also strip query params.
    if "?" in text:
        text = text.split("?", 1)[0]

    # Try full UUID or 32-hex match.
    m = _NOTION_PAGE_ID_RE.search(text)
    if m:
        # Normalize: remove dashes, lowercase to get a 32-char hex string.
        return m.group(1).replace("-", "").lower()

    # Try slug suffix (at least 12 hex chars after the last dash).
    m = _NOTION_SLUG_SUFFIX_RE.search(text)
    if m and len(m.group(1)) == 32:
        return m.group(1).lower()

    return None


def _format_page_id(raw_id: str) -> str:
    """Format a 32-hex-char page ID as a UUID with dashes for the API."""
    return f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"


def _read_notion_link(branch: str, repo: str) -> str | None:
    """Read the .notion link file from a branch context dir.

    Returns the raw URL/ID string, or None if the file doesn't exist or
    is empty.
    """
    link_file = branch_dir(repo, branch) / ".notion"
    if not link_file.is_file():
        return None
    content = link_file.read_text().strip()
    return content or None


def _extract_page_title(properties: dict[str, Any]) -> str:
    """Extract the page title from Notion page properties.

    Notion stores titles in a property of type "title", which contains
    a list of rich-text objects. The property name varies (commonly
    "title" or "Name"), so we scan all properties for the title type.
    """
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title_parts = prop.get("title", [])
            return "".join(part.get("plain_text", "") for part in title_parts)
    return ""


class NotionProvider:
    """Notion documentation provider."""

    @property
    def name(self) -> str:
        return "notion"

    @property
    def capabilities(self) -> set[str]:
        return {"docs"}

    def test_connection(self, token: str) -> bool:
        """Validate the token by fetching the current bot user."""
        req = urllib.request.Request(
            _NOTION_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("type") in ("bot", "person")
        except Exception:
            logger.warning("notion_test_failed")
            return False

    def enrich_branch(self, branch: str, repo: str, token: str) -> dict[str, Any] | None:
        """Fetch Notion page metadata if the branch has a .notion link file.

        Returns a dict with notion_url, page_title, last_edited, and
        status, or None if no .notion file exists or the API call fails.
        """
        notion_url = _read_notion_link(branch, repo)
        if not notion_url:
            return None

        page_id = _parse_page_id(notion_url)
        if not page_id:
            logger.warning("notion_bad_url", url=notion_url)
            return None

        # Notion API expects UUID-formatted IDs.
        formatted_id = _format_page_id(page_id)
        api_url = f"{_NOTION_PAGES_API}/{formatted_id}"
        req = urllib.request.Request(
            api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception:
            logger.warning("notion_enrich_failed", page_id=page_id)
            return None

        # Extract page title from properties.
        properties = data.get("properties", {})
        page_title = _extract_page_title(properties)

        # Extract status from the "Status" property if present.
        status = None
        status_prop = properties.get("Status")
        if isinstance(status_prop, dict) and status_prop.get("type") == "status":
            status_val = status_prop.get("status")
            if isinstance(status_val, dict):
                status = status_val.get("name")

        return {
            "notion_url": notion_url,
            "page_title": page_title,
            "last_edited": data.get("last_edited_time", ""),
            "status": status,
        }

    def get_user_stats(self, github_username: str, token: str) -> dict[str, Any] | None:
        """Not useful for Notion -- no per-user stats API."""
        return None


# Self-register when this module is imported.
register(NotionProvider())
