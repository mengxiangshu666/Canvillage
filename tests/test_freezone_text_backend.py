from __future__ import annotations

from pathlib import Path
import json

import pytest

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.schemas import FreezoneStoryScriptGenerateData, FreezoneStoryScriptRow
from novelvideo.freezone.text_prepare import (
    FREEZONE_TEXT_PREPARE_SYSTEM_PROMPT,
    FreezoneTextPrepareResult,
    build_freezone_text_prepare_task,
    prepare_freezone_text,
)
from novelvideo.freezone.text_node import (
    FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
    FREEZONE_TRANSLATION_MODEL,
    FREEZONE_TRANSLATION_PROVIDER,
    FreezoneTranslationResult,
    _incomplete_story_script_rows,
    _story_script_shot_numbers,
    build_freezone_story_script_task,
    build_freezone_translation_task,
    bind_story_script_assets,
    translate_freezone_text,
)


def test_story_script_task_counts_repeated_scene_shot_numbers() -> None:
    source = "镜 1：开场。\n镜 2：反打。\n\n镜 1：下一场。\n镜 2：收尾。"

    assert _story_script_shot_numbers(source) == [1, 2, 1, 2]
    assert "明确标注了 4 个镜头" in build_freezone_story_script_task(
        source_text=source,
        prompt="",
    )


def test_generated_story_script_rows_must_have_executable_prompts() -> None:
    data = FreezoneStoryScriptGenerateData(
        title="缺提示词",
        rows=[
            FreezoneStoryScriptRow(
                shot_no=1,
                duration=4,
                visual_description="人物走进房间。",
                shot_prompt="",
                video_motion_prompt="",
            )
        ],
    )

    assert _incomplete_story_script_rows(data) == [
        "第 1 镜缺少分镜提示词",
        "第 1 镜缺少视频运动提示词",
    ]


@pytest.mark.parametrize("duration", [0.5, 3.4, 99])
def test_story_script_and_rewrite_preserve_duration(duration) -> None:
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow

    fields = dict(duration=duration, visual_description="闪现", shot="特写",
                  character_action="眨眼", emotion="惊讶", shot_prompt="画面", video_motion_prompt="动作")
    row = FreezoneStoryScriptRow(shot_no=1, **fields)
    rewrite = FreezoneShotRewriteRow(**fields)
    assert FreezoneStoryScriptRow.model_validate_json(row.model_dump_json()).duration == duration
    assert FreezoneShotRewriteRow.model_validate_json(rewrite.model_dump_json()).duration == duration


def test_keyframe_plan_round_trips_and_is_preserved_by_local_rewrite() -> None:
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow, merge_freezone_shot_rewrite

    plan = [
        {"role": "contact_state", "state": "前轮压住坡沿", "purpose": "锁住支撑关系", "required": True},
        {"role": "ending_state", "state": "主体腾空越过坡沿", "purpose": "锁住切点", "required": False},
        {"role": "spatial_reveal", "generation_strategy": "independent", "framing": "坡沿侧面全景，板与落点同框",
         "state": "主体腾空越过坡沿", "purpose": "看清落点距离", "required": True},
    ]
    original = {
        "shot_no": 1,
        "duration": 6,
        "visual_description": "滑板冲上坡沿",
        "shot": "全景",
        "character_action": "压低重心",
        "emotion": "专注",
        "shot_prompt": "画面构图：全景 + 角色卡/主体描述：[小兽人: 红色护具]",
        "video_motion_prompt": "摄影机运镜：跟拍 + 主体物理动作：冲上坡沿",
        "keyframe_plan": plan,
    }
    rewritten = FreezoneShotRewriteRow(
        duration=6,
        visual_description=original["visual_description"],
        shot=original["shot"],
        character_action=original["character_action"],
        emotion=original["emotion"],
        shot_prompt=original["shot_prompt"],
        video_motion_prompt=original["video_motion_prompt"],
        keyframe_plan=plan,
    )
    assert FreezoneStoryScriptRow.model_validate_json(
        FreezoneStoryScriptRow(shot_no=1, **{k: v for k, v in original.items() if k != "shot_no"}).model_dump_json()
    ).keyframe_plan[0].state == "前轮压住坡沿"
    assert merge_freezone_shot_rewrite([original], 0, rewritten)["keyframe_plan"] == plan


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_story_script_and_rewrite_reject_invalid_duration(duration) -> None:
    from pydantic import ValidationError
    from novelvideo.freezone.text_node import FreezoneShotRewriteRow

    fields = dict(duration=duration, visual_description="闪现", shot="特写",
                  character_action="眨眼", emotion="惊讶", shot_prompt="画面", video_motion_prompt="动作")
    with pytest.raises(ValidationError):
        FreezoneStoryScriptRow(shot_no=1, **fields)
    with pytest.raises(ValidationError):
        FreezoneShotRewriteRow(**fields)


def _patch_project_resolution(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    *,
    username: str = "admin",
):
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return None, username, project, project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)


def test_build_freezone_translation_task_mentions_languages_and_node_type() -> None:
    task = build_freezone_translation_task(
        text="手持镜头，雨夜街头，人物缓慢向前走。",
        node_type="video",
    )

    assert "视频节点提示词" in task
    assert "Simplified Chinese" in task
    assert "English" in task
    assert "You must decide whether the dominant natural language" in task
    assert "手持镜头" in task


def test_build_freezone_text_prepare_task_keeps_story_stage_out_of_storyboard() -> None:
    task = build_freezone_text_prepare_task(
        text="旧车站里，一封信让两个人重新见面。",
        mode="faithful",
    )

    assert "faithful mode" in task
    assert "旧车站" in task
    assert "not a storyboard table" in task
    assert "No shot numbers" in task
    assert "camera package" in task
    assert "image prompt" in task
    assert "video prompt" in task
    assert "Return the structured result now." in task
    assert "Do not write shot numbers" in FREEZONE_TEXT_PREPARE_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_prepare_freezone_text_returns_auditable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeAgent:
        async def run(self, task: str):
            captured["task"] = task

            class Response:
                output = FreezoneTextPrepareResult(
                    prepared_text="场景 1\n旧车站的雨声压低了两个人的脚步。",
                    change_summary=["补全了场景动作和因果衔接"],
                    unresolved=["两人的关系背景仍未交代"],
                    warnings=["没有明确结尾地点"],
                )

            return Response()

    monkeypatch.setattr(
        "novelvideo.freezone.text_prepare.get_freezone_text_prepare_agent",
        lambda _model=None: FakeAgent(),
    )

    result = await prepare_freezone_text(
        text="旧车站里，一封信让两个人重新见面。",
        mode="creative",
        model="direct/text-primary",
    )

    assert "creative mode" in captured["task"]
    assert result["prepared_text"].startswith("场景 1")
    assert result["source_text"] == "旧车站里，一封信让两个人重新见面。"
    assert len(str(result["source_hash"])) == 64
    assert result["mode"] == "creative"
    assert result["model"] == "direct/text-primary"
    assert result["change_summary"] == ["补全了场景动作和因果衔接"]
    assert result["unresolved"] == ["两人的关系背景仍未交代"]
    assert result["warnings"] == ["没有明确结尾地点"]


@pytest.mark.asyncio
async def test_translate_freezone_text_trusts_model_detected_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeAgent:
        async def run(self, task: str):
            captured["task"] = task

            class Response:
                output = FreezoneTranslationResult(
                    translated_text="生成一个分镜节拍的故事板草图面板。",
                    source_language="en",
                    target_language="zh",
                )

            return Response()

    monkeypatch.setattr("novelvideo.freezone.text_node.get_freezone_translation_agent", FakeAgent)

    translated, source_language, target_language = await translate_freezone_text(
        text="Generate ONE storyboard sketch panel for this storyboard beat. 颜色法则：保留 [CM_6932]",
        node_type="image",
    )

    assert "You must decide whether the dominant natural language" in captured["task"]
    assert "[CM_6932]" in captured["task"]
    assert translated == "生成一个分镜节拍的故事板草图面板。"
    assert source_language == "en"
    assert target_language == "zh"


@pytest.mark.asyncio
async def test_translate_freezone_text_flips_invalid_same_language_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgent:
        async def run(self, _task: str):
            class Response:
                output = FreezoneTranslationResult(
                    translated_text="雨夜街头",
                    source_language="zh",
                    target_language="zh",
                )

            return Response()

    monkeypatch.setattr("novelvideo.freezone.text_node.get_freezone_translation_agent", FakeAgent)

    translated, source_language, target_language = await translate_freezone_text(
        text="雨夜街头",
        node_type="image",
    )

    assert translated == "雨夜街头"
    assert source_language == "zh"
    assert target_language == "en"


def test_translation_defaults_do_not_inject_hidden_model() -> None:
    assert FREEZONE_TRANSLATION_PROVIDER == "newapi"
    assert FREEZONE_TRANSLATION_MODEL == ""


def test_build_freezone_story_script_task_mentions_required_columns() -> None:
    task = build_freezone_story_script_task(
        source_text="沈昭昭在深夜办公室醒来。",
        prompt="节奏要快，压迫感强",
    )

    assert "镜号" in task
    assert "画面描述" in task
    assert "视频运动提示词" in task
    assert "角色图1" in task
    assert "道具标签" in task
    assert "沈昭昭" in task
    assert "节奏要快" in task
    assert "括号分段" in task
    assert "分镜提示词必须像高质量图像生成提示词" in task
    assert "最好严格按 8 段写" in task
    assert "最好严格按 6 段写" in task
    assert "第二段必须**逐字照抄**角色描述1" in task
    assert "「视觉风格」段必须全片统一" in task
    assert "「技术参数」段按每镜观看目的选择焦段、光圈与景深" in task
    assert "不要逐镜换焦段" not in task


def test_story_script_prompt_separates_inanimate_subjects_from_characters_and_scene_details() -> None:
    prompt = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT
    assert "产品、器物、武器、家具、车辆" in prompt
    assert "必须放进 `prop_tags`" in prompt
    assert "光带、尘粒、倒影、材质纹理" in prompt


def test_story_script_system_prompt_locks_character_card_and_style() -> None:
    """跨行一致性是硬要求：角色卡逐字、风格段/参数段全片唯一。

    此前两处都是软的（「尽量直接复用或轻改」「技术参数段尽量保留」），模型逐镜改写
    角色卡、逐镜换焦段，出的分镜图人物和质感对不上。

    社区语料实测（`E:/AI影视研究/社区项目复刻/snapshots_all`，4,740 份含提示词的快照 /
    339,235 个图片节点）：只有 14,044 行（4.1%）真的写了 `[视觉风格…]` 段，其中 20.9%
    落在「整份快照该段逐字一致」的快照里 —— 社区用户多数也没锁住。我们把这条升成硬
    要求，依据不是「照抄社区做法」，而是已经量到**自家**产品 6 次生成 / 74 行分镜里
    74 段互不相同的 `[视觉风格]`。技术参数不同不再作为漂移依据，各镜光学选择可以不同。
    """
    prompt = FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT

    assert "## Cross-row consistency (hard requirement)" in prompt
    assert "**byte-identical** in every row" in prompt
    assert "Keep segment 7" in prompt
    assert "Choose segment 8" in prompt
    assert "Never vary focal length" not in prompt
    # 角色2 也必须整段进分镜提示词（双人镜头此前只留角色1）。
    assert "角色2 同理照抄 character_description_2" in prompt


def test_story_script_schema_keeps_video_and_character_reference_inputs() -> None:
    request = freezone_routes.FreezoneStoryScriptGenerateRequest.model_validate(
        {
            "video_url": "/static/admin/58/freezone/_uploads/reference.mp4",
            "duration_sec": 12.5,
            "character_refs": [
                {
                    "name": "沈昭昭_现代",
                    "image_url": "/static/admin/58/freezone/_uploads/zhaozhao.png",
                }
            ],
        }
    )

    assert request.video_url.endswith("reference.mp4")
    assert request.duration_sec == 12.5
    assert request.character_refs[0].name == "沈昭昭_现代"
    assert request.model == ""


def test_story_script_asset_binding_uses_verified_urls_not_model_urls() -> None:
    data = FreezoneStoryScriptGenerateData(
        rows=[
            FreezoneStoryScriptRow(
                shot_no=1,
                duration=3,
                visual_description="雨夜办公室，沈昭昭抬头。",
                character_1="沈昭昭_现代",
                character_2="李默",
                keyframe_index=99,
            ),
            FreezoneStoryScriptRow(
                shot_no=2,
                duration=3,
                visual_description="李默推门而入。",
                character_1="李默_制服",
                keyframe_index=2,
            ),
        ]
    )

    bind_story_script_assets(
        data,
        frame_urls=["/static/project/frame-1.png", "/static/project/frame-2.png"],
        character_refs=[
            {"name": "沈昭昭_现代", "image_url": "/static/project/zhaozhao.png"},
            {"name": "李默", "image_url": "/static/project/limo.png"},
        ],
    )

    assert data.rows[0].reference == "/static/project/frame-1.png"
    assert data.rows[0].keyframe_index == 1
    assert data.rows[0].character_image_1 == "/static/project/zhaozhao.png"
    assert data.rows[0].character_image_2 == "/static/project/limo.png"
    assert data.rows[1].reference == "/static/project/frame-2.png"
    assert data.rows[1].character_image_1 == "/static/project/limo.png"
    assert data.rows[0].shot_id.startswith("shot_")
    assert data.rows[1].shot_id.startswith("shot_")
    assert data.rows[0].shot_id != data.rows[1].shot_id
    assert [row.shot_order for row in data.rows] == [1, 2]
    assert [row.display_shot_no for row in data.rows] == ["1", "2"]
    assert data.rows[0].generation_mode == "image_to_video"
    assert data.rows[0].transition_plan == "direct_cut"
    assert "角色资产" in data.rows[0].reference_requirements


def test_story_script_annotation_keeps_prop_state_across_video_seams() -> None:
    data = FreezoneStoryScriptGenerateData(
        rows=[
            FreezoneStoryScriptRow(
                shot_no=1,
                duration=3,
                visual_description="蒲扇静置后被拿起展开。",
                character_action="手部拿起并展开蒲扇",
                prop_tags="蒲扇",
                video_motion_prompt="镜头跟随手部展开蒲扇",
            )
        ]
    )
    bind_story_script_assets(data, frame_urls=[], character_refs=[])
    row = data.rows[0]
    assert row.prop_state_start == "保持道具资产基准状态"
    assert row.prop_state_end == "保持道具资产基准状态"
    assert "按本镜动作完成道具状态变化" in row.prop_state_change


def test_story_script_identity_fields_are_not_requested_from_model() -> None:
    schema = FreezoneStoryScriptGenerateData.model_json_schema()
    row_properties = schema["$defs"]["FreezoneStoryScriptRow"]["properties"]

    assert "shot_no" in row_properties
    assert "shot_id" not in row_properties
    assert "shot_order" not in row_properties
    assert "display_shot_no" not in row_properties


def test_story_script_asset_binding_preserves_existing_identity_and_assets_on_rewrite() -> None:
    data = FreezoneStoryScriptGenerateData(rows=[FreezoneStoryScriptRow(
        shot_no=1, duration=3, visual_description="改写镜头", shot_id="shot_existing",
        shot_order=7, display_shot_no="补拍", reference="/old/frame.png", character_image_1="/old/role.png",
    )])
    bind_story_script_assets(data, frame_urls=[], character_refs=[], preserve_existing=True)
    row = data.rows[0]
    assert row.shot_id == "shot_existing"
    assert row.shot_order == 7
    assert row.display_shot_no == "补拍"
    assert row.reference == "/old/frame.png"
    assert row.character_image_1 == "/old/role.png"
    bind_story_script_assets(data, frame_urls=[], character_refs=[])
    assert row.shot_id != "shot_existing"
    assert row.reference == ""
    assert row.character_image_1 == ""


@pytest.mark.asyncio
async def test_freezone_text_translate_route_returns_task_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    _patch_project_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    def _fake_start_text_translate_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes, "_start_freezone_text_translate_task", _fake_start_text_translate_task
    )

    result = await freezone_routes.freezone_text_translate(
        project="58",
        body=freezone_routes.FreezoneTextTranslateRequest(
            text="电影感特写，雨夜街头",
            node_type="image",
            model="direct/text-primary",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_text_translate"
    assert captured["text"] == "电影感特写，雨夜街头"
    assert captured["node_type"] == "image"
    assert captured["model"] == "direct/text-primary"


@pytest.mark.asyncio
async def test_freezone_text_prepare_route_returns_task_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    _patch_project_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    def _fake_start_text_prepare_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes,
        "_start_freezone_text_prepare_task",
        _fake_start_text_prepare_task,
    )

    result = await freezone_routes.freezone_text_prepare(
        project="58",
        body=freezone_routes.FreezoneTextPrepareRequest(
            text="雨夜里，一封信改变了两个人的选择。",
            mode="creative",
            model="direct/text-primary",
            canvas_id="canvas-a",
            node_id="text-a",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_text_prepare"
    assert captured["text"] == "雨夜里，一封信改变了两个人的选择。"
    assert captured["mode"] == "creative"
    assert captured["model"] == "direct/text-primary"
    assert captured["canvas_id"] == "canvas-a"
    assert captured["node_id"] == "text-a"


@pytest.mark.asyncio
async def test_freezone_image_reverse_prompt_route_returns_task_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    source = project_dir / "freezone" / "_uploads" / "sample.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake")

    _patch_project_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    def _fake_start_image_reverse_prompt_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes,
        "_start_freezone_image_reverse_prompt_task",
        _fake_start_image_reverse_prompt_task,
    )

    result = await freezone_routes.freezone_image_reverse_prompt(
        project="58",
        body=freezone_routes.FreezoneImageReversePromptRequest(
            source_url="/static/admin/58/freezone/_uploads/sample.png"
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_image_reverse_prompt"
    assert captured["source_path"] == source


@pytest.mark.asyncio
async def test_freezone_story_script_route_uses_source_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    _patch_project_resolution(monkeypatch, project_dir)
    captured: dict[str, object] = {}

    def _fake_start_story_script_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes, "_start_freezone_story_script_task", _fake_start_story_script_task
    )

    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            source_text="沈昭昭在深夜办公室醒来。"
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_story_script"
    assert captured["source_text"] == "沈昭昭在深夜办公室醒来。"
    assert captured["prompt"] == "根据我上传的剧本生成一个完整的故事脚本"
    assert captured["model"] == ""


@pytest.mark.asyncio
async def test_freezone_story_script_route_reads_source_url_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    source = project_dir / "freezone" / "_uploads" / "script.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("沈昭昭在深夜办公室醒来。", encoding="utf-8")

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda *_args, **_kwargs: source,
    )
    captured: dict[str, object] = {}

    def _fake_start_story_script_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes, "_start_freezone_story_script_task", _fake_start_story_script_task
    )

    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            source_url="/static/admin/58/freezone/_uploads/script.txt"
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_story_script"
    assert captured["source_text"] == "沈昭昭在深夜办公室醒来。"


@pytest.mark.asyncio
async def test_story_script_video_and_character_inputs_reach_background_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    video = project_dir / "freezone" / "_uploads" / "reference.mp4"
    portrait = project_dir / "freezone" / "_uploads" / "zhaozhao.png"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    portrait.write_bytes(b"image")

    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return object(), "admin", project, project_dir, str(project_dir)

    async def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "data": {"task_type": "freezone_story_script"}}

    def _resolve(path: str, _project_dir: Path) -> Path:
        return video if path.endswith("reference.mp4") else portrait

    captured: dict[str, object] = {}
    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_enqueue_freezone_background_job", _fake_enqueue)
    monkeypatch.setattr(freezone_routes, "resolve_static_url_to_path", _resolve)

    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            video_url="/static/admin/58/freezone/_uploads/reference.mp4",
            duration_sec=9,
            character_refs=[
                freezone_routes.FreezoneStoryScriptCharacterRef(
                    name="沈昭昭_现代",
                    image_url="/static/admin/58/freezone/_uploads/zhaozhao.png",
                )
            ],
            prompt="按原视频节奏拆分镜头",
        ),
        user={"username": "admin"},
    )

    payload = captured["payload"]
    assert result["ok"] is True
    assert payload["video_path"] == video.as_posix()
    assert payload["duration_sec"] == 9
    assert payload["character_image_paths"] == [portrait.as_posix()]
    assert payload["character_refs"][0]["image_url"].endswith("zhaozhao.png")


@pytest.mark.asyncio
async def test_story_script_rejects_when_every_character_reference_was_dropped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有素材但全被拒时，400 必须说清是「被拒」而不是「没给」。

    此前 `_resolve_story_script_character_refs` 对解析失败静默 `continue`，纯角色图
    输入的请求会翻成一句英文的字段缺失提示，用户无从判断到底哪里不对。
    """

    from fastapi import HTTPException

    project_dir = tmp_path / "project"
    _patch_project_resolution(monkeypatch, project_dir)

    def _reject(_url: str, _project_dir: Path) -> Path:
        raise ValueError("outside project")

    monkeypatch.setattr(freezone_routes, "resolve_static_url_to_path", _reject)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_story_script_generate(
            project="58",
            body=freezone_routes.FreezoneStoryScriptGenerateRequest(
                character_refs=[
                    freezone_routes.FreezoneStoryScriptCharacterRef(
                        name="沈昭昭_现代",
                        image_url="/static/other/9/freezone/_uploads/zhaozhao.png",
                    )
                ],
            ),
            user={"username": "admin"},
        )

    assert excinfo.value.status_code == 400
    assert "沈昭昭_现代" in excinfo.value.detail


@pytest.mark.asyncio
async def test_story_script_keeps_dropped_refs_in_binding_but_reports_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只要还有一条可用输入就照常生成，被拒的参考图只记日志、不打断。"""

    project_dir = tmp_path / "project"
    portrait = project_dir / "freezone" / "_uploads" / "ok.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"image")
    _patch_project_resolution(monkeypatch, project_dir)

    def _resolve(url: str, _project_dir: Path) -> Path:
        if url.endswith("ok.png"):
            return portrait
        raise ValueError("outside project")

    monkeypatch.setattr(freezone_routes, "resolve_static_url_to_path", _resolve)
    captured: dict[str, object] = {}

    def _fake_start_story_script_task(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        freezone_routes, "_start_freezone_story_script_task", _fake_start_story_script_task
    )

    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            source_text="沈昭昭在深夜办公室醒来。",
            character_refs=[
                freezone_routes.FreezoneStoryScriptCharacterRef(
                    name="可用角色", image_url="/static/admin/58/freezone/_uploads/ok.png"
                ),
                freezone_routes.FreezoneStoryScriptCharacterRef(
                    name="被拒角色",
                    image_url="/static/other/9/freezone/_uploads/bad.png",
                ),
            ],
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    # 被拒的那条仍在 character_refs 里（行内绑定要用），但没有视觉附件。
    assert [ref["name"] for ref in captured["character_refs"]] == ["可用角色", "被拒角色"]
    assert captured["character_image_paths"] == [portrait.as_posix()]


@pytest.mark.asyncio
async def test_story_script_rewrite_mode_needs_no_source_but_needs_a_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单镜重写不要求 source_text / 视频 / 角色图——改一镜所需的一切都在表里。

    但少了「改哪一镜」或「怎么改」就必须当场拒绝：那种请求跑起来只会得到一份
    被随手润色过的整表，用户却以为自己只改了一行。
    """

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return object(), "admin", project, project_dir, str(project_dir)

    async def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "data": {"task_type": "freezone_story_script"}}

    captured: dict[str, object] = {}
    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_enqueue_freezone_background_job", _fake_enqueue)

    from fastapi import HTTPException

    rows = [
        FreezoneStoryScriptRow(
            shot_id="SHOT-A", shot_no=1, duration=5, visual_description="她抬眼看屏"
        ),
        FreezoneStoryScriptRow(
            shot_id="SHOT-B", shot_no=2, duration=4, visual_description="她起身"
        ),
    ]

    # 定位得到：按稳定身份。
    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            current_rows=rows,
            rewrite_shot_id="SHOT-B",
            prompt="让她停在原地",
            title="我在盛唐写天下",
        ),
        user={"username": "admin"},
    )
    assert result["ok"] is True
    payload = captured["payload"]
    assert [row["shot_id"] for row in payload["current_rows"]] == ["SHOT-A", "SHOT-B"]
    assert payload["rewrite_shot_id"] == "SHOT-B"
    assert payload["title"] == "我在盛唐写天下"
    # 重写模式下不要求任何源素材。
    assert payload["source_text"] == ""

    with pytest.raises(HTTPException) as no_instruction:
        await freezone_routes.freezone_story_script_generate(
            project="58",
            body=freezone_routes.FreezoneStoryScriptGenerateRequest(
                current_rows=rows, rewrite_shot_id="SHOT-B", prompt="   "
            ),
            user={"username": "admin"},
        )
    assert no_instruction.value.status_code == 400
    assert "修改要求" in str(no_instruction.value.detail)

    with pytest.raises(HTTPException) as no_target:
        await freezone_routes.freezone_story_script_generate(
            project="58",
            body=freezone_routes.FreezoneStoryScriptGenerateRequest(
                current_rows=rows, rewrite_index=-1, prompt="改一下"
            ),
            user={"username": "admin"},
        )
    assert no_target.value.status_code == 400
    assert "定位" in str(no_target.value.detail)


@pytest.mark.asyncio
async def test_story_script_rewrite_index_is_used_when_identity_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧数据没有 shot_id 时退化到行序，但仍然要在范围内。"""

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return object(), "admin", project, project_dir, str(project_dir)

    async def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "data": {"task_type": "freezone_story_script"}}

    captured: dict[str, object] = {}
    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_enqueue_freezone_background_job", _fake_enqueue)

    rows = [
        FreezoneStoryScriptRow(shot_no=1, duration=5, visual_description="她抬眼看屏"),
        FreezoneStoryScriptRow(shot_no=2, duration=4, visual_description="她起身"),
    ]
    result = await freezone_routes.freezone_story_script_generate(
        project="58",
        body=freezone_routes.FreezoneStoryScriptGenerateRequest(
            current_rows=rows, rewrite_index=1, prompt="换个动作"
        ),
        user={"username": "admin"},
    )
    assert result["ok"] is True
    assert captured["payload"]["rewrite_index"] == 1


@pytest.mark.asyncio
async def test_story_script_route_accepts_idea_without_uploaded_script(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _patch_project_resolution(monkeypatch, project_dir)
    captured = {}
    monkeypatch.setattr(freezone_routes, "_start_freezone_story_script_task", lambda **kwargs: captured.update(kwargs))
    result = await freezone_routes.freezone_story_script_generate(
        project="58", body=freezone_routes.FreezoneStoryScriptGenerateRequest(prompt="小兽人挑战高空滑板"), user={"username": "admin"},
    )
    assert result["ok"] is True
    assert captured["source_text"] == "小兽人挑战高空滑板"


@pytest.mark.asyncio
async def test_freezone_story_script_job_result_returns_json_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    job_id = "storyjob1"
    out = project_dir / "freezone" / "_outputs" / "freezone_story_script" / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": "我在盛唐写天下",
        "rows": [
            {
                "shot_no": 1,
                "duration": 4,
                "visual_description": "现代深夜，沈昭昭在办公室过度劳累加班。",
                "character_1": "",
                "character_description_1": "",
                "character_image_1": "",
                "reference": "",
                "shot": "",
                "character_action": "",
                "emotion": "",
                "scene_tags": "",
                "lighting_mood": "",
                "sound": "",
                "dialogue": "",
                "shot_prompt": "近景特写",
                "video_motion_prompt": "缓慢推进",
            }
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FakeManager:
        def get_task(self, *args, **kwargs):
            return None

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())

    result = await freezone_routes.freezone_job_result(
        project="58",
        task_type="freezone_story_script",
        job_id=job_id,
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["title"] == "我在盛唐写天下"
    assert result["data"]["rows"][0]["shot_no"] == 1


@pytest.mark.asyncio
async def test_freezone_text_translate_job_result_returns_json_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    job_id = "translatejob1"
    out = project_dir / "freezone" / "_outputs" / "freezone_text_translate" / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "translated_text": "Today is Monday",
        "source_language": "zh",
        "target_language": "en",
        "node_type": "generic",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FakeManager:
        def get_task(self, *args, **kwargs):
            return None

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())

    result = await freezone_routes.freezone_job_result(
        project="58",
        task_type="freezone_text_translate",
        job_id=job_id,
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["translated_text"] == "Today is Monday"
    assert result["data"]["target_language"] == "en"


@pytest.mark.asyncio
async def test_freezone_text_prepare_job_result_returns_json_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    job_id = "preparejob1"
    out = project_dir / "freezone" / "_outputs" / "freezone_text_prepare" / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "prepared_text": "场景 1\n雨水落在旧车站的玻璃上。",
        "source_text": "旧车站里，一封信让两个人重新见面。",
        "source_hash": "a" * 64,
        "mode": "faithful",
        "model": "direct/text-primary",
        "change_summary": ["补全了动作"],
        "unresolved": [],
        "warnings": [],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FakeManager:
        def get_task(self, *args, **kwargs):
            return None

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())

    result = await freezone_routes.freezone_job_result(
        project="58",
        task_type="freezone_text_prepare",
        job_id=job_id,
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["prepared_text"].startswith("场景 1")
    assert result["data"]["mode"] == "faithful"
    assert result["data"]["change_summary"] == ["补全了动作"]


@pytest.mark.asyncio
async def test_freezone_image_reverse_prompt_job_result_returns_json_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    job_id = "reverseprompt1"
    out = project_dir / "freezone" / "_outputs" / "freezone_image_reverse_prompt" / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt": "雨夜街头，电影感近景特写，人物侧脸被霓虹照亮",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class FakeManager:
        def get_task(self, *args, **kwargs):
            return None

    _patch_project_resolution(monkeypatch, project_dir)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())

    result = await freezone_routes.freezone_job_result(
        project="58",
        task_type="freezone_image_reverse_prompt",
        job_id=job_id,
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["prompt"].startswith("雨夜街头")
