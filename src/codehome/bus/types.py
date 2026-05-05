"""Event types, filter results, and transport modes for the event bus."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Transport(Enum):
    """How an event propagates through the bus."""

    FILTER = "FILTER"  # Sequential, handlers return FilterResult
    DONE = "DONE"  # Parallel, fire-and-forget notifications


class Event(BaseModel):
    """A single event flowing through the bus."""

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit: bool = False


class FilterResult:
    """Base class for FILTER handler return values."""


class Ok(FilterResult):
    """Handler approves the event."""


class Veto(FilterResult):
    """Handler rejects the event."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
