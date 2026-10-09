import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from novelvideo.models import build_scene_ref, sync_beat_asset_refs
from novelvideo.workflows.literal_script_writing import (
    LiteralBeatMetaBatchOutput,
    LiteralBeatMetaOutput,
    LiteralScriptWritingWorkflow,
    SceneBlock,
    SceneLineContext,
    _content_filter_hint_matches,
    _literal_beat_batch_size,
    _same_scene_line_batch,
    _validated_batch_output_map,
)
from novelvideo.workflows.script_writing import create_script_writing_workflow


def _payload(audio_type: str | None = None) -> dict:
    payload = {
        "speaker": "",
        "speaker_kind": "character",
        "visual_description": "昏暗的街区里只有面馆亮着灯。",
        "scene_id": "",
    }
    if audio_type is not None:
        payload["audio_type"] = audio_type
    return payload


def test_literal_beat_meta_defaults_to_silence_audio_type():
    beat = LiteralBeatMetaOutput.model_validate(_payload())

    assert beat.audio_type == "silence"
    assert not hasattr(beat, "scene_variant_id")


def test_literal_batch_size_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("LITERAL_BEAT_META_BATCH_SIZE", raising=False)
    assert _literal_beat_batch_size() == 4
    monkeypatch.setenv("LITERAL_BEAT_META_BATCH_SIZE", "99")
    assert _literal_beat_batch_size() == 6
    monkeypatch.setenv("LITERAL_BEAT_META_BATCH_SIZE", "bad")
    assert _literal_beat_batch_size() == 4


def test_literal_batch_partition_never_crosses_scene_block():
    first = SceneBlock(header_line="第一场")
    second = SceneBlock(header_line="第二场")
    contexts = [
        SceneLineContext(raw_line="一", scene_block=first),
        SceneLineContext(raw_line="二", scene_block=first),
        SceneLineContext(raw_line="三", scene_block=second),
    ]

    batch = _same_scene_line_batch(contexts, start_index=0, batch_size=4)

    assert [(index, item.raw_line) for index, item in batch] == [(1, "一"), (2, "二")]


def test_literal_batch_output_preserves_nested_validation_and_exact_order():
    output = LiteralBeatMetaBatchOutput.model_validate(
        {
            "items": [
                {"content_index": 3, **_payload("silence")},
                {"content_index": 4, **_payload("narration")},
            ]
        },
        context={"valid_identity_ids": set(), "valid_scene_ids": set(), "valid_prop_ids": set()},
    )

    mapped = _validated_batch_output_map(output, expected_indices=[3, 4])

    assert list(mapped) == [3, 4]
    with pytest.raises(ValueError, match="批响应行序号不匹配"):
        _validated_batch_output_map(output, expected_indices=[4, 3])


def test_scene_ref_exposes_lightweight_variant_id():
    ref = build_scene_ref("故宫", "下雪")

    assert ref is not None
    assert ref.scene_id == "故宫"
    assert ref.variant_id == "下雪"


def test_sync_beat_asset_refs_preserves_canonical_scene_variant():
    beat = {
        "scene_ref": {
            "scene_id": "故宫",
            "variant_id": "下雪",
            "render_anchor_id": "selected_background",
        }
    }

    sync_beat_asset_refs(beat)

    assert beat["scene_ref"] == {
        "scene_id": "故宫",
        "variant_id": "下雪",
        "render_anchor_id": "selected_background",
        "render_anchor_source_id": "",
    }


def test_literal_beat_meta_accepts_silence_audio_type_for_visual_only_beats():
    beat = LiteralBeatMetaOutput.model_validate(_payload("silence"))

    assert beat.audio_type == "silence"


def test_literal_beat_meta_accepts_narration_audio_type_for_non_dialogue():
    beat = LiteralBeatMetaOutput.model_validate(_payload("narration"))

    assert beat.audio_type == "narration"


def test_literal_beat_meta_rejects_action_audio_type():
    with pytest.raises(ValidationError, match="silence/narration/dialogue"):
        LiteralBeatMetaOutput.model_validate(_payload("action"))


def test_literal_workflow_leaves_silence_narration_segment_empty():
    assert (
        LiteralScriptWritingWorkflow._derive_narration_segment(
            "△昏暗的街区中只有面馆的照明灯亮着。",
            "silence",
        )
        == ""
    )


def test_literal_workflow_keeps_narration_and_extracts_dialogue_text():
    assert (
        LiteralScriptWritingWorkflow._derive_narration_segment(
            "旁白：他终于找到线索。",
            "narration",
        )
        == "旁白：他终于找到线索。"
    )
    assert (
        LiteralScriptWritingWorkflow._derive_narration_segment(
            "谢铮：走。",
            "dialogue",
        )
        == "走。"
    )


def test_narrated_workflow_converts_silence_to_narration():
    workflow = LiteralScriptWritingWorkflow(
        cognee_store=None,
        audio_type_mode="narrated",
    )

    audio_type = workflow._normalize_audio_type_for_mode("silence")

    assert audio_type == "narration"
    assert (
        LiteralScriptWritingWorkflow._derive_narration_segment(
            "昏暗的街区中只有面馆的照明灯亮着。",
            audio_type,
        )
        == "昏暗的街区中只有面馆的照明灯亮着。"
    )


def test_literal_workflow_converts_non_character_dialogue_to_narration():
    audio_type, speaker = LiteralScriptWritingWorkflow._normalize_audio_metadata(
        audio_type="dialogue",
        speaker_kind="non_character",
        speaker="中年男声",
    )

    assert audio_type == "narration"
    assert speaker == ""


def test_literal_content_filter_log_mentions_line_context_and_terms():
    messages = LiteralScriptWritingWorkflow._content_filter_log_messages(
        content_index=8,
        total=37,
        line_ctx=SimpleNamespace(
            raw_line="沈晚握紧匕首，指尖渗出血。",
            scene_block=SimpleNamespace(header_line="苏鸾寝殿 夜 内", location="苏鸾寝殿"),
            prev_window=["苏糖后退一步。"],
            next_line="屋内灯光闪烁。",
            source_line_number=11,
        ),
        error=RuntimeError("Content filter triggered"),
    )
    joined = "\n".join(messages)

    assert "第 8/37 行生成失败" in joined
    assert "需修改行（源文本第 11 行，内容行 8/37）: 沈晚握紧匕首" in joined
    assert "所属场次: 苏鸾寝殿 夜 内" in joined
    assert "上一行=苏糖后退一步。" in joined
    assert "下一行=屋内灯光闪烁。" in joined
    assert "疑似高风险表达" in joined
    assert "匕首" in joined
    assert "血" in joined


def test_literal_content_filter_hint_matches_english_terms():
    matches = _content_filter_hint_matches(
        "The dagger points at her chest. Blood drips down. I can't die."
    )
    joined = "\n".join(matches)

    assert "暴力/武器" in joined
    assert "dagger" in joined
    assert "blood" in joined
    assert "自伤/生命威胁" in joined
    assert "i can't die" in joined


def test_literal_line_context_preserves_source_line_number():
    lines = [
        "第1场 苏鸾寝殿 夜 内",
        "△烛火摇晃。",
        "△匕首映出少女脸。",
    ]
    blocks = LiteralScriptWritingWorkflow._build_scene_blocks(lines)
    contexts = LiteralScriptWritingWorkflow._build_scene_line_contexts(
        blocks,
        source_lines=lines,
    )

    assert contexts[0].source_line_number == 2
    assert contexts[1].source_line_number == 3


def test_literal_scene_menu_prompt_lists_scene_ids_without_variant_menu():
    workflow = LiteralScriptWritingWorkflow(cognee_store=None)
    episode = SimpleNamespace(
        scene_menu=[
            SimpleNamespace(scene_id="故宫"),
            SimpleNamespace(scene_id="故宫_下雪", base_scene_id="故宫", variant_id="下雪"),
        ]
    )

    section = workflow._build_scene_menu_for_episode(episode)

    assert "`故宫`" in section
    assert "`故宫_下雪`" in section
    assert "变体" not in section


def test_literal_scene_id_validator_clears_invalid_scene_id():
    beat = LiteralBeatMetaOutput.model_validate(
        {
            **_payload(),
            "scene_id": "不存在",
        },
        context={"valid_scene_ids": {"故宫", "故宫_下雪"}},
    )

    assert beat.scene_id == ""


def test_literal_script_restores_missing_identity_markers_from_source_line():
    workflow = LiteralScriptWritingWorkflow(cognee_store=None)
    result = workflow._ensure_identity_markers(
        "反派挥出重拳，男主侧身闪避。",
        raw_line="反派一记重拳直扑面门，男主侧身避过。",
        candidate_identity_ids={"男主_青年时期", "反派_青年时期"},
        sticky_identities={
            "男主": "男主_青年时期",
            "反派": "反派_青年时期",
        },
        identity_metadata={
            "男主_青年时期": {"character_name": "男主", "character_aliases": []},
            "反派_青年时期": {"character_name": "反派", "character_aliases": []},
        },
    )

    assert "{{反派_青年时期}}" in result
    assert "{{男主_青年时期}}" in result
    assert "__NO_CHARACTER__" not in result


def test_literal_locked_base_allows_derived_scene_candidates():
    workflow = LiteralScriptWritingWorkflow(cognee_store=None)
    episode = SimpleNamespace(
        scene_menu=[
            SimpleNamespace(scene_id="故宫_下雪", base_scene_id="故宫", variant_id="下雪"),
            SimpleNamespace(scene_id="故宫"),
            SimpleNamespace(scene_id="御花园"),
        ]
    )

    workflow._build_scene_menu_for_episode(episode)

    assert workflow._resolve_scene_id("故宫", episode) == "故宫"
    assert workflow._allowed_scene_ids_for_block("故宫") == {"故宫", "故宫_下雪"}


def test_literal_canonical_scene_ref_uses_menu_split_not_prefix_guess():
    workflow = LiteralScriptWritingWorkflow(cognee_store=None)
    episode = SimpleNamespace(
        scene_menu=[
            SimpleNamespace(scene_id="卫生间"),
            SimpleNamespace(
                scene_id="卫生间_漏水",
                base_scene_id="卫生间",
                variant_id="漏水",
            ),
            SimpleNamespace(scene_id="卫生间_主控室"),
        ]
    )
    workflow._build_scene_menu_for_episode(episode)

    derived_ref = workflow._canonical_scene_ref_for_menu_choice("卫生间_漏水")
    independent_ref = workflow._canonical_scene_ref_for_menu_choice("卫生间_主控室")

    assert derived_ref is not None
    assert derived_ref.scene_id == "卫生间"
    assert derived_ref.variant_id == "漏水"
    assert independent_ref is not None
    assert independent_ref.scene_id == "卫生间_主控室"
    assert independent_ref.variant_id == ""


def test_literal_locked_base_rejects_other_scene_candidate():
    workflow = LiteralScriptWritingWorkflow(cognee_store=None)
    episode = SimpleNamespace(
        scene_menu=[
            SimpleNamespace(scene_id="故宫"),
            SimpleNamespace(scene_id="故宫_下雪"),
            SimpleNamespace(scene_id="御花园"),
        ]
    )

    workflow._build_scene_menu_for_episode(episode)

    allowed = workflow._allowed_scene_ids_for_block(workflow._resolve_scene_id("故宫", episode))
    assert "御花园" not in allowed


def test_create_script_workflow_uses_narrated_audio_type_mode():
    class _Store:
        output_dir = ""

    workflow = create_script_writing_workflow(
        _Store(),
        spine_template="narrated",
    )

    assert workflow.audio_type_mode == "narrated"
