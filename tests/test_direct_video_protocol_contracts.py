from __future__ import annotations

from dataclasses import replace

from novelvideo.generators.video.direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    OPENAI_VIDEO_CONTRACT,
    build_minimax_video_v2_payload,
    canvas_comes_from_the_media,
    endpoint_fallback_allowed,
    extract_result_url,
    extract_task_error,
    extract_task_id,
    extract_task_status,
    get_direct_video_protocol_contract,
    protocol_base_url,
    query_url,
    resolve_protocol_from_metadata,
    submit_url,
)


def test_minimax_protocol_is_resolved_from_model_metadata() -> None:
    assert resolve_protocol_from_metadata(["minimax:video_generation_v2"]) == (
        DIRECT_VIDEO_PROTOCOL_MINIMAX_V2
    )


def test_unknown_declared_protocol_is_not_misreported_as_openai_video() -> None:
    assert resolve_protocol_from_metadata(["provider:future_video_v9"]) == "unresolved"


def test_minimax_gateway_urls_are_siblings_of_v1_directory() -> None:
    contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2)
    base_url = "https://tokendance.space/gateway/v1"

    assert protocol_base_url(base_url, contract) == (
        "https://tokendance.space/gateway/minimax/v2"
    )
    assert submit_url(base_url, contract) == (
        "https://tokendance.space/gateway/minimax/v2/video_generation"
    )
    assert query_url(base_url, contract, "task-1") == (
        "https://tokendance.space/gateway/minimax/v2/query/video_generation/task-1"
    )


def test_minimax_v2_root_is_used_when_station_serves_it_at_origin() -> None:
    contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2)
    base_url = "https://dmc.cc/v1"

    assert protocol_base_url(base_url, contract) == "https://dmc.cc/v2"
    assert protocol_base_url("https://dmc.cc", contract) == "https://dmc.cc/v2"
    assert submit_url(base_url, contract) == "https://dmc.cc/v2/video_generation"
    assert query_url(base_url, contract, "task-2") == (
        "https://dmc.cc/v2/query/video_generation/task-2"
    )


def test_minimax_explicit_v2_directory_is_kept_verbatim() -> None:
    contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2)

    for base_url in (
        "https://host.test/v2",
        "https://host.test/gateway/v2",
        "https://host.test/gateway/minimax/v2",
    ):
        assert protocol_base_url(base_url, contract) == base_url


def test_directory_strategies_never_rewrite_a_declared_v2_root() -> None:
    contract = replace(OPENAI_VIDEO_CONTRACT, base_strategy="relay-directory")

    assert protocol_base_url("https://relay.test/api/v2", contract) == (
        "https://relay.test/api/v2"
    )
    assert protocol_base_url("https://relay.test/api/v1", contract) == (
        "https://relay.test/api/v1"
    )


def test_minimax_text_payload_includes_ratio_and_native_audio_is_implicit() -> None:
    payload = build_minimax_video_v2_payload(
        model="minimax-h3",
        prompt="cinematic fixture",
        resolution="2k",
        duration=6,
        ratio="16:9",
    )

    assert payload == {
        "model": "minimax-h3",
        "resolution": "2K",
        "duration": 6,
        "ratio": "16:9",
        "content": [{"type": "text", "text": "cinematic fixture"}],
    }
    assert "generate_audio" not in payload


def test_minimax_multimodal_payload_preserves_media_roles() -> None:
    payload = build_minimax_video_v2_payload(
        model="minimax-h3",
        prompt="fixture",
        resolution="768p",
        duration=8,
        ratio="9:16",
        first_frame_url="https://media.test/first.png",
        last_frame_url="https://media.test/last.png",
        reference_images=["https://media.test/reference.png"],
        reference_videos=["https://media.test/reference.mp4"],
        reference_audios=["https://media.test/reference.mp3"],
    )

    assert payload["resolution"] == "768P"
    # Frames/references are validated against the ``adaptive`` ratio family by
    # the native H3 service; a fixed canvas is rejected as an FL2VA ratio.
    assert payload["ratio"] == "adaptive"
    assert [item["role"] for item in payload["content"][1:]] == [
        "first_frame",
        "last_frame",
        "reference_image",
        "reference_video",
        "reference_audio",
    ]


def test_minimax_response_contract_reads_nested_task_fields() -> None:
    contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2)
    submitted = {"task_id": "task-1"}
    completed = {
        "task": {
            "status": "succeeded",
            "content": {"url": "https://media.test/result.mp4"},
        }
    }

    assert extract_task_id(submitted, contract) == "task-1"
    assert extract_task_status(completed, contract) == "succeeded"
    assert extract_result_url(completed, contract) == (
        "https://media.test/result.mp4"
    )


def test_openai_video_error_contract_reads_common_relay_shapes() -> None:
    contract = get_direct_video_protocol_contract("openai-video")
    payloads = [
        ({"status": "failed", "errorMessage": "invalid reference"}, "invalid reference"),
        ({"status": "failed", "failureReason": "quota exhausted"}, "quota exhausted"),
        ({"status": "failed", "data": {"error": {"message": "content rejected"}}}, "content rejected"),
        ({"status": "failed", "result": {"failure_message": "render crashed"}}, "render crashed"),
        ({"status": "failed", "response": {"task": {"detail": "worker lost"}}}, "worker lost"),
    ]

    for payload, expected in payloads:
        assert extract_task_error(payload, contract) == expected


def test_video_error_contract_redacts_credentials() -> None:
    contract = get_direct_video_protocol_contract("openai-video")
    error = extract_task_error(
        {"status": "failed", "error_message": "api_key=secret-value rejected"},
        contract,
    )

    assert "secret-value" not in error
    assert "[redacted]" in error


def test_video_error_contract_keeps_provider_error_code_when_message_is_missing() -> None:
    contract = get_direct_video_protocol_contract("openai-video")

    assert extract_task_error(
        {"status": "failed", "error": {"code": "CONTENT_REJECTED"}},
        contract,
    ) == "CONTENT_REJECTED"


def test_endpoint_fallback_only_accepts_path_mismatch_statuses() -> None:
    assert endpoint_fallback_allowed(404) is True
    assert endpoint_fallback_allowed(405) is True
    for status in (400, 401, 403, 415, 422, 429, 500, None):
        assert endpoint_fallback_allowed(status) is False


def test_minimax_v2_takes_the_canvas_from_the_media_not_from_the_ratio_field() -> None:
    """带素材时画幅由素材决定——节点上写的比例不生效。

    实测踩过：请求 9:16、参考图是方的，三镜齐出 768×768，画幅不符却找不出原因。
    这条断言把"写了也不会生效"钉在代码里，避免下一次再从成片里反推。
    """

    with_media = build_minimax_video_v2_payload(
        model="MiniMax-H3",
        prompt="x",
        resolution="768p",
        duration=4,
        ratio="9:16",
        reference_images=["a.png"],
    )
    text_only = build_minimax_video_v2_payload(
        model="MiniMax-H3",
        prompt="x",
        resolution="768p",
        duration=4,
        ratio="9:16",
    )

    assert with_media["ratio"] == "adaptive"
    assert text_only["ratio"] == "9:16"


def test_only_the_minimax_v2_protocol_derives_the_canvas_from_media() -> None:
    assert canvas_comes_from_the_media(DIRECT_VIDEO_PROTOCOL_MINIMAX_V2) is True
    assert canvas_comes_from_the_media(DIRECT_VIDEO_PROTOCOL_OPENAI) is False
    assert canvas_comes_from_the_media("") is False
    assert canvas_comes_from_the_media(None) is False
