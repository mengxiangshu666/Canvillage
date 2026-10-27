"""角色档案解析：一句话/参考图 → 结构化档案。

覆盖三段：解析器的输入校验与字段映射（不调真实模型）、任务 runner 的返回
契约、以及 HTTP 路由的入参校验和项目内参考图解析。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.agents.character_profiler import (
    CharacterProfile,
    CharacterSignatureFeature,
    NEGATIVE_CONSTRAINTS_TEMPLATE,
    build_generation_prompt,
    parse_character_profile,
    profile_to_character_fields,
    profile_to_identity_fields,
)


def _profile() -> CharacterProfile:
    return CharacterProfile(
        suggested_name="沈青禾",
        gender="女",
        age_group="青年",
        brief="二十二岁的建筑师",
        face_prompt="鹅蛋脸，柳叶眉",
        appearance_details="深灰长风衣",
        body_type="纤细高挑",
        signature_features=[
            CharacterSignatureFeature(
                feature="柳叶眉", priority="primary", visual_expression="不显凌厉"
            ),
            CharacterSignatureFeature(feature="左眼下小痣", priority="secondary"),
        ],
    )


async def test_parse_requires_exactly_one_input():
    with pytest.raises(ValueError, match="一句话描述或一张参考图"):
        await parse_character_profile()
    with pytest.raises(ValueError, match="只能二选一"):
        await parse_character_profile(description="红衣女侠", image_path="a.png")


def test_age_group_is_normalized_to_english_slots():
    assert _profile().age_group == "youth"
    assert CharacterProfile(age_group="老年").age_group == "elder"
    assert CharacterProfile(age_group="儿童").age_group == "child"
    assert CharacterProfile(age_group="middle").age_group == "middle"


def test_character_fields_carry_signature_and_body_type():
    fields = profile_to_character_fields(_profile())

    assert fields["gender"] == "女"
    assert fields["age_group"] == "youth"
    assert fields["body_type"] == "纤细高挑"
    assert fields["description"] == "二十二岁的建筑师"
    assert "鹅蛋脸，柳叶眉" in fields["face_prompt"]
    assert "签名特征：柳叶眉" in fields["face_prompt"]
    assert "次要特征：左眼下小痣" in fields["face_prompt"]


def test_identity_fields_are_the_costume_layer():
    fields = profile_to_identity_fields(_profile())

    assert fields["appearance_details"] == "深灰长风衣"
    assert fields["face_prompt"] == "鹅蛋脸，柳叶眉"
    assert "signature_features" not in fields


def test_generation_prompt_always_carries_the_negative_list():
    assert build_generation_prompt(_profile()) == NEGATIVE_CONSTRAINTS_TEMPLATE

    prompt = build_generation_prompt(
        _profile().model_copy(
            update={"generation_prompt": "单人正面全身，纯色背景"}
        )
    )
    assert "单人正面全身，纯色背景" in prompt
    assert NEGATIVE_CONSTRAINTS_TEMPLATE in prompt


async def test_runner_returns_profile_character_fields_and_prompt(monkeypatch):
    from novelvideo.agents import character_profiler
    from novelvideo.task_backend.runners import character_profile as runner

    async def fake_parse(*, description=None, image_path=None):
        assert description == "红衣女侠"
        assert image_path is None
        return _profile()

    class _Manager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    monkeypatch.setattr(character_profiler, "parse_character_profile", fake_parse)
    monkeypatch.setattr(runner, "get_task_manager", lambda: _Manager())

    result = await runner._run_character_profile_parse(
        {"payload": {"description": "红衣女侠"}, "scope": None},
        object(),
    )

    assert result is not None
    assert result["ok"] is True
    assert result["profile"]["suggested_name"] == "沈青禾"
    assert result["character_fields"]["body_type"] == "纤细高挑"
    assert result["identity_fields"]["appearance_details"] == "深灰长风衣"
    assert NEGATIVE_CONSTRAINTS_TEMPLATE in result["generation_prompt"]


class _Queued:
    def __init__(self):
        self.task_state = SimpleNamespace(task_id="task-1", metadata={})
        self.backend = "local"
        self.queue = "default"


class _Backend:
    def __init__(self):
        self.calls: list[dict] = []

    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append(kwargs)
        return _Queued()


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import characters

    project_dir = tmp_path / "output" / "admin" / "demo"
    project_dir.mkdir(parents=True)
    backend = _Backend()

    async def fake_resolve_project(project: str, user: dict, *, required_role: str = "viewer"):
        return SimpleNamespace(
            ctx=SimpleNamespace(project_id="proj_demo", output_dir=str(project_dir)),
            project_dir=project_dir,
            output_dir=str(project_dir),
        )

    monkeypatch.setattr(characters, "resolve_project_scope", fake_resolve_project)
    monkeypatch.setattr(characters, "get_task_backend", lambda: backend)

    app = FastAPI()
    app.include_router(characters.router)
    app.dependency_overrides[characters.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app), backend, project_dir


def test_route_rejects_empty_and_ambiguous_input(monkeypatch, tmp_path):
    client, backend, _ = _client(monkeypatch, tmp_path)

    empty = client.post("/projects/demo/characters/parse-profile", json={})
    assert empty.status_code == 200
    assert empty.json()["ok"] is False
    assert "一句话描述或一张参考图" in empty.json()["error"]

    both = client.post(
        "/projects/demo/characters/parse-profile",
        json={"description": "红衣女侠", "image_url": "/freezone/_uploads/a.png"},
    )
    assert both.json()["ok"] is False
    assert "只能二选一" in both.json()["error"]
    assert backend.calls == []


def test_route_queues_description_only(monkeypatch, tmp_path):
    client, backend, _ = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/characters/parse-profile",
        json={"description": "红衣女侠，冷面，使双刀"},
    )

    body = response.json()
    assert body["ok"] is True
    assert body["task_type"] == "character_profile_parse"
    assert body["scope"] == "character-profile:红衣女侠，冷面，使双刀"
    assert "character_profile_parse" in body["task_key"]
    assert len(backend.calls) == 1
    assert backend.calls[0]["payload"]["description"] == "红衣女侠，冷面，使双刀"
    assert backend.calls[0]["payload"]["image_path"] == ""


def test_route_resolves_project_static_url_with_cache_buster(monkeypatch, tmp_path):
    client, backend, project_dir = _client(monkeypatch, tmp_path)
    ref = project_dir / "freezone" / "_uploads" / "ref.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"png")

    response = client.post(
        "/projects/demo/characters/parse-profile",
        json={"image_url": "/static/projects/proj_demo/freezone/_uploads/ref.png?v=1"},
    )

    body = response.json()
    assert body["ok"] is True, body
    assert backend.calls[0]["payload"]["image_path"] == str(ref)


def test_route_rejects_image_outside_the_project(monkeypatch, tmp_path):
    client, backend, _ = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/characters/parse-profile",
        json={"image_url": "../../../../secret.png"},
    )

    body = response.json()
    assert body["ok"] is False
    assert "当前项目内" in body["error"]
    assert backend.calls == []


def test_route_rejects_missing_image_file(monkeypatch, tmp_path):
    client, backend, _ = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/characters/parse-profile",
        json={"image_url": "/freezone/_uploads/missing.png"},
    )

    body = response.json()
    assert body["ok"] is False
    assert "参考图不存在" in body["error"]
    assert backend.calls == []
