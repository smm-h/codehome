"""Tests for subscription_parser: @on() decorator discovery, static enforcement, TOML round-trip."""

from __future__ import annotations

import logging
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from codehome.plugins.subscription_parser import (
    parse_plugin_subscriptions,
    read_events_toml,
    write_events_toml,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_py(tmp_path: Path, filename: str, source: str) -> Path:
    """Write a Python source string to a file in tmp_path, return the file path."""
    p = tmp_path / filename
    p.write_text(dedent(source))
    return p


# ── 1. Simple decorator ──────────────────────────────────────────────────


def test_simple_decorator(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        @on("order.created")
        def handle_order():
            pass
    """,
    )
    subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 1
    assert subs[0].event_name == "order.created"
    assert subs[0].handler_name == "handle_order"
    assert subs[0].module == "handlers"


# ── 2. Multiple decorators on one function ────────────────────────────────


def test_multiple_decorators_same_function(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        @on("a.event")
        @on("b.event")
        def multi_handler():
            pass
    """,
    )
    subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 2
    events = {s.event_name for s in subs}
    assert events == {"a.event", "b.event"}
    # Both point to the same handler.
    assert all(s.handler_name == "multi_handler" for s in subs)


# ── 3. Multiple functions ─────────────────────────────────────────────────


def test_multiple_functions(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        @on("first")
        def handler_one():
            pass

        @on("second")
        def handler_two():
            pass
    """,
    )
    subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 2
    names = {s.handler_name for s in subs}
    assert names == {"handler_one", "handler_two"}


# ── 4. f-string rejection ─────────────────────────────────────────────────


def test_fstring_raises(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        name = "order"

        @on(f"event.{name}")
        def bad_handler():
            pass
    """,
    )
    with pytest.raises(TypeError, match="must be a string literal"):
        parse_plugin_subscriptions(tmp_path)


# ── 5. Constant-ref (variable) rejection ──────────────────────────────────


def test_variable_arg_raises(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        EVENT_NAME = "order.created"

        @on(EVENT_NAME)
        def bad_handler():
            pass
    """,
    )
    with pytest.raises(TypeError, match="must be a string literal"):
        parse_plugin_subscriptions(tmp_path)


# ── 6. Bare @on() with no args ───────────────────────────────────────────


def test_bare_on_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        @on()
        def empty_handler():
            pass
    """,
    )
    with caplog.at_level(logging.WARNING):
        subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 0
    assert "bare @on() with no arguments" in caplog.text


# ── 7. Nested function rejection ─────────────────────────────────────────


def test_nested_function_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        def outer():
            @on("nested.event")
            def inner():
                pass
    """,
    )
    with caplog.at_level(logging.WARNING):
        subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 0
    assert "nested function or method" in caplog.text


# ── 8. Class method rejection ─────────────────────────────────────────────


def test_class_method_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        class MyHandler:
            @on("class.event")
            def handle(self):
                pass
    """,
    )
    with caplog.at_level(logging.WARNING):
        subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 0
    assert "nested function or method" in caplog.text


# ── 9. Round-trip: parse -> write_events_toml -> read_events_toml ─────────


def test_round_trip(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        from events import on

        @on("order.created")
        def handle_order():
            pass

        @on("order.shipped")
        def handle_shipment():
            pass
    """,
    )
    subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 2

    write_events_toml(tmp_path, subs)
    toml_path = tmp_path / "events.toml"
    assert toml_path.exists()

    loaded = read_events_toml(tmp_path)
    assert len(loaded) == 2

    # Compare as sets of (event, handler, module) -- line numbers may differ
    # due to sort order, but the data should match.
    original = {(s.event_name, s.handler_name, s.module) for s in subs}
    restored = {(s.event_name, s.handler_name, s.module) for s in loaded}
    assert original == restored


# ── 10. No decorators ────────────────────────────────────────────────────


def test_no_decorators(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "handlers.py",
        """\
        def plain_function():
            pass

        def another():
            return 42
    """,
    )
    subs = parse_plugin_subscriptions(tmp_path)
    assert len(subs) == 0
