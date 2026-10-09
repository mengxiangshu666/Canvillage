from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo import config
from novelvideo.freezone.jobs import (
    CompletedVideoDownloadPending,
    FreezoneVideoGenerationError,
    VideoSubmissionPending,
    run_freezone_video_gen,
)
from novelvideo.freezone.jobs import _format_video_generation_failure
from novelvideo.generators.video_generator import (
    HuimengVideoGenerator,
    NewApiVideoGenerator,
    Seedance2VideoGenerator,
    ShotReference,
    newapi_video_backend_options,
)
from novelvideo.generators.video.generic_video_adapter import (
    GenericVideoAdapterGenerator,
)
from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus
from novelvideo.freezone.video_node import (
    FREEZONE_DEFAULT_VIDEO_BACKEND,
    FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON,
    add_video_character_library_item,
    assert_freezone_video_generation_enabled,
    build_freezone_image_to_video_prompt,
    build_freezone_keyframe_video_prompt,
    build_freezone_omni_video_prompt,
    build_freezone_video_prompt,
    delete_video_character_library_item,
    freezone_video_channel_status,
    freezone_video_generation_enabled,
    freezone_video_edit_contract,
    get_freezone_video_model_names,
    get_freezone_video_model_options,
    get_video_camera_template,
    get_video_camera_templates,
    is_freezone_happyhorse_backend,
    is_freezone_seedance2_backend,
    load_video_character_library,
    normalize_video_aspect_ratio,
    normalize_video_duration_for_backend,
    normalize_video_resolution,
    normalize_video_resolution_for_backend,
    resolve_freezone_video_backend,
    summarize_omni_reference_counts,
    validate_omni_reference_limits,
)
from novelvideo.api.schemas import (
    FreezoneImageToVideoRequest,
    FreezoneKeyframeVideoRequest,
    FreezoneVideoGenRequest,
    FreezoneVideoOmniGenRequest,
)
from novelvideo.model_gateway_settings import save_direct_video_models
from novelvideo.generators.video.direct_video_capability_cache import record_capability


def _configure_direct_video_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *model_ids: str,
) -> dict[str, str]:
    """Persist test-only direct records and return their canvas backends."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "direct-video-state"))
    saved = save_direct_video_models(
        [
            {
                "label": f"测试直连 {index + 1}",
                "modelId": model_id,
                "baseUrl": "http://127.0.0.1:9800/v1",
                "apiKey": "direct-test-key",
                "enabled": True,
            }
            for index, model_id in enumerate(model_ids)
        ]
    )
    for model_id in model_ids:
        record_capability(
            base_url="http://127.0.0.1:9800/v1",
            protocol="openai-video",
            upstream_model=model_id,
            capability={
                "verificationStatus": "contract-resolved",
                "modelFound": True,
                "discoveredModelCount": len(model_ids),
                "supportedProtocols": ["openai:video_generation"],
                # 「能用」的渠道在真实产品里必然点过检测连接；缓存里要留一次
                # 真实凭据检测的痕迹，否则按当前规则不该判为可运行。
                "credentialValidation": {"status": "accepted", "httpStatus": 200},
                "checkedAt": "2026-10-03T00:00:00+00:00",
            },
        )
    return {str(record["modelId"]): f"direct_{record['id']}" for record in saved}


def test_direct_freezone_contract_exposes_live_resolution_and_feature_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backends = _configure_direct_video_models(monkeypatch, tmp_path, "minimax_h3")
    contract = get_freezone_video_model_options()[0]

    assert contract["apiModel"] == "minimax_h3"
    assert contract["resolutionOptions"] == ["768p", "2k"]
    assert contract["aspectRatioOptions"] == [
        "16:9",
        "9:16",
        "1:1",
        "4:3",
        "3:4",
        "21:9",
    ]
    assert contract["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "imageReference",
        "allReference",
        "videoEdit",
    ]
    assert contract["referenceLimits"]["allReference"] == {
        "image": 9,
        "video": 3,
        "audio": 3,
    }
    assert contract["minDuration"] == 4
    assert contract["maxDuration"] == 15
    assert backends["minimax_h3"].startswith("direct_")


def test_freezone_video_channel_defaults_offline_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VILLAGE_CANVAS_FREEZONE_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_VIDEO_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_FREEZONE_VIDEO_ALLOWLIST", raising=False)

    assert freezone_video_generation_enabled() is False
    status = freezone_video_channel_status()
    assert status["enabled"] is False
    assert FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON in status["disabled_reason"]

    with pytest.raises(ValueError, match="视频渠道未接通"):
        assert_freezone_video_generation_enabled()

    # Direct-only mode must not project stale NewAPI video models into the
    # picker when no locally configured record exists.
    assert get_freezone_video_model_options() == []


def test_freezone_video_channel_enables_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("VILLAGE_CANVAS_FREEZONE_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_FREEZONE_VIDEO_ALLOWLIST", raising=False)
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.0-fast"
    )

    assert freezone_video_generation_enabled() is True
    status = freezone_video_channel_status()
    assert status["enabled"] is True
    assert status["disabled_reason"] == ""

    options = get_freezone_video_model_options()
    assert [item["id"] for item in options] == [backends["jimeng-seedance-2.0-fast"]]
    assert options[0]["provider"] == "direct"
    assert options[0]["enabled"] is True


def test_freezone_video_channel_force_off_beats_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_direct_video_models(monkeypatch, tmp_path, "jimeng-seedance-2.0-fast")
    monkeypatch.setenv("VILLAGE_CANVAS_FREEZONE_VIDEO_ENABLED", "0")

    assert freezone_video_generation_enabled() is False
    with pytest.raises(ValueError, match="视频渠道未接通"):
        assert_freezone_video_generation_enabled()


def test_build_freezone_video_prompt_includes_camera_template_and_character_names() -> (
    None
):
    prompt = build_freezone_video_prompt(
        user_prompt="赛博朋克街头，角色缓慢向前走",
        camera_template_id="follow_tracking",
        character_names=["林小满", "阿七"],
        marks=[{"label": "老人", "point_x": 0.2, "point_y": 0.5}],
    )

    assert "赛博朋克街头" in prompt
    assert "跟随拍摄" in prompt
    assert "林小满、阿七" in prompt
    assert "画面重点元素为" in prompt
    assert "老人" in prompt


#: 交付文本里不允许出现的内部规则标记。对标 `drama-skills/delivery-profile.md`：
#: 「交付文本里不出现 hash、参考图文件名、内部规则 ID、锁定标记、草图指代或任务备注」。
#: 这些标签会把一段散文变成可朗读的清单，是「模型把整段提示词念出来」的燃料之一。
_VIDEO_INTERNAL_RULE_LABELS = (
    "运镜模板：",
    "重点元素标记",
    "输出要求：",
    "角色一致性要求：",
    "图片参考约束：",
    "首帧约束：",
    "尾帧约束：",
    "首尾帧约束：",
    "主题要求：",
    "全能参考模式要求：",
)


def _assert_no_internal_rule_labels(prompt: str) -> None:
    leaked = [label for label in _VIDEO_INTERNAL_RULE_LABELS if label in prompt]
    assert not leaked, f"内部规则标记泄漏进交付文本：{leaked}"


def test_video_prompt_builders_never_deliver_internal_rule_labels() -> None:
    """四个 builder 的产出都必须是一段可交付的散文，不是带标签的规则单。"""

    _assert_no_internal_rule_labels(
        build_freezone_video_prompt(
            user_prompt="赛博朋克街头，角色缓慢向前走",
            camera_template_id="follow_tracking",
            character_names=["林小满"],
            marks=[{"label": "老人", "point_x": 0.2, "point_y": 0.5}],
        )
    )
    _assert_no_internal_rule_labels(
        build_freezone_image_to_video_prompt(
            user_prompt="老人缓慢抬眼。",
            camera_template_id="pedestal_up",
            marks=[{"label": "老人", "point_x": 0.15, "point_y": 0.45}],
        )
    )
    _assert_no_internal_rule_labels(
        build_freezone_image_to_video_prompt(
            user_prompt="老人微微抬头。",
            camera_template_id="follow_tracking",
            reference_image_count=3,
        )
    )
    _assert_no_internal_rule_labels(
        build_freezone_keyframe_video_prompt(
            user_prompt="老人抬眼。",
            camera_template_id="pedestal_up",
            has_first_frame=True,
            has_last_frame=True,
        )
    )
    _assert_no_internal_rule_labels(
        build_freezone_omni_video_prompt(
            user_prompt="雨夜中老人躺在病床上。",
            theme="压抑、克制、纪实感",
            camera_template_id="orbit_up",
        )
    )


def test_video_prompt_builders_end_with_one_negative_line() -> None:
    """负向约束收敛成一行短名词表，替代原来每处各写一遍的长句样板。"""

    prompt = build_freezone_video_prompt(
        user_prompt="赛博朋克街头",
        camera_template_id="locked_off",
    )
    assert prompt.count("拒绝：") == 1
    assert "主体身份漂移" in prompt
    # 原来那句「输出要求：生成单条连贯视频镜头，动作自然，运动平滑」是纯样板，
    # 不带任何镜头信息，却是一整句可念的中文。
    assert "生成单条连贯视频镜头" not in prompt

    multi_image = build_freezone_image_to_video_prompt(
        user_prompt="老人微微抬头。",
        reference_image_count=3,
    )
    assert multi_image.count("拒绝：") == 1
    assert "多画面拼贴" in multi_image


def test_video_camera_template_lookup_works() -> None:
    template = get_video_camera_template("locked_off")

    assert template is not None
    assert template["name"] == "固定镜头"
    fallback_template = get_video_camera_template("dolly-in")
    assert fallback_template is not None
    assert fallback_template["id"] == "dolly_in"


def test_video_camera_templates_expose_the_full_23_entry_catalog() -> None:
    templates = get_video_camera_templates()
    assert len(templates) == 23
    ids = {item["id"] for item in templates}
    assert {
        "dolly_in",
        "dolly_out",
        "zoom_in",
        "zoom_out",
        "dolly_zoom",
        "orbit_around",
        "roll_360",
        "pov",
        "drone_shot",
        "epic_helicopter",
        "handheld",
    } <= ids
    assert all(
        str(item["name"]).strip() and str(item["prompt"]).strip() for item in templates
    )


def test_video_character_library_roundtrip(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    item = add_video_character_library_item(
        project_dir,
        name="林小满",
        image_urls=["/static/admin/58/freezone/_uploads/char.png"],
    )

    items = load_video_character_library(project_dir)
    assert len(items) == 1
    assert items[0]["id"] == item["id"]
    assert items[0]["name"] == "林小满"

    deleted = delete_video_character_library_item(project_dir, item["id"])
    assert deleted is True
    assert load_video_character_library(project_dir) == []


def test_video_ratio_and_resolution_normalization() -> None:
    assert normalize_video_aspect_ratio("auto") == "16:9"
    assert normalize_video_aspect_ratio("9:16") == "9:16"
    assert normalize_video_resolution(None) == "480p"
    assert normalize_video_resolution("720P") == "720p"


def test_freezone_video_defaults_defer_to_the_runtime_direct_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.0-fast"
    )
    assert FREEZONE_DEFAULT_VIDEO_BACKEND == "direct_default"
    default_contract = get_freezone_video_model_options()[0]
    assert default_contract["id"] == backends["jimeng-seedance-2.0-fast"]
    assert default_contract["resolutionOptions"] == ["720p"]
    assert default_contract["minDuration"] == 4
    assert default_contract["maxDuration"] == 15
    assert default_contract["referenceLimits"]["allReference"]["image"] == 9
    for request in [
        FreezoneVideoGenRequest(prompt="雨夜街头"),
        FreezoneImageToVideoRequest(image_urls=["/static/a.png"]),
        FreezoneKeyframeVideoRequest(first_frame_url="/static/a.png"),
        FreezoneVideoOmniGenRequest(prompt="雨夜街头"),
    ]:
        assert request.model == ""
        assert request.resolution == "720p"


def test_video_request_schema_keeps_provider_specific_values() -> None:
    request = FreezoneVideoGenRequest(
        prompt="custom contract",
        aspect_ratio="2.39:1",
        resolution="1440p",
    )
    assert request.aspect_ratio == "2.39:1"
    assert request.resolution == "1440p"


def test_build_freezone_omni_video_prompt_includes_theme() -> None:
    prompt = build_freezone_omni_video_prompt(
        user_prompt="雨夜中老人躺在病床上，年轻男子伸手整理氧气管。",
        theme="压抑、克制、纪实感",
        camera_template_id="orbit_up",
        marks=[{"label": "氧气管", "point_x": 0.7, "point_y": 0.6}],
    )

    assert "压抑、克制、纪实感" in prompt
    assert "盘旋抬升" in prompt
    assert "氧气管" in prompt


def test_build_freezone_image_to_video_prompt_includes_first_frame_and_marks() -> None:
    prompt = build_freezone_image_to_video_prompt(
        user_prompt="老人缓慢抬眼，呼吸微弱。",
        camera_template_id="pedestal_up",
        marks=[{"label": "老人", "point_x": 0.15, "point_y": 0.45, "note": "主体"}],
    )

    assert "老人缓慢抬眼" in prompt
    assert "镜头上升" in prompt
    assert "老人" in prompt
    assert "主体" in prompt
    assert "把输入图作为视频首帧" in prompt


def test_build_freezone_image_to_video_prompt_supports_multi_image_references() -> None:
    prompt = build_freezone_image_to_video_prompt(
        user_prompt="老人微微抬头，保持病房压抑氛围。",
        camera_template_id="follow_tracking",
        reference_image_count=3,
    )

    assert "不要把多张图拼贴成多画面" in prompt
    assert "多张输入图片" in prompt
    assert "跟随拍摄" in prompt


def test_build_freezone_image_to_video_prompt_supports_box_marks() -> None:
    prompt = build_freezone_image_to_video_prompt(
        user_prompt="老人微微转头。",
        camera_template_id="locked_off",
        marks=[
            {
                "label": "老人",
                "box_x": 0.05,
                "box_y": 0.2,
                "box_width": 0.3,
                "box_height": 0.5,
            }
        ],
    )

    assert "画面重点元素为" in prompt
    assert "老人" in prompt
    assert "左侧中间" in prompt


def test_build_freezone_keyframe_video_prompt_handles_first_and_last_frame() -> None:
    prompt = build_freezone_keyframe_video_prompt(
        user_prompt="老人抬眼后镜头缓慢推进到病床侧面。",
        camera_template_id="pedestal_up",
        marks=[{"label": "老人", "point_x": 0.4, "point_y": 0.4}],
        has_first_frame=True,
        has_last_frame=True,
    )

    assert "老人抬眼后镜头缓慢推进到病床侧面" in prompt
    assert "镜头上升" in prompt
    assert "从首帧自然过渡到尾帧" in prompt
    assert "老人" in prompt


def test_video_model_options_and_resolution_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch,
        tmp_path,
        "jimeng-seedance-2.0-fast",
        "jimeng-seedance-2.5",
    )
    names = get_freezone_video_model_names()
    options = get_freezone_video_model_options()

    assert names == [
        backends["jimeng-seedance-2.0-fast"],
        backends["jimeng-seedance-2.5"],
    ]
    assert all(item["providerId"] == "direct" for item in options)
    assert all(not item["id"].startswith("newapi_") for item in options)
    fast = options[0]
    assert fast["apiModel"] == "jimeng-seedance-2.0-fast"
    assert fast["resolutionOptions"] == ["720p"]
    assert fast["minDuration"] == 4
    assert fast["maxDuration"] == 15
    assert fast["supportedModes"] == [
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "allReference",
        "videoEdit",
    ]
    assert normalize_video_resolution_for_backend(fast["id"], "480p") == "720p"


def test_direct_kacang_model_exposes_its_declared_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "s-videos-f-933-fast-480-2"
    )
    option = get_freezone_video_model_options()[0]

    assert option["id"] == backends["s-videos-f-933-fast-480-2"]
    assert option["resolutionOptions"] == ["480p"]
    assert option["minDuration"] == 4
    assert option["maxDuration"] == 15
    assert option["family"] == "kacang-933"
    assert option["referenceLimits"]["allReference"] == {
        "image": 9,
        "video": 3,
        "audio": 3,
    }


def test_direct_wokey_25_keeps_its_long_duration_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.5"
    )
    option = get_freezone_video_model_options()[0]

    assert option["id"] == backends["jimeng-seedance-2.5"]
    assert option["resolutionOptions"] == ["480p", "720p"]
    assert option["minDuration"] == 4
    assert option["maxDuration"] == 30


def test_direct_s_25db_route_exposes_thirty_second_canvas_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(monkeypatch, tmp_path, "S-2.5db-线路三")
    option = get_freezone_video_model_options()[0]

    assert option["id"] == backends["S-2.5db-线路三"]
    assert option["minDuration"] == 30
    assert option["maxDuration"] == 30
    assert normalize_video_duration_for_backend(option["id"], 15) == 30


def test_unknown_declared_protocol_is_hidden_from_canvas_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch,
        tmp_path,
        "future-video-v9",
    )
    record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="unresolved",
        upstream_model="future-video-v9",
        capability={
            "verificationStatus": "degraded",
            "detectedProtocol": "unresolved",
            "supportedProtocols": ["provider:future_video_v9"],
        },
    )

    assert get_freezone_video_model_names() == []
    assert get_freezone_video_model_options()[0]["runtimeReady"] is False
    assert get_freezone_video_model_options()[0]["disabled"] is True
    with pytest.raises(ValueError, match="not runtime-ready"):
        resolve_freezone_video_backend(backends["future-video-v9"])


def test_video_capability_cache_sequence_prefers_latest_evidence_when_clock_repeats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_direct_video_models(monkeypatch, tmp_path, "future-video-v9")
    import novelvideo.generators.video.direct_video_capability_cache as cache

    monkeypatch.setattr(cache.time, "time_ns", lambda: 42)
    cache.record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="openai-video",
        upstream_model="future-video-v9",
        capability={"modelFound": True, "verificationStatus": "contract-resolved"},
    )
    cache.record_capability(
        base_url="http://127.0.0.1:9800/v1",
        protocol="unresolved",
        upstream_model="future-video-v9",
        capability={"detectedProtocol": "unresolved", "verificationStatus": "degraded"},
    )

    latest = cache.get_cached_capability_for_model(
        base_url="http://127.0.0.1:9800/v1",
        upstream_model="future-video-v9",
    )
    assert latest["protocol"] == "unresolved"


def test_legacy_newapi_ids_are_not_exposed_and_require_a_matching_direct_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.0-fast"
    )

    assert "newapi_grok-video-channel" not in newapi_video_backend_options()
    assert "newapi_grok-video-channel" not in get_freezone_video_model_names()
    assert (
        resolve_freezone_video_backend("newapi_jimeng-seedance-2.0-fast")
        == (backends["jimeng-seedance-2.0-fast"])
    )
    with pytest.raises(ValueError, match="未配置为直连 API"):
        resolve_freezone_video_backend("newapi_grok-video-channel")


def test_legacy_kling_id_migrates_only_to_the_matching_direct_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "kling-v3-omni-v2v-create"
    )

    assert get_freezone_video_model_names() == [backends["kling-v3-omni-v2v-create"]]
    assert (
        resolve_freezone_video_backend("newapi_kling-v3-omni-v2v-create")
        == (backends["kling-v3-omni-v2v-create"])
    )


def test_resolve_freezone_video_backend_migrates_saved_wokey_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.0-fast", "jimeng-seedance-2.5"
    )

    assert (
        resolve_freezone_video_backend("huimeng_seedance20_fast")
        == (backends["jimeng-seedance-2.0-fast"])
    )
    assert (
        resolve_freezone_video_backend("newapi_jimeng-seedance-2.5")
        == (backends["jimeng-seedance-2.5"])
    )
    assert resolve_freezone_video_backend(None) == backends["jimeng-seedance-2.0-fast"]


def test_resolve_freezone_video_backend_accepts_saved_direct_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch,
        tmp_path,
        "jimeng-seedance-2.0-fast",
    )

    assert (
        resolve_freezone_video_backend("测试直连 1")
        == (backends["jimeng-seedance-2.0-fast"])
    )


def test_seedance2_backend_detection_accepts_newapi_and_legacy_values() -> None:
    assert is_freezone_seedance2_backend("newapi_seedance-2.0-fast")
    assert is_freezone_seedance2_backend("huimeng_seedance-2.0-fast")
    assert is_freezone_seedance2_backend("seedance_2")
    assert not is_freezone_seedance2_backend("newapi_seedance-1.5-pro")


def test_happyhorse_backend_detection_accepts_newapi_value() -> None:
    assert is_freezone_happyhorse_backend("newapi_happyhorse-1.0")
    assert not is_freezone_happyhorse_backend("newapi_seedance-2.0-fast")


def test_source_video_edit_contract_is_derived_from_live_model_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "kling-v3-omni-v2v-create", "jimeng-seedance-2.5"
    )
    assert freezone_video_edit_contract(backends["kling-v3-omni-v2v-create"]) == {
        "image": 9,
        "video": 1,
        "audio": 0,
    }
    assert freezone_video_edit_contract(backends["jimeng-seedance-2.5"]) == {
        "image": 9,
        "video": 3,
        "audio": 3,
    }


@pytest.mark.asyncio
async def test_freezone_video_gen_allows_direct_wokey_text_to_video(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, dict] = {}

    class FakeVideoGenerator(NewApiVideoGenerator):
        def __init__(self):
            pass

        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            output_path = Path(kwargs["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    def fake_create_video_generator(**kwargs):
        captured["create"] = kwargs
        return FakeVideoGenerator()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        fake_create_video_generator,
    )
    backends = _configure_direct_video_models(
        monkeypatch, tmp_path, "jimeng-seedance-2.0-fast"
    )

    out = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_direct_t2v",
        prompt="雨夜街头，镜头缓慢推进",
        reference_items=[],
        backend=backends["jimeng-seedance-2.0-fast"],
        gen_mode="textToVideo",
    )

    assert out.exists()
    assert captured["create"]["backend"] == backends["jimeng-seedance-2.0-fast"]
    assert captured["generate"]["image_path"] is None
    assert captured["generate"]["references"] == []
    assert captured["generate"]["gen_mode"] == "textToVideo"


@pytest.mark.asyncio
async def test_freezone_video_gen_allows_newapi_fast_text_to_video(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, dict] = {}

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            output_path = Path(kwargs["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    def fake_create_video_generator(**kwargs):
        captured["create"] = kwargs
        return FakeVideoGenerator()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        fake_create_video_generator,
    )

    out = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_newapi_fast_t2v",
        prompt="雨夜街头，镜头缓慢推进",
        reference_items=[],
        backend="newapi_seedance-1.0-pro-fast",
    )

    assert out.exists()
    assert captured["create"]["backend"] == "newapi_seedance-1.0-pro-fast"
    assert captured["generate"]["image_path"] is None
    assert captured["generate"]["references"] == []


@pytest.mark.asyncio
async def test_freezone_video_gen_forwards_gen_mode_to_generic_adapter(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    class FakeGenericVideoGenerator(GenericVideoAdapterGenerator):
        async def generate(self, **kwargs):
            captured.update(kwargs)
            output_path = Path(str(kwargs["output_path"]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeGenericVideoGenerator.__new__(FakeGenericVideoGenerator),
    )

    out = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_generic_mode",
        prompt="保持首帧构图并开始运动",
        reference_items=[],
        backend="newapi_video-fixture",
        gen_mode="imageToVideo",
    )

    assert out.exists()
    assert captured["gen_mode"] == "imageToVideo"


@pytest.mark.asyncio
async def test_freezone_video_gen_forwards_durable_provider_callbacks(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, dict] = {}
    received_logs: list[str] = []
    received_progress: list[float] = []
    received_events: list[dict[str, object]] = []

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            kwargs["on_log"]("上游视频任务已提交")
            kwargs["on_progress"](0.2)
            kwargs["on_task_event"](
                {
                    "stage": "submitted",
                    "provider_task_id": "provider-task-123",
                    "model": "fixture-video",
                    "preview_url": "https://cdn.example/provider-preview.mp4",
                }
            )
            output_path = Path(kwargs["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    out = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_callbacks",
        prompt="测试真实视频任务状态",
        reference_items=[],
        backend="newapi_seedance-1.0-pro-fast",
        resolution="480p",
        generate_audio=True,
        on_log=received_logs.append,
        on_progress=received_progress.append,
        on_task_event=received_events.append,
    )

    assert out.exists()
    assert captured["generate"]["project_output_dir"] == str(tmp_path)
    assert captured["generate"]["task_type"] == "freezone_video_gen"
    assert captured["generate"]["resolution"] == "480p"
    assert captured["generate"]["generate_audio"] is True
    assert received_logs == ["上游视频任务已提交"]
    assert received_progress == [0.2]
    assert received_events[0]["provider_task_id"] == "provider-task-123"
    assert (
        received_events[0]["preview_url"] == "https://cdn.example/provider-preview.mp4"
    )


@pytest.mark.asyncio
async def test_freezone_video_gen_marks_completed_download_failure_recoverable(
    monkeypatch, tmp_path: Path
):
    class FakeVideoGenerator:
        async def generate(self, **_kwargs):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                task_id="provider-video-download-1",
                error="Village Infinite Canvas API result download failed after 3 attempts: timeout",
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    with pytest.raises(CompletedVideoDownloadPending) as exc_info:
        await run_freezone_video_gen(
            project_dir=tmp_path,
            job_id="job_download_recovery",
            prompt="生成连贯镜头",
            reference_items=[],
            backend="newapi_jimeng-seedance-2.0-fast",
        )

    assert exc_info.value.provider_task_id == "provider-video-download-1"


@pytest.mark.asyncio
async def test_freezone_video_gen_keeps_submit_timeout_pending(
    monkeypatch, tmp_path: Path
):
    class FakeVideoGenerator:
        async def generate(self, **_kwargs):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="视频提交结果未知：渠道可能已经接收任务",
                error_metadata={"error_code": "VIDEO_SUBMIT_RESULT_UNKNOWN"},
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    with pytest.raises(VideoSubmissionPending) as exc_info:
        await run_freezone_video_gen(
            project_dir=tmp_path,
            job_id="job_submit_pending",
            prompt="保留原任务",
            reference_items=[],
            backend="newapi_video-fixture",
        )

    assert exc_info.value.idempotency_key.startswith("job_submit_pending-")


@pytest.mark.asyncio
async def test_freezone_video_gen_retains_safe_provider_request_contract(
    monkeypatch, tmp_path: Path
):
    request_contract = {
        "content_type": "application/json; charset=utf-8",
        "body_bytes": 321,
        "body_sha256": "0123456789abcdef",
        "payload_keys": ["model", "prompt", "metadata"],
        "metadata_keys": ["reference_images"],
        "media_counts": {"image": 2},
    }

    class FakeVideoGenerator:
        async def generate(self, **_kwargs):
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="HTTP 400 canonicalize JSON request body",
                error_metadata={
                    "error_code": "VIDEO_REQUEST_REJECTED",
                    "http_status": 400,
                    "request_contract": request_contract,
                },
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    with pytest.raises(FreezoneVideoGenerationError) as exc_info:
        await run_freezone_video_gen(
            project_dir=tmp_path,
            job_id="job_request_contract",
            prompt="生成连贯镜头",
            reference_items=[],
            backend="newapi_video-fixture",
        )

    assert exc_info.value.provider_error_metadata == {
        "error_code": "VIDEO_REQUEST_REJECTED",
        "http_status": 400,
        "request_contract": request_contract,
    }


@pytest.mark.asyncio
async def test_freezone_video_gen_does_not_implicitly_retry_another_provider(
    monkeypatch, tmp_path: Path
):
    create_calls: list[str] = []
    generate_calls: list[dict] = []

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="video_capacity_unavailable",
            )

    def fake_create_video_generator(**kwargs):
        backend = str(kwargs["backend"])
        create_calls.append(backend)
        return FakeVideoGenerator()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        fake_create_video_generator,
    )
    backends = _configure_direct_video_models(
        monkeypatch,
        tmp_path,
        "jimeng-seedance-2.0-fast",
        "s-videos-f-933-fast-480-2",
    )

    with pytest.raises(RuntimeError, match="video_capacity_unavailable"):
        await run_freezone_video_gen(
            project_dir=tmp_path,
            job_id="job_wokey_capacity_no_implicit_fallback",
            prompt="保持参考图构图并轻微运动",
            reference_items=[{"type": "image", "path": "fixture.png", "role": "首帧"}],
            backend=backends["jimeng-seedance-2.0-fast"],
        )

    assert create_calls == [backends["jimeng-seedance-2.0-fast"]]
    assert generate_calls[0]["idempotency_key"].startswith(
        "job_wokey_capacity_no_implicit_fallback-"
    )


@pytest.mark.asyncio
async def test_freezone_video_gen_preflights_reference_contract_before_generator(
    monkeypatch, tmp_path: Path
):
    create_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **kwargs: create_calls.append(kwargs),
    )

    from novelvideo.freezone.video_request_contract import VideoRequestContractError

    with pytest.raises(VideoRequestContractError) as exc_info:
        await run_freezone_video_gen(
            project_dir=tmp_path,
            job_id="job_contract_preflight",
            prompt="镜头持续 12 秒，并保持 @图片1 的角色外观",
            reference_items=[],
            duration_seconds=5,
            backend="newapi_video-fixture",
        )

    assert [issue.code for issue in exc_info.value.issues] == [
        "prompt_reference_missing",
    ]
    assert create_calls == []
    assert exc_info.value.provider_error_metadata["verification_stage"] == "contract"


@pytest.mark.asyncio
async def test_freezone_video_gen_resume_skips_new_prompt_preflight(
    monkeypatch, tmp_path: Path
):
    calls: dict[str, object] = {}

    class FakeNewApiVideoGenerator(NewApiVideoGenerator):
        def __init__(self):
            self.model = "fixture-video"

        async def generate(self, **_kwargs):
            raise AssertionError("recovery must not submit a second provider task")

        async def recover_task(self, **kwargs):
            calls.update(kwargs)
            output_path = Path(kwargs["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"recovered mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeNewApiVideoGenerator(),
    )

    out = await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_contract_resume",
        prompt="镜头持续 99 秒，并保持 @图片1 的角色外观",
        reference_items=[],
        duration_seconds=5,
        backend="newapi_video-fixture",
        resume_provider_task_id="provider-task-1",
    )

    assert out.exists()
    assert calls["task_id"] == "provider-task-1"


@pytest.mark.asyncio
async def test_freezone_video_gen_uses_marked_first_frame_ahead_of_character_sheet(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, dict] = {}

    class FakeVideoGenerator(NewApiVideoGenerator):
        def __init__(self):
            pass

        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            output_path = Path(kwargs["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake mp4")
            return VideoGenResult(
                status=VideoGenStatus.DONE, video_path=str(output_path)
            )

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: FakeVideoGenerator(),
    )

    await run_freezone_video_gen(
        project_dir=tmp_path,
        job_id="job_marked_first_frame",
        prompt="人物从门口继续向前走",
        reference_items=[
            {"type": "image", "path": "character-sheet.png", "role": "角色参考"},
            {"type": "image", "path": "previous-tail.png", "role": "首帧"},
        ],
        backend="newapi_video-fixture",
    )

    assert captured["generate"]["image_path"] == "previous-tail.png"
    assert [item.role for item in captured["generate"]["references"]] == [
        "角色参考",
        "首帧",
    ]


def test_video_generation_failure_surfaces_diagnostic_contract() -> None:
    message = _format_video_generation_failure(
        "HTTP 400 canonicalize JSON request body: unexpected end of JSON input",
        {
            "error_code": "VIDEO_RELAY_JSON_BODY_REJECTED",
            "stage": "submit",
            "http_status": 400,
            "suggested_action": "检查中转层是否截断请求体。",
        },
    )

    assert "VIDEO_RELAY_JSON_BODY_REJECTED" in message
    assert "阶段=submit" in message
    assert "HTTP=400" in message
    assert "检查中转层是否截断请求体" in message


def test_seedance2_model_selection_prefers_omni_model_for_mixed_references() -> None:
    generator = object.__new__(Seedance2VideoGenerator)

    assert (
        generator._select_generation_model(image_count=1, video_count=0, audio_count=0)
        == "seedance-2.0-i2v"
    )
    assert (
        generator._select_generation_model(image_count=1, video_count=1, audio_count=0)
        == "seedance-2.0"
    )
    assert (
        generator._select_generation_model(image_count=0, video_count=1, audio_count=0)
        == "seedance-2.0"
    )


@pytest.mark.parametrize(
    ("mode", "expected_kinds"),
    [
        ("textToVideo", []),
        ("imageToVideo", ["image"]),
        ("firstLastFrame", ["image", "image"]),
        ("allReference", ["image", "image", "video", "audio"]),
        ("imageReference", ["image", "image"]),
        ("videoEdit", ["video"]),
    ],
)
def test_seedance2_explicit_mode_filters_media_before_upload(
    tmp_path: Path, mode: str, expected_kinds: list[str]
) -> None:
    generator = object.__new__(Seedance2VideoGenerator)
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    video = tmp_path / "reference.mp4"
    audio = tmp_path / "reference.mp3"
    for path in (first, last, video, audio):
        path.write_bytes(b"fixture")

    image_path, last_frame_path, filtered = generator._filter_explicit_references(
        mode=mode,
        image_path=str(first),
        last_frame_path=str(last),
        references=[
            ShotReference("image", str(first), "首帧"),
            ShotReference("image", str(last), "尾帧"),
            ShotReference("video", str(video), "视频参考"),
            ShotReference("audio", str(audio), "音频参考"),
        ],
    )

    assert [item.type for item in filtered] == expected_kinds
    if mode == "textToVideo":
        assert image_path is None
        assert last_frame_path is None
    elif mode == "firstLastFrame":
        assert image_path == str(first)
        assert last_frame_path == str(last)
    elif mode in {"imageReference", "videoEdit"}:
        assert image_path is None
        assert last_frame_path is None


def test_seedance2_image_reference_stays_on_omni_model() -> None:
    generator = object.__new__(Seedance2VideoGenerator)

    assert (
        generator._select_generation_model(
            image_count=1,
            video_count=0,
            audio_count=0,
            explicit_mode="imageReference",
        )
        == "seedance-2.0"
    )


def test_huimeng_multimodal_reference_params_support_images_videos_and_audio(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "ref.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    video_path = tmp_path / "ref.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    audio_path = tmp_path / "ref.wav"
    audio_path.write_bytes(b"RIFFfakeWAVEfmt ")

    generator = object.__new__(HuimengVideoGenerator)
    params, counts = generator._build_reference_params(
        [
            ShotReference("image", str(image_path), "角色参考"),
            ShotReference("video", str(video_path), "动作参考"),
            ShotReference("audio", str(audio_path), "音频参考"),
        ],
        log=lambda _msg: None,
    )

    assert counts == {"image_count": 1, "video_count": 1, "audio_count": 1}
    assert params["reference_images"][0].startswith("data:image/png;base64,")
    assert params["reference_videos"][0].startswith("data:video/mp4;base64,")
    assert params["reference_audios"][0].startswith("data:audio/x-wav;base64,")


def test_validate_omni_reference_limits_and_summary() -> None:
    items = [{"type": "image", "url": f"/static/{i}.png"} for i in range(9)]
    items += [{"type": "video", "url": f"/static/{i}.mp4"} for i in range(3)]
    counts = summarize_omni_reference_counts(items)

    assert counts == {
        "image_count": 9,
        "video_count": 3,
        "audio_count": 0,
        "total_count": 12,
    }

    validate_omni_reference_limits(items)

    too_many_images = [{"type": "image", "url": f"/static/{i}.png"} for i in range(10)]
    try:
        validate_omni_reference_limits(too_many_images)
        raise AssertionError("expected validate_omni_reference_limits to fail")
    except ValueError as exc:
        assert "最多支持 9 个图片参考" in str(exc)


def test_validate_omni_reference_limits_uses_selected_model_contract() -> None:
    limits = {"image": 0, "video": 1, "audio": 0}

    validate_omni_reference_limits(
        [{"type": "video", "url": "/static/source.mp4"}],
        limits,
    )
    with pytest.raises(ValueError, match="不支持图片参考"):
        validate_omni_reference_limits(
            [{"type": "image", "url": "/static/reference.png"}],
            limits,
        )
    with pytest.raises(ValueError, match="最多支持 1 个视频参考"):
        validate_omni_reference_limits(
            [
                {"type": "video", "url": "/static/a.mp4"},
                {"type": "video", "url": "/static/b.mp4"},
            ],
            limits,
        )
    with pytest.raises(ValueError, match="不支持音频参考"):
        validate_omni_reference_limits(
            [{"type": "audio", "url": "/static/reference.mp3"}],
            limits,
        )
