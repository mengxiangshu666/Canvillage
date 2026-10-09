from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandError, CanvasCommandGateway
from novelvideo.freezone.paths import canvas_path
from novelvideo.freezone.script_contract import (
    SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
    script_media_action_gate,
    script_row_fingerprint,
    script_row_key,
)
from novelvideo.freezone.script_media_gateway import (
    evaluate_script_media_readiness,
)
from novelvideo.project_context import ProjectContext


def _row(*, shot_no: int = 1, shot_prompt: str | None = None, visual: str = "她抬眼看屏") -> dict:
    return {
        "shot_id": f"shot:{shot_no}",
        "shot_no": shot_no,
        "duration": 5,
        "visual_description": visual,
        "shot": "近景 / 平视",
        "shot_prompt": shot_prompt
        if shot_prompt is not None
        else (
            "[画面构图：近景特写，平视机位] + "
            "[角色卡/主体描述：[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]] + "
            "[主体/人物空间与互动关系：她独坐在办公桌前] + "
            "[极具体的微表情：眼下发青，手指微颤] + "
            "[明确的场景环境元素：深夜办公室、冷掉的咖啡杯] + "
            "[光影几何与大气效果：冷蓝主调] + "
            "[视觉风格/质感：都市悬疑写实电影感] + "
            "[技术参数：85mm镜头，f/1.8，浅景深]"
        ),
        "video_motion_prompt": (
            "[明确的摄影机运镜轨迹与速度：镜头前推，极慢速] + "
            "[主体极其具体的物理动作细节：她抬眼看屏] + "
            "[环境物理动态：纸张被空调风吹起] + "
            "[音效与氛围描述：键盘声、室内低频电流声] + "
            "[对话台词与语气：无] + "
            "[时长：5s]"
        ),
    }


def _seed_canvas(
    project_dir: Path,
    *,
    rows: list[dict],
    target: dict,
    linked_group_id: str | None = None,
) -> None:
    payload = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="test-user",
    )
    payload.update(
        canvas_id="canvas-1",
        nodes=[
            {
                "id": "script-1",
                "type": "scriptNode",
                "position": {"x": 0.0, "y": 0.0},
                "data": {
                    "scriptResult": {"title": "门禁夹具", "rows": rows},
                    **({"linkedImageGroupId": linked_group_id} if linked_group_id else {}),
                },
            },
            target,
        ],
        edges=[],
    )
    if linked_group_id:
        payload["nodes"].insert(
            1,
            {
                "id": linked_group_id,
                "type": "groupNode",
                "position": {"x": 800.0, "y": 0.0},
                "data": {"displayName": "分镜图组"},
            },
        )
    path = canvas_path(project_dir, "canvas-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(path, payload)


def _gateway(project_dir: Path) -> CanvasCommandGateway:
    return CanvasCommandGateway(
        project_dir=project_dir,
        project_id="project-1",
        actor_id="test-user",
    )


def _seed_ready_shot_video(project_dir: Path, *, row: dict | None = None) -> dict:
    current = row or _row()
    _seed_canvas(
        project_dir,
        rows=[current],
        target={
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 900.0, "y": 0.0},
            "data": {"prompt": "镜头前推"},
        },
    )
    snapshot = canvas_store.read_canvas(project_dir, "canvas-1")
    assert snapshot is not None
    video = next(node for node in snapshot["nodes"] if node["id"] == "video-1")
    image = {
        "id": "image-1",
        "type": "imageGenNode",
        "position": {"x": 700.0, "y": 0.0},
        "data": {
            "scriptShotId": "shot:1",
            "scriptRowKey": "shot:1",
            "imageUrl": "frame-1.png",
            "scriptRowAssetSnapshot": None,
        },
    }
    snapshot["nodes"].append(image)
    snapshot["edges"].append(
        {
            "id": "edge-image-video",
            "source": "image-1",
            "target": "video-1",
            "data": {"role": "scriptShotVideo"},
        }
    )
    video["data"].update(
        {
            "scriptShotSourceNodeId": "script-1",
            "scriptShotRowKey": "shot:1",
            "scriptShotImageNodeId": "image-1",
            "scriptShotFirstFrameUrl": "frame-1.png",
            "scriptShotRowFingerprint": script_row_fingerprint(current),
            "scriptShotAssetRevisionSnapshot": None,
        }
    )
    canvas_store.atomic_write_json(canvas_path(project_dir, "canvas-1"), deepcopy(snapshot))
    return current


def _seed_ready_shot_video_with_asset(project_dir: Path) -> dict:
    current = _row()
    current.update(
        character_1="阿雀",
        character_description_1="白发少年，旧式短打",
        character_image_1="character-row.png",
        scene_tags="办公室",
    )
    _seed_ready_shot_video(project_dir, row=current)
    snapshot = canvas_store.read_canvas(project_dir, "canvas-1")
    assert snapshot is not None
    asset_id = "character:阿雀"
    revision = 1
    content_hash = "a" * 64
    identity_locks = ["face", "costume", "age", "temperament"]
    asset_line = f"{asset_id}@{revision}@{content_hash}@{','.join(identity_locks)}@"
    image = next(node for node in snapshot["nodes"] if node["id"] == "image-1")
    video = next(node for node in snapshot["nodes"] if node["id"] == "video-1")
    image["data"]["scriptRowAssetSnapshot"] = asset_line
    image["data"]["scriptRowReference"] = "character-generated.png"
    video["data"]["scriptShotAssetRevisionSnapshot"] = asset_line
    asset_node = {
        "id": "asset-1",
        "type": "imageGenNode",
        "position": {"x": 500.0, "y": 0.0},
        "data": {
            "scriptAssetId": asset_id,
            "scriptAssetOwnerId": "script-1",
            "scriptAssetRevision": revision,
            "scriptAssetContentHash": content_hash,
            "scriptAssetDependencies": [],
            "imageUrl": "character-generated.png",
        },
    }
    snapshot["nodes"].append(asset_node)
    canvas_store.atomic_write_json(canvas_path(project_dir, "canvas-1"), deepcopy(snapshot))
    return asset_node


def _auto_generate_envelope(command_id: str, node_id: str) -> dict:
    return {
        "schema": "canvas_chat_commands.v1",
        "command_id": command_id,
        "commands": [
            {
                "type": "update_node_data",
                "node_id": node_id,
                "node_data": {"canvas_auto_generate_once": True},
            }
        ],
    }


def _http_client(monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> TestClient:
    from novelvideo.api.routes import workflows

    output_dir = project_dir / "output"
    runtime_dir = project_dir / "runtime"
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ctx = ProjectContext(
        project_id="project-1",
        project_name="demo",
        owner_type="user",
        owner_id="test-user",
        owner_username="tester",
        requester_user_id="test-user",
        requester_username="tester",
        requester_principals=(("user", "test-user"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=project_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )

    async def resolve_project_context(*, user, project_id, required_role):
        assert user["user_id"] == "test-user"
        assert project_id == "project-1"
        assert required_role == "editor"
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_project_context)
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "tester",
        "user_id": "test-user",
    }
    return TestClient(app)


def _http_apply(client: TestClient, *, command_id: str, node_id: str):
    return client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/commands:apply",
        json={
            "command_id": command_id,
            "commands": [
                {
                    "type": "update_node_data",
                    "node_id": node_id,
                    "node_data": {"canvas_auto_generate_once": True},
                }
            ],
            "source_turn_id": "http-acceptance",
        },
    )


def _http_revalidate(
    client: TestClient,
    *,
    node_id: str,
    action: str,
    step_id: str = "media_generation",
):
    return client.get(
        (
            "/api/v1/projects/project-1/freezone/canvases/canvas-1/"
            "script-media/readiness"
        ),
        params={"node_id": node_id, "action": action, "step_id": step_id},
    )


def test_script_media_gate_blocks_empty_but_allows_valid_freeform_prompts():
    empty = script_media_action_gate([], action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS)
    assert empty["allowed"] is False
    assert empty["reason_code"] == "script_media_empty"

    freeform = script_media_action_gate(
        [_row(shot_prompt="[画面构图：近景]")],
        action=SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
    )
    assert freeform["allowed"] is True
    assert freeform["blocking_count"] == 0
    assert freeform["advisory_count"] > 0


def test_script_media_gate_still_blocks_rows_without_a_prompt():
    result = script_media_action_gate(
        [_row(shot_prompt="", visual="")],
        action=SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
    )
    assert result["allowed"] is False
    assert result["reason_code"] == "script_media_prompt_missing"


def test_script_media_gate_checks_action_specific_prompt_fallback():
    assert (
        script_media_action_gate(
            [_row(shot_prompt="", visual="")],
            action=SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
        )["allowed"]
        is False
    )
    assert (
        script_media_action_gate(
            [_row()],
            action=SCRIPT_MEDIA_ACTION_STORYBOARD_IMAGES,
        )["allowed"]
        is True
    )
    assert (
        script_media_action_gate(
            [_row()],
            action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
        )["allowed"]
        is True
    )


def test_script_row_identity_and_fingerprint_match_web_vector():
    row = {
        "shot_id": "shot_fixed",
        "shot_no": 7,
        "duration": "5",
        "visual_description": "雨落",
        "video_motion_prompt": "镜头前推",
    }
    assert script_row_key(row, 0) == "shot_fixed"
    assert (
        script_row_fingerprint(row)
        == "81e112e6e5f275c0575addce41d42cc7f6035b42728a2e29046da940961c30be"
    )


def test_gateway_blocks_video_auto_generate_until_script_contract_is_ready(tmp_path: Path):
    _seed_canvas(
        tmp_path,
        rows=[_row(shot_prompt="[画面构图：近景]")],
        target={
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 900.0, "y": 0.0},
            "data": {
                "prompt": "镜头前推",
                "scriptShotSourceNodeId": "script-1",
                "scriptShotRowKey": "shot:1",
            },
        },
    )

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("blocked-video", "video-1"),
        )

    assert failed.value.code == "canvas_script_media_not_ready"
    assert failed.value.details["action"] == SCRIPT_MEDIA_ACTION_SHOT_VIDEOS


def test_gateway_allows_current_shot_video_snapshot(tmp_path: Path):
    _seed_ready_shot_video(tmp_path)

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=_auto_generate_envelope("ready-shot-video", "video-1"),
    )

    assert receipt["applied_ops"] == 1


def test_gateway_blocks_video_when_current_row_fingerprint_changed(tmp_path: Path):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    script = next(node for node in snapshot["nodes"] if node["id"] == "script-1")
    script["data"]["scriptResult"]["rows"][0]["visual_description"] = "她停下脚步"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("stale-row", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_row_stale"
    assert failed.value.details["stale_reason"] == "row-changed"


def test_gateway_blocks_video_when_storyboard_first_frame_changed(tmp_path: Path):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    image = next(node for node in snapshot["nodes"] if node["id"] == "image-1")
    image["data"]["imageUrl"] = "frame-2.png"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("stale-frame", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "first-frame-changed"


def test_gateway_blocks_video_when_asset_revision_snapshot_changed(tmp_path: Path):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    image = next(node for node in snapshot["nodes"] if node["id"] == "image-1")
    image["data"]["scriptRowAssetSnapshot"] = "character:hero@2@hash"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("stale-asset", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "asset-revision-changed"


def test_gateway_blocks_video_when_current_asset_node_metadata_changed(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    asset = next(node for node in snapshot["nodes"] if node["id"] == "asset-1")
    asset["data"]["scriptAssetContentHash"] = "b" * 64
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("current-asset-drift", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "asset-revision-changed"
    assert failed.value.details["asset_id"] == "character:阿雀"


def test_gateway_allows_strict_current_asset_with_defaulted_identity_locks(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=_auto_generate_envelope("current-asset-default-locks", "video-1"),
    )

    assert receipt["applied_ops"] == 1


def test_gateway_blocks_video_when_current_asset_image_url_changed(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    asset = next(node for node in snapshot["nodes"] if node["id"] == "asset-1")
    asset["data"]["imageUrl"] = "character-generated-v2.png"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("current-reference-url-drift", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "reference-changed"


def test_gateway_blocks_video_when_new_scene_asset_enters_reference_set(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    snapshot["nodes"].append(
        {
            "id": "asset-scene-1",
            "type": "imageGenNode",
            "position": {"x": 500.0, "y": 400.0},
            "data": {
                "scriptAssetId": "scene:办公室",
                "scriptAssetOwnerId": "script-1",
                "scriptAssetRevision": 1,
                "scriptAssetContentHash": "c" * 64,
                "scriptAssetDependencies": [],
                "imageUrl": "office-generated.png",
            },
        }
    )
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("new-scene-reference", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "reference-changed"
    assert failed.value.details["expected_reference_count"] == 1
    assert failed.value.details["current_reference_count"] == 2


def test_gateway_blocks_video_when_current_asset_node_is_deleted(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    snapshot["nodes"] = [node for node in snapshot["nodes"] if node["id"] != "asset-1"]
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("deleted-current-asset", "video-1"),
        )

    assert failed.value.details["reason_code"] == "script_media_shot_image_stale"
    assert failed.value.details["stale_reason"] == "asset-revision-changed"
    assert failed.value.details["asset_id"] == "character:阿雀"


def test_gateway_allows_legacy_current_asset_node_without_identity_metadata(tmp_path: Path):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    asset = next(node for node in snapshot["nodes"] if node["id"] == "asset-1")
    asset["data"].pop("scriptAssetRevision")
    asset["data"].pop("scriptAssetContentHash")
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=_auto_generate_envelope("legacy-current-asset", "video-1"),
    )

    assert receipt["applied_ops"] == 1


def test_gateway_allows_ready_script_shot_and_resolves_grouped_image(tmp_path: Path):
    _seed_canvas(
        tmp_path,
        rows=[_row()],
        linked_group_id="group-1",
        target={
            "id": "image-1",
            "type": "imageGenNode",
            "parentId": "group-1",
            "position": {"x": 900.0, "y": 0.0},
            "data": {"prompt": "镜头推进", "scriptRowKey": "shot:1"},
        },
    )

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=_auto_generate_envelope("ready-image", "image-1"),
    )

    assert receipt["applied_ops"] == 1
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    target = next(node for node in snapshot["nodes"] if node["id"] == "image-1")
    assert target["data"]["canvas_auto_generate_once"] is True


def test_gateway_does_not_apply_script_gate_to_independent_media(tmp_path: Path):
    _seed_canvas(
        tmp_path,
        rows=[_row(shot_prompt="[画面构图：近景]")],
        target={
            "id": "image-1",
            "type": "imageGenNode",
            "position": {"x": 900.0, "y": 0.0},
            "data": {"prompt": "独立素材"},
        },
    )

    _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=_auto_generate_envelope("independent-image", "image-1"),
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    target = next(node for node in snapshot["nodes"] if node["id"] == "image-1")
    assert target["data"]["canvas_auto_generate_once"] is True


def test_gateway_duplicate_clears_auto_generate_flag(tmp_path: Path):
    _seed_canvas(
        tmp_path,
        rows=[_row()],
        target={
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 900.0, "y": 0.0},
            "data": {
                "prompt": "镜头前推",
                "canvas_auto_generate_once": True,
                "scriptShotSourceNodeId": "script-1",
            },
        },
    )
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "duplicate-video",
        "commands": [
            {
                "type": "duplicate_node",
                "node_id": "video-1",
                "created_node_id": "video-copy",
            }
        ],
    }

    _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    created = next(node for node in snapshot["nodes"] if node["id"] == "video-copy")
    assert created["data"]["canvas_auto_generate_once"] is False


def test_gateway_recomputes_instead_of_trusting_stale_embedded_report(tmp_path: Path):
    row = _row(shot_prompt="[画面构图：近景]")
    target = {
        "id": "video-1",
        "type": "videoNode",
        "position": {"x": 900.0, "y": 0.0},
        "data": {
            "prompt": "镜头前推",
            "scriptShotSourceNodeId": "script-1",
        },
    }
    _seed_canvas(tmp_path, rows=[row], target=target)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    script = next(node for node in snapshot["nodes"] if node["id"] == "script-1")
    script["data"]["scriptContractReport"] = {
        "schema": "freezone.script-contract.v1",
        "issues": [],
        "rows_fingerprint": "stale",
    }
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=_auto_generate_envelope("stale-report", "video-1"),
        )

    assert failed.value.code == "canvas_script_media_not_ready"


def test_gateway_gates_same_patch_link_before_queueing(tmp_path: Path):
    _seed_canvas(
        tmp_path,
        rows=[_row(shot_prompt="[画面构图：近景]")],
        target={
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 900.0, "y": 0.0},
            "data": {"prompt": "原先只是草稿"},
        },
    )
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "same-patch-link",
        "commands": [
            {
                "type": "update_node_data",
                "node_id": "video-1",
                "node_data": {
                    "scriptShotSourceNodeId": "script-1",
                    "canvas_auto_generate_once": True,
                },
            }
        ],
    }

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert failed.value.code == "canvas_script_media_not_ready"


def test_http_commands_apply_rejects_stale_video_before_paid_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    script = next(node for node in snapshot["nodes"] if node["id"] == "script-1")
    script["data"]["scriptResult"]["rows"][0]["visual_description"] = "她停下脚步"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    response = _http_apply(
        _http_client(monkeypatch, tmp_path),
        command_id="http-stale-video",
        node_id="video-1",
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "canvas_script_media_not_ready"
    assert detail["details"]["reason_code"] == "script_media_shot_row_stale"
    assert detail["details"]["stale_reason"] == "row-changed"

    after = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert after is not None
    video = next(node for node in after["nodes"] if node["id"] == "video-1")
    assert video["data"].get("canvas_auto_generate_once") is not True


def test_readiness_revalidation_reports_stale_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    script = next(node for node in snapshot["nodes"] if node["id"] == "script-1")
    script["data"]["scriptResult"]["rows"][0]["visual_description"] = "她停下脚步"
    canvas_store.atomic_write_json(
        canvas_path(tmp_path, "canvas-1"),
        deepcopy(snapshot),
    )
    before = canvas_path(tmp_path, "canvas-1").read_bytes()

    response = _http_revalidate(
        _http_client(monkeypatch, tmp_path),
        node_id="video-1",
        action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["schema"] == "script_media_readiness.v1"
    assert data["ready"] is False
    assert data["reason_code"] == "script_media_shot_row_stale"
    assert data["stale_reason"] == "row-changed"
    assert data["target_node_id"] == "video-1"
    assert data["recovery"]["action"] == "regenerate_storyboard"
    assert data["recovery"]["media_action"] == SCRIPT_MEDIA_ACTION_SHOT_VIDEOS
    assert data["recovery"]["target_node_id"] == "video-1"
    assert canvas_path(tmp_path, "canvas-1").read_bytes() == before
    after = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert after is not None
    video = next(node for node in after["nodes"] if node["id"] == "video-1")
    assert video["data"].get("canvas_auto_generate_once") is not True


def test_readiness_revalidation_requires_real_repair_not_unrelated_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    current = _seed_ready_shot_video(tmp_path)
    stale = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert stale is not None
    stale_script = next(node for node in stale["nodes"] if node["id"] == "script-1")
    stale_script["data"]["scriptResult"]["rows"][0]["visual_description"] = "她停下脚步"
    canvas_store.atomic_write_json(
        canvas_path(tmp_path, "canvas-1"),
        deepcopy(stale),
    )
    client = _http_client(monkeypatch, tmp_path)
    stale_revision = stale.get("revision")
    stale_bytes = canvas_path(tmp_path, "canvas-1").read_bytes()

    first = _http_revalidate(
        client,
        node_id="video-1",
        action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    )
    assert first.status_code == 200
    assert first.json()["data"]["ready"] is False
    assert first.json()["data"]["canvas_revision"] == stale_revision
    assert canvas_path(tmp_path, "canvas-1").read_bytes() == stale_bytes

    unrelated = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert unrelated is not None
    video = next(node for node in unrelated["nodes"] if node["id"] == "video-1")
    video["data"]["prompt"] = "无关的显示性修改"
    canvas_store.atomic_write_json(
        canvas_path(tmp_path, "canvas-1"),
        deepcopy(unrelated),
    )
    unrelated_bytes = canvas_path(tmp_path, "canvas-1").read_bytes()
    still_stale = _http_revalidate(
        client,
        node_id="video-1",
        action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    )
    assert still_stale.status_code == 200
    assert still_stale.json()["data"]["ready"] is False
    assert (
        still_stale.json()["data"]["reason_code"]
        == "script_media_shot_row_stale"
    )
    assert still_stale.json()["data"]["canvas_revision"] == stale_revision
    assert canvas_path(tmp_path, "canvas-1").read_bytes() == unrelated_bytes

    repaired = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert repaired is not None
    repaired_script = next(
        node for node in repaired["nodes"] if node["id"] == "script-1"
    )
    repaired_script["data"]["scriptResult"]["rows"][0] = deepcopy(current)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path, "canvas-1"),
        deepcopy(repaired),
    )
    repaired_bytes = canvas_path(tmp_path, "canvas-1").read_bytes()
    ready = _http_revalidate(
        client,
        node_id="video-1",
        action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    )
    assert ready.status_code == 200
    assert ready.json()["data"]["ready"] is True
    assert ready.json()["data"]["shot_id"] == "shot:1"
    assert ready.json()["data"]["canvas_revision"] == stale_revision
    assert canvas_path(tmp_path, "canvas-1").read_bytes() == repaired_bytes
    final = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert final is not None
    final_video = next(node for node in final["nodes"] if node["id"] == "video-1")
    assert final_video["data"].get("canvas_auto_generate_once") is not True
    assert not [path for path in (tmp_path / "runtime").rglob("*") if path.is_file()]
    assert not [path for path in (tmp_path / "output").rglob("*") if path.is_file()]


def test_readonly_readiness_uses_same_gate_as_write(tmp_path: Path):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    before_revision = snapshot.get("revision")

    readiness = evaluate_script_media_readiness(
        snapshot,
        node_id="video-1",
        action=SCRIPT_MEDIA_ACTION_SHOT_VIDEOS,
    )

    assert readiness["ready"] is True
    assert readiness["canvas_revision"] == before_revision
    assert canvas_store.read_canvas(tmp_path, "canvas-1") == snapshot


def test_http_commands_apply_allows_current_video_after_number_only_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _seed_ready_shot_video(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    script = next(node for node in snapshot["nodes"] if node["id"] == "script-1")
    row = script["data"]["scriptResult"]["rows"][0]
    row["shot_no"] = 27
    row["display_shot_no"] = "S27"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    response = _http_apply(
        _http_client(monkeypatch, tmp_path),
        command_id="http-number-only-change",
        node_id="video-1",
    )

    assert response.status_code == 200
    assert response.json()["data"]["applied_ops"] == 1
    after = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert after is not None
    video = next(node for node in after["nodes"] if node["id"] == "video-1")
    assert video["data"]["canvas_auto_generate_once"] is True


def test_http_commands_apply_rejects_current_asset_node_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    asset = next(node for node in snapshot["nodes"] if node["id"] == "asset-1")
    asset["data"]["scriptAssetRevision"] = 2
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    response = _http_apply(
        _http_client(monkeypatch, tmp_path),
        command_id="http-current-asset-drift",
        node_id="video-1",
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "canvas_script_media_not_ready"
    assert detail["details"]["reason_code"] == "script_media_shot_image_stale"
    assert detail["details"]["stale_reason"] == "asset-revision-changed"
    assert detail["details"]["asset_id"] == "character:阿雀"


def test_http_commands_apply_rejects_reference_url_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _seed_ready_shot_video_with_asset(tmp_path)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    asset = next(node for node in snapshot["nodes"] if node["id"] == "asset-1")
    asset["data"]["imageUrl"] = "character-generated-v2.png"
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), deepcopy(snapshot))

    response = _http_apply(
        _http_client(monkeypatch, tmp_path),
        command_id="http-reference-url-drift",
        node_id="video-1",
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "canvas_script_media_not_ready"
    assert detail["details"]["reason_code"] == "script_media_shot_image_stale"
    assert detail["details"]["stale_reason"] == "reference-changed"
