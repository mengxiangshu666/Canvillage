from __future__ import annotations

from novelvideo.generators.video.upstream_profiles import (
    compiled_aspect_ratio,
    compiled_duration,
    compile_payload,
    resolve_profile,
)


def test_prompt_hubs_flex_uses_declared_duration_and_ratio_contract() -> None:
    profile = resolve_profile("kling-3.0-omni")
    payload = compile_payload(
        model_key="kling-3.0-omni",
        prompt="角色自然转身，镜头平稳跟拍。",
        duration_seconds=8,
        aspect_ratio="9:16",
        profile=profile,
    )

    assert profile.profile_id == "prompt_hubs_flex"
    assert payload == {
        "model": "kling-3.0-omni",
        "prompt": "角色自然转身，镜头平稳跟拍。",
        "duration": 8,
        "ratio": "9:16",
    }


def test_profile_readers_prevent_missing_field_crashes_for_new_models() -> None:
    assert compiled_duration({"duration_seconds": "7"}, 5) == 7
    assert compiled_duration({}, 5.1) == 6
    assert compiled_aspect_ratio({"aspect_ratio": "9:16"}, "16:9") == "9:16"
    assert compiled_aspect_ratio({}, "9:16") == "9:16"


def test_sd25_m_full_family_keeps_30_second_requests() -> None:
    profile = resolve_profile("sd-2.5-M-720P-v1")
    assert profile.profile_id == "sd2_5_m_full"
    payload = compile_payload(
        model_key="sd-2.5-M-720P-v1",
        prompt="赛博校园，黄昏走廊。",
        duration_seconds=30,
        aspect_ratio="16:9",
        profile=profile,
    )
    assert payload["duration"] == 30


def test_huabu_legacy_sd20_profiles_still_align_5_10_15() -> None:
    profile = resolve_profile("sd-2.0-fast-v1")
    assert profile.profile_id == "huabu"
    payload = compile_payload(
        model_key="sd-2.0-fast-v1",
        prompt="p",
        duration_seconds=30,
        aspect_ratio="16:9",
        profile=profile,
    )
    assert payload["duration"] == 15


def test_sd25_m_direct_profile_and_wire_profile_share_duration_choices() -> None:
    """能力档案（选择层）与出线档案（发送层）必须是同一组真值。"""

    from novelvideo.generators.video.direct_video_profiles import (
        resolve_direct_video_profile,
    )

    declared = resolve_direct_video_profile(
        "sd-2.5-M-720P-v1",
        base_url="https://dubai3000.xyz/v1",
        protocol="openai-video",
    )
    wire = resolve_profile("sd-2.5-M-720P-v1")
    assert wire.profile_id == "sd2_5_m_full"
    assert set(wire.duration_choices) == set(declared.duration)
