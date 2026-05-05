"""Streaming command types for PDUI.

Plugin command handlers are async generators that yield these types.
The streaming endpoint serialises them as SSE events.

Usage in a plugin handler::

    from codehome.sdk import CommandProgress, CommandResult, CommandError

    async def cmd_bump(args: dict):
        yield CommandProgress(message="Bumping version...")
        # ... do work ...
        yield CommandResult(toast="Version bumped to 1.2.3")
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CommandProgress(BaseModel):
    """Intermediate progress update sent during command execution."""

    type: str = "progress"
    message: str


class CommandResult(BaseModel):
    """Terminal success response.

    - toast: short notification message shown to the user
    - modal: SDUI node tree to render in a modal dialog
    - navigate: URL/route to navigate to after completion
    - state: partial state update merged into plugin state
    """

    type: str = "result"
    toast: str | None = None
    modal: dict[str, Any] | None = None
    navigate: str | None = None
    state: dict[str, Any] | None = None


class CommandError(BaseModel):
    """Terminal error response."""

    type: str = "error"
    message: str
    detail: str | None = None
