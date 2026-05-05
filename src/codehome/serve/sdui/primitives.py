"""Pydantic schemas for all 39 SDUI (Server-Driven UI) primitives.

These models validate and type the raw dict trees that plugins build for
the dashboard's SDUIRenderer.  Each model serialises to the exact dict
shape the renderer expects (``type``, ``props``, ``children``, optional
``if``).

Usage::

    from codehome.serve.sdui import Stack, Button, Alert, node

    # Typed construction with validation
    btn = Button(label="Deploy", variant="primary", command="deploy")
    print(btn.to_node())   # {"type": "button", "props": {"label": ...}}

    # Quick untyped helper (no validation, but terse)
    tree = node("stack", [node("heading", text="Hello", level=2)])

Grouping:
    Layout (9)   -- Stack, ZStack, Spacer, Divider, Grid, Card, Tabs, Breadcrumb, Empty
    Display (10) -- Heading, Text, Code, Status, Badge, ProgressBar, Spinner, Timeline, Diff, Markdown
    Data (5)     -- Table, List, KeyValue, JsonView, Tree
    Input (8)    -- Button, Input, TextArea, Select, Checkbox, Switch, Radio, Slider
    Feedback (3) -- Alert, Toast, Logs
    Overlay (4)  -- Modal, Drawer, Popover, Confirm
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class SDUINode(BaseModel):
    """Base class for all SDUI nodes.

    Every node serialises to ``{"type": ..., "props": ..., "children": [...]}``
    with an optional ``"if"`` key for conditional rendering.
    """

    type: str
    children: list[SDUINode] = []
    props: dict[str, Any] = {}
    if_condition: str | None = Field(None, alias="if")
    """Conditional rendering expression (e.g. ``${state.running}``)."""

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def node(node_type: str, children: list[dict[str, Any]] | None = None, **props: Any) -> dict[str, Any]:
    """Build an SDUI node dict for use in providers.

    This is the quick, untyped helper -- no Pydantic validation, just dict
    construction matching the shape SDUIRenderer expects::

        node("heading", content="Hello", level=2)
        node("column", [node("text", content="A"), node("text", content="B")], gap=16)
    """
    result: dict[str, Any] = {"type": node_type, "props": props}
    if children:
        result["children"] = children
    return result


# ---------------------------------------------------------------------------
# Prop sub-models (reusable typed dicts for structured props)
# ---------------------------------------------------------------------------


class TabItem(BaseModel):
    """A single tab definition for Tabs."""

    label: str
    value: str


class BreadcrumbItem(BaseModel):
    """A single breadcrumb segment."""

    label: str
    href: str | None = None


class TimelineItem(BaseModel):
    """A single entry in a Timeline."""

    label: str
    time: str | None = None
    status: str | None = None
    detail: str | None = None


class ColumnDef(BaseModel):
    """A column definition for Table."""

    key: str
    label: str | None = None
    width: str | None = None


class KVEntry(BaseModel):
    """A key-value entry for KeyValue (matches renderer's SDUIKVEntry)."""

    key: str | None = None
    label: str | None = None
    value: str | None = None


class OptionItem(BaseModel):
    """An option for Select, Radio, etc."""

    label: str
    value: str


# ---------------------------------------------------------------------------


class _PrimitiveBase(BaseModel):
    """Internal base for individual primitive props.

    Subclasses define typed props.  ``to_node()`` serialises the primitive
    into the raw dict tree the renderer consumes.  ``if_condition`` is
    lifted to the node-level ``"if"`` key.
    """

    if_condition: str | None = Field(None, exclude=True)

    model_config = {"populate_by_name": True}

    def _node_type(self) -> str:
        raise NotImplementedError

    def to_node(self) -> dict[str, Any]:
        """Serialise to the dict shape SDUIRenderer expects."""
        props = self.model_dump(exclude={"if_condition"}, exclude_none=True)
        result: dict[str, Any] = {"type": self._node_type(), "props": props}
        if self.if_condition is not None:
            result["if"] = self.if_condition
        return result


class Stack(_PrimitiveBase):
    """Layout container -- renders as ``column`` or ``row`` depending on direction.

    The renderer uses separate ``column``/``row`` types.  ``to_node()``
    maps ``direction`` to the appropriate type.

    Note: the renderer's ``stack`` type is a *z-axis overlay* (position:
    absolute children).  This model represents the flexbox stack concept.
    """

    direction: Literal["column", "row"] = "column"
    gap: int | None = None
    align: str | None = None
    justify: str | None = None
    wrap: bool | None = None

    def _node_type(self) -> str:
        return self.direction

    def to_node(self) -> dict[str, Any]:
        """Serialise to the dict shape SDUIRenderer expects.

        Direction is encoded in the type, not in props.
        """
        props = self.model_dump(exclude={"if_condition", "direction"}, exclude_none=True)
        result: dict[str, Any] = {"type": self._node_type(), "props": props}
        if self.if_condition is not None:
            result["if"] = self.if_condition
        return result


class Grid(_PrimitiveBase):
    """CSS-grid layout."""

    columns: int | str | None = None
    gap: int | None = None
    min_width: str | None = None

    def _node_type(self) -> str:
        return "grid"


class Card(_PrimitiveBase):
    """Elevated card container."""

    title: str | None = None
    subtitle: str | None = None
    padding: int | str | None = None
    elevated: bool | None = None

    def _node_type(self) -> str:
        return "card"


class Tabs(_PrimitiveBase):
    """Tab switcher."""

    items: list[TabItem] = []
    active: str | None = None

    def _node_type(self) -> str:
        return "tabs"


class Breadcrumb(_PrimitiveBase):
    """Navigation breadcrumb trail."""

    items: list[BreadcrumbItem] = []

    def _node_type(self) -> str:
        return "breadcrumb"


class Empty(_PrimitiveBase):
    """Empty-state placeholder."""

    message: str | None = None
    icon: str | None = None

    def _node_type(self) -> str:
        return "empty"


class Spacer(_PrimitiveBase):
    """Empty space with optional fixed height.

    When ``size`` is set the spacer has a fixed height of that many pixels.
    When omitted it flexes to fill available space (``flex: 1``).
    """

    size: int | None = None

    def _node_type(self) -> str:
        return "spacer"


class Divider(_PrimitiveBase):
    """Horizontal line separator (``<hr>``).

    Renders a 1px border-top rule.  Accepts generic style overrides via
    ``resolveStyles`` but defines no props of its own.
    """

    def _node_type(self) -> str:
        return "divider"


class ZStack(_PrimitiveBase):
    """Z-axis overlay container (position-absolute children).

    Each child is rendered with ``position: absolute; inset: 0`` inside a
    ``position: relative`` wrapper.  Optional ``width`` and ``height``
    (in px) size the container explicitly.

    Note: this is *not* the flexbox Stack (column/row) -- it corresponds
    to the renderer's ``stack`` type which layers children on top of each
    other.
    """

    width: int | None = None
    height: int | None = None

    def _node_type(self) -> str:
        return "stack"


class Heading(_PrimitiveBase):
    """Section heading (h1-h6).

    The renderer reads ``content`` and ``level`` from props.
    """

    content: str = ""
    """Named ``content`` to match SDUIRenderer (not ``text``)."""
    level: int = Field(default=2, ge=1, le=6)

    def _node_type(self) -> str:
        return "heading"


class Text(_PrimitiveBase):
    """Inline text span.

    The renderer reads ``content``, ``size``, and ``weight`` from props.
    """

    content: str = ""
    size: Literal["xs", "sm", "md", "lg", "xl"] | None = None
    weight: Literal["normal", "medium", "semibold", "bold"] | None = None
    color: str | None = None
    truncate: bool | None = None

    def _node_type(self) -> str:
        return "text"


class Code(_PrimitiveBase):
    """Syntax-highlighted code block.

    Serialises as type ``code-block`` with ``content`` prop to match the
    renderer.
    """

    content: str = ""
    language: str | None = None

    def _node_type(self) -> str:
        return "code-block"


class Status(_PrimitiveBase):
    """Status badge / indicator."""

    label: str = ""
    variant: Literal["success", "error", "warning", "info", "neutral"] = "neutral"

    def _node_type(self) -> str:
        return "status"


class Spinner(_PrimitiveBase):
    """Loading spinner."""

    size: Literal["sm", "md", "lg"] = "md"

    def _node_type(self) -> str:
        return "spinner"


class Timeline(_PrimitiveBase):
    """Vertical timeline of events."""

    items: list[TimelineItem] = []

    def _node_type(self) -> str:
        return "timeline"


class Diff(_PrimitiveBase):
    """Side-by-side or unified diff view."""

    old_text: str = ""
    new_text: str = ""
    language: str | None = None

    def _node_type(self) -> str:
        return "diff"


class Markdown(_PrimitiveBase):
    """Rendered Markdown content."""

    content: str = ""

    def _node_type(self) -> str:
        return "markdown"


class Badge(_PrimitiveBase):
    """Small label / tag displayed as a rounded pill.

    The renderer reads ``content`` (the text) and ``color`` (one of
    ``blue``, ``green``/``success``, ``red``/``danger``/``error``,
    ``yellow``/``warning``, or ``gray`` which is the default).
    """

    content: str = ""
    color: str | None = None

    def _node_type(self) -> str:
        return "badge"


class ProgressBar(_PrimitiveBase):
    """Horizontal progress bar.

    The renderer reads ``value`` (0-100), ``color`` (any CSS colour,
    defaults to ``var(--accent)``), and an optional ``label`` string
    centred over the bar.
    """

    value: float = Field(default=0, ge=0, le=100)
    color: str | None = None
    label: str | None = None

    def _node_type(self) -> str:
        return "progress-bar"


class Table(_PrimitiveBase):
    """Data table with typed columns.

    The renderer reads ``columns`` (list of {key, label, width}) and
    ``rows`` (list of row dicts) from props.
    """

    columns: list[ColumnDef] = []
    rows_key: str | None = None

    def _node_type(self) -> str:
        return "table"


class List(_PrimitiveBase):
    """Iterable list whose children are stamped per item.

    The renderer reads ``items`` from props.
    """

    items_key: str | None = None

    def _node_type(self) -> str:
        return "list"


class KeyValue(_PrimitiveBase):
    """Key-value display (definition list).

    The renderer reads ``entries`` (list of {key/label, value}) from
    props -- note: *entries*, not *items*.
    """

    entries: list[KVEntry] = []

    def _node_type(self) -> str:
        return "key-value"


class JsonView(_PrimitiveBase):
    """Interactive JSON tree viewer."""

    data_key: str | None = None

    def _node_type(self) -> str:
        return "json-view"


class Tree(_PrimitiveBase):
    """Hierarchical tree view."""

    items_key: str | None = None
    label_key: str | None = None
    children_key: str | None = None

    def _node_type(self) -> str:
        return "tree"


class Button(_PrimitiveBase):
    """Clickable button that dispatches a command.

    The renderer reads ``label``, ``variant``, ``command``, ``disabled``
    from props.  ``command`` is the action identifier (string), not a
    dict -- the renderer calls ``onCommand(command, params)``.
    """

    label: str = ""
    variant: Literal["primary", "danger", "ghost", "outline"] = "primary"
    command: str | None = None
    confirm: str | None = None
    disabled: bool | str | None = None
    size: Literal["sm", "md"] | None = None

    def _node_type(self) -> str:
        return "button"


class Input(_PrimitiveBase):
    """Single-line text input."""

    name: str = ""
    label: str | None = None
    placeholder: str | None = None
    input_type: Literal["text", "number", "email", "password"] = Field(default="text", alias="type")

    def _node_type(self) -> str:
        return "input"


class TextArea(_PrimitiveBase):
    """Multi-line text input."""

    name: str = ""
    label: str | None = None
    placeholder: str | None = None
    rows: int | None = None

    def _node_type(self) -> str:
        return "textarea"


class Select(_PrimitiveBase):
    """Dropdown select."""

    name: str = ""
    label: str | None = None
    options: list[OptionItem] = []
    placeholder: str | None = None

    def _node_type(self) -> str:
        return "select"


class Checkbox(_PrimitiveBase):
    """Boolean checkbox."""

    name: str = ""
    label: str | None = None
    checked: bool = False

    def _node_type(self) -> str:
        return "checkbox"


class Switch(_PrimitiveBase):
    """Toggle switch."""

    name: str = ""
    label: str | None = None
    checked: bool = False

    def _node_type(self) -> str:
        return "switch"


class Radio(_PrimitiveBase):
    """Radio button group."""

    name: str = ""
    label: str | None = None
    options: list[OptionItem] = []

    def _node_type(self) -> str:
        return "radio"


class Slider(_PrimitiveBase):
    """Numeric slider."""

    name: str = ""
    label: str | None = None
    min: float = 0
    max: float = 100
    step: float = 1
    value: float | None = None

    def _node_type(self) -> str:
        return "slider"


class Alert(_PrimitiveBase):
    """Inline alert banner.

    The renderer reads ``severity``, ``title``, ``message`` from props.
    """

    severity: Literal["info", "success", "warning", "error"] = "info"
    title: str | None = None
    message: str = ""

    def _node_type(self) -> str:
        return "alert"


class Toast(_PrimitiveBase):
    """Ephemeral toast notification."""

    message: str = ""
    variant: Literal["info", "success", "warning", "error"] = "info"
    duration_ms: int = 3000

    def _node_type(self) -> str:
        return "toast"


class Logs(_PrimitiveBase):
    """Streaming log viewer."""

    source_event: str | None = None
    max_lines: int = 200
    auto_scroll: bool = True

    def _node_type(self) -> str:
        return "logs"


class Modal(_PrimitiveBase):
    """Overlay modal dialog."""

    title: str | None = None
    open_event: str | None = None
    close_event: str | None = None

    def _node_type(self) -> str:
        return "modal"


class Drawer(_PrimitiveBase):
    """Slide-in panel."""

    title: str | None = None
    position: Literal["left", "right"] = "right"
    width: str | None = None

    def _node_type(self) -> str:
        return "drawer"


class Popover(_PrimitiveBase):
    """Popover tooltip / flyout."""

    trigger: str | None = None
    placement: Literal["top", "bottom", "left", "right"] = "bottom"

    def _node_type(self) -> str:
        return "popover"


class Confirm(_PrimitiveBase):
    """Confirmation dialog before a destructive action."""

    title: str | None = None
    message: str = ""
    confirm_label: str = "Confirm"
    cancel_label: str = "Cancel"
    action: str | None = None

    def _node_type(self) -> str:
        return "confirm"


# Allow forward references in SDUINode.children to resolve.
SDUINode.model_rebuild()
