from __future__ import annotations

import json
import os

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from novelvideo import config, model_gateway_settings
from novelvideo.api.routes import model_gateway
from novelvideo.official_defaults import OFFICIAL_NEWAPI_BASE_URL
from novelvideo.model_gateway_settings import (
    DirectModelClearNotConfirmed,
    MODE_CUSTOM,
    MODE_OFFICIAL,
    MODE_UNIFIED,
    build_direct_models_status,
    build_newapi_database_status,
    build_model_gateway_status,
    get_direct_models,
    get_effective_cognee_embedding_config,
    get_effective_newapi_config,
    ensure_unified_gateway_migration,
    normalize_direct_model_id,
    normalize_relay_base_url,
    save_direct_models,
    save_official_newapi_key,
    save_custom_newapi_gateway,
    save_newapi_embedding_model_config,
    save_media_relay_config,
    save_newapi_database_config,
    save_newapi_provider_channels,
    set_model_gateway_mode,
)
from novelvideo.model_gateway_runtime import (
    AGENT_CACHE_TARGETS,
    _clear_agent_singletons,
    refresh_model_gateway_runtime,
)
from novelvideo.newapi_provisioner import (
    AdminToken,
    build_channel_payload,
    ensure_newapi_setup,
    get_provisioner_config,
    NewApiSetupCredentials,
    NewApiProvisionerConfig,
    normalize_admin_base_url,
    open_newapi_db,
    require_provisioner_enabled,
    update_provider_channel_credentials,
    upsert_channel,
)


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
        "VILLAGE_CANVAS_GATEWAY_API_KEY",
        "VILLAGE_CANVAS_GATEWAY_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_direct_image_status_exposes_canvas_capabilities(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_direct_models(
        "image",
        [
            {
                "label": "我的生图",
                "modelId": "gpt-image-2",
                "baseUrl": "https://image.example/v1",
                "apiKey": "sk-image-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    image_status = build_direct_models_status()["image"][0]

    assert image_status["label"] == "我的生图"
    assert image_status["configured"] is True
    assert image_status["apiKeyPreview"] != "sk-image-secret"
    assert image_status["supportedModes"] == ["textToImage", "imageToImage"]
    assert image_status["useCase"] == "已识别：文生图、图生图"
    assert image_status["parameterDefaults"] == {
        "resolution": "2K",
        "aspectRatio": "1:1",
        "strategy": "balanced",
    }


def test_gemini_image_auto_protocol_heals_saved_rows(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_direct_models(
        "image",
        [
            {
                "label": "Gemini 生图",
                "modelId": "gemini-3-pro-image",
                "baseUrl": "https://img.yunfei.best/v1",
                "apiKey": "sk-gemini-image-secret",
                "protocol": "auto",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    saved = get_direct_models("image")[0]
    assert saved["requestedProtocol"] == "auto"
    assert saved["protocol"] == "gemini-image"


def test_empty_direct_model_save_requires_server_side_confirmation(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_direct_models(
        "image",
        [
            {
                "label": "要保留的生图",
                "modelId": "gpt-image-2",
                "baseUrl": "https://image.example/v1",
                "apiKey": "sk-image-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    with pytest.raises(DirectModelClearNotConfirmed, match="confirmClear=true"):
        save_direct_models("image", [])
    assert [item["modelId"] for item in get_direct_models("image")] == ["gpt-image-2"]

    save_direct_models("image", [], confirm_clear=True)
    assert get_direct_models("image") == []
    # Saving an empty registry again is idempotent and needs no confirmation.
    save_direct_models("image", [])


def test_direct_model_api_maps_unconfirmed_clear_to_409(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_direct_models(
        "image",
        [
            {
                "label": "要保留的生图",
                "modelId": "gpt-image-2",
                "baseUrl": "https://image.example/v1",
                "apiKey": "sk-image-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    monkeypatch.setattr(
        model_gateway, "require_ce_direct_model_management", lambda: None
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        rejected = client.post(
            "/model-gateway/direct-models/image",
            json={"models": []},
        )
        assert rejected.status_code == 409
        assert "confirmClear=true" in rejected.json()["detail"]
        assert len(get_direct_models("image")) == 1

        accepted = client.post(
            "/model-gateway/direct-models/image",
            json={"models": [], "confirmClear": True},
        )

    assert accepted.status_code == 200
    assert accepted.json()["data"] == []
    assert get_direct_models("image") == []


def test_direct_model_ids_are_canonicalized_before_persisting(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    saved = save_direct_models(
        "vision",
        [
            {
                "id": "vision-existing",
                "label": "视觉模型",
                "modelId": "a/gemini-3-flash",
                "baseUrl": "https://vision.example/v1",
                "apiKey": "sk-vision-secret",
                "enabled": True,
                "isDefault": True,
            },
            {
                "id": "vision-legacy",
                "label": "旧视觉别名",
                "modelId": "DC-freezone-vision-fast-LLM",
                "baseUrl": "https://legacy.example/v1",
                "apiKey": "sk-legacy-secret",
                "enabled": False,
                "isDefault": False,
            },
        ],
    )

    assert normalize_direct_model_id("vision", "a/gemini-3-flash") == "gemini-3-flash"
    assert saved[0]["modelId"] == "gemini-3-flash"
    assert saved[1]["modelId"] == "gemini-3-flash"
    assert [item["modelId"] for item in get_direct_models("vision")] == [
        "gemini-3-flash",
        "gemini-3-flash",
    ]


def test_retired_channel_rows_are_flattened_without_losing_credentials(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings._write_many(
        {
            "direct_model_channels_v1": json.dumps(
                [
                    {
                        "id": "channel-legacy",
                        "label": "旧渠道",
                        "baseUrl": "https://relay.example/v1",
                        "apiKey": "sk-retired-secret",
                        "enabled": True,
                    }
                ]
            ),
            "direct_text_models": json.dumps(
                [
                    {
                        "id": "text-legacy",
                        "label": "旧文字模型",
                        "modelId": "text-v1",
                        "channelId": "channel-legacy",
                        "enabled": True,
                        "isDefault": True,
                        "verificationRequired": True,
                        "legacyCompatibility": False,
                    }
                ]
            ),
        }
    )

    first = get_direct_models("text")
    second = get_direct_models("text")
    persisted = model_gateway_settings.get_model_gateway_settings()
    raw_row = json.loads(persisted["direct_text_models"])[0]

    assert first == second
    assert first[0]["baseUrl"] == "https://relay.example/v1"
    assert first[0]["apiKey"] == "sk-retired-secret"
    assert persisted["direct_model_channels_v1"] == ""
    assert "channelId" not in raw_row
    assert "verificationRequired" not in raw_row
    assert "legacyCompatibility" not in raw_row


def test_direct_model_status_exposes_capabilities_for_every_non_video_family(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    inputs = {
        "chat": "deepseek-chat",
        "image": "gpt-image-2",
        "embedding": "text-embedding-3-small",
    }
    for kind, model_id in inputs.items():
        save_direct_models(
            kind,
            [
                {
                    "label": f"{kind} 模型",
                    "modelId": model_id,
                    "baseUrl": f"https://{kind}.example/v1",
                    "apiKey": f"sk-{kind}-secret",
                    "enabled": True,
                    "isDefault": True,
                }
            ],
        )

    status = build_direct_models_status()

    assert list(status) == ["chat", "agent", "text", "vision", "embedding", "image"]
    for alias in ("agent", "text", "vision"):
        assert status[alias] == status["chat"]
    for kind in inputs:
        item = status[kind][0]
        assert item["configured"] is True
        assert item["parameterDefaults"]
        assert item["parameter_defaults"] == item["parameterDefaults"]

    assert status["embedding"][0]["parameterDefaults"]["dimensions"] == 1536
    assert status["chat"][0]["supportsTools"] is True
    assert status["chat"][0]["supportsVision"] is True


def test_withdrawn_audio_family_stays_readable_but_cannot_be_saved(monkeypatch, tmp_path):
    """7e92f79 撤下音频族，但节点绑定和额度报价仍会传 ``kind="audio"``。

    撤下时只删了 ``DIRECT_MODEL_KINDS`` 里的 ``audio``，读取路径却照旧传
    ``"audio"``：``canonical_direct_model_kind`` 抛 ValueError，
    ``GET /generation-credit-cost?kind=beat_tts`` 变成 HTTP 500。只读身份补回来，
    写入侧必须继续关闭，否则模型中心会重新出现音频族。
    """

    _isolate_settings_db(monkeypatch, tmp_path)

    # 只读：状态列表里没有 audio，但查询本身不抛错。
    assert "audio" not in build_direct_models_status()
    assert get_direct_models("audio") == []

    # 写入：仍然拒绝，且带上与「未知族」不同的原因。
    with pytest.raises(ValueError, match="retired"):
        save_direct_models("audio", [])

    with pytest.raises(ValueError, match="unsupported direct model kind"):
        save_direct_models("nonsense", [])


def test_saved_audio_rows_keep_their_declared_modes(monkeypatch):
    """已保存的音频行必须保留 ``supportedModes``。

    旧音频注册表用每行的 ``supportedModes`` 区分「语音模型」与「音乐模型」；
    读取时丢掉它会让 ``ensure_direct_model_supports_mode`` 只能拿静态默认值
    ``("text_to_speech",)`` 比较，于是所有音乐请求都被判「不支持当前模式」。
    """

    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        direct_models,
        "get_direct_models",
        lambda _kind: [
            {
                "id": "audio-music",
                "label": "音乐模型",
                "modelId": "music-v1",
                "baseUrl": "https://audio.example/v1",
                "apiKey": "placeholder",
                "enabled": True,
                "isDefault": True,
                "supportedModes": ["text_to_music"],
            }
        ],
    )
    # 上游没有声明 supportedModes 时，保存的每行声明就是唯一依据。
    monkeypatch.setattr(
        direct_models,
        "direct_model_capability_summary",
        lambda *_args, **_kwargs: {"supportedModes": ["text_to_speech"]},
    )

    rows = direct_models.list_direct_models("audio")
    assert [row.supported_modes for row in rows] == [("text_to_music",)]
    assert direct_models.resolved_direct_model_modes(rows[0]) == ("text_to_music",)
    direct_models.ensure_direct_model_supports_mode(rows[0], "text_to_music")


@pytest.mark.parametrize(
    ("kind", "base_url", "model_id", "expected_protocol", "runtime_ready"),
    [
            ("text", "https://opencode.ai/zen/go/v1", "deepseek-v4-flash", "openai-compatible", True),
            # agent/text/vision are aliases of the unified chat family, so an
            # Anthropic endpoint is runtime-ready for the row itself.
            ("agent", "https://api.anthropic.com/v1", "claude-4-sonnet", "anthropic-messages", True),
        ("text", "https://api.anthropic.com/v1", "claude-4-sonnet", "anthropic-messages", True),
        ("vision", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-pro", "gemini", True),
        ("vision", "https://aiwble.com", "gemini-3.6-flash", "openai-compatible", True),
        ("agent", "https://relay.example/v1", "claude-4-sonnet", "openai-compatible", True),
        ("text", "http://127.0.0.1:11434/v1", "qwen3:8b", "ollama-openai", True),
    ],
)
def test_direct_model_protocol_auto_detection(
    monkeypatch,
    tmp_path,
    kind,
    base_url,
    model_id,
    expected_protocol,
    runtime_ready,
):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_direct_models(
        kind,
        [
            {
                "label": "协议测试模型",
                "modelId": model_id,
                "baseUrl": base_url,
                "apiKey": "sk-protocol-secret",
                "protocol": "auto",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    item = build_direct_models_status()[kind][0]

    assert item["protocol"] == expected_protocol
    assert item["requestedProtocol"] == "auto"
    assert item["protocolLabel"]
    assert item["runtimeReady"] is runtime_ready
    assert item["runtime_ready"] is runtime_ready
    assert item["apiKeyPreview"] != "sk-protocol-secret"


def test_direct_video_protocol_auto_without_probe_fails_closed(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    saved = model_gateway_settings.save_direct_video_models(
        [
            {
                "label": "未知视频协议",
                "modelId": "future-video-v9",
                "baseUrl": "https://relay.example/v1",
                "apiKey": "sk-video-secret",
                "protocol": "auto",
                "enabled": True,
                "isDefault": True,
            }
        ]
    )
    status = model_gateway_settings.build_direct_video_models_status()[0]

    assert saved[0]["protocol"] == "unresolved"
    assert status["effectiveProtocol"] == "unresolved"
    assert status["runtimeReady"] is False
    assert status["disabled"] is True
    assert "检测" in status["disabledReason"]


def test_direct_model_protocol_override_is_persisted(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_direct_models(
        "text",
        [
            {
                "label": "强制 Claude",
                "modelId": "my-alias",
                "baseUrl": "https://proxy.example/v1",
                "apiKey": "sk-force-secret",
                "protocol": "anthropic-messages",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    item = build_direct_models_status()["text"][0]

    assert item["protocol"] == "anthropic-messages"
    assert item["runtimeReady"] is True
    assert item["requestedProtocol"] == "anthropic-messages"


def test_direct_model_gateway_root_is_normalized_to_v1(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_direct_models(
        "vision",
        [
            {
                "label": "aiwble Gemini 视觉",
                "modelId": "gemini-3.6-flash",
                "baseUrl": "https://aiwble.com",
                "apiKey": "sk-protocol-secret",
                "protocol": "gemini",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    item = build_direct_models_status()["vision"][0]

    assert item["baseUrl"] == "https://aiwble.com/v1"
    assert item["protocol"] == "openai-compatible"
    assert item["requestedProtocol"] == "gemini"


def test_direct_model_blank_key_reuses_secret_only_for_the_same_endpoint(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    saved = save_direct_models(
        "text",
        [
            {
                "label": "文字模型",
                "modelId": "model-v1",
                "baseUrl": "https://first.example/v1/",
                "apiKey": "sk-first-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    updated = save_direct_models(
        "text",
        [
            {
                "id": saved[0]["id"],
                "label": "文字模型 v2",
                "modelId": "model-v2",
                "baseUrl": "https://first.example/v1",
                "apiKey": "",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    assert updated[0]["apiKey"] == "sk-first-secret"


def test_direct_model_blank_key_is_rejected_after_endpoint_change(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    saved = save_direct_models(
        "agent",
        [
            {
                "label": "Agent 模型",
                "modelId": "agent-v1",
                "baseUrl": "https://first.example/v1",
                "apiKey": "sk-first-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="apiKey is required"):
        save_direct_models(
            "agent",
            [
                {
                    "id": saved[0]["id"],
                    "label": "Agent 模型",
                    "modelId": "agent-v1",
                    "baseUrl": "https://second.example/v1",
                    "apiKey": "",
                    "enabled": True,
                    "isDefault": True,
                }
            ],
        )

    assert get_direct_models("agent")[0]["apiKey"] == "sk-first-secret"


def test_direct_model_probe_does_not_reuse_key_for_another_endpoint(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    saved = save_direct_models(
        "agent",
        [
            {
                "label": "Agent 模型",
                "modelId": "agent-v1",
                "baseUrl": "https://first.example/v1",
                "apiKey": "sk-first-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    monkeypatch.setattr(model_gateway, "require_ce_direct_model_management", lambda: None)
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-models/agent/probe",
            json={
                "id": saved[0]["id"],
                "label": "Agent 模型",
                "modelId": "agent-v1",
                "baseUrl": "https://second.example/v1",
                "apiKey": "",
                "protocol": "auto",
                "enabled": True,
                "isDefault": True,
            },
        )

    assert response.status_code == 400
    assert "apiKey is required" in response.text


def test_image_probe_failure_revokes_previous_upstream_capability(
    monkeypatch, tmp_path
):
    """图片模型探测失败时，上一轮从上游读到的能力必须作废，不能继续挂在节点目录上。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    saved = save_direct_models(
        "image",
        [
            {
                "label": "上游图片模型",
                "modelId": "gpt-image-1k-th",
                "baseUrl": "https://relay.example/v1",
                "apiKey": "sk-image-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    monkeypatch.setattr(model_gateway, "require_ce_direct_model_management", lambda: None)

    from novelvideo.generators import direct_model_probe
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    body = {
        "id": saved[0]["id"],
        "label": "上游图片模型",
        "modelId": "gpt-image-1k-th",
        "baseUrl": "https://relay.example/v1",
        "apiKey": "",
        "protocol": "auto",
        "enabled": True,
        "isDefault": True,
    }

    monkeypatch.setattr(
        direct_model_probe,
        "probe_direct_model_endpoint",
        lambda **_kwargs: {
            "ok": True,
            "modelFound": True,
            "discoveredModelCount": 1,
            "protocol": "openai-images",
            "detectedProtocol": "openai-images",
            "verificationStatus": "contract-resolved",
            "modelMetadata": {
                "supported_aspect_ratios": ["7:5"],
                "supported_image_sizes": ["2K"],
            },
            "models": [],
        },
    )
    with TestClient(app) as client:
        first = client.post("/model-gateway/direct-models/image/probe", json=body)

    assert first.status_code == 200
    cached = get_cached_direct_model_capability(
        base_url="https://relay.example/v1",
        kind="image",
        upstream_model="gpt-image-1k-th",
    )
    assert cached["modelMetadata"]["supported_aspect_ratios"] == ["7:5"]
    assert first.json()["data"]["capabilities"]["aspectRatioSource"] == "upstream"

    monkeypatch.setattr(
        direct_model_probe,
        "probe_direct_model_endpoint",
        lambda **_kwargs: {
            "ok": False,
            "modelFound": False,
            "discoveredModelCount": 0,
            "protocol": "openai-images",
            "error": "上游连接失败",
        },
    )
    with TestClient(app) as client:
        second = client.post("/model-gateway/direct-models/image/probe", json=body)

    assert second.status_code == 200
    data = second.json()["data"]
    assert data["ok"] is False
    assert data["modelFound"] is False
    assert data["capabilities"]["runtimeReady"] is False
    # 上一轮的上游能力必须消失：不能残留 7:5/2K，比例回落到本地合同并如实标注。
    assert "7:5" not in json.dumps(data, ensure_ascii=False)
    assert data["capabilities"]["aspectRatioSource"] == "profile"
    refreshed = get_cached_direct_model_capability(
        base_url="https://relay.example/v1",
        kind="image",
        upstream_model="gpt-image-1k-th",
    )
    assert refreshed["modelMetadata"] == {}
    assert refreshed["modelFound"] is False
    assert refreshed["verificationStatus"] == "degraded"


def test_direct_model_discovery_api_returns_only_public_catalog_data(monkeypatch):
    monkeypatch.setattr(model_gateway, "require_ce_direct_model_management", lambda: None)
    captured: dict[str, str] = {}

    def fake_discover(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "protocol": "openai-compatible",
            "discoveredModelCount": 1,
            "models": [{"id": "deepseek-v4", "metadata": {"tags": ["text"]}}],
        }

    from novelvideo.generators import direct_model_probe

    monkeypatch.setattr(direct_model_probe, "discover_direct_models", fake_discover)
    app = FastAPI()
    app.include_router(model_gateway.router)

    with TestClient(app) as client:
        response = client.post(
            "/model-gateway/direct-models/text/discover",
            json={
                "baseUrl": "https://relay.example/v1",
                "apiKey": "discovery-secret",
                "protocol": "auto",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["models"][0]["id"] == "deepseek-v4"
    assert captured["api_key"] == "discovery-secret"
    assert "discovery-secret" not in response.text


def test_model_gateway_uses_explicit_custom_mode(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        admin_base_url="http://127.0.0.1:3000",
        token_name="village-canvas-ce-runtime",
        token_id=3,
        activate=True,
    )

    effective = get_effective_newapi_config(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert effective.mode == MODE_CUSTOM
    assert effective.base_url == "http://127.0.0.1:3000/v1"
    assert effective.api_key == "sk-custom-secret"


def test_unified_gateway_migration_prefers_launcher_hk_route(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("VILLAGE_CANVAS_GATEWAY_BASE_URL", "http://10.66.66.1:3000/v1")
    monkeypatch.setenv("VILLAGE_CANVAS_GATEWAY_API_KEY", "unified-test-token")
    save_official_newapi_key(api_key="stale-official-token", activate=True)
    save_custom_newapi_gateway(
        base_url="http://156.245.244.194:3000/v1",
        api_key="stale-public-token",
        activate=True,
    )

    effective = ensure_unified_gateway_migration()

    assert effective.mode == MODE_UNIFIED
    assert effective.source == "unified-environment"
    assert effective.base_url == "http://10.66.66.1:3000/v1"
    assert effective.api_key == "unified-test-token"
    assert get_effective_newapi_config().base_url == "http://10.66.66.1:3000/v1"


def test_newapi_runtime_credentials_prefer_saved_custom_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://env.example/v1")

    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        admin_base_url="http://127.0.0.1:3000",
        token_name="village-canvas-ce-runtime",
        token_id=3,
        activate=True,
    )

    api_key, base_url = config.get_newapi_runtime_credentials()

    assert api_key == "sk-custom-secret"
    assert base_url == "http://127.0.0.1:3000/v1"


def test_newapi_runtime_credentials_allow_explicit_override(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    api_key, base_url = config.get_newapi_runtime_credentials(
        api_key_override="sk-request-secret",
        base_url_override="https://request.example/v1",
    )

    assert api_key == "sk-request-secret"
    assert base_url == "https://request.example/v1"


def test_legacy_pydantic_factory_uses_ce_gateway_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "0")
    monkeypatch.setenv("MODEL_API_KEY", "sk-stale-env-secret")
    monkeypatch.setenv("MODEL_BASE_URL", "https://stale-env.example/v1")
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-database-secret",
        activate=True,
    )
    captured: dict[str, object] = {}

    def fake_model(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return "newapi-model"

    monkeypatch.setattr(config, "_newapi_text_openai_model", fake_model)

    result = config.get_pydantic_model(
        provider_override="openrouter",
        model_name_override="openrouter/DC-legacy-agent-LLM",
    )

    assert result == "newapi-model"
    assert captured["model_name"] == "DC-legacy-agent-LLM"
    assert captured["api_key"] == "sk-database-secret"
    assert captured["base_url"] == "http://new-api:3000/v1"
    assert captured["timeout_seconds"] == 300.0


def test_legacy_pydantic_factory_uses_ee_deployment_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "0")
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    monkeypatch.setattr(config, "NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setattr(config, "NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    captured: dict[str, object] = {}

    def fake_model(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return "newapi-model"

    monkeypatch.setattr(config, "_newapi_text_openai_model", fake_model)

    result = config.get_pydantic_model(model_name_override="DC-legacy-agent-LLM")

    assert result == "newapi-model"
    assert captured["model_name"] == "DC-legacy-agent-LLM"
    assert captured["api_key"] == "sk-ee-secret"
    assert captured["base_url"] == "https://ee-gateway.example/v1"


def test_legacy_pydantic_model_settings_match_newapi_transport(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")

    settings = config.get_pydantic_model_settings(
        provider_override="openrouter",
        thinking_level_override="low",
    )

    assert settings == {"openai_reasoning_effort": "low"}


def test_cognee_newapi_resolution_prefers_saved_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "0")
    monkeypatch.delenv("COGNEE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("COGNEE_LLM_MODEL", raising=False)
    monkeypatch.delenv("NEWAPI_BASE_URL", raising=False)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")

    save_custom_newapi_gateway(
        base_url="https://custom.example",
        api_key="sk-custom-secret",
        activate=True,
    )

    from novelvideo.cognee import config as cognee_config

    assert cognee_config._resolve_llm_provider() == "newapi"
    assert (
        cognee_config._resolve_llm_api_key("newapi", "openai/DC-model")
        == "sk-custom-secret"
    )
    assert (
        cognee_config._get_endpoint_env("newapi", "COGNEE_LLM_ENDPOINT", "LLM_ENDPOINT")
        == "https://custom.example/v1"
    )


def test_cognee_provider_env_cannot_bypass_newapi(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    monkeypatch.setenv("COGNEE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_LLM_API_KEY", "direct-secret")
    monkeypatch.setattr(
        cognee_config,
        "_effective_newapi_gateway",
        lambda: ("gateway-secret", "https://gateway.example/v1"),
    )

    assert cognee_config._resolve_llm_provider() == "newapi"
    assert (
        cognee_config._resolve_llm_api_key("newapi", "DC-cognee-LLM")
        == "gateway-secret"
    )


def test_cognee_embedding_provider_env_cannot_bypass_newapi(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("COGNEE_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-cognee-embedding")

    effective = get_effective_cognee_embedding_config(llm_provider="gemini")

    assert effective.source == "unconfigured"
    assert effective.provider == ""
    assert effective.model == ""
    assert effective.dimensions == ""


def test_ee_cognee_embedding_ignores_ce_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="stale-ce-embedding",
        dimension=3072,
    )
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-ee-embedding")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "1536")

    effective = get_effective_cognee_embedding_config()

    assert effective.source == "environment"
    assert effective.provider == "newapi"
    assert effective.model == "DC-ee-embedding"
    assert effective.dimensions == "1536"
    assert effective.upstream_model == ""


def test_model_gateway_can_switch_back_to_official(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000/v1",
        api_key="sk-custom-secret",
        activate=True,
    )
    set_model_gateway_mode(MODE_OFFICIAL)

    effective = get_effective_newapi_config(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert effective.mode == MODE_OFFICIAL
    assert effective.base_url == OFFICIAL_NEWAPI_BASE_URL
    assert effective.api_key == "sk-official-secret"

    status = build_model_gateway_status(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert status["custom"]["configured"] is True
    assert status["effective"]["source"] == "official"


def test_model_gateway_status_keeps_official_section_when_custom_is_active(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    status = build_model_gateway_status(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )

    assert status["mode"] == MODE_CUSTOM
    assert status["effective"]["source"] == "custom"
    assert status["effective"]["baseUrl"] == "http://new-api:3000/v1"
    assert status["official"]["baseUrl"] == OFFICIAL_NEWAPI_BASE_URL
    assert status["official"]["source"] == "database"


def test_model_gateway_official_database_key_overrides_env(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-user-official-secret",
        activate=True,
    )

    effective = get_effective_newapi_config(
        official_base_url="https://env-official.example/v1",
        official_api_key="sk-env-official-secret",
    )
    assert effective.mode == MODE_OFFICIAL
    assert effective.base_url == OFFICIAL_NEWAPI_BASE_URL
    assert effective.api_key == "sk-user-official-secret"

    status = build_model_gateway_status(
        official_base_url="https://env-official.example/v1",
        official_api_key="sk-env-official-secret",
    )
    assert status["official"]["source"] == "database"
    assert status["official"]["environment"]["configured"] is False


def test_model_gateway_official_url_ignores_newapi_base_url_env(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(api_key="sk-database-secret", activate=True)
    monkeypatch.setenv("MODEL_GATEWAY_MODE", MODE_OFFICIAL)
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://malicious.example/v1")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")

    effective = get_effective_newapi_config()

    assert effective.mode == MODE_OFFICIAL
    assert effective.base_url == OFFICIAL_NEWAPI_BASE_URL
    assert effective.api_key == "sk-database-secret"


def test_ce_gateway_does_not_fall_back_to_env_after_database_is_initialized(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_OFFICIAL)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-later-secret")

    effective = get_effective_newapi_config()
    api_key, base_url = config.get_newapi_runtime_credentials()

    assert effective.api_key == ""
    assert api_key == ""
    assert base_url == OFFICIAL_NEWAPI_BASE_URL


def test_ee_gateway_uses_environment_and_ignores_ce_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(api_key="sk-ce-secret", activate=True)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")

    effective = get_effective_newapi_config()

    assert effective.mode == MODE_OFFICIAL
    assert effective.source == "environment"
    assert effective.base_url == "https://ee-gateway.example/v1"
    assert effective.api_key == "sk-ee-secret"

    status = build_model_gateway_status()
    assert status["effective"]["baseUrl"] == "https://ee-gateway.example/v1"
    assert status["official"]["source"] == "environment"


def test_ee_cannot_mutate_ce_model_gateway_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    response = TestClient(app).post(
        "/model-gateway/official/config",
        json={"newApiApiKey": "sk-should-not-be-saved"},
    )

    assert response.status_code == 403
    assert "only available in CE" in response.json()["detail"]


def test_ce_runtime_refresh_never_mutates_process_environment(monkeypatch, tmp_path):
    from novelvideo.agents import global_video_optimizer

    _isolate_settings_db(monkeypatch, tmp_path)
    tracked = {
        "MODEL_GATEWAY_RUNTIME_VERSION": "startup-version",
        "NEWAPI_API_KEY": "startup-newapi-key",
        "NEWAPI_BASE_URL": "https://startup.example/v1",
        "OPENAI_API_KEY": "startup-openai-key",
        "OPENAI_BASE_URL": "https://startup-openai.example/v1",
        "LLM_API_KEY": "startup-llm-key",
        "LLM_ENDPOINT": "https://startup-llm.example/v1",
        "EMBEDDING_API_KEY": "startup-embedding-key",
        "EMBEDDING_ENDPOINT": "https://startup-embedding.example/v1",
    }
    for key, value in tracked.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(global_video_optimizer, "_global_video_optimizer", object())
    save_official_newapi_key(api_key="sk-database-secret", activate=True)

    runtime = refresh_model_gateway_runtime()

    assert runtime["configured"] is True
    assert {key: os.environ.get(key) for key in tracked} == tracked
    assert global_video_optimizer._global_video_optimizer is None
    assert (
        "novelvideo.agents.global_video_optimizer._global_video_optimizer"
        in runtime["clearedCaches"]
    )


def test_agent_cache_targets_match_producing_modules():
    """每个缓存名都必须在生产模块里真实存在。

    这条用例防的是「改名漂移」：`_clear_agent_singletons` 对不存在的名字是
    **静默跳过**，所以一旦上游把缓存改名，失效逻辑会假装成功。此前 targets 写的
    `_translation_agent` / `_story_script_agent` 就是错的（真名是复数 dict），
    用户换完模型/密钥后脚本节点继续用旧 agent 直到重启。
    """

    import novelvideo.agents.global_video_optimizer as global_video_optimizer
    import novelvideo.freezone.text_node as text_node

    modules = {
        "novelvideo.freezone.text_node": text_node,
        "novelvideo.agents.global_video_optimizer": global_video_optimizer,
    }
    for module_name, attrs in AGENT_CACHE_TARGETS.items():
        module = modules[module_name]
        for attr in attrs:
            assert hasattr(module, attr), f"{module_name}.{attr} 不存在，缓存失效会静默跳过"

    # 钉住旧错名不再出现（否则改名回退时用例仍然全绿）。
    assert "_translation_agent" not in AGENT_CACHE_TARGETS["novelvideo.freezone.text_node"]
    assert "_story_script_agent" not in AGENT_CACHE_TARGETS["novelvideo.freezone.text_node"]


def test_clear_agent_singletons_empties_plural_dict_caches():
    import novelvideo.agents.global_video_optimizer as global_video_optimizer
    import novelvideo.freezone.text_node as text_node

    text_node._translation_agents["k"] = object()
    text_node._story_script_agents["k"] = object()
    text_node._vision_story_script_agents["k"] = object()
    global_video_optimizer._global_video_optimizer = object()

    cleared = _clear_agent_singletons()

    assert text_node._translation_agents == {}
    assert text_node._story_script_agents == {}
    assert text_node._vision_story_script_agents == {}
    assert global_video_optimizer._global_video_optimizer is None
    for attr in (
        "_translation_agents",
        "_story_script_agents",
        "_vision_story_script_agents",
    ):
        assert f"novelvideo.freezone.text_node.{attr}" in cleared


def test_newapi_base_url_normalizers_keep_admin_and_relay_urls_separate():
    assert normalize_admin_base_url("http://new-api:3000/v1") == "http://new-api:3000"
    assert normalize_admin_base_url("http://new-api:3000/") == "http://new-api:3000"
    assert normalize_relay_base_url("http://new-api:3000") == "http://new-api:3000/v1"
    assert (
        normalize_relay_base_url("http://new-api:3000/v1") == "http://new-api:3000/v1"
    )


def test_build_channel_payload_maps_dc_models_to_upstream_models():
    payload = build_channel_payload(
        provider="ali",
        name="user-supplied-name-is-ignored",
        upstream_key="sk-upstream",
        model_mapping={
            "DC-screenplay-normalizer-LLM": "qwen-plus",
            "DC-staging-prop-planner-LLM": "qwen-max",
        },
        group="default,drama",
        priority=2,
    )

    channel = payload["channel"]
    assert payload["mode"] == "single"
    assert channel["name"] == "DC-ali"
    assert channel["type"] == 17
    assert (
        channel["models"] == "DC-screenplay-normalizer-LLM,DC-staging-prop-planner-LLM"
    )
    assert channel["group"] == ",default,drama,"
    assert channel["test_model"] == "DC-screenplay-normalizer-LLM"
    assert channel["model_mapping"] == (
        '{"DC-screenplay-normalizer-LLM":"qwen-plus",'
        '"DC-staging-prop-planner-LLM":"qwen-max"}'
    )


@respx.mock
def test_ensure_newapi_setup_creates_root_when_instance_is_fresh():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": False,
                        "database_type": "postgres",
                    },
                },
            ),
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(
        cfg,
        NewApiSetupCredentials(
            username="admin",
            password="strongpass",
            confirm_password="strongpass",
        ),
    )

    assert status.initialized is True
    assert status.root_initialized is True
    assert status.setup_performed is True
    assert status.already_initialized is False
    assert setup_request.calls.last.request.content
    assert json.loads(setup_request.calls.last.request.content) == {
        "SelfUseModeEnabled": True,
        "DemoSiteEnabled": False,
        "username": "admin",
        "password": "strongpass",
        "confirmPassword": "strongpass",
    }


@respx.mock
def test_ensure_newapi_setup_requires_credentials_for_fresh_instance():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": False,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )

    with pytest.raises(ValueError, match="setupUsername"):
        ensure_newapi_setup(cfg, NewApiSetupCredentials(username="admin"))


@respx.mock
def test_ensure_newapi_setup_finishes_setup_when_root_already_exists():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(cfg)

    assert status.initialized is True
    assert status.setup_performed is True
    assert status.already_initialized is False
    assert json.loads(setup_request.calls.last.request.content) == {
        "SelfUseModeEnabled": True,
        "DemoSiteEnabled": False,
    }


@respx.mock
def test_ensure_newapi_setup_skips_post_when_instance_is_initialized():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(
        cfg,
        NewApiSetupCredentials(
            username="root",
            password="strongpass",
            confirm_password="strongpass",
        ),
    )

    assert status.initialized is True
    assert status.root_initialized is True
    assert status.setup_performed is False
    assert status.already_initialized is True
    assert not setup_request.called


@respx.mock
def test_upsert_channel_merges_existing_dc_provider_channel():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )
    payload = build_channel_payload(
        provider="ali",
        upstream_key="sk-upstream-new",
        model_mapping={"DC-screenplay-normalizer-LLM": "qwen-plus"},
        base_url="https://dashscope-new.example.com",
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model",
                    "model_mapping": json.dumps({"DC-old-model": "qwen-old"}),
                    "group": ",default,",
                    "status": 1,
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = upsert_channel(cfg, admin, payload)

    assert result["ok"] is True
    assert result["action"] == "update"
    assert result["channelId"] == 3
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["id"] == 3
    assert channel["name"] == "DC-ali"
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == "https://dashscope-new.example.com"
    assert channel["models"] == "DC-old-model,DC-screenplay-normalizer-LLM"
    assert json.loads(channel["model_mapping"]) == {
        "DC-old-model": "qwen-old",
        "DC-screenplay-normalizer-LLM": "qwen-plus",
    }


@respx.mock
def test_update_provider_channel_credentials_preserves_models_and_mapping():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model,DC-screenplay-normalizer-LLM",
                    "model_mapping": json.dumps(
                        {
                            "DC-old-model": "qwen-old",
                            "DC-screenplay-normalizer-LLM": "qwen-plus",
                        }
                    ),
                    "group": ",default,",
                    "priority": 2,
                    "weight": 3,
                    "test_model": "DC-old-model",
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = update_provider_channel_credentials(
        cfg,
        admin,
        provider="ali",
        upstream_key="sk-upstream-new",
        base_url="https://dashscope-new.example.com/",
    )

    assert result["ok"] is True
    assert result["action"] == "update"
    assert result["channelId"] == 3
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == "https://dashscope-new.example.com"
    assert channel["models"] == "DC-old-model,DC-screenplay-normalizer-LLM"
    assert json.loads(channel["model_mapping"]) == {
        "DC-old-model": "qwen-old",
        "DC-screenplay-normalizer-LLM": "qwen-plus",
    }
    assert channel["priority"] == 2
    assert channel["weight"] == 3
    assert channel["test_model"] == "DC-old-model"


@respx.mock
def test_update_provider_channel_credentials_clears_base_url_override():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model",
                    "model_mapping": json.dumps({"DC-old-model": "qwen-old"}),
                    "group": ",default,",
                    "test_model": "DC-old-model",
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = update_provider_channel_credentials(
        cfg,
        admin,
        provider="ali",
        upstream_key="sk-upstream-new",
        base_url="",
    )

    assert result["ok"] is True
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == ""
    assert channel["models"] == "DC-old-model"
    assert json.loads(channel["model_mapping"]) == {"DC-old-model": "qwen-old"}


@respx.mock
def test_upsert_channel_removes_same_dc_model_from_other_provider_channels():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="village-canvas-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )
    payload = build_channel_payload(
        provider="openrouter",
        upstream_key="sk-openrouter",
        model_mapping={"DC-hermes-LLM": "google/gemini-2.5-flash"},
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        side_effect=[
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "items": [
                            {"id": 4, "name": "DC-openrouter", "type": 20},
                            {"id": 3, "name": "DC-ali", "type": 17},
                        ],
                    },
                },
            ),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "items": [
                            {"id": 4, "name": "DC-openrouter", "type": 20},
                            {"id": 3, "name": "DC-ali", "type": 17},
                        ],
                    },
                },
            ),
        ]
    )
    respx.get("http://new-api:3000/api/channel/4").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 4,
                    "name": "DC-openrouter",
                    "type": 20,
                    "key": "sk-old-openrouter",
                    "base_url": "https://openrouter.ai/api",
                    "models": "DC-old-openrouter-model",
                    "model_mapping": json.dumps(
                        {"DC-old-openrouter-model": "openrouter/old"}
                    ),
                    "group": ",default,",
                    "status": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-ali",
                    "base_url": "https://dashscope.aliyuncs.com",
                    "models": "DC-hermes-LLM,DC-screenplay-normalizer-LLM",
                    "model_mapping": json.dumps(
                        {
                            "DC-hermes-LLM": "qwen-plus",
                            "DC-screenplay-normalizer-LLM": "qwen-max",
                        }
                    ),
                    "group": ",default,",
                    "status": 1,
                    "test_model": "DC-hermes-LLM",
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = upsert_channel(cfg, admin, payload)

    assert result["ok"] is True
    assert result["action"] == "update"
    assert result["dedupedChannels"] == [
        {
            "channelId": 3,
            "name": "DC-ali",
            "ok": True,
            "httpStatus": 200,
            "removedModels": ["DC-hermes-LLM"],
        }
    ]
    target_update = json.loads(update_route.calls[0].request.content)
    assert target_update["id"] == 4
    assert json.loads(target_update["model_mapping"]) == {
        "DC-old-openrouter-model": "openrouter/old",
        "DC-hermes-LLM": "google/gemini-2.5-flash",
    }
    stale_update = json.loads(update_route.calls[1].request.content)
    assert stale_update["id"] == 3
    assert stale_update["models"] == "DC-screenplay-normalizer-LLM"
    assert stale_update["test_model"] == "DC-screenplay-normalizer-LLM"
    assert json.loads(stale_update["model_mapping"]) == {
        "DC-screenplay-normalizer-LLM": "qwen-max",
    }


def test_provisioner_enabled_by_default_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("NEWAPI_PROVISIONER_ENABLED", raising=False)
    require_provisioner_enabled()

    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "false")
    with pytest.raises(PermissionError, match="not enabled"):
        require_provisioner_enabled()

    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    require_provisioner_enabled()


def test_provisioner_is_always_disabled_in_ee(monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    with pytest.raises(PermissionError, match="not enabled"):
        require_provisioner_enabled()


def test_newapi_db_defaults_to_managed_ce_sqlite_and_does_not_create_empty_file(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NEWAPI_SQL_DSN", raising=False)
    monkeypatch.delenv("NEWAPI_SQLITE_PATH", raising=False)

    cfg = model_gateway.get_provisioner_config()

    assert cfg.admin_base_url == "http://127.0.0.1:3000"
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == str(tmp_path / "state" / "newapi" / "one-api.db")
    with pytest.raises(RuntimeError, match="does not exist"):
        open_newapi_db(cfg)

    assert not (tmp_path / "state" / "newapi" / "one-api.db").exists()


def test_newapi_db_rejects_missing_sqlite_file(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    missing = tmp_path / "missing-one-api.db"
    monkeypatch.setenv("NEWAPI_SQL_DSN", "local")
    monkeypatch.setenv("NEWAPI_SQLITE_PATH", str(missing))

    with pytest.raises(RuntimeError, match="does not exist"):
        open_newapi_db(model_gateway.get_provisioner_config())

    assert not missing.exists()


def test_provisioner_config_prefers_saved_database_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "NEWAPI_SQL_DSN", "postgresql://env:envpass@127.0.0.1:5432/envdb"
    )
    monkeypatch.setenv("NEWAPI_SQLITE_PATH", "/env/one-api.db")
    monkeypatch.setenv("NEWAPI_ADMIN_USERNAME", "env-root")
    monkeypatch.setenv("NEWAPI_ADMIN_BASE_URL", "http://env-new-api:3000")
    save_custom_newapi_gateway(
        base_url="http://saved-new-api:3000/v1",
        api_key="sk-custom-secret",
        admin_base_url="http://saved-new-api:3000",
        activate=True,
    )
    save_newapi_database_config(
        sql_dsn="local",
        sqlite_path="/saved/one-api.db",
        admin_username="saved-root",
    )

    cfg = get_provisioner_config()

    assert cfg.admin_base_url == "http://saved-new-api:3000"
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == "/saved/one-api.db"
    assert cfg.admin_username == "saved-root"


def test_provisioner_config_request_database_overrides_saved_settings(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_database_config(
        sql_dsn="local",
        sqlite_path="/saved/one-api.db",
        admin_username="saved-root",
    )

    cfg = get_provisioner_config(
        "http://request-new-api:3000",
        sql_dsn="postgresql://request:secret@127.0.0.1:5432/newapi",
        sqlite_path="",
        admin_username="request-root",
    )

    assert cfg.admin_base_url == "http://request-new-api:3000"
    assert cfg.sql_dsn == "postgresql://request:secret@127.0.0.1:5432/newapi"
    assert cfg.sqlite_path == ""
    assert cfg.admin_username == "request-root"


def test_database_status_does_not_expose_database_credentials(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_database_config(
        sql_dsn="postgresql://root:secret@127.0.0.1:5432/newapi",
        admin_username="root",
    )

    status = build_newapi_database_status()

    assert status["configured"] is True
    assert status["source"] == "database"
    assert status["databaseType"] == "external"
    assert "sqlDsnPreview" not in status
    assert "sqlitePath" not in status
    assert "adminUsername" not in status
    assert "secret" not in str(status)


def test_model_gateway_config_route_masks_effective_key(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://official.example/v1"
    )
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_API_KEY", "sk-official-secret"
    )
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/config")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == MODE_OFFICIAL
    assert data["effective"]["apiKeyPreview"] == "sk-o...cret"
    assert "sk-official-secret" not in response.text


def test_model_gateway_config_excludes_closed_source_provider_presets(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/config")

    assert response.status_code == 200
    providers = response.json()["data"]["provisioner"]["providers"]
    assert "ali" in providers
    assert "openrouter" in providers
    assert "deepseek" in providers
    assert "openai" in providers
    assert providers["azure"]["type"] == 3
    assert providers["gemini"]["type"] == 24
    assert providers["volcengine"]["type"] == 45
    assert "huimeng" not in providers
    assert "fal" not in providers


def test_enable_official_gateway_route_switches_mode_when_enabled(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://official.example/v1"
    )
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_API_KEY", "sk-official-secret"
    )
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post("/model-gateway/official/enable")

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == MODE_UNIFIED
    assert (
        get_effective_newapi_config(
            official_base_url="https://official.example/v1",
            official_api_key="sk-official-secret",
        ).mode
        == MODE_UNIFIED
    )


def test_save_official_gateway_route_persists_user_registered_key(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setattr(model_gateway.app_config, "VILLAGE_CANVAS_GATEWAY_BASE_URL", "")
    monkeypatch.setattr(model_gateway.app_config, "NEWAPI_API_KEY", "sk-env-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/official/config",
        json={
            "newApiApiKey": "user-registered-token",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == MODE_UNIFIED
    assert data["unified"]["baseUrl"] == "https://env.example/v1"
    assert data["unified"]["source"] == "database"
    assert data["unified"]["apiKeyPreview"] == "user...oken"
    assert "user-registered-token" not in response.text

    effective = get_effective_newapi_config(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )
    assert effective.base_url == "https://env.example/v1"
    assert effective.api_key == "user-registered-token"


def test_save_official_gateway_route_ignores_submitted_gateway_url(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setattr(model_gateway.app_config, "VILLAGE_CANVAS_GATEWAY_BASE_URL", "")
    monkeypatch.setattr(model_gateway.app_config, "NEWAPI_API_KEY", "sk-env-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/official/config",
        json={
            "newApiBaseUrl": "https://official-user.example",
            "newApiApiKey": "user-registered-token",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["unified"]["baseUrl"] == "https://env.example/v1"
    effective = get_effective_newapi_config(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )
    assert effective.base_url == "https://env.example/v1"
    assert effective.api_key == "user-registered-token"


def test_custom_newapi_init_route_accepts_empty_body(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls = {}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **_kwargs):
        calls["base_url"] = base_url
        return type(
            "Cfg",
            (),
            {
                "admin_base_url": "http://new-api:3000",
                "relay_token_name": "village-canvas-ce-runtime",
            },
        )()

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(
        model_gateway,
        "ensure_newapi_setup",
        lambda *_args, **_kwargs: type(
            "SetupStatus",
            (),
            {
                "initialized": True,
                "root_initialized": True,
                "database_type": "sqlite",
                "setup_performed": False,
                "already_initialized": True,
            },
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "create_or_reuse_relay_token",
        lambda *_args, **_kwargs: {
            "created": False,
            "tokenId": 2,
            "name": "village-canvas-ce-runtime",
            "key": "sk-runtime-secret",
            "keyPreview": "sk-r...cret",
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post("/model-gateway/custom/newapi/init")

    assert response.status_code == 200
    data = response.json()["data"]
    assert calls["base_url"] is None
    assert data["mode"] == MODE_CUSTOM
    assert data["newApiAdminBaseUrl"] == "http://new-api:3000"
    assert data["newApiBaseUrl"] == "http://new-api:3000/v1"
    assert "sk-runtime-secret" not in response.text


def test_custom_newapi_init_route_persists_request_database_config(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls = {}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **kwargs):
        calls["base_url"] = base_url
        calls["kwargs"] = kwargs
        return type(
            "Cfg",
            (),
            {
                "admin_base_url": "http://new-api:3000",
                "relay_token_name": "village-canvas-ce-runtime",
                "sql_dsn": kwargs["sql_dsn"],
                "sqlite_path": kwargs["sqlite_path"],
                "admin_username": kwargs["admin_username"],
            },
        )()

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(
        model_gateway,
        "ensure_newapi_setup",
        lambda *_args, **_kwargs: type(
            "SetupStatus",
            (),
            {
                "initialized": True,
                "root_initialized": True,
                "database_type": "sqlite",
                "setup_performed": False,
                "already_initialized": True,
            },
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "create_or_reuse_relay_token",
        lambda *_args, **_kwargs: {
            "created": True,
            "tokenId": 7,
            "name": "village-canvas-ce-runtime",
            "key": "sk-runtime-secret",
            "keyPreview": "sk-r...cret",
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/init",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "database": {
                "sqlDsn": "local",
                "sqlitePath": "/Users/hg/data/new-api/one-api.db",
                "adminUsername": "root",
            },
        },
    )

    assert response.status_code == 200
    assert calls["base_url"] == "http://new-api:3000"
    assert calls["kwargs"] == {
        "sql_dsn": "local",
        "sqlite_path": "/Users/hg/data/new-api/one-api.db",
        "admin_username": "root",
    }
    data = response.json()["data"]
    assert data["database"]["configured"] is True
    assert data["database"]["source"] == "database"
    assert data["database"]["databaseType"] == "sqlite"
    assert "sqlitePath" not in data["database"]
    cfg = get_provisioner_config()
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == "/Users/hg/data/new-api/one-api.db"
    assert cfg.admin_username == "root"
    assert cfg.admin_base_url == "http://new-api:3000"


def test_custom_newapi_channels_batch_reuses_admin_and_masks_keys(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls: dict[str, list[object] | int] = {"payloads": [], "ensure_admin": 0}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **_kwargs):
        assert base_url == "http://new-api:3000"
        return type("Cfg", (), {"admin_base_url": "http://new-api:3000"})()

    def fake_ensure_admin(_cfg):
        calls["ensure_admin"] = int(calls["ensure_admin"]) + 1
        return Admin()

    def fake_upsert_channel(_cfg, _admin, payload):
        calls["payloads"].append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(model_gateway, "ensure_admin_access_token", fake_ensure_admin)
    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "channels": [
                {
                    "provider": "ali",
                    "name": "ali-text",
                    "upstreamKey": "sk-upstream-one",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                },
                {
                    "provider": "deepseek",
                    "name": "deepseek-text",
                    "upstreamKey": "sk-upstream-two",
                    "modelMapping": {"DC-hermes-LLM": "deepseek-chat"},
                    "priority": 3,
                },
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert response.json()["ok"] is True
    assert data["succeeded"] == 2
    assert data["failed"] == 0
    assert calls["ensure_admin"] == 1
    assert len(calls["payloads"]) == 2
    assert (
        data["results"][0]["sentPayload"]["channel"]["models"]
        == "DC-screenplay-normalizer-LLM"
    )
    assert data["results"][1]["sentPayload"]["channel"]["type"] == 43
    assert "sk-upstream-one" not in response.text
    assert "sk-upstream-two" not in response.text


def test_custom_newapi_provider_channels_route_persists_and_masks_keys(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "upstreamKey": "sk-ali-upstream-secret",
                    "baseUrl": "https://dashscope.example.com/",
                },
                {
                    "provider": "deepseek",
                    "upstreamKey": "sk-deepseek-upstream-secret",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "sk-ali-upstream-secret" not in response.text
    assert "sk-deepseek-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    assert channels == [
        {
            "provider": "ali",
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "https://dashscope.example.com",
        },
        {
            "provider": "deepseek",
            "configured": True,
            "upstreamKeyPreview": "sk-d...cret",
            "baseUrl": "",
        },
    ]
    assert "sk-ali-upstream-secret" not in config_response.text
    assert "sk-deepseek-upstream-secret" not in config_response.text


def test_custom_newapi_provider_channel_sync_updates_newapi_and_local_config(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_update_credentials(_cfg, _admin, *, provider, upstream_key, base_url=None):
        calls["provider"] = provider
        calls["upstream_key"] = upstream_key
        calls["base_url"] = base_url
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": upstream_key,
                    "base_url": base_url,
                },
            },
            "channelId": 7,
        }

    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        fake_update_credentials,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
            "baseUrl": "https://dashscope-new.example.com/",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == {
        "provider": "ali",
        "upstream_key": "sk-ali-new-upstream-secret",
        "base_url": "https://dashscope-new.example.com/",
    }
    assert "sk-ali-new-upstream-secret" not in response.text
    assert response.json()["data"]["savedChannel"] == {
        "provider": "ali",
        "configured": True,
        "upstreamKeyPreview": "sk-a...cret",
        "baseUrl": "https://dashscope-new.example.com",
    }

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    assert channels == [
        {
            "provider": "ali",
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "https://dashscope-new.example.com",
        }
    ]
    assert "sk-ali-new-upstream-secret" not in config_response.text


def test_custom_newapi_provider_channel_sync_allows_clearing_saved_base_url(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "ali",
                "upstreamKey": "sk-ali-old-upstream-secret",
                "baseUrl": "https://dashscope-old.example.com",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_update_credentials(_cfg, _admin, *, provider, upstream_key, base_url=None):
        calls["provider"] = provider
        calls["upstream_key"] = upstream_key
        calls["base_url"] = base_url
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": upstream_key,
                    "base_url": base_url,
                },
            },
            "channelId": 7,
        }

    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        fake_update_credentials,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
            "baseUrl": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == {
        "provider": "ali",
        "upstream_key": "sk-ali-new-upstream-secret",
        "base_url": "",
    }
    assert "https://dashscope-old.example.com" not in response.text

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    assert channels == [
        {
            "provider": "ali",
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "",
        }
    ]


def test_custom_newapi_provider_channel_sync_does_not_save_when_newapi_update_fails(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        lambda *_args, **_kwargs: {
            "ok": False,
            "httpStatus": 400,
            "newApiResponse": {"success": False, "message": "invalid key"},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": "sk-ali-new-upstream-secret",
                },
            },
            "channelId": 7,
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["data"]["savedChannel"] is None
    assert "sk-ali-new-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    assert config_response.json()["data"]["provisioner"]["providerChannels"] == []


def test_custom_newapi_channels_batch_uses_saved_provider_channel_config(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "ali",
                "upstreamKey": "sk-saved-upstream-secret",
                "baseUrl": "https://saved-dashscope.example.com",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert payloads[0]["channel"]["key"] == "sk-saved-upstream-secret"
    assert payloads[0]["channel"]["base_url"] == "https://saved-dashscope.example.com"
    assert "sk-saved-upstream-secret" not in response.text


def test_custom_newapi_media_models_groups_by_provider_and_persists_mapping(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "openai",
                "upstreamKey": "sk-openai-upstream-secret",
                "baseUrl": "",
            },
            {
                "provider": "volcengine",
                "upstreamKey": "sk-volc-upstream-secret",
                "baseUrl": "https://ark.example.com",
            },
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "update",
            "channelId": 3,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/media-models",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "models": {
                "LingShan-G2": {
                    "provider": "openai",
                    "upstreamModel": "gpt-image-upstream",
                },
                "seedance-1.5-pro": {
                    "provider": "volcengine",
                    "upstreamModel": "doubao-seedance-1-5",
                },
                "seedance-2.0-fast": {
                    "provider": "volcengine",
                    "upstreamModel": "",
                },
                "index-tts-2": {
                    "provider": "volcengine",
                    "upstreamModel": "index-tts-2-upstream",
                },
                "LingShan-MU-11": {
                    "provider": "volcengine",
                    "upstreamModel": "lingshan-mu-upstream",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["succeeded"] == 2
    assert len(payloads) == 2
    by_name = {payload["channel"]["name"]: payload["channel"] for payload in payloads}
    assert json.loads(by_name["DC-openai"]["model_mapping"]) == {
        "LingShan-G2": "gpt-image-upstream",
    }
    assert json.loads(by_name["DC-volcengine"]["model_mapping"]) == {
        "seedance-1.5-pro": "doubao-seedance-1-5",
        "seedance-2.0-fast": "seedance-2.0-fast",
        "index-tts-2": "index-tts-2-upstream",
        "LingShan-MU-11": "lingshan-mu-upstream",
    }
    assert by_name["DC-openai"]["key"] == "sk-openai-upstream-secret"
    assert by_name["DC-volcengine"]["base_url"] == "https://ark.example.com"
    assert "sk-openai-upstream-secret" not in response.text
    assert "sk-volc-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    media_models = config_response.json()["data"]["provisioner"]["mediaModels"]
    assert media_models == {
        "LingShan-G2": {
            "provider": "openai",
            "upstreamModel": "gpt-image-upstream",
        },
        "seedance-1.5-pro": {
            "provider": "volcengine",
            "upstreamModel": "doubao-seedance-1-5",
        },
        "seedance-2.0-fast": {
            "provider": "volcengine",
            "upstreamModel": "",
        },
        "index-tts-2": {
            "provider": "volcengine",
            "upstreamModel": "index-tts-2-upstream",
        },
        "LingShan-MU-11": {
            "provider": "volcengine",
            "upstreamModel": "lingshan-mu-upstream",
        },
    }


def test_custom_newapi_media_models_rejects_official_value_models(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/media-models",
        json={
            "models": {
                "seedance-2.0-value": {
                    "provider": "volcengine",
                    "upstreamModel": "seedance-2.0-value",
                }
            }
        },
    )

    assert response.status_code == 400
    assert "official-channel only" in response.text


def test_custom_newapi_embedding_model_writes_mapping_and_persists_dimension(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "openai",
                "upstreamKey": "sk-openai-upstream-secret",
                "baseUrl": "",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "update",
            "channelId": 7,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/embedding-model",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "openai",
            "upstreamModel": "text-embedding-3-large",
            "dimension": 1024,
            "batchSize": 36,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert payloads[0]["channel"]["name"] == "DC-openai"
    assert payloads[0]["channel"]["key"] == "sk-openai-upstream-secret"
    assert json.loads(payloads[0]["channel"]["model_mapping"]) == {
        "DC-cognee-embedding": "text-embedding-3-large",
    }
    assert "dimension" not in payloads[0]["channel"]
    assert "1024" not in payloads[0]["channel"]["model_mapping"]
    assert "sk-openai-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    embedding = config_response.json()["data"]["provisioner"]["embeddingModel"]
    assert embedding == {
        "provider": "openai",
        "upstreamModel": "text-embedding-3-large",
        "dimension": 1024,
        "batchSize": 36,
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }


def test_custom_newapi_embedding_model_accepts_positive_project_dimension(
):
    body = model_gateway.SaveEmbeddingModelBody.model_validate(
        {
            "provider": "openai",
            "upstreamModel": "text-embedding-3-large",
            "dimension": 3072,
        }
    )

    _, normalized = model_gateway._build_embedding_model_channel_spec(body)

    assert normalized["dimension"] == 3072
    assert normalized["sendDimensions"] is True


def test_effective_cognee_embedding_prefers_saved_custom_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_CUSTOM)
    monkeypatch.setenv("COGNEE_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "768")

    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="text-embedding-3-large",
        dimension=3072,
    )

    effective = get_effective_cognee_embedding_config(llm_provider="gemini")

    assert effective.source == "database"
    assert effective.provider == "newapi"
    assert effective.model == "DC-cognee-embedding"
    assert effective.dimensions == "3072"
    assert effective.upstream_provider == "openai"
    assert effective.upstream_model == "text-embedding-3-large"


def test_effective_cognee_embedding_keeps_saved_batch_size(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_CUSTOM)

    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="text-embedding-v3",
        dimension=1024,
        batch_size=10,
    )

    effective = get_effective_cognee_embedding_config(llm_provider="newapi")

    assert effective.source == "database"
    assert effective.provider == "newapi"
    assert effective.model == "DC-cognee-embedding"
    assert effective.dimensions == "1024"
    assert effective.batch_size == "10"


def test_ce_official_embedding_ignores_saved_custom_model(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="stale-custom-model",
        dimension=1024,
    )
    set_model_gateway_mode(MODE_OFFICIAL)
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-cognee-embedding")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "1024")

    effective = get_effective_cognee_embedding_config()

    assert effective.source == "unconfigured"
    assert effective.provider == ""
    assert effective.model == ""
    assert effective.dimensions == ""
    assert effective.upstream_model == ""


def test_cognee_apply_embedding_env_sets_saved_batch_size(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.delenv("EMBEDDING_BATCH_SIZE", raising=False)

    save_custom_newapi_gateway(
        base_url="https://custom.example",
        api_key="sk-custom-secret",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="text-embedding-v3",
        dimension=1024,
        batch_size=10,
    )

    from novelvideo.cognee import config as cognee_config

    cognee_config._apply_embedding_env("newapi", "sk-custom-secret")

    assert os.environ["EMBEDDING_BATCH_SIZE"] == "10"


def test_custom_newapi_channels_batch_reports_partial_failure(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        if "DC-staging-prop-planner-LLM" in payload["channel"]["models"]:
            return {
                "ok": False,
                "httpStatus": 400,
                "newApiResponse": {"success": False, "message": "bad model"},
                "action": "update",
                "channelId": 7,
            }
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "name": "ok-channel",
                    "upstreamKey": "sk-upstream-one",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                },
                {
                    "provider": "ali",
                    "name": "bad-channel",
                    "upstreamKey": "sk-upstream-two",
                    "modelMapping": {"DC-staging-prop-planner-LLM": "qwen-plus"},
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["data"]["succeeded"] == 1
    assert body["data"]["failed"] == 1
    assert body["data"]["results"][0]["ok"] is True
    assert body["data"]["results"][1]["ok"] is False
    assert body["data"]["results"][1]["httpStatus"] == 400


def test_media_relay_config_route_persists_and_masks_oss_keys(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "env-ak-secret")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "env-sk-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 900,
            "endpoint": "oss-cn-shanghai.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-user-secret",
            "accessKeySecret": "SK-user-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "database"
    assert data["ttlSeconds"] == 900
    assert data["endpoint"] == "oss-cn-shanghai.aliyuncs.com"
    assert data["bucket"] == "user-relay"
    assert data["accessKeyIdPreview"] == "LTAI...cret"
    assert data["accessKeySecretPreview"] == "SK-u...cret"
    assert "LTAI-user-secret" not in response.text
    assert "SK-user-secret" not in response.text


def test_media_relay_config_route_persists_and_masks_cloudinary_keys(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "env-ak-secret")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "env-sk-secret")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "relay")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "ttlSeconds": 900,
            "cloudName": "demo-cloud",
            "apiKey": "cloudinary-api-key-secret",
            "apiSecret": "cloudinary-api-secret",
            "apiFolder": "village-canvas-relay",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "database"
    assert data["provider"] == "cloudinary"
    assert data["ttlSeconds"] == 900
    assert data["cloudName"] == "demo-cloud"
    assert data["apiFolder"] == "village-canvas-relay"
    assert data["cloudinaryApiKeyPreview"] == "clou...cret"
    assert data["cloudinaryApiSecretPreview"] == "clou...cret"
    assert data["configured"] is True
    assert "cloudinary-api-key-secret" not in response.text
    assert "cloudinary-api-secret" not in response.text


def test_media_relay_config_route_rejects_project_label_as_cloudinary_name(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "ttlSeconds": 900,
            "cloudName": "村长无限画布",
            "apiKey": "cloudinary-api-key-secret",
            "apiSecret": "cloudinary-api-secret",
        },
    )

    assert response.status_code == 400
    assert "cloudName" in response.json()["detail"]



def test_media_relay_config_route_supports_partial_credential_updates(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    initial = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 900,
            "endpoint": "oss-cn-shanghai.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-old-secret",
            "accessKeySecret": "SK-old-secret",
        },
    )
    assert initial.status_code == 200

    updated = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 1200,
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-new-secret",
            "accessKeySecret": "",
        },
    )

    assert updated.status_code == 200
    data = updated.json()["data"]
    assert data["ttlSeconds"] == 1200
    assert data["endpoint"] == "oss-cn-beijing.aliyuncs.com"
    assert data["accessKeyIdPreview"] == "LTAI...cret"
    assert data["accessKeySecretPreview"] == "SK-o...cret"
    assert data["configured"] is True
    assert "LTAI-new-secret" not in updated.text
    assert "SK-old-secret" not in updated.text


def test_media_relay_config_route_preserves_inactive_provider_credentials(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    oss = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
            "accessKeyId": "oss-access-key",
            "accessKeySecret": "oss-secret-key",
        },
    )
    assert oss.status_code == 200

    cloudinary = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "cloudName": "demo-cloud",
            "apiKey": "cloudinary-api-key",
            "apiSecret": "cloudinary-api-secret",
        },
    )
    assert cloudinary.status_code == 200
    cloudinary_data = cloudinary.json()["data"]
    assert cloudinary_data["accessKeyIdPreview"] == "oss-...-key"
    assert cloudinary_data["accessKeySecretPreview"] == "oss-...-key"

    cloudinary_partial = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "cloudName": "demo-cloud",
            "apiKey": "",
            "apiSecret": "cloudinary-new-secret",
        },
    )
    assert cloudinary_partial.status_code == 200
    cloudinary_partial_data = cloudinary_partial.json()["data"]
    assert cloudinary_partial_data["cloudinaryApiKeyPreview"] == "clou...-key"
    assert cloudinary_partial_data["cloudinaryApiSecretPreview"] == "clou...cret"

    switched_back = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
        },
    )
    assert switched_back.status_code == 200
    switched_data = switched_back.json()["data"]
    assert switched_data["configured"] is True
    assert switched_data["cloudinaryApiKeyPreview"] == "clou...-key"
    assert switched_data["cloudinaryApiSecretPreview"] == "clou...cret"


def test_media_relay_status_prefers_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=600,
        endpoint="db.endpoint",
        bucket="db-bucket",
        access_key_id="db-ak-secret",
        access_key_secret="db-sk-secret",
    )

    status = model_gateway._media_relay_status()

    assert status["source"] == "database"
    assert status["ttlSeconds"] == 600
    assert status["endpoint"] == "db.endpoint"
    assert status["bucket"] == "db-bucket"
    assert status["configured"] is True


def test_media_relay_status_includes_credential_free_http_runtime_health(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL",
        "http://10.66.66.1:8782/upload",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "private-token")

    status = model_gateway._media_relay_status()

    assert status["httpRuntime"] == {
        "enabled": True,
        "configured": True,
        "state": "closed",
        "consecutiveFailures": 0,
        "retryAfterSeconds": 0,
        "lastErrorType": "",
        "lastSuccessAt": None,
    }
    serialized = str(status["httpRuntime"])
    assert "10.66.66.1" not in serialized
    assert "private-token" not in serialized


def test_ee_media_relay_ignores_ce_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=600,
        endpoint="stale-ce.endpoint",
        bucket="stale-ce-bucket",
        access_key_id="stale-ce-ak",
        access_key_secret="stale-ce-sk",
    )
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "ee.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "ee-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "ee-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "ee-sk")

    status = model_gateway._media_relay_status()

    assert status["source"] == "environment"
    assert status["endpoint"] == "ee.endpoint"
    assert status["bucket"] == "ee-bucket"
    assert status["configured"] is True


def test_retired_chat_families_merge_into_one_capability_tagged_list(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings._write_many(
        {
            "direct_agent_models": json.dumps(
                [
                    {
                        "id": "agent-legacy",
                        "label": "gemini-3.8-flash",
                        "modelId": "gemini-3.8-flash",
                        "baseUrl": "https://relay.example/v1",
                        "apiKey": "sk-agent-secret",
                        "enabled": True,
                        "isDefault": True,
                    }
                ]
            ),
            "direct_text_models": json.dumps(
                [
                    {
                        "id": "text-legacy",
                        "label": "gemini-3.8-flash",
                        "modelId": "gemini-3.8-flash",
                        "baseUrl": "https://relay.example/v1",
                        "apiKey": "sk-text-secret",
                        "enabled": True,
                        "isDefault": True,
                    }
                ]
            ),
            "direct_vision_models": json.dumps(
                [
                    {
                        "id": "vision-legacy",
                        "label": "gemini-3.8-flash",
                        "modelId": "gemini-3.8-flash",
                        "baseUrl": "https://relay.example/v1",
                        "apiKey": "sk-vision-secret",
                        "enabled": True,
                        "isDefault": True,
                    }
                ]
            ),
        }
    )

    chat = get_direct_models("chat")

    assert len(chat) == 1
    row = chat[0]
    assert row["id"] == "agent-legacy"
    assert row["supportsTools"] is True
    assert row["supportsVision"] is True
    assert row["aliases"] == ["text-legacy", "vision-legacy"]
    # Every retired name reads the exact same merged list.
    assert get_direct_models("agent") == chat
    assert get_direct_models("text") == chat
    assert get_direct_models("vision") == chat

    from novelvideo.generators.direct_models import resolve_direct_model

    for binding in ("direct/agent-legacy", "direct/text-legacy", "direct/vision-legacy"):
        assert resolve_direct_model("chat", binding) is not None
        assert resolve_direct_model("agent", binding) is not None


def test_chat_capability_flags_gate_agent_and_vision_resolution(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_direct_models(
        "chat",
        [
            {
                "label": "纯文字模型",
                "modelId": "deepseek-chat",
                "baseUrl": "https://text.example/v1",
                "apiKey": "sk-text",
                "supportsTools": False,
                "supportsVision": False,
                "enabled": True,
                "isDefault": True,
            },
            {
                "label": "全能力模型",
                "modelId": "gemini-3.8-flash",
                "baseUrl": "https://vision.example/v1",
                "apiKey": "sk-all",
                "supportsTools": True,
                "supportsVision": True,
                "enabled": True,
            },
        ],
    )

    from novelvideo.generators.direct_models import list_direct_models

    assert [model.label for model in list_direct_models("chat")] == [
        "纯文字模型",
        "全能力模型",
    ]
    assert [model.label for model in list_direct_models("agent")] == ["全能力模型"]
    assert [model.label for model in list_direct_models("vision")] == ["全能力模型"]
    assert [model.label for model in list_direct_models("text")] == [
        "纯文字模型",
        "全能力模型",
    ]


def test_chat_capabilities_survive_a_client_save_that_omits_them(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_direct_models(
        "chat",
        [
            {
                "label": "无工具模型",
                "modelId": "text-only",
                "baseUrl": "https://text.example/v1",
                "apiKey": "sk-text",
                "supportsTools": False,
                "supportsVision": False,
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    # A PATCH-style payload that sends explicit nulls must not silently turn
    # the capabilities back on (or off) for the existing row.
    saved_row_id = get_direct_models("chat")[0]["id"]
    saved = save_direct_models(
        "chat",
        [
            {
                "id": saved_row_id,
                "label": "无工具模型",
                "modelId": "text-only",
                "baseUrl": "https://text.example/v1",
                "apiKey": "",
                "supportsTools": None,
                "supportsVision": None,
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    assert saved[0]["id"] == saved_row_id
    assert saved[0]["supportsTools"] is False
    assert saved[0]["supportsVision"] is False


def test_canonical_chat_save_retires_the_legacy_registries(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_direct_models(
        "text",
        [
            {
                "label": "旧文字模型",
                "modelId": "gemini-3.8-flash",
                "baseUrl": "https://relay.example/v1",
                "apiKey": "sk-text-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    save_direct_models(
        "chat",
        [
            {
                "label": "新对话模型",
                "modelId": "gemini-3.8-flash",
                "baseUrl": "https://relay.example/v1",
                "apiKey": "sk-text-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    persisted = model_gateway_settings.get_model_gateway_settings()

    assert persisted["direct_text_models"] == ""
    assert persisted["direct_agent_models"] == ""
    assert persisted["direct_vision_models"] == ""
    assert [item["label"] for item in get_direct_models("chat")] == ["新对话模型"]


def test_chat_row_inherits_probe_evidence_from_a_retired_family(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )

    record_direct_model_capability(
        base_url="https://relay.example/v1",
        kind="text",
        upstream_model="gemini-3.8-flash",
        protocol="openai-compatible",
        capability={
            "modelFound": True,
            "verificationStatus": "runtime-verified",
            "probeContractVersion": 1,
            "chatProbeStatus": "passed",
            "chatResponseUsable": True,
            "streamProbeStatus": "passed",
            "streamResponseUsable": True,
        },
    )
    save_direct_models(
        "chat",
        [
            {
                "label": "gemini-3.8-flash",
                "modelId": "gemini-3.8-flash",
                "baseUrl": "https://relay.example/v1",
                "apiKey": "sk-chat-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )

    item = build_direct_models_status()["chat"][0]

    # The retired text row already proved this endpoint; the merged chat row
    # must not look unprobed and drop out of every picker.
    assert item["runtimeReady"] is True
    assert item["runtimeProbeRequired"] is True
    assert item["runtimeProbeComplete"] is True


def test_failed_image_catalog_probe_invalidates_saved_row(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )

    save_direct_models(
        "image",
        [
            {
                "label": "Image model",
                "modelId": "missing-image-model",
                "baseUrl": "https://image-relay.example/v1",
                "apiKey": "sk-image-secret",
                "protocol": "openai-images",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    record_direct_model_capability(
        base_url="https://image-relay.example/v1",
        kind="image",
        upstream_model="missing-image-model",
        protocol="openai-images",
        capability={
            "verificationStatus": "metadata",
            "modelFound": False,
            "discoveredModelCount": 4,
            "lastFailure": "upstream catalog did not contain the model",
        },
    )

    item = build_direct_models_status()["image"][0]

    assert item["runtimeProbeRequired"] is True
    assert item["runtimeProbeComplete"] is False
    assert item["catalogVerification"] == "catalog-mismatch"
    assert item["runtimeReady"] is False
    assert item["usable"] is False


def test_unlisted_preview_model_stays_runnable_after_a_real_probe(
    monkeypatch, tmp_path
):
    """The catalog is advisory: a passed runtime probe must keep the row usable."""

    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )
    from novelvideo.generators.direct_models import (
        DirectModel,
        direct_model_catalog_verification,
        is_direct_model_runtime_ready,
    )

    record_direct_model_capability(
        base_url="https://api.deepseek.com",
        kind="chat",
        upstream_model="deepseek-v4.1-flash-expires-on-0910",
        protocol="openai-compatible",
        capability={
            "modelFound": False,
            "discoveredModelCount": 3,
            "verificationStatus": "runtime-verified",
            "probeContractVersion": 1,
            "chatProbeStatus": "passed",
            "chatResponseUsable": True,
            "streamProbeStatus": "passed",
            "streamResponseUsable": True,
        },
    )
    row = DirectModel(
        kind="chat",
        registry_id="chat-preview",
        label="deepseek-v4.1-flash-expires-on-0910",
        upstream_model="deepseek-v4.1-flash-expires-on-0910",
        base_url="https://api.deepseek.com",
        api_key="sk-preview-secret",
        enabled=True,
        is_default=True,
        protocol="openai-compatible",
    )

    assert direct_model_catalog_verification(row) == "runtime-verified"
    assert is_direct_model_runtime_ready(row) is True

    save_direct_models(
        "chat",
        [
            {
                "label": "deepseek-v4.1-flash",
                "modelId": "deepseek-v4.1-flash-expires-on-0910",
                "baseUrl": "https://api.deepseek.com",
                "apiKey": "sk-preview-secret",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    status = build_direct_models_status()["chat"][0]

    assert status["runtimeReady"] is True
    assert status["runtimeProbeComplete"] is True
    assert status["catalogMissing"] is True
    assert status["verificationStatus"] == "runtime-verified"
    assert not status.get("disabledReason")


def test_capability_evidence_survives_family_renames(monkeypatch, tmp_path):
    """Identity is endpoint+model: renaming a family must not drop evidence."""

    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import direct_model_capability_cache as cache

    base = "https://relay.example/v1"
    # An entry written by the pre-merge code, whose key still held the family.
    legacy_key = cache._legacy_fingerprint(
        base_url=base, kind="text", upstream_model="model-legacy"
    )
    cache._save(
        {
            "schemaVersion": cache._SCHEMA_VERSION,
            "entries": {
                legacy_key: {
                    "protocol": "openai-compatible",
                    "modelFound": True,
                    "verificationStatus": "runtime-verified",
                }
            },
        }
    )
    for kind in ("chat", "text", "agent", "vision"):
        assert cache.get_cached_direct_model_capability(
            base_url=base, kind=kind, upstream_model="model-legacy"
        ), kind

    # New probes are stored once and read by every alias of the family.
    cache.record_direct_model_capability(
        base_url=base,
        kind="chat",
        upstream_model="model-new",
        protocol="openai-compatible",
        capability={"modelFound": True, "verificationStatus": "runtime-verified"},
    )
    for kind in ("chat", "text", "agent", "vision"):
        assert cache.get_cached_direct_model_capability(
            base_url=base, kind=kind, upstream_model="model-new"
        ), kind

    # A different family never inherits chat evidence.
    assert (
        cache.get_cached_direct_model_capability(
            base_url=base, kind="embedding", upstream_model="model-new"
        )
        == {}
    )


def test_invalidating_a_family_also_clears_its_legacy_evidence(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import direct_model_capability_cache as cache

    base = "https://relay.example/v1"
    cache._save(
        {
            "schemaVersion": cache._SCHEMA_VERSION,
            "entries": {
                cache._legacy_fingerprint(
                    base_url=base, kind="vision", upstream_model="model-x"
                ): {"modelFound": True, "verificationStatus": "runtime-verified"},
                cache._fingerprint(
                    base_url=base, kind="chat", upstream_model="model-x"
                ): {"modelFound": True, "verificationStatus": "runtime-verified"},
            },
        }
    )

    cache.invalidate_direct_model_capability(
        base_url=base, kind="chat", upstream_model="model-x"
    )

    for kind in ("chat", "text", "agent", "vision"):
        assert (
            cache.get_cached_direct_model_capability(
                base_url=base, kind=kind, upstream_model="model-x"
            )
            == {}
        )
