from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api import auth as api_auth
from novelvideo.api.deps import ProjectResolution
from novelvideo.api.routes import story_lab
from novelvideo.project_context import ProjectContext
from novelvideo.story_lab.models import DraftResult, StoryLabStage, StoryLabStageArtifact
from novelvideo.story_lab.persistence import StoryLabRepository


class _FakeTaskBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append({"ctx": ctx, **kwargs})
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id=f"task-{kwargs['task_type']}"),
            backend="inline",
            queue="inline",
        )


def _client(tmp_path: Path, monkeypatch):
    output_dir = tmp_path / "output" / "alice" / "story"
    state_dir = tmp_path / "state" / "alice" / "story"
    runtime_dir = tmp_path / "runtime" / "alice" / "story"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    ctx = ProjectContext(
        project_id="project-1",
        project_name="story",
        owner_type="user",
        owner_id="user-alice",
        owner_username="alice",
        requester_user_id="user-alice",
        requester_username="alice",
        requester_principals=(("user", "user-alice"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )
    resolution = ProjectResolution(
        ctx=ctx,
        username="alice",
        project_name="story",
        project_dir=output_dir,
        output_dir=str(output_dir),
        state_dir=str(state_dir),
        runtime_dir=str(runtime_dir),
    )

    async def resolve_project_scope(project: str, _user: dict, *, required_role="viewer"):
        assert project == "project-1"
        assert required_role in {"viewer", "editor"}
        return resolution

    task_backend = _FakeTaskBackend()
    monkeypatch.setattr(story_lab, "resolve_project_scope", resolve_project_scope)
    monkeypatch.setattr(story_lab, "get_task_backend", lambda: task_backend)

    app = FastAPI()
    app.include_router(story_lab.router, prefix="/api/v1")
    user = {
        "id": "user-alice",
        "user_id": "user-alice",
        "username": "alice",
        "role": "owner",
    }
    app.dependency_overrides[api_auth.get_api_user] = lambda: user
    app.dependency_overrides[story_lab.get_api_user] = lambda: user
    return TestClient(app), task_backend, ctx


def _config_payload() -> dict:
    return {
        "title": "风雪山神庙",
        "logline": "落魄教头在风雪夜识破陷阱并完成命运反击。",
        "work_type": "micro_drama",
        "genre": "古装悬疑",
        "theme": "背叛与觉醒",
        "target_units": 12,
        "target_length": 1200,
        "target_duration_seconds": 90,
        "style_mode": "infer",
    }


def test_story_lab_save_restore_and_enqueue(tmp_path: Path, monkeypatch) -> None:
    client, backend, _ctx = _client(tmp_path, monkeypatch)

    saved = client.put("/api/v1/projects/project-1/story-lab/config", json=_config_payload())
    restored = client.get("/api/v1/projects/project-1/story-lab")
    queued = client.post(
        "/api/v1/projects/project-1/story-lab/generate",
        json={"stage": "bible"},
    )

    assert saved.status_code == 200
    assert saved.json()["data"]["config"]["title"] == "风雪山神庙"
    assert saved.json()["data"]["artifacts"] == {}
    assert saved.json()["data"]["stages"] == {}
    assert restored.json()["data"]["config"]["target_units"] == 12
    task = queued.json()["data"]
    assert task["task_type"] == "story_lab_bible"
    assert task["task_id"] == "task-story_lab_bible"
    assert backend.calls[0]["payload"]["stage"] == "bible"
    assert backend.calls[0]["scope"] == "bible"

    missing_result = client.get("/api/v1/projects/project-1/story-lab/results/bible")
    assert missing_result.json() == {"ok": True, "data": None}
    missing_stage = client.get("/api/v1/projects/project-1/story-lab/stages/bible")
    assert missing_stage.json() == {"ok": True, "data": None}

    blocked = client.post(
        "/api/v1/projects/project-1/story-lab/generate",
        json={"stage": "outline"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["missing"] == ["bible"]


def test_story_lab_export_without_config_returns_conflict(tmp_path: Path, monkeypatch) -> None:
    client, _backend, _ctx = _client(tmp_path, monkeypatch)

    response = client.post("/api/v1/projects/project-1/story-lab/export", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "story_lab_not_configured"

def test_story_lab_generation_requires_saved_config(tmp_path: Path, monkeypatch) -> None:
    client, backend, _ctx = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/projects/project-1/story-lab/generate",
        json={"stage": "bible"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "story_lab_not_configured"
    assert backend.calls == []


def test_story_lab_publish_reuses_ingest_task_contract(tmp_path: Path, monkeypatch) -> None:
    client, backend, ctx = _client(tmp_path, monkeypatch)
    repository = StoryLabRepository(ctx)
    repository.save_config(_config_payload())
    repository.save_artifact(
        StoryLabStageArtifact(
            stage=StoryLabStage.DRAFT,
            prompt_version="story-lab.draft.v1",
            model="fixture-model",
            result=DraftResult(
                title="风雪山神庙",
                format="episode_script",
                units=[
                    {
                        "number": 1,
                        "title": "风雪逼近",
                        "content": "林冲顶着风雪走向山神庙。",
                    }
                ],
                full_text="第1集 风雪逼近\n\n林冲顶着风雪走向山神庙。",
            ).model_dump(mode="json"),
        )
    )

    response = client.post(
        "/api/v1/projects/project-1/story-lab/publish",
        json={"filename": "风雪山神庙.txt", "rebuild": True},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["filename"] == "风雪山神庙.txt"
    assert (ctx.output_dir / "uploads" / "风雪山神庙.txt").is_file()
    assert backend.calls[-1]["task_type"] == "ingest_fast"
    project_config = (ctx.state_dir / "project_config.json").read_text(encoding="utf-8")
    assert '"target_duration_total": 90.0' in project_config
    assert backend.calls[-1]["payload"]["novel_path"].endswith("风雪山神庙.txt")
    assert backend.calls[-1]["payload"]["config"] == {"rebuild": True}


def test_story_lab_publish_sends_complete_single_episode_to_ingest(
    tmp_path: Path, monkeypatch
) -> None:
    client, backend, ctx = _client(tmp_path, monkeypatch)
    config = _config_payload()
    config["target_units"] = 1
    repository = StoryLabRepository(ctx)
    repository.save_config(config)
    full_text = """第1集 风雪逼近

1-1 山神庙 黄昏 内
人物：林冲
林冲推门入庙，发现墙角散落着新鲜脚印。

1-2 山神庙 夜 内
人物：林冲、陆谦
陆谦破门而入，林冲拔刀迎敌。"""
    repository.save_artifact(
        StoryLabStageArtifact(
            stage=StoryLabStage.DRAFT,
            prompt_version="story-lab.draft.v1",
            model="fixture-model",
            result=DraftResult(
                title="风雪山神庙",
                format="episode_script",
                units=[
                    {"number": 1, "title": "逼近", "content": "林冲入庙。"},
                    {"number": 2, "title": "发现", "content": "发现脚印。"},
                    {"number": 3, "title": "埋伏", "content": "陆谦现身。"},
                    {"number": 4, "title": "反击", "content": "林冲迎敌。"},
                ],
                full_text=full_text,
            ).model_dump(mode="json"),
        )
    )

    response = client.post(
        "/api/v1/projects/project-1/story-lab/publish",
        json={"filename": "单集成稿.txt", "rebuild": True},
    )

    assert response.status_code == 200, response.text
    published = (ctx.output_dir / "uploads" / "单集成稿.txt").read_text(
        encoding="utf-8-sig"
    )
    assert published == f"{full_text}\n"
    assert published.count("第1集") == 1
    assert "第2集 发现" not in published
    assert backend.calls[-1]["task_type"] == "ingest_fast"


def test_story_lab_export_returns_authenticated_download_url(tmp_path: Path, monkeypatch) -> None:
    client, _backend, ctx = _client(tmp_path, monkeypatch)
    repository = StoryLabRepository(ctx)
    repository.save_config(_config_payload())
    repository.save_artifact(
        StoryLabStageArtifact(
            stage=StoryLabStage.DRAFT,
            prompt_version="story-lab.draft.v1",
            model="fixture-model",
            result=DraftResult(
                title="风雪山神庙",
                format="episode_script",
                units=[{"number": 1, "title": "风雪逼近", "content": "林冲入庙。"}],
            ).model_dump(mode="json"),
        )
    )

    response = client.post("/api/v1/projects/project-1/story-lab/export", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["url"].endswith(f"/{data['filename']}")
    download = client.get(data["url"])
    assert download.status_code == 200
    assert download.content.startswith(b"\xef\xbb\xbf")
