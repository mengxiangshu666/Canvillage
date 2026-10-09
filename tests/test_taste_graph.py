from __future__ import annotations

import json
from dataclasses import replace

from novelvideo.chat.memory_index import MemoryRecord
from novelvideo.chat.taste_graph import project_taste_graph
from novelvideo.chat import taste_graph


def _preference(
    memory_id: int,
    content: str,
    *,
    status: str = "confirmed",
    positive: int = 0,
    negative: int = 0,
    locked: bool = False,
    metadata: dict | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        scope_kind="user",
        scope_id="",
        kind="preference",
        source="growth_distiller",
        source_id=f"event:{memory_id}",
        content=content,
        importance=0.8,
        status=status,
        confidence=0.7,
        locked=locked,
        applies_when=json.dumps({"task_stage": "media_generation"}),
        evidence_count=positive + negative,
        positive_count=positive,
        negative_count=negative,
        metadata_json=json.dumps(
            metadata
            or {
                "memory_schema": "xiaoshu.memory.v3",
                "action": [content],
                "evidence": [
                    {"ref": f"workflow:run-{memory_id}", "outcome": "positive"}
                ]
                if positive
                else [],
            }
        ),
        updated_at=f"2026-09-0{memory_id}T00:00:00+00:00",
    )


def test_taste_graph_uses_structured_confirmed_and_verified_preferences_only():
    records = [
        _preference(1, "电影画面要保持清冷色调和真实材质", positive=2),
        _preference(2, "我今天心情不好", status="candidate"),
        _preference(
            3,
            "不要使用塑料 CG 质感",
            positive=1,
            metadata={
                "memory_schema": "xiaoshu.memory.v3",
                "action": ["保持真实材质"],
                "avoid": ["不要使用塑料 CG 质感"],
                "evidence": [{"ref": "workflow:run-3", "outcome": "positive"}],
            },
        ),
        MemoryRecord(
            id=4,
            scope_kind="user",
            scope_id="",
            kind="preference",
            source="profile_file",
            source_id="preferences.md",
            content="未经提炼的旧偏好全文",
            importance=0.9,
        ),
    ]

    graph = project_taste_graph(records, project="movie-a")

    assert graph["schema"] == "taste_graph.v1"
    assert [item["memory_id"] for item in graph["hard_loves"]] == [1]
    assert [item["memory_id"] for item in graph["hard_antis"]] == [3]
    assert graph["hard_loves"][0]["confidence"] == "H"
    assert graph["hard_loves"][0]["category"] in {"color", "material"}
    assert graph["hard_antis"][0]["evidence_ids"] == ["workflow:run-3"]
    assert graph["stats"] == {
        "eligible": 2,
        "rejected": 2,
        "love_count": 1,
        "anti_count": 1,
    }


def test_taste_graph_rejects_contradicted_candidate_and_scrubs_private_identity():
    contradicted = _preference(
        1,
        "所有项目都使用一个固定风格",
        status="candidate",
        positive=1,
        negative=2,
    )
    private = _preference(
        2,
        r"参考 C:\Users\Alice\secret\look.png 并联系 artist@example.com",
        locked=True,
    )

    graph = project_taste_graph([contradicted, private])

    assert graph["stats"]["eligible"] == 1
    rendered = json.dumps(graph, ensure_ascii=False)
    assert "C:\\Users\\Alice" not in rendered
    assert "artist@example.com" not in rendered
    assert "[local-path]" in rendered
    assert "[email]" in rendered


def test_taste_graph_revision_changes_with_memory_version():
    base = _preference(1, "偏好稳定镜头和真实动作反馈", positive=1)
    changed = MemoryRecord(
        **{
            field: getattr(base, field)
            for field in MemoryRecord.__dataclass_fields__
            if field != "version"
        },
        version=2,
    )

    assert project_taste_graph([base])["revision"] != project_taste_graph([changed])["revision"]


def test_build_taste_graph_keeps_project_preferences_in_their_project(monkeypatch):
    global_pref = _preference(1, "所有项目保持真实材质", positive=2)
    project_a = replace(
        _preference(2, "项目 A 使用冷青色调", positive=1),
        scope_kind="project",
        scope_id="project-a",
    )
    project_b = replace(
        _preference(3, "项目 B 使用暖橙色调", positive=1),
        scope_kind="project",
        scope_id="project-b",
    )
    monkeypatch.setattr(
        taste_graph,
        "list_memories",
        lambda *_args, **_kwargs: [global_pref, project_a, project_b],
    )

    graph_a = taste_graph.build_taste_graph("alice", "project-a")
    graph_b = taste_graph.build_taste_graph("alice", "project-b")
    ids_a = {item["memory_id"] for item in graph_a["hard_loves"]}
    ids_b = {item["memory_id"] for item in graph_b["hard_loves"]}

    assert ids_a == {1, 2}
    assert ids_b == {1, 3}
