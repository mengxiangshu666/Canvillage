"""M04 pilot: opt-in strongly typed tool surface, default stays indexed."""

from __future__ import annotations

from novelvideo.agent_tools.village_canvas import native_tool_entries
from novelvideo.agent_tools.village_canvas.core import (
    TYPED_CANVAS_AGENT_TOOL_NAMES,
)


def _names() -> set[str]:
    return {name for name, _schema, _handler in native_tool_entries()}


def test_default_surface_keeps_the_indexed_broker(monkeypatch) -> None:
    monkeypatch.delenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", raising=False)
    names = _names()

    assert "village_canvas_capability" in names
    assert "village_canvas_dispatch_action" in names
    assert "village_canvas_apply_commands" not in names


def test_typed_surface_has_no_capability_broker_or_generic_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "typed")
    names = _names()

    assert TYPED_CANVAS_AGENT_TOOL_NAMES <= names
    assert "vision_analyze" in names
    assert "village_canvas_capability" not in names
    assert "village_canvas_dispatch_action" not in names


def test_typed_tools_have_real_object_schemas(monkeypatch) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "typed")
    entries = {name: schema for name, schema, _handler in native_tool_entries()}

    for name in TYPED_CANVAS_AGENT_TOOL_NAMES:
        schema = entries[name]
        parameters = schema.get("parameters")
        assert parameters["type"] == "object", name
        properties = parameters.get("properties") or {}
        assert "arguments" not in properties, name
        assert properties, name


def test_full_surface_is_still_available_for_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "full")
    names = _names()

    assert "village_canvas_apply_commands" in names
    assert "village_canvas_capability" not in names


def test_unknown_mode_falls_back_to_indexed(monkeypatch) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "typo")
    names = _names()

    assert "village_canvas_capability" in names
    assert "village_canvas_dispatch_action" in names
