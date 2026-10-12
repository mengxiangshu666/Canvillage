"""SparkCine (hi.hi.run) OpenAI 兼容视频渠道的档案、尺寸槽位与出线报文回归。"""

from __future__ import annotations

import asyncio
import json

import pytest

from novelvideo.generators.video import direct_video_profiles
from novelvideo.generators.video.direct_video_profiles import (
    resolve_direct_video_profile,
)
from novelvideo.generators.video.direct_video_probe import (
    _builtin_video_profile_capability,
)
from novelvideo.generators.video.direct_video_protocol_contracts import (
    OPENAI_VIDEO_CONTRACT,
    extract_result_url,
)
from novelvideo.generators.video.newapi import _declared_size_slot_request
from novelvideo.generators.video_generator import (
    NewApiVideoError,
    NewApiVideoGenerator,
)


pytestmark = pytest.mark.m09

BASE_URL = "https://hi.hi.run/api/openai/v1"
# 站点任务记录里真实出现过的 7 个档位（回显口径「分辨率@比例」）。
# 入参口径是 "WxH" 像素，见 _WIRE_SIZES：站点对 size=720p@16:9 回 422，
# 对 size=1280x720 通过校验（2026-10-11 免费探测）。
MEASURED_SLOTS = (
    "720p@16:9",
    "720p@9:16",
    "768p@16:9",
    "768p@9:16",
    "1080p@16:9",
    "1080p@9:16",
    "1080p@1:1",
)

WIRE_SIZES = {
    "720p@16:9": "1280x720",
    "720p@9:16": "720x1280",
    "768p@16:9": "1366x768",
    "768p@9:16": "768x1366",
    "1080p@16:9": "1920x1080",
    "1080p@9:16": "1080x1920",
    "1080p@1:1": "1080x1080",
}


def test_sparkcine_profile_declares_measured_slots_instead_of_generic_fallback() -> None:
    profile = resolve_direct_video_profile("h3-video")
    assert profile.name == "sparkcine-h3-video"
    assert profile.family == "direct"
    # 原生目录声明了三个 mode（文生视频 / 图生视频 / 多模态参考）。
    assert [mode.value for mode in profile.modes] == [
        "text_to_video",
        "image_to_video",
        "reference_to_video",
    ]
    assert profile.size_slots == MEASURED_SLOTS
    assert profile.size_field == "size"
    assert profile.resolution == ("480p", "720p", "768p", "960p", "1080p")
    assert profile.aspect == ("16:9", "9:16", "21:9", "4:3", "1:1", "3:4")
    assert profile.reference_limits.reference_images == 9
    assert profile.reference_limits.input_images == 1
    assert profile.reference_limits.reference_videos == 1
    assert profile.reference_limits.reference_audios == 3
    assert profile.default_resolution == "768p"
    assert profile.default_aspect_ratio == "16:9"
    assert profile.default_duration_seconds == 5
    # 站点目录里 h3-video 的兄弟型号共用同一份合同，不能落回 generic 兜底。
    for sibling in ("h3-video-2", "h3-video-6", "h3-video-frames"):
        assert resolve_direct_video_profile(sibling).name == "sparkcine-h3-video"


def test_composite_size_slots_resolve_by_resolution_and_ratio() -> None:
    assert direct_video_profiles._size_slot_ratio("768p@16:9") == pytest.approx(16 / 9)
    assert direct_video_profiles._size_slot_ratio("1080p@1:1") == pytest.approx(1.0)
    profile = resolve_direct_video_profile("h3-video")
    # 复合槽位以前解析不出比例，竖屏请求会被第一个槽位吃掉。
    assert profile.resolve_size("9:16") == "720p@9:16"
    assert profile.resolve_size("1:1") == "1080p@1:1"
    assert profile.resolve_size("1080p@9:16") == "1080p@9:16"


def test_declared_size_slot_request_keeps_the_users_resolution() -> None:
    profile = resolve_direct_video_profile("h3-video")
    assert (
        _declared_size_slot_request(
            profile, {}, {"resolution": "1080p", "ratio": "9:16"}
        )
        == "1080p@9:16"
    )
    assert (
        _declared_size_slot_request(
            profile, {"video_resolution": "1080p"}, {"aspect_ratio": "16:9"}
        )
        == "1080p@16:9"
    )
    # 720p 仍在声明范围内：用户选的档位不能被同比例的第一个槽位吃掉。
    assert (
        _declared_size_slot_request(
            profile, {"video_resolution": "720p"}, {"aspect_ratio": "16:9"}
        )
        == "720p@16:9"
    )
    # 只给解析度时用档案默认比例补全，而不是退回第一个槽位。
    assert (
        _declared_size_slot_request(profile, {}, {"resolution": "1080p"})
        == "1080p@16:9"
    )
    # 站点没声明过的组合一概不算数。
    assert _declared_size_slot_request(profile, {}, {"resolution": "2k", "ratio": "9:16"}) == ""
    assert (
        _declared_size_slot_request(profile, {}, {"resolution": "1080p", "ratio": "4:3"})
        == ""
    )
    # 纯像素/纯比例槽位的渠道拼不出复合值，行为保持不变。
    generic = resolve_direct_video_profile("some-unknown-vendor-model")
    assert _declared_size_slot_request(generic, {}, {"resolution": "1080p", "ratio": "9:16"}) == ""


def test_probe_publishes_declared_size_slots_for_the_settings_panel() -> None:
    capability = _builtin_video_profile_capability(
        upstream_model="h3-video",
        base_url=BASE_URL,
        protocol="openai-video",
    )
    assert capability["source"] == "models-profile-contract"
    assert capability["verificationStatus"] == "contract-resolved"
    assert capability["modes"] == ["textToVideo", "imageToVideo", "allReference"]
    assert capability["sizeSlots"] == list(MEASURED_SLOTS)
    assert capability["sizeField"] == "size"
    assert capability["durationRange"] == [4, 30]
    assert capability["resolutionOptions"] == [
        "480p",
        "720p",
        "768p",
        "960p",
        "1080p",
    ]
    assert capability["aspectRatios"] == [
        "16:9",
        "9:16",
        "21:9",
        "4:3",
        "1:1",
        "3:4",
    ]


def test_result_url_is_read_from_the_nested_sparkcine_outputs() -> None:
    task = {
        "id": "d0703015-ff59-4e59-80e6-39fdb76aa9e3",
        "status": "completed",
        "sparkcine": {
            "outputs": [
                {
                    "asset_id": "a3d8fbf2",
                    "download_url": "https://hi.hi.run/files/a3d8fbf2.mp4",
                }
            ]
        },
    }
    assert extract_result_url(task, OPENAI_VIDEO_CONTRACT) == (
        "https://hi.hi.run/files/a3d8fbf2.mp4"
    )


@pytest.mark.asyncio
async def test_outgoing_body_keeps_only_fields_the_site_echoes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 400
        headers: dict[str, str] = {}

        async def text(self):
            return '{"error":{"message":"Some fields need attention.","code":"validation_failed"}}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.aiohttp.ClientSession",
        FakeSession,
    )
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=BASE_URL,
        model="h3-video",
        resolution="1080p",
        protocol="openai-video",
        create_path="/videos",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    # 这里刻意照抄 newapi_generate 组装的基础体（metadata 里带 resolution/ratio）。
    # 站点是严格校验：历史上被它接受的真实报文只有 model/prompt/seconds/size 四个键，
    # 多一个 metadata 包装就整包 422「Some fields need attention.」；size 又只认
    # "WxH" 像素写法，所以出线体必须是 1080x1920 而不是站点回显的 1080p@9:16。
    payload = {
        "model": "h3-video",
        "prompt": "一只猫在窗台上打哈欠",
        "seconds": "5",
        "metadata": {
            "resolution": "1080p",
            "ratio": "9:16",
            "watermark": False,
            "generate_audio": False,
        },
    }

    with pytest.raises(NewApiVideoError):
        await generator._post_json(f"{BASE_URL}/videos", payload, idempotency_key="job-1")

    assert str(captured["url"]) == f"{BASE_URL}/videos"
    body = json.loads(bytes(captured["data"]).decode("utf-8"))
    assert body == {
        "model": "h3-video",
        "prompt": "一只猫在窗台上打哈欠",
        "seconds": "5",
        "size": "1080x1920",
    }


def test_other_profiles_still_send_the_metadata_wrapper() -> None:
    """严格模式的开关只对声明过的档案生效，别的渠道报文形状不变。"""
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=BASE_URL,
        model="seedance-2.5",
        protocol="openai-video",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )

    body = generator._apply_capability_contract(
        {
            "model": "seedance-2.5",
            "prompt": "x",
            "seconds": "5",
            "metadata": {"watermark": False, "ratio": "16:9", "resolution": "720p"},
        }
    )

    assert body["metadata"]["watermark"] is False
    assert body["metadata"]["ratio"] == "16:9"




def test_every_declared_slot_has_a_pixel_form_for_the_wire() -> None:
    """回显口径与入参口径必须一一对应，漏一个就会在上游 422。"""
    profile = resolve_direct_video_profile("h3-video")
    assert dict(profile.size_wire_values) == WIRE_SIZES
    for slot in MEASURED_SLOTS:
        assert profile.wire_size_value(slot) == WIRE_SIZES[slot]
    # 没有映射的档案原样发出档位名，行为不变。
    generic = resolve_direct_video_profile("some-unknown-vendor-model")
    assert generic.wire_size_value("768p@16:9") == "768p@16:9"


def test_gemini_style_pixel_slots_are_sent_verbatim() -> None:
    """像素槽位的渠道本来就发 WxH，翻译层不能改动它们的报文。"""
    profile = resolve_direct_video_profile("gemini-omni-flash")
    assert profile.size_slots
    for slot in profile.size_slots:
        assert profile.wire_size_value(slot) == slot


def test_read_credential_probe_falls_back_to_the_contract_query_path() -> None:
    from novelvideo.generators.video.direct_video_probe import _video_task_read_path

    # 站点不发布 OpenAPI 文档；此时必须用合同里的查询模板，否则凭据校验根本
    # 发不出去，渠道会被显示成「凭据未验证」。
    assert _video_task_read_path(protocol="openai-video", openapi_task_path="") == (
        "/videos/{task_id}"
    )
    # 文档给的路由优先，其它协议没有这层兜底。
    assert _video_task_read_path(protocol="openai-video", openapi_task_path="/v1/tasks/{id}") == (
        "/v1/tasks/{id}"
    )
    assert _video_task_read_path(protocol="sparkcine-native", openapi_task_path="") == (
        "/generations/{task_id}"
    )
    assert _video_task_read_path(protocol="minimax-video-v2", openapi_task_path="") == ""


def test_read_credential_probe_accepts_the_sites_not_found_envelope(monkeypatch) -> None:
    import httpx

    from novelvideo.generators.video.direct_video_probe import (
        _probe_video_task_read_credential,
    )

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 404

        @staticmethod
        def json():
            # hi.hi.run 对不存在的任务返回这个信封（真身实测）。
            return {
                "error": {
                    "message": "Generation not found.",
                    "type": "not_found_error",
                    "code": "not_found",
                },
                "request_id": None,
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = _probe_video_task_read_credential(
        base_url=BASE_URL,
        api_key="sc_live_test",
        task_path="/videos/{task_id}",
        timeout=3.0,
    )
    assert result["status"] == "accepted"
    assert result["httpStatus"] == 404
    assert captured["url"].startswith(f"{BASE_URL}/videos/")
    assert "{task_id}" not in captured["url"]


def test_read_credential_probe_retries_with_a_dashed_uuid_id(monkeypatch) -> None:
    import httpx

    from novelvideo.generators.video.direct_video_probe import (
        _probe_video_task_read_credential,
        _task_read_probe_ids,
    )

    hex_id, dashed_id = _task_read_probe_ids()
    assert len(hex_id) == 32 and "-" not in hex_id
    assert dashed_id == (
        f"{hex_id[0:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"
    )

    seen: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def json(self):
            if self.status_code == 422:
                return {
                    "error": {
                        "message": "video_id must be a valid UUID.",
                        "type": "invalid_request_error",
                        "code": "validation_failed",
                    }
                }
            return {
                "error": {"message": "Generation not found.", "code": "not_found"}
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            seen.append(url)
            # 真身实测：32 位十六进制吃 422，带横线的 UUID 才回结构化 404。
            return FakeResponse(422 if len(url.rsplit("/", 1)[-1]) == 32 else 404)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = _probe_video_task_read_credential(
        base_url=BASE_URL,
        api_key="sc_live_test",
        task_path="/videos/{task_id}",
        timeout=3.0,
    )
    assert result["status"] == "accepted"
    assert result["httpStatus"] == 404
    assert len(seen) == 2
    first_id = seen[0].rsplit("/", 1)[-1]
    second_id = seen[1].rsplit("/", 1)[-1]
    assert len(first_id) == 32 and "-" not in first_id
    assert second_id.replace("-", "") == first_id


def test_read_credential_probe_retries_when_the_site_names_the_id_field(monkeypatch) -> None:
    """原生层的 422 文案是中文，只在 details.field 里点出 generationId。"""

    import httpx

    from novelvideo.generators.video.direct_video_probe import (
        _probe_video_task_read_credential,
    )

    seen: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def json(self):
            if self.status_code == 422:
                return {
                    "error": {
                        "code": "VALIDATION_FAILED",
                        "message": "部分内容需要检查。",
                        "details": {"field": "generationId"},
                    }
                }
            return {"error": {"code": "NOT_FOUND", "message": "未找到所请求的内容。"}}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            seen.append(url)
            return FakeResponse(422 if len(url.rsplit("/", 1)[-1]) == 32 else 404)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = _probe_video_task_read_credential(
        base_url="https://hi.hi.run/api/open/v1",
        api_key="sc_live_test",
        task_path="/generations/{task_id}",
        timeout=3.0,
    )

    # 32 位十六进制先吃 422，站点点名 generationId，才换带横线的 UUID。
    assert len(seen) == 2
    assert "-" not in seen[0].rsplit("/", 1)[-1]
    assert result["status"] == "accepted"
    assert result["httpStatus"] == 404


def test_read_credential_probe_keeps_unrelated_422_as_unverified(monkeypatch) -> None:
    import httpx

    from novelvideo.generators.video.direct_video_probe import (
        _probe_video_task_read_credential,
    )

    seen: list[str] = []

    class FakeResponse:
        status_code = 422

        @staticmethod
        def json():
            return {"error": {"message": "prompt is required", "code": "validation_failed"}}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            seen.append(url)
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = _probe_video_task_read_credential(
        base_url=BASE_URL,
        api_key="sc_live_test",
        task_path="/videos/{task_id}",
        timeout=3.0,
    )

    # 与 id 格式无关的 422 不能触发第二次请求，也不能算「凭据被接受」。
    assert len(seen) == 1
    assert result["status"] == "unverified"
    assert result["httpStatus"] == 422


# --- 原生协议（POST /api/open/v1/generations）：目录、模式、素材上传 ---------

NATIVE_BASE_URL = "https://hi.hi.run/api/openai/v1"
NATIVE_PARAMETERS = {
    "duration": 5,
    "resolution": "1080p",
    "aspect_ratio": "9:16",
    "generate_audio": False,
}
# GET {origin}/api/open/v1/models 的裁剪版：字段名照抄站点目录。
NATIVE_CATALOG = {
    "object": "list",
    "data": [
        {
            "id": "h3-video",
            "object": "model",
            "display_name": "H3 Video",
            "output": "video",
            "capability_revision": 4,
            "modes": {
                "text_to_video": {"display_name": "Text to video", "inputs": []},
                "image_to_video": {
                    "display_name": "Reference images to video",
                    "inputs": [
                        {
                            "role": "reference_image",
                            "media_type": "image",
                            "min": 1,
                            "max": 9,
                        }
                    ],
                },
                "multimodal_reference": {
                    "display_name": "Reference images and voice to video",
                    "inputs": [
                        {
                            "role": "reference_image",
                            "media_type": "image",
                            "min": 0,
                            "max": 9,
                        },
                        {
                            "role": "reference_audio",
                            "media_type": "audio",
                            "min": 0,
                            "max": 3,
                        },
                        {
                            "role": "reference_video",
                            "media_type": "video",
                            "min": 0,
                            "max": 1,
                        },
                    ],
                },
            },
            "parameters": {
                "duration": {
                    "default": 6,
                    "minimum": 4,
                    "maximum": 30,
                    "valueType": "integer",
                },
                "resolution": {
                    "default": "768p",
                    "valueType": "string",
                    "allowedValues": ["480p", "720p", "768p", "960p", "1080p"],
                },
                "aspect_ratio": {
                    "default": "16:9",
                    "valueType": "string",
                    "allowedValues": ["16:9", "9:16", "21:9", "4:3", "1:1", "3:4"],
                },
                "generate_audio": {"default": True, "valueType": "boolean"},
            },
            "parameter_constraints": [
                {
                    "when": {"resolution": "720p"},
                    "parameters": {"aspect_ratio": {"allowedValues": ["16:9", "9:16"]}},
                }
            ],
            "pricing": {
                "key": "video",
                "displayName": "视频",
                "mediaType": "video",
                "minimumCredits": 6,
                "perSecond": [{"resolution": "768p", "credits": 1.5}],
            },
        },
        {
            "id": "img-2-5s",
            "object": "model",
            "display_name": "Image",
            "output": "image",
        },
    ],
}


def _native_generator() -> NewApiVideoGenerator:
    """原生协议的生成器实例；参考素材解析沿用真实实现。"""

    return NewApiVideoGenerator(
        api_key="sc_live_test",
        endpoint=NATIVE_BASE_URL,
        model="h3-video",
        resolution="1080p",
        protocol="sparkcine-native",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )


class _FakeAioSession:
    """原生上传只要求会话能被关闭。"""

    def __init__(self, **_kwargs) -> None:
        pass

    async def close(self) -> None:
        return None


def _run_native_request(
    *,
    explicit_mode: str = "",
    image_path: str = "",
    last_frame_path: str = "",
    references: tuple[object, ...] = (),
) -> tuple[dict[str, object], str | None]:
    return asyncio.run(
        _native_generator()._sparkcine_native_request(
            explicit_mode=explicit_mode,
            parameters=dict(NATIVE_PARAMETERS),
            image_path=image_path,
            last_frame_path=last_frame_path,
            references=references,
            log=lambda _message: None,
        )
    )


@pytest.mark.parametrize(
    ("protocol", "base_url"),
    [
        ("sparkcine-native", NATIVE_BASE_URL),
        ("auto", NATIVE_BASE_URL),
        ("auto", "https://hi.hi.run"),
        ("auto", "https://hi.hi.run/api/open/v1"),
    ],
)
def test_native_probe_reads_the_sites_own_catalog(monkeypatch, protocol, base_url) -> None:
    import httpx

    from novelvideo.generators.video.direct_video_probe import probe_direct_video_model

    seen: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False)

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, url, **_kwargs):
            seen.append(url)
            if url.endswith("/models"):
                return FakeResponse(200, NATIVE_CATALOG)
            return FakeResponse(
                404,
                {"error": {"message": "Generation not found.", "code": "not_found"}},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = probe_direct_video_model(
        upstream_model="h3-video",
        base_url=base_url,
        api_key="sc_live_test",
        protocol=protocol,
        timeout=3.0,
    )

    assert result["ok"] is True
    assert result["protocol"] == "sparkcine-native"
    assert result["modelFound"] is True
    # 原生协议读站点自己的目录，不再去猜 /api/openai/v1 那一层。
    assert seen[0] == "https://hi.hi.run/api/open/v1/models"
    assert seen[1].startswith("https://hi.hi.run/api/open/v1/generations/")
    capability = result["capability"]
    assert capability["source"] == "sparkcine-native-catalog"
    assert capability["verificationStatus"] == "catalog-confirmed"
    assert capability["detectedProtocol"] == "sparkcine-native"
    assert capability["modes"] == ["textToVideo", "imageToVideo", "allReference"]
    assert capability["resolutionOptions"] == ["480p", "720p", "768p", "960p", "1080p"]
    assert capability["aspectRatios"] == ["16:9", "9:16", "21:9", "4:3", "1:1", "3:4"]
    assert capability["durationRange"] == [4, 30]
    assert capability["nativeAudio"] == "optional"
    assert result["adapterFamily"] == "sparkcine-native"
    assert result["credentialValidation"]["status"] == "accepted"
    assert result["capabilityDiscovery"]["status"] == "catalog"


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_native_probe_catalog_failure_does_not_claim_success(monkeypatch, status_code) -> None:
    import httpx

    from novelvideo.generators.video.direct_video_probe import probe_direct_video_model

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False)

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, url, **_kwargs):
            if url.endswith("/models"):
                return FakeResponse(status_code, {"error": {"message": "boom"}})
            pytest.fail("A failed catalog must stop before further requests")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = probe_direct_video_model(
        upstream_model="h3-video",
        base_url=NATIVE_BASE_URL,
        api_key="sc_live_test",
        protocol="sparkcine-native",
        timeout=3.0,
    )

    assert result["ok"] is False
    assert result["httpStatus"] == status_code
    assert not result.get("capability")


def test_native_request_without_assets_stays_text_to_video() -> None:
    request, error = _run_native_request()

    assert error is None
    assert request["mode"] == "text_to_video"
    assert request["inputs"] == []
    assert request["parameters"] == NATIVE_PARAMETERS


def test_native_request_uploads_reference_images_and_picks_image_to_video(
    monkeypatch,
) -> None:
    import aiohttp

    from novelvideo.generators.video import sparkcine_native

    uploads: list[dict[str, object]] = []

    async def _fake_upload(_session, **kwargs):
        uploads.append(kwargs)
        return f"asset-{len(uploads)}"

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeAioSession)
    monkeypatch.setattr(sparkcine_native, "upload_media_asset", _fake_upload)
    request, error = _run_native_request(
        references=({"type": "image", "path": "C:/tmp/ref.png"},)
    )

    assert error is None
    assert request["mode"] == "image_to_video"
    assert request["inputs"] == [{"asset_id": "asset-1", "role": "reference_image"}]
    # 站点只认自己的 asset_id，所以上传必须打到原生根上，并带上渠道密钥。
    assert uploads[0]["base_url"] == "https://hi.hi.run/api/open/v1"
    authorization = str(uploads[0]["authorization"])
    assert authorization.startswith("Bearer ")
    assert authorization.endswith("sc_live_test")
    assert uploads[0]["kind"] == "image"
    assert uploads[0]["media_value"] == "C:/tmp/ref.png"


def test_native_request_normalizes_canvas_reference_roles_for_multimodal_mode(
    monkeypatch,
) -> None:
    import aiohttp

    from novelvideo.generators.video import sparkcine_native

    async def _fake_upload(_session, **_kwargs):
        return "asset-1"

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeAioSession)
    monkeypatch.setattr(sparkcine_native, "upload_media_asset", _fake_upload)
    request, error = _run_native_request(
        explicit_mode="allReference",
        references=(
            {"type": "image", "path": "C:/tmp/ref.png", "role": "首帧参考"},
        ),
    )

    assert error is None
    assert request["mode"] == "multimodal_reference"
    assert request["inputs"] == [{"asset_id": "asset-1", "role": "reference_image"}]


def test_native_request_keeps_audio_references_on_multimodal_reference(
    monkeypatch,
) -> None:
    import aiohttp

    from novelvideo.generators.video import sparkcine_native

    kinds: list[str] = []

    async def _fake_upload(_session, **kwargs):
        kinds.append(str(kwargs["kind"]))
        return f"asset-{len(kinds)}"

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeAioSession)
    monkeypatch.setattr(sparkcine_native, "upload_media_asset", _fake_upload)
    request, error = _run_native_request(
        references=({"type": "audio", "path": "C:/tmp/voice.mp3"},)
    )

    assert error is None
    assert request["mode"] == "multimodal_reference"
    assert kinds == ["audio"]
    assert request["inputs"] == [{"asset_id": "asset-1", "role": "reference_audio"}]


def test_native_request_rejects_image_to_video_without_an_image() -> None:
    request, error = _run_native_request(explicit_mode="imageToVideo")

    assert request == {}
    assert error is not None
    assert "参考图" in error


def test_native_request_rejects_more_reference_images_than_the_site_allows() -> None:
    references = tuple(
        {"type": "image", "path": f"C:/tmp/ref{index}.png"} for index in range(10)
    )
    request, error = _run_native_request(references=references)

    assert request == {}
    assert error is not None
    assert "参考图 10 张" in error


def test_native_request_reports_upload_failures_readably(monkeypatch) -> None:
    import aiohttp

    from novelvideo.generators.video import sparkcine_native

    async def _failing_upload(_session, **_kwargs):
        raise sparkcine_native.SparkcineNativeError(
            "参考素材文件不存在：C:/tmp/missing.png", stage="upload"
        )

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeAioSession)
    monkeypatch.setattr(sparkcine_native, "upload_media_asset", _failing_upload)
    request, error = _run_native_request(image_path="C:/tmp/missing.png")

    assert request == {}
    assert error == "参考素材文件不存在：C:/tmp/missing.png"


def test_native_payload_shape_matches_the_published_contract() -> None:
    from novelvideo.generators.video import sparkcine_native

    payload = sparkcine_native.build_generation_payload(
        model="h3-video",
        prompt="一只猫在窗台上打哈欠",
        inputs=({"asset_id": "asset-1", "role": "reference_image"},),
        mode="image_to_video",
        parameters=dict(NATIVE_PARAMETERS),
        top_level={"route_goal": "best", "optimize_prompt": True},
    )

    assert payload == {
        "model": "h3-video",
        "prompt": "一只猫在窗台上打哈欠",
        "inputs": [{"asset_id": "asset-1", "role": "reference_image"}],
        "mode": "image_to_video",
        "parameters": NATIVE_PARAMETERS,
        "route_goal": "best",
        "optimize_prompt": True,
    }


def test_native_contract_keeps_mode_and_inputs_and_drops_openai_fields() -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=NATIVE_BASE_URL,
        model="h3-video",
        resolution="1080p",
        protocol="sparkcine-native",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    # 原生协议只有一个提交路由，不能按 OpenAI 习惯猜成 /video/generations。
    assert generator.create_path == "/generations"

    body = generator._apply_capability_contract(
        {
            "model": "h3-video",
            "prompt": "一只猫在窗台上打哈欠",
            "mode": "image_to_video",
            "inputs": [{"asset_id": "asset-1", "role": "reference_image"}],
            "parameters": dict(NATIVE_PARAMETERS),
            "metadata": {"resolution": "1080p", "ratio": "9:16"},
            # 上游流程会把 OpenAI 兼容层的字段一起塞进来；站点对未知键是整包
            # 拒绝（实测 unrecognized_keys: "images"），必须一个都不剩。
            "images": ["data:image/png;base64,AAAA"],
            "media_inputs": [{"kind": "image"}],
            "seconds": "5",
            "size": "1080p@9:16",
        }
    )

    assert body["mode"] == "image_to_video"
    assert body["inputs"] == [{"asset_id": "asset-1", "role": "reference_image"}]
    assert body["parameters"] == NATIVE_PARAMETERS
    # 原生协议只认合同里的顶层键，多一个都会 422。
    assert set(body) == {"model", "prompt", "inputs", "mode", "parameters", "metadata"}
    # OpenAI 兼容层那套顶层字段在原生协议里都不该出现。
    for key in ("seconds", "duration", "size", "ratio", "resolution", "images", "media_inputs"):
        assert key not in body
    # 站点要求 metadata 是字符串→字符串；保留空对象给提供方参数落脚。
    assert body["metadata"] == {}


def test_native_contract_fills_defaults_when_parameters_are_missing() -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint=NATIVE_BASE_URL,
        model="h3-video",
        protocol="sparkcine-native",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )

    body = generator._apply_capability_contract(
        {"model": "h3-video", "prompt": "x", "seconds": "8"}
    )

    assert body["parameters"]["duration"] == 8
    assert body["parameters"]["resolution"] == "768p"
    assert body["parameters"]["aspect_ratio"] == "16:9"
    assert "seconds" not in body


@pytest.mark.parametrize("protocol", ["auto", "sparkcine-native"])
def test_native_discovery_without_model_id_reads_only_video_catalog(monkeypatch, protocol) -> None:
    import httpx
    from novelvideo.generators.video.direct_video_probe import discover_direct_video_models

    original_client = httpx.Client
    requests: list[httpx.Request] = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://hi.hi.run/api/open/v1/models"
        return httpx.Response(200, json=NATIVE_CATALOG)

    monkeypatch.setattr(
        httpx, "Client",
        lambda **_kwargs: original_client(transport=httpx.MockTransport(respond)),
    )
    result = discover_direct_video_models(
        base_url=NATIVE_BASE_URL, api_key="test-key", protocol=protocol,
    )
    assert result["ok"] is True
    assert result["protocol"] == "sparkcine-native"
    assert [item["id"] for item in result["models"]] == ["h3-video"]
    assert not result["capability"]
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("model_id", "query_status", "expected_error"),
    [
        ("not-a-model", 404, "model_not_found"),
        ("img-2-5s", 404, "model_not_found"),
        ("h3-video", 401, "VIDEO_AUTH_REJECTED"),
        ("h3-video", 403, "VIDEO_AUTH_REJECTED"),
    ],
)
def test_native_probe_rejects_missing_video_or_query_auth(
    monkeypatch, model_id, query_status, expected_error,
) -> None:
    import httpx
    from novelvideo.generators.video.direct_video_probe import probe_direct_video_model

    original_client = httpx.Client

    def respond(request):
        assert request.method == "GET"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=NATIVE_CATALOG)
        return httpx.Response(
            query_status, json={"error": {"code": "not_found", "message": "Generation not found."}},
        )

    monkeypatch.setattr(
        httpx, "Client",
        lambda **_kwargs: original_client(transport=httpx.MockTransport(respond)),
    )
    result = probe_direct_video_model(
        upstream_model=model_id, base_url=NATIVE_BASE_URL, api_key="test-key",
    )
    assert result["ok"] is False
    assert result["protocol"] == "sparkcine-native"
    assert result["errorCode"] == expected_error
    assert not result["capability"]


def test_native_probe_network_failure_is_actionable(monkeypatch) -> None:
    from novelvideo.generators.video import direct_video_probe

    def unavailable(**_kwargs):
        raise OSError("private connection details")

    monkeypatch.setattr(direct_video_probe, "_sparkcine_native_catalog", unavailable)
    result = direct_video_probe.probe_direct_video_model(
        upstream_model="h3-video", base_url=NATIVE_BASE_URL, api_key="test-key",
    )
    assert result["ok"] is False
    assert result["protocol"] == "sparkcine-native"
    assert result["errorCode"] == "network_error"
    assert "private" not in result["error"]


def test_native_model_without_cached_capability_has_a_valid_local_contract(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators.video.direct_models import DirectVideoModel, direct_video_model_option

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS", "1")
    model = DirectVideoModel(
        registry_id="new-h3", label="H3", upstream_model="h3-video",
        base_url=NATIVE_BASE_URL, api_key="test-key", enabled=True,
        requested_protocol="sparkcine-native", protocol="sparkcine-native",
    )
    assert model.capability.reference_limits.input_images == 1
    assert direct_video_model_option(model)["runtimeReady"] is False


@pytest.mark.parametrize(
    ("base_url", "protocol"),
    [
        (NATIVE_BASE_URL, "openai-video"),
        ("https://hi.hi.run.example/api/openai/v1", "auto"),
        ("https://other.example/v1", "auto"),
    ],
)
def test_native_auto_route_does_not_override_other_protocols(monkeypatch, base_url, protocol) -> None:
    import httpx
    from novelvideo.generators.video.direct_video_probe import discover_direct_video_models

    original_client = httpx.Client
    seen: list[str] = []

    def respond(request):
        seen.append(str(request.url))
        assert request.method == "GET"
        return httpx.Response(200, json={"data": [{"id": "h3-video"}]})

    monkeypatch.setattr(
        httpx, "Client",
        lambda **_kwargs: original_client(transport=httpx.MockTransport(respond)),
    )
    result = discover_direct_video_models(
        base_url=base_url, api_key="test-key", protocol=protocol,
    )
    assert result["ok"] is True
    assert result["protocol"] == "openai-video"
    assert seen == [f"{base_url}/models"]


def test_native_http_probe_publishes_capability_and_revokes_failed_snapshot(monkeypatch, tmp_path) -> None:
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo import config
    from novelvideo.api.routes import model_gateway
    from novelvideo.generators.video.direct_models import direct_video_model_option, resolve_direct_video_model
    from novelvideo.model_gateway_settings import save_direct_video_models

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(model_gateway, "require_ce_direct_video_management", lambda: None)
    saved = save_direct_video_models([{
        "label": "H3", "modelId": "h3-video", "baseUrl": NATIVE_BASE_URL,
        "apiKey": "isolated-test-key", "protocol": "auto", "enabled": True,
    }])
    app = FastAPI()
    app.include_router(model_gateway.router)
    # Construct the local HTTP client before replacing upstream HTTP transport.
    with TestClient(app) as client:
        original_client = httpx.Client
        catalog_status = 200
        methods: list[str] = []

        def respond(request):
            methods.append(request.method)
            assert request.method == "GET"
            if request.url.path.endswith("/models"):
                return httpx.Response(catalog_status, json=NATIVE_CATALOG)
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

        monkeypatch.setattr(
            httpx, "Client",
            lambda **_kwargs: original_client(transport=httpx.MockTransport(respond)),
        )
        body = {
            "id": saved[0]["id"], "label": "H3", "modelId": "h3-video",
            "baseUrl": NATIVE_BASE_URL, "apiKey": "", "protocol": "auto", "enabled": True,
        }
        response = client.post("/model-gateway/direct-video-models/probe", json=body)
        assert response.status_code == 200
        assert response.json()["data"]["ok"] is True
        assert "isolated-test-key" not in response.text
        model = resolve_direct_video_model(f"direct_{saved[0]['id']}")
        assert model is not None
        assert model.effective_protocol == "sparkcine-native"
        option = direct_video_model_option(model)
        assert option["runtimeReady"] is True
        assert option["supportedModes"] == ["textToVideo", "imageToVideo", "allReference"]
        assert option["resolutionOptions"] == ["480p", "720p", "768p", "960p", "1080p"]
        catalog_status = 401
        rejected = client.post("/model-gateway/direct-video-models/probe", json=body)
        assert rejected.status_code == 200
        assert rejected.json()["data"]["ok"] is False
        assert "isolated-test-key" not in rejected.text
        model = resolve_direct_video_model(f"direct_{saved[0]['id']}")
        assert model is not None
        assert model.runtime_ready is False
        assert direct_video_model_option(model)["resolutionOptions"] == []
        assert methods == ["GET", "GET", "GET"]
