from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.routes import model_gateway


def _isolated_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    for name, value in {
        "MEDIA_RELAY_PROVIDER": "aliyun_oss",
        "MEDIA_RELAY_TTL_SECONDS": 1800,
        "OSS_RELAY_ENDPOINT": "",
        "OSS_RELAY_BUCKET": "",
        "OSS_RELAY_AK": "",
        "OSS_RELAY_SK": "",
        "CLOUDINARY_RELAY_CLOUD_NAME": "",
        "CLOUDINARY_RELAY_API_KEY": "",
        "CLOUDINARY_RELAY_API_SECRET": "",
        "CLOUDINARY_RELAY_FOLDER": "",
    }.items():
        monkeypatch.setattr(model_gateway.app_config, name, value)

    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


def test_partial_oss_update_preserves_omitted_and_blank_secrets(monkeypatch, tmp_path):
    client = _isolated_client(monkeypatch, tmp_path)
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


def test_switching_relay_provider_preserves_inactive_credentials(monkeypatch, tmp_path):
    client = _isolated_client(monkeypatch, tmp_path)
    assert client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
            "accessKeyId": "oss-access-key",
            "accessKeySecret": "oss-secret-key",
        },
    ).status_code == 200

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
    assert cloudinary.json()["data"]["accessKeyIdPreview"] == "oss-...-key"

    switched_back = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
        },
    )
    assert switched_back.status_code == 200
    data = switched_back.json()["data"]
    assert data["configured"] is True
    assert data["cloudinaryApiKeyPreview"] == "clou...-key"
    assert data["cloudinaryApiSecretPreview"] == "clou...cret"

