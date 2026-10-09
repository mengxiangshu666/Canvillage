"""The declared-spec assembler must rebuild the surface, or refuse loudly.

T-158 moves capabilities out of the ordered shared-globals modules and into one
`ToolSpec` each. The failure modes are the interesting part: an entry left
behind in both places, an entry deleted from both, or a capability pointed at a
handler that no longer exists. Each of those must stop the import rather than
quietly change what the agent is offered.
"""

from __future__ import annotations

import pytest

from novelvideo.agent_tools.village_canvas.tools.spec import (
    SurfaceError,
    ToolSpec,
    assemble_capability_handler_defaults,
    assemble_core_cards,
    assemble_tools,
)


def _card(card_id: str) -> dict:
    return {"id": card_id, "domain": "test", "purpose": card_id}


def test_assemble_core_cards_keeps_the_frozen_order_and_swaps_in_specs() -> None:
    legacy = (_card("a"), _card("c"))
    specs = (
        ToolSpec(
            id="b",
            handler_name="_handle_b",
            card={**_card("b"), "purpose": "moved"},
        ),
    )

    assembled = assemble_core_cards(order=("a", "b", "c"), legacy=legacy, specs=specs)

    assert [card["id"] for card in assembled] == ["a", "b", "c"]
    assert assembled[1]["purpose"] == "moved"
    assert assembled[0] is legacy[0]
    assert assembled[2] is legacy[1]


def test_assemble_tools_uses_the_declared_schema_and_handler() -> None:
    legacy = ()
    namespace = {"handle_x": lambda **_: "declared"}
    spec = ToolSpec(
        id="x",
        handler_name="handle_x",
        tool_name="t_a",
        description="declared",
        properties={"p": {"type": "string"}},
        required=("p",),
    )

    assembled = assemble_tools(
        order=("t_a",),
        legacy=legacy,
        specs=(spec,),
        namespace=namespace,
    )

    name, schema, handler = assembled[0]
    assert name == "t_a"
    assert schema == {
        "name": "t_a",
        "description": "declared",
        "parameters": {
            "type": "object",
            "properties": {"p": {"type": "string"}},
            "required": ["p"],
        },
    }
    assert handler() == "declared"
    namespace["handle_x"] = lambda **_: "replaced"
    assert handler() == "replaced"


def test_legacy_entry_missing_from_the_order_is_refused() -> None:
    with pytest.raises(SurfaceError, match="missing from the frozen order"):
        assemble_core_cards(order=("a",), legacy=(_card("a"), _card("b")), specs=())


def test_entry_cannot_be_both_legacy_and_declared() -> None:
    with pytest.raises(SurfaceError, match="both legacy and declared"):
        assemble_core_cards(
            order=("a",),
            legacy=(_card("a"),),
            specs=(ToolSpec(id="a", handler_name="h", card=_card("a")),),
        )


def test_order_entry_with_no_provider_is_refused() -> None:
    with pytest.raises(SurfaceError, match="neither a spec nor the legacy"):
        assemble_core_cards(order=("a", "gone"), legacy=(_card("a"),), specs=())


def test_spec_outside_the_frozen_order_is_refused() -> None:
    with pytest.raises(SurfaceError, match="not listed in the frozen order"):
        assemble_core_cards(
            order=("a",),
            legacy=(_card("a"),),
            specs=(ToolSpec(id="new", handler_name="h", card=_card("new")),),
        )


def test_declared_handler_must_exist_in_the_namespace() -> None:
    with pytest.raises(SurfaceError, match="is not defined"):
        assemble_tools(
            order=("t",),
            legacy=(),
            specs=(
                ToolSpec(
                    id="x",
                    handler_name="missing",
                    tool_name="t",
                    description="d",
                ),
            ),
            namespace={},
        )


def test_migrated_state_capabilities_come_from_the_declared_specs() -> None:
    """The point of the migration: one declaration answers all three faces."""

    from novelvideo.agent_tools import village_canvas as plugin
    from novelvideo.agent_tools.village_canvas.tools.state import STATE_SPECS

    assert [spec.id for spec in STATE_SPECS] == ["state.sql.schema", "state.sql.query"]

    card_ids = [card["id"] for card in plugin._CORE_CAPABILITY_INDEX]
    assert card_ids.index("state.sql.schema") < card_ids.index("state.sql.query")

    for spec in STATE_SPECS:
        card = next(
            item for item in plugin._CORE_CAPABILITY_INDEX if item["id"] == spec.id
        )
        assert card == dict(spec.card)
        assert plugin._capability_handler(spec.id) is plugin.__dict__[spec.handler_name]


def test_migrated_capability_handler_lookup_keeps_runtime_replacement() -> None:
    from novelvideo.agent_tools import village_canvas as plugin

    original = plugin._handle_get_canvas_snapshot

    def replacement(_args):
        return {"ok": True}

    try:
        plugin._handle_get_canvas_snapshot = replacement
        assert plugin._capability_handler("canvas.snapshot") is replacement
    finally:
        plugin._handle_get_canvas_snapshot = original


def test_declared_handler_defaults_are_assembled() -> None:
    specs = (
        ToolSpec(
            id="canvas.node.video.capture_first_frame",
            handler_name="_handle_frontend_ui_tool",
            card=_card("canvas.node.video.capture_first_frame"),
            handler_defaults={"action": "video_capture_frame", "mode": "first"},
        ),
    )

    assert assemble_capability_handler_defaults(specs) == {
        "canvas.node.video.capture_first_frame": {
            "action": "video_capture_frame",
            "mode": "first",
        }
    }


def test_frontend_ui_capabilities_merge_fixed_arguments_after_caller_input() -> None:
    from novelvideo.agent_tools import village_canvas as plugin
    from novelvideo.agent_tools.village_canvas.tools.frontend_ui import (
        FRONTEND_UI_SPECS,
    )

    assert len(FRONTEND_UI_SPECS) == 16
    original = plugin._handle_frontend_ui_tool
    seen: dict[str, object] = {}

    def replacement(args):
        seen.update(args)
        return {"ok": True}

    try:
        plugin._handle_frontend_ui_tool = replacement
        handler = plugin._capability_handler("canvas.node.video.capture_first_frame")
        assert callable(handler)
        assert handler({"node_id": "node-1", "mode": "current"}) == {"ok": True}
    finally:
        plugin._handle_frontend_ui_tool = original

    assert seen == {
        "node_id": "node-1",
        "action": "video_capture_frame",
        "mode": "first",
    }
