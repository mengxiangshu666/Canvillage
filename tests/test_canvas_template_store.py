from __future__ import annotations

import pytest

from novelvideo.freezone.canvas_template_store import (
    CanvasTemplateError,
    CanvasTemplateStore,
)


def _node(key: str, node_type: str, *, parent_key: str = "", data=None):
    value = {
        "key": key,
        "type": node_type,
        "offset": {"x": 10, "y": 20},
        "data": data or {},
    }
    if parent_key:
        value["parent_key"] = parent_key
    return value


def test_user_canvas_template_round_trips_sanitized_subgraph(tmp_path):
    store = CanvasTemplateStore(tmp_path / "canvas_templates.db")
    created = store.create(
        owner_id="user-a",
        title="雨夜追逐",
        description="两个镜头加合成",
        nodes=[
            _node(
                "image-1",
                "imageGenNode",
                data={
                    "prompt": "雨夜街头",
                    "model": "image-model",
                    "imageUrl": "https://example.test/generated.png",
                    "agent_command_id": "cmd-1",
                    "resourceMeta": {"sha256": "generated"},
                },
            ),
            _node(
                "video-1",
                "videoNode",
                data={
                    "prompt": "角色向前奔跑",
                    "videoUrl": "https://example.test/generated.mp4",
                    "isGenerating": True,
                },
            ),
        ],
        edges=[
            {
                "source": "image-1",
                "target": "video-1",
                "relation": "references",
            }
        ],
        source_project_id="project-a",
        source_canvas_id="canvas-a",
    )

    assert created["id"].startswith("ct_")
    assert created["node_count"] == 2
    assert created["edge_count"] == 1
    assert created["nodes"][0]["data"] == {
        "prompt": "雨夜街头",
        "model": "image-model",
    }
    assert created["nodes"][1]["data"] == {"prompt": "角色向前奔跑"}

    items = store.list_for_owner("user-a")
    assert [item["id"] for item in items] == [created["id"]]
    assert items[0]["title"] == "雨夜追逐"
    assert store.list_for_owner("user-b") == []

    detail = store.get("user-a", created["id"])
    assert detail is not None
    assert detail["edges"] == [
        {
            "source": "image-1",
            "target": "video-1",
            "relation": "references",
        }
    ]
    assert store.get("user-b", created["id"]) is None
    assert store.delete("user-b", created["id"]) is False
    assert store.delete("user-a", created["id"]) is True
    assert store.list_for_owner("user-a") == []


def test_user_canvas_template_rejects_invalid_graphs(tmp_path):
    store = CanvasTemplateStore(tmp_path / "canvas_templates.db")

    with pytest.raises(CanvasTemplateError, match="unsupported"):
        store.create(
            owner_id="user-a",
            title="bad",
            nodes=[_node("n1", "unknownNode")],
            edges=[],
        )

    with pytest.raises(CanvasTemplateError, match="unknown node"):
        store.create(
            owner_id="user-a",
            title="bad edge",
            nodes=[_node("n1", "imageGenNode")],
            edges=[{"source": "n1", "target": "missing"}],
        )

    with pytest.raises(CanvasTemplateError, match="cycle"):
        store.create(
            owner_id="user-a",
            title="cycle",
            nodes=[
                _node("group-a", "groupNode", parent_key="group-b"),
                _node("group-b", "groupNode", parent_key="group-a"),
            ],
            edges=[],
        )


def test_user_canvas_template_preserves_group_parent_metadata(tmp_path):
    store = CanvasTemplateStore(tmp_path / "canvas_templates.db")
    created = store.create(
        owner_id="user-a",
        title="grouped",
        nodes=[
            {
                **_node("group-1", "groupNode"),
                "width": 800,
                "height": 500,
            },
            {
                **_node("image-1", "imageGenNode", parent_key="group-1"),
                "offset": {"x": 60, "y": 80},
            },
        ],
        edges=[],
    )

    assert created["nodes"][0]["width"] == 800
    assert created["nodes"][0]["height"] == 500
    assert created["nodes"][1]["parent_key"] == "group-1"
