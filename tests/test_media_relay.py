from __future__ import annotations

import io
import re
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from novelvideo import config
from novelvideo.model_gateway_settings import save_media_relay_config
from novelvideo.storage import media_relay


class FakeAuth:
    def __init__(self, ak: str, sk: str) -> None:
        self.ak = ak
        self.sk = sk


class FakeBucket:
    instances: list["FakeBucket"] = []

    def __init__(self, auth: FakeAuth, endpoint: str, bucket_name: str) -> None:
        self.auth = auth
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.puts: list[tuple[str, bytes, dict[str, str] | None]] = []
        self.signs: list[tuple[str, str, int, bool]] = []
        FakeBucket.instances.append(self)

    def put_object(self, key: str, data: bytes, headers=None):
        self.puts.append((key, data, headers))
        return object()

    def sign_url(self, method: str, key: str, ttl: int, slash_safe: bool = True) -> str:
        self.signs.append((method, key, ttl, slash_safe))
        return f"https://relay.test/{key}?signed=1"


@pytest.fixture(autouse=True)
def fake_oss2(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", raising=False)
    media_relay._HTTP_RELAY_CIRCUITS.clear()
    FakeBucket.instances.clear()
    fake_module = types.SimpleNamespace(Auth=FakeAuth, Bucket=FakeBucket)
    monkeypatch.setitem(sys.modules, "oss2", fake_module)


def test_aliyun_oss_relay_uploads_under_relay_prefix_and_signs_get_url() -> None:
    relay = media_relay.AliyunOSSRelay(
        endpoint="oss-cn-chengdu.aliyuncs.com",
        bucket_name="claymore-llm-relay",
        access_key_id="ak",
        access_key_secret="sk",
    )

    url = relay.upload_bytes(b"image-bytes", ext="PNG", ttl=1800)

    bucket = FakeBucket.instances[0]
    assert bucket.endpoint == "https://oss-cn-chengdu.aliyuncs.com"
    assert bucket.bucket_name == "claymore-llm-relay"
    key, data, headers = bucket.puts[0]
    assert data == b"image-bytes"
    assert headers == {
        "Content-Type": "image/png",
        "Content-Disposition": 'inline; filename="reference.png"',
    }
    assert re.match(r"^relay/\d{8}/[0-9a-f]{32}\.png$", key)
    assert bucket.signs == [("GET", key, 1800, True)]
    assert url == f"https://relay.test/{key}?signed=1"


def test_aliyun_oss_relay_requires_credentials() -> None:
    with pytest.raises(media_relay.MediaRelayConfigError) as exc_info:
        media_relay.AliyunOSSRelay(
            endpoint="oss-cn-chengdu.aliyuncs.com",
            bucket_name="claymore-llm-relay",
            access_key_id="",
            access_key_secret="sk",
        )

    assert "OSS_RELAY_AK" in str(exc_info.value)


def test_http_media_relay_posts_raw_bytes_with_private_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "url": "http://156.245.244.194:8782/media/fixture.webp",
            }

    class FakeClient:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            assert timeout == 60.0
            assert trust_env is False

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, params, headers, content):
            calls.append(
                {
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "content": content,
                }
            )
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL",
        "http://10.66.66.1:8782/upload",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "secret-token")
    relay = media_relay.HttpMediaRelay(
        upload_url="http://10.66.66.1:8782/upload",
        token="secret-token",
    )

    url = relay.upload_bytes(b"image-bytes", ext="webp", ttl=600)

    assert url == "http://156.245.244.194:8782/media/fixture.webp"
    assert calls == [
        {
            "url": "http://10.66.66.1:8782/upload",
            "params": {"ext": "webp", "ttl": 600},
            "headers": {
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/octet-stream",
            },
            "content": b"image-bytes",
        }
    ]


def test_http_media_relay_retries_one_transient_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    calls = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"url": "https://relay.test/media/retried.png"}

    class FakeClient:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            assert timeout == 60.0
            assert trust_env is False

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("relay response timed out")
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    relay = media_relay.HttpMediaRelay(
        upload_url="http://10.66.66.1:8782/upload",
        token="secret-token",
    )

    assert relay.upload_bytes(b"image-bytes") == "https://relay.test/media/retried.png"
    assert calls == 2


def test_http_media_relay_does_not_retry_http_status_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    calls = 0

    class FakeClient:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", "http://10.66.66.1:8782/upload")
            response = httpx.Response(400, request=request)
            response.raise_for_status()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    relay = media_relay.HttpMediaRelay(
        upload_url="http://10.66.66.1:8782/upload",
        token="secret-token",
    )

    with pytest.raises(media_relay.MediaRelayConfigError):
        relay.upload_bytes(b"image-bytes")
    assert calls == 1


def test_http_media_relay_circuit_opens_after_repeated_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    calls = 0

    class FakeClient:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("relay response timed out")

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL",
        "http://10.66.66.1:8782/upload",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "secret-token")
    relay = media_relay.HttpMediaRelay(
        upload_url="http://10.66.66.1:8782/upload",
        token="secret-token",
    )

    for _ in range(media_relay.HTTP_RELAY_FAILURE_THRESHOLD):
        with pytest.raises(media_relay.MediaRelayTransientError):
            relay.upload_bytes(b"image-bytes")
    calls_after_open = calls

    with pytest.raises(media_relay.MediaRelayTransientError, match="circuit is open"):
        relay.upload_bytes(b"image-bytes")
    assert calls == calls_after_open
    status = media_relay.build_http_media_relay_runtime_status()
    assert status["state"] == "open"
    assert status["consecutiveFailures"] == media_relay.HTTP_RELAY_FAILURE_THRESHOLD


def test_http_media_relay_runtime_status_never_exposes_endpoint_or_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL",
        "http://10.66.66.1:8782/upload",
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "secret-token")

    status = media_relay.build_http_media_relay_runtime_status()

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["state"] == "closed"
    serialized = str(status)
    assert "10.66.66.1" not in serialized
    assert "secret-token" not in serialized


def test_failover_media_relay_uses_secondary_only_for_transient_failure() -> None:
    class Primary:
        def upload_bytes(self, data: bytes, *, ext: str, ttl: int) -> str:
            raise media_relay.MediaRelayTransientError("temporary outage")

        def upload_file(self, path, *, ttl: int) -> str:
            raise AssertionError("upload_file should delegate through upload_bytes")

    class Fallback:
        def __init__(self) -> None:
            self.calls: list[tuple[bytes, str, int]] = []

        def upload_bytes(self, data: bytes, *, ext: str, ttl: int) -> str:
            self.calls.append((data, ext, ttl))
            return "https://fallback.test/reference.webp"

        def upload_file(self, path, *, ttl: int) -> str:
            raise AssertionError("upload_file should delegate through upload_bytes")

    fallback = Fallback()
    relay = media_relay.FailoverMediaRelay(Primary(), fallback)

    assert (
        relay.upload_bytes(b"image", ext="webp", ttl=600)
        == "https://fallback.test/reference.webp"
    )
    assert fallback.calls == [(b"image", "webp", 600)]


def test_get_media_relay_prefers_managed_http_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "http://10.66.66.1:8782/upload"
    )
    monkeypatch.setenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "secret-token")

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.HttpMediaRelay)


def test_incomplete_managed_http_relay_falls_back_to_saved_oss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "env-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "env-sk")
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "http://relay.invalid/upload"
    )
    monkeypatch.delenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", raising=False)
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=900,
        endpoint="db.endpoint",
        bucket="db-bucket",
        access_key_id="db-ak",
        access_key_secret="db-sk",
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.AliyunOSSRelay)


def test_missing_media_relay_is_optional_and_does_not_report_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "http://relay.invalid/upload"
    )
    monkeypatch.delenv("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", raising=False)
    monkeypatch.setattr(
        media_relay,
        "_configured_media_relay",
        lambda: (_ for _ in ()).throw(media_relay.MediaRelayConfigError("fixture")),
    )

    assert media_relay.is_media_relay_configured() is False
    with pytest.raises(media_relay.MediaRelayNotConfiguredError) as exc_info:
        media_relay.get_media_relay()
    assert "VILLAGE_CANVAS_MEDIA_RELAY_TOKEN" not in str(exc_info.value)


def test_build_inline_media_url_preserves_media_type() -> None:
    url = media_relay.build_inline_media_url(b"video-bytes", ext="mp4")

    assert url == "data:video/mp4;base64,dmlkZW8tYnl0ZXM="


def test_get_media_relay_uses_saved_runtime_oss_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "env-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "env-sk")
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=900,
        endpoint="db.endpoint",
        bucket="db-bucket",
        access_key_id="db-ak",
        access_key_secret="db-sk",
    )

    relay = media_relay.get_media_relay()
    url = relay.upload_bytes(b"image-bytes", ext="png", ttl=900)

    bucket = FakeBucket.instances[0]
    assert bucket.endpoint == "https://db.endpoint"
    assert bucket.bucket_name == "db-bucket"
    assert bucket.auth.ak == "db-ak"
    assert bucket.auth.sk == "db-sk"
    assert bucket.signs[0][2] == 900
    assert url.startswith("https://relay.test/relay/")


def test_get_media_relay_uses_saved_runtime_cloudinary_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "env-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "env-sk")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_FOLDER", "relay")
    save_media_relay_config(
        provider="cloudinary",
        ttl_seconds=900,
        cloud_name="demo-cloud",
        cloudinary_api_key="api-key",
        cloudinary_api_secret="api-secret",
        cloudinary_folder="village-canvas-relay",
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.CloudinaryRelay)
    assert relay._cloud_name == "demo-cloud"
    assert relay._api_key == "api-key"
    assert relay._api_secret == "api-secret"
    assert relay._folder == "village-canvas-relay"


def test_cloudinary_relay_rejects_invalid_cloud_name() -> None:
    with pytest.raises(media_relay.MediaRelayConfigError, match="cloud name is invalid"):
        media_relay.CloudinaryRelay(
            cloud_name="村长无限画布",
            api_key="api-key",
            api_secret="api-secret",
        )


def test_cloudinary_relay_uploads_bytes_with_signed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"secure_url": "https://res.cloudinary.com/demo/image/upload/abc.png"}

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, data, files):
            calls.append({"url": url, "data": data, "files": files})
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(media_relay.time, "time", lambda: 1_700_000_000)
    relay = media_relay.CloudinaryRelay(
        cloud_name="demo-cloud",
        api_key="api-key",
        api_secret="api-secret",
        folder="village-canvas-relay",
    )

    url = relay.upload_bytes(b"image-bytes", ext="PNG", ttl=900)

    assert url == "https://res.cloudinary.com/demo/image/upload/abc.png"
    assert calls[0]["url"] == "https://api.cloudinary.com/v1_1/demo-cloud/image/upload"
    assert calls[0]["data"] == {
        "folder": "village-canvas-relay",
        "timestamp": 1_700_000_000,
        "api_key": "api-key",
        "signature": media_relay._cloudinary_signature(
            {"folder": "village-canvas-relay", "timestamp": 1_700_000_000},
            "api-secret",
        ),
    }
    filename, data, content_type = calls[0]["files"]["file"]
    assert filename.endswith(".png")
    assert data == b"image-bytes"
    assert content_type == "image/png"


def test_cloudinary_relay_routes_mp4_to_signed_video_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"secure_url": "https://res.cloudinary.com/demo/video/upload/clip.mp4"}

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, data, files):
            calls.append({"url": url, "data": data, "files": files})
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(media_relay.time, "time", lambda: 1_700_000_000)
    relay = media_relay.CloudinaryRelay(
        cloud_name="demo-cloud",
        api_key="api-key",
        api_secret="api-secret",
    )

    url = relay.upload_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00", ext="mp4")

    assert url.endswith("/clip.mp4")
    assert calls[0]["url"] == "https://api.cloudinary.com/v1_1/demo-cloud/video/upload"
    assert calls[0]["data"] == {
        "timestamp": 1_700_000_000,
        "api_key": "api-key",
        "signature": media_relay._cloudinary_signature(
            {"timestamp": 1_700_000_000}, "api-secret"
        ),
    }
    _filename, _data, content_type = calls[0]["files"]["file"]
    assert content_type == "video/mp4"


def test_cloudinary_relay_returns_sanitized_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeResponse:
        status_code = 400
        text = "api-key api-secret should never be displayed"

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "400 Bad Request",
                request=httpx.Request("POST", "https://api.cloudinary.com/upload"),
                response=self,
            )

        def json(self) -> dict[str, dict[str, str]]:
            return {"error": {"message": "Invalid Signature"}}

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    relay = media_relay.CloudinaryRelay(
        cloud_name="demo-cloud",
        api_key="api-key",
        api_secret="api-secret",
    )

    with pytest.raises(media_relay.MediaRelayConfigError) as exc_info:
        relay.upload_bytes(b"image-bytes", ext="png")

    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "Invalid Signature" in message
    assert "api-key" not in message
    assert "api-secret" not in message


class CaptureRelay:
    def __init__(self) -> None:
        self.uploaded_bytes: list[tuple[bytes, str, int]] = []
        self.uploaded_files: list[tuple[Path, int]] = []

    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str:
        self.uploaded_bytes.append((data, ext, ttl))
        return f"https://relay.test/bytes.{ext}?ttl={ttl}"

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        self.uploaded_files.append((file_path, ttl))
        return f"https://relay.test/{file_path.name}?ttl={ttl}"


def test_ensure_image_url_returns_remote_url_without_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.ensure_image_url("https://example.test/a.png", ttl=60)

    assert url == "https://example.test/a.png"
    assert relay.uploaded_bytes == []
    assert relay.uploaded_files == []


def test_ensure_image_url_uploads_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.ensure_image_url("data:image/jpeg;base64,aGVsbG8=", ttl=60)

    assert url == "https://relay.test/bytes.jpg?ttl=60"
    assert relay.uploaded_bytes == [(b"hello", "jpg", 60)]
    assert relay.uploaded_files == []


def test_upload_image_bytes_defaults_to_raw_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.upload_image_bytes(b"raw-png-bytes", ext="png", ttl=60)

    assert url == "https://relay.test/bytes.png?ttl=60"
    assert relay.uploaded_bytes == [(b"raw-png-bytes", "png", 60)]


def test_upload_media_bytes_preserves_video_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.upload_media_bytes(b"video-bytes", ext="mp4", ttl=60)

    assert url == "https://relay.test/bytes.mp4?ttl=60"
    assert relay.uploaded_bytes == [(b"video-bytes", "mp4", 60)]


def test_upload_media_bytes_deduplicates_same_asset_for_same_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    media_relay._MEDIA_RELAY_CACHE.clear()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    first = media_relay.upload_media_bytes(b"same-video", ext="mp4", ttl=600)
    second = media_relay.upload_media_bytes(b"same-video", ext="mp4", ttl=600)

    assert first == second
    assert relay.uploaded_bytes == [(b"same-video", "mp4", 600)]


def test_upload_image_bytes_can_normalize_ai_reference_jpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)
    source = Image.new("RGBA", (32, 16), (255, 0, 0, 255))
    source_buf = io.BytesIO()
    source.save(source_buf, format="PNG")

    url = media_relay.upload_image_bytes(
        source_buf.getvalue(),
        ext="png",
        ttl=60,
        image_transform=media_relay.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    )

    assert url == "https://relay.test/bytes.jpg?ttl=60"
    assert len(relay.uploaded_bytes) == 1
    uploaded_bytes, ext, ttl = relay.uploaded_bytes[0]
    assert ext == "jpg"
    assert ttl == 60
    uploaded_image = Image.open(io.BytesIO(uploaded_bytes))
    assert uploaded_image.format == "JPEG"
    assert uploaded_image.mode == "RGB"
    assert uploaded_image.size == (32, 16)


def test_upload_image_bytes_downscales_large_ai_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)
    source = Image.new("RGB", (2048, 1024), (32, 64, 96))
    source_buf = io.BytesIO()
    source.save(source_buf, format="PNG")

    media_relay.upload_image_bytes(
        source_buf.getvalue(),
        ext="png",
        image_transform=media_relay.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    )

    uploaded_bytes, ext, _ttl = relay.uploaded_bytes[0]
    uploaded_image = Image.open(io.BytesIO(uploaded_bytes))
    assert ext == "jpg"
    assert uploaded_image.size == (768, 384)


def test_ensure_image_url_uploads_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)
    image_path = tmp_path / "portrait.png"
    image_path.write_bytes(b"png-bytes")

    url = media_relay.ensure_image_url(image_path, ttl=60)

    assert url == "https://relay.test/portrait.png?ttl=60"
    assert relay.uploaded_bytes == []
    assert relay.uploaded_files == [(image_path, 60)]
