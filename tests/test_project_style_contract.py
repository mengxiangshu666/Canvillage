from novelvideo.project_config import _default_project_config
from novelvideo.styles.project_style import (
    AUTO_VISUAL_STYLE,
    apply_style_snapshot_to_prompt,
    build_project_style_snapshot,
)


def test_new_projects_default_to_script_directed_style():
    assert _default_project_config()["visual_style"] == AUTO_VISUAL_STYLE


def test_script_directed_style_does_not_inject_an_unrelated_preset():
    snapshot = build_project_style_snapshot(AUTO_VISUAL_STYLE)

    assert snapshot["mode"] == "auto"
    assert snapshot["style_id"] == AUTO_VISUAL_STYLE
    assert "screenplay" in snapshot["image_prompt"].lower()
    assert "start frame" in snapshot["video_prompt"].lower()
    assert "剧本" in snapshot["script_prompt"]
    assert "chinese_period_drama" not in snapshot["script_prompt"]

    from novelvideo.config import get_style_preset

    runtime_preset = get_style_preset(AUTO_VISUAL_STYLE)
    assert "screenplay" in runtime_preset["style_instructions"].lower()
    assert runtime_preset["avoid_instructions"] == ""

    from novelvideo.services.style_service import StyleService

    assert StyleService.get_style_family(AUTO_VISUAL_STYLE) == ""
    assert StyleService.get_style_branch(AUTO_VISUAL_STYLE) == ("", "")


def test_auto_style_snapshot_is_bound_to_one_screenplay_profile(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "novel.txt").write_text(
        "剧本名称：《纸月》\n类型：黑色幽默 / 荒诞喜剧\n视觉：黑白胶片与超现实城市。",
        encoding="utf-8",
    )
    (second / "novel.txt").write_text(
        "剧本名称：《海边》\n类型：温暖家庭片\n视觉：水彩绘本与柔和晨光。",
        encoding="utf-8",
    )

    first_snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE, project_dir=str(first)
    )
    repeated = build_project_style_snapshot(AUTO_VISUAL_STYLE, project_dir=str(first))
    second_snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE, project_dir=str(second)
    )

    assert first_snapshot == repeated
    assert first_snapshot["fingerprint"] != second_snapshot["fingerprint"]
    assert "黑白胶片" in first_snapshot["image_prompt"]
    assert "黑白胶片" in first_snapshot["video_prompt"]
    assert "黑白胶片" in first_snapshot["script_prompt"]
    assert "水彩绘本" in second_snapshot["image_prompt"]


def test_auto_style_asset_prompt_excludes_character_action_and_dialogue(tmp_path):
    (tmp_path / "novel.txt").write_text(
        "\n".join(
            [
                "类型：荒诞科幻 / 黑色喜剧 / 生物朋克",
                "视觉风格：冷青色赛博城市，粗粝胶片颗粒。",
                "第108章：然后，他死了。",
                "场景1：阿诚坐在电脑屏幕前，死死盯着网页。",
                "镜头极速推近阿诚的脸。",
                "阿诚：这不可能！",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE,
        project_dir=str(tmp_path),
    )

    assert "荒诞科幻" in snapshot["image_prompt"]
    assert "冷青色赛博城市" in snapshot["image_prompt"]
    for forbidden in ("阿诚", "第108章", "电脑屏幕", "这不可能"):
        assert forbidden not in snapshot["image_prompt"]
    assert len(snapshot["screenplay_evidence"]) <= 480


def test_auto_style_profile_stops_before_scene_level_visual_blocks(tmp_path):
    (tmp_path / "novel.txt").write_text(
        "\n".join(
            [
                "类型：荒诞科幻 / 黑色喜剧",
                "风格：极端超现实、微缩景观美学",
                "导演阐述：人体内部像劣质霓虹摇滚酒吧，外部都市冷清压抑。",
                "### 剧本正文",
                "第一场：深夜·作家公寓",
                "视觉：阿诚坐在电脑前，屏幕上写着第108章。",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE,
        project_dir=str(tmp_path),
    )

    assert "微缩景观美学" in snapshot["image_prompt"]
    assert "劣质霓虹摇滚酒吧" in snapshot["image_prompt"]
    for forbidden in ("阿诚", "电脑前", "第108章"):
        assert forbidden not in snapshot["image_prompt"]


def test_auto_style_profile_stops_at_scene_without_explicit_body_heading(tmp_path):
    (tmp_path / "novel.txt").write_text(
        "\n".join(
            [
                "类型：科幻",
                "视觉风格：纸雕定格",
                "第一场：卧室",
                "视觉：阿诚坐在电脑前，屏幕显示第108章。",
                "阿诚：不可能。",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE,
        project_dir=str(tmp_path),
    )

    assert "科幻" in snapshot["image_prompt"]
    assert "纸雕定格" in snapshot["image_prompt"]
    for forbidden in ("阿诚", "电脑前", "第108章", "不可能"):
        assert forbidden not in snapshot["image_prompt"]


def test_auto_style_is_attached_to_provider_prompt_once(tmp_path):
    (tmp_path / "novel.txt").write_text(
        "类型：武侠悬疑\n视觉风格：水墨长卷，留白与雾气。",
        encoding="utf-8",
    )
    snapshot = build_project_style_snapshot(
        AUTO_VISUAL_STYLE, project_dir=str(tmp_path)
    )

    prompt = apply_style_snapshot_to_prompt(
        "镜头穿过山雾。", snapshot, modality="video"
    )
    repeated = apply_style_snapshot_to_prompt(prompt, snapshot, modality="video")

    assert prompt == repeated
    assert "PROJECT SCRIPT-DERIVED STYLE" in prompt
    assert "水墨长卷" in prompt


def test_locked_paper_cut_style_compiles_for_script_image_and_video():
    snapshot = build_project_style_snapshot(
        "paper_cut_folk",
        image_model="LingShan-G2",
        video_model="seedance-2.0",
    )

    assert snapshot["mode"] == "locked"
    assert snapshot["style_id"] == "paper_cut_folk"
    assert "cut-paper" in snapshot["image_prompt"].lower()
    assert "parallax" in snapshot["video_prompt"].lower()
    assert "paper_cut_folk" in snapshot["script_prompt"]
    assert len(snapshot["fingerprint"]) == 64


def test_locked_style_is_appended_once_to_the_actual_provider_prompt():
    snapshot = build_project_style_snapshot(
        "paper_cut_folk", video_model="seedance-2.0"
    )
    prompt = apply_style_snapshot_to_prompt(
        "镜头缓缓推近人物。", snapshot, modality="video"
    )
    repeated = apply_style_snapshot_to_prompt(prompt, snapshot, modality="video")

    assert "PROJECT STYLE LOCK [paper_cut_folk]" in prompt
    assert "parallax" in prompt.lower()
    assert repeated == prompt


def test_style_evidence_invalidates_assets_and_stages_from_an_older_style(tmp_path):
    from novelvideo.styles.project_style import (
        artifact_matches_style,
        stage_matches_style,
        write_artifact_style_evidence,
        write_stage_style_evidence,
    )

    artifact = tmp_path / "master.png"
    artifact.write_bytes(b"image")
    old = build_project_style_snapshot("chinese_period_drama")
    new = build_project_style_snapshot("paper_cut_folk")

    write_artifact_style_evidence(artifact, old)
    write_stage_style_evidence(tmp_path, "script", old, episode=1)

    assert artifact_matches_style(artifact, old)
    assert not artifact_matches_style(artifact, new)
    assert stage_matches_style(tmp_path, "script", old, episode=1)
    assert not stage_matches_style(tmp_path, "script", new, episode=1)


def test_paper_cut_prop_prompt_does_not_force_photoreal_product_photography():
    from novelvideo.generators.nanobanana_prop import build_prop_reference_prompt

    prompt = build_prop_reference_prompt(
        "一盏古老灯笼",
        style="paper_cut_folk",
    )

    assert "cut-paper" in prompt.lower()
    assert "PRODUCT PHOTOGRAPHY STYLE" not in prompt
    assert "Professional product shot quality" not in prompt


def _render_prompt_for_style(style: str) -> str:
    from novelvideo.generators.prompt_builder import (
        GridConfig,
        PromptComponents,
        PromptContext,
        PromptMode,
        RenderModeStrategy,
        StyleConfig,
    )

    context = PromptContext(
        grid=GridConfig(rows=1, cols=1, aspect_ratio="16:9"),
        characters={},
        style=StyleConfig(style_name=style),
        beats=[
            {
                "beat_number": 1,
                "visual_description": "人物走过红色窗花街道。",
                "detected_identities": [],
                "detected_props": [],
                "scene_ref": {"scene_id": "街道"},
            }
        ],
        mode=PromptMode.RENDER,
    )
    return RenderModeStrategy().build(context, PromptComponents())


def test_script_auto_directive_reaches_the_actual_render_prompt():
    prompt = _render_prompt_for_style(AUTO_VISUAL_STYLE)

    assert "Infer the visual medium" in prompt
    assert "Do not impose a generic live-action" in prompt
    assert "chinese_period_drama" not in prompt


def test_paper_cut_render_prompt_has_no_forced_photoreal_character_treatment():
    prompt = _render_prompt_for_style("paper_cut_folk")

    assert "cut-paper" in prompt.lower()
    assert "No photorealism" in prompt
    for conflicting_phrase in (
        "REALISTIC PROPORTIONS",
        "natural human skin tone",
        "natural adult human anatomy",
        "Final rendering realism",
        "Scene realism rules",
        "final skin texture",
    ):
        assert conflicting_phrase not in prompt
