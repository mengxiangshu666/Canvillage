"""Transient media relay for LLM-visible image URLs."""

from __future__ import annotations

import uuid
import base64
import hashlib
import io
import logging
import re
import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

AI_REFERENCE_JPEG_QUALITY = 90
AI_REFERENCE_MAX_EDGE = 768
IMAGE_TRANSFORM_AI_REFERENCE_JPEG = "ai_reference_jpeg"
HTTP_RELAY_FAILURE_THRESHOLD = 3
HTTP_RELAY_COOLDOWN_SECONDS = 30.0
MEDIA_RELAY_CACHE_MAX_ENTRIES = 256
MEDIA_RELAY_CACHE_SAFETY_SECONDS = 30.0

_CLOUDINARY_IMAGE_EXTENSIONS = frozenset(
    {
        "avif",
        "bmp",
        "gif",
        "heic",
        "heif",
        "ico",
        "jpg",
        "png",
        "svg",
        "tif",
        "tiff",
        "webp",
    }
)
_CLOUDINARY_VIDEO_EXTENSIONS = frozenset(
    {
        "3gp",
        "aac",
        "avi",
        "flac",
        "m4a",
        "m4v",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "mpeg",
        "mpg",
        "ogg",
        "oga",
        "ogv",
        "wav",
        "webm",
        "wmv",
    }
)


class MediaRelayConfigError(RuntimeError):
    """Raised when the media relay is not configured for URL input."""


class MediaRelayNotConfiguredError(MediaRelayConfigError):
    """Raised when no complete public media relay is configured."""


class MediaRelayTransientError(MediaRelayConfigError):
    """Raised for a temporary relay failure that may use a configured fallback."""


class _MediaRelay(Protocol):
    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str: ...

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str: ...


@dataclass
class _HttpRelayCircuitState:
    consecutive_failures: int = 0
    opened_until: float = 0.0
    last_error_type: str = ""
    last_success_at: float = 0.0


_HTTP_RELAY_CIRCUITS: dict[str, _HttpRelayCircuitState] = {}
_HTTP_RELAY_CIRCUITS_LOCK = threading.Lock()
_MEDIA_RELAY_CACHE: dict[tuple[str, str, str, str, int], tuple[str, float]] = {}
_MEDIA_RELAY_CACHE_LOCK = threading.Lock()


def _http_relay_circuit_before_request(upload_url: str) -> None:
    now = time.monotonic()
    with _HTTP_RELAY_CIRCUITS_LOCK:
        state = _HTTP_RELAY_CIRCUITS.setdefault(upload_url, _HttpRelayCircuitState())
        if state.opened_until > now:
            retry_after = max(1, int(state.opened_until - now + 0.999))
            raise MediaRelayTransientError(
                f"HTTP media relay circuit is open; retry after {retry_after}s"
            )
        if state.opened_until:
            state.opened_until = 0.0
            state.consecutive_failures = 0


def _record_http_relay_success(upload_url: str) -> None:
    with _HTTP_RELAY_CIRCUITS_LOCK:
        state = _HTTP_RELAY_CIRCUITS.setdefault(upload_url, _HttpRelayCircuitState())
        state.consecutive_failures = 0
        state.opened_until = 0.0
        state.last_error_type = ""
        state.last_success_at = time.time()


def _record_http_relay_transient_failure(upload_url: str, exc: BaseException) -> None:
    now = time.monotonic()
    with _HTTP_RELAY_CIRCUITS_LOCK:
        state = _HTTP_RELAY_CIRCUITS.setdefault(upload_url, _HttpRelayCircuitState())
        state.consecutive_failures += 1
        state.last_error_type = type(exc).__name__
        if state.consecutive_failures >= HTTP_RELAY_FAILURE_THRESHOLD:
            state.opened_until = now + HTTP_RELAY_COOLDOWN_SECONDS


def _relay_cache_namespace(relay: _MediaRelay) -> str:
    """Identify a configured relay without retaining credentials in the cache."""
    if isinstance(relay, CloudinaryRelay):
        return "cloudinary:" + ":".join((relay._cloud_name, relay._folder))
    if isinstance(relay, AliyunOSSRelay):
        return "aliyun_oss:" + ":".join((relay._endpoint, relay._bucket_name))
    if isinstance(relay, HttpMediaRelay):
        return "http:" + relay._upload_url
    if isinstance(relay, FailoverMediaRelay):
        return "failover:" + _relay_cache_namespace(relay._primary) + ":" + _relay_cache_namespace(relay._fallback)
    return f"{type(relay).__name__}:{id(relay)}"


def _media_relay_cache_key(
    relay: _MediaRelay,
    data: bytes,
    *,
    ext: str,
    image_transform: str | None,
    ttl_seconds: int,
) -> tuple[str, str, str, str, int]:
    return (
        _relay_cache_namespace(relay),
        hashlib.sha256(data).hexdigest(),
        _normalize_ext(ext),
        image_transform or "",
        ttl_seconds,
    )


def _get_cached_media_url(
    key: tuple[str, str, str, str, int],
) -> str | None:
    now = time.monotonic()
    with _MEDIA_RELAY_CACHE_LOCK:
        cached = _MEDIA_RELAY_CACHE.get(key)
        if cached is None:
            return None
        url, expires_at = cached
        if expires_at <= now:
            _MEDIA_RELAY_CACHE.pop(key, None)
            return None
        return url


def _cache_media_url(
    key: tuple[str, str, str, str, int],
    url: str,
    *,
    ttl_seconds: int,
) -> None:
    now = time.monotonic()
    with _MEDIA_RELAY_CACHE_LOCK:
        expired = [
            cache_key
            for cache_key, (_cached_url, expires_at) in _MEDIA_RELAY_CACHE.items()
            if expires_at <= now
        ]
        for cache_key in expired:
            _MEDIA_RELAY_CACHE.pop(cache_key, None)
        if len(_MEDIA_RELAY_CACHE) >= MEDIA_RELAY_CACHE_MAX_ENTRIES:
            _MEDIA_RELAY_CACHE.pop(next(iter(_MEDIA_RELAY_CACHE)))
        _MEDIA_RELAY_CACHE[key] = (
            url,
            now + max(1.0, ttl_seconds - MEDIA_RELAY_CACHE_SAFETY_SECONDS),
        )


def build_http_media_relay_runtime_status() -> dict[str, object]:
    """Return credential-free health telemetry for the managed HTTP relay."""
    upload_url = os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "").strip()
    configured = bool(
        upload_url and os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "").strip()
    )
    if not upload_url:
        return {
            "enabled": False,
            "configured": False,
            "state": "disabled",
            "consecutiveFailures": 0,
            "retryAfterSeconds": 0,
            "lastErrorType": "",
            "lastSuccessAt": None,
        }
    now = time.monotonic()
    with _HTTP_RELAY_CIRCUITS_LOCK:
        state = _HTTP_RELAY_CIRCUITS.get(upload_url, _HttpRelayCircuitState())
        retry_after = max(0, int(state.opened_until - now + 0.999))
        circuit_state = "open" if retry_after else "closed"
        return {
            "enabled": True,
            "configured": configured,
            "state": circuit_state,
            "consecutiveFailures": state.consecutive_failures,
            "retryAfterSeconds": retry_after,
            "lastErrorType": state.last_error_type,
            "lastSuccessAt": state.last_success_at or None,
        }


class AliyunOSSRelay:
    """Upload transient bytes to Aliyun OSS and return a short-lived signed URL."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket_name: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> None:
        missing = [
            name
            for name, value in {
                "OSS_RELAY_ENDPOINT": endpoint,
                "OSS_RELAY_BUCKET": bucket_name,
                "OSS_RELAY_AK": access_key_id,
                "OSS_RELAY_SK": access_key_secret,
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise MediaRelayConfigError(
                "OSS media relay config missing: " + ", ".join(missing)
            )

        try:
            import oss2
        except ImportError as exc:
            raise MediaRelayConfigError(
                "oss2 is not installed; install project dependencies before using media relay"
            ) from exc

        self._bucket = oss2.Bucket(
            oss2.Auth(access_key_id, access_key_secret),
            f"https://{endpoint.strip()}",
            bucket_name.strip(),
        )

    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")
        ext = _normalize_ext(ext)
        key = f"relay/{datetime.now(timezone.utc):%Y%m%d}/{uuid.uuid4().hex}.{ext}"
        content_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")
        self._bucket.put_object(
            key,
            data,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'inline; filename="reference.{ext}"',
            },
        )
        return self._bucket.sign_url("GET", key, int(ttl), slash_safe=True)

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


class CloudinaryRelay:
    """Upload transient bytes to Cloudinary and return its secure delivery URL."""

    def __init__(
        self,
        *,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        folder: str = "",
    ) -> None:
        missing = [
            name
            for name, value in {
                "CLOUDINARY_RELAY_CLOUD_NAME": cloud_name,
                "CLOUDINARY_RELAY_API_KEY": api_key,
                "CLOUDINARY_RELAY_API_SECRET": api_secret,
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise MediaRelayConfigError(
                "Cloudinary media relay config missing: " + ", ".join(missing)
            )
        from novelvideo.model_gateway_settings import is_valid_cloudinary_cloud_name

        if not is_valid_cloudinary_cloud_name(cloud_name):
            raise MediaRelayConfigError(
                "Cloudinary media relay cloud name is invalid; use the Cloudinary Cloud Name"
            )

        self._cloud_name = cloud_name.strip()
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._folder = str(folder or "").strip().strip("/")

    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")

        import httpx

        ext = _normalize_ext(ext)
        filename = f"{uuid.uuid4().hex}.{ext}"
        content_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")
        resource_type = _cloudinary_resource_type(data, ext=ext)
        signature_params: dict[str, str | int] = {"timestamp": int(time.time())}
        if self._folder:
            signature_params["folder"] = self._folder
        payload = {
            **signature_params,
            "api_key": self._api_key,
            "signature": _cloudinary_signature(signature_params, self._api_secret),
        }
        url = (
            f"https://api.cloudinary.com/v1_1/{self._cloud_name}/"
            f"{resource_type}/upload"
        )
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    url,
                    data=payload,
                    files={"file": (filename, data, content_type)},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _cloudinary_error_detail(
                exc.response,
                api_key=self._api_key,
                api_secret=self._api_secret,
            )
            raise MediaRelayConfigError(
                f"Cloudinary media relay upload rejected (HTTP {exc.response.status_code}): "
                f"{detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MediaRelayTransientError(
                "Cloudinary media relay transport failed: "
                f"{type(exc).__name__}"
            ) from exc

        result = response.json()
        secure_url = str(result.get("secure_url") or result.get("url") or "").strip()
        if not secure_url:
            raise MediaRelayConfigError("Cloudinary media relay upload returned no URL")
        return secure_url

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


class HttpMediaRelay:
    """Upload temporary bytes to the private HK relay and return a public URL."""

    def __init__(self, *, upload_url: str, token: str) -> None:
        if not str(upload_url or "").strip():
            raise MediaRelayConfigError("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL is required")
        if not str(token or "").strip():
            raise MediaRelayConfigError("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN is required")
        self._upload_url = upload_url.strip()
        self._token = token.strip()

    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")
        import httpx

        ext = _normalize_ext(ext)
        _http_relay_circuit_before_request(self._upload_url)
        try:
            with httpx.Client(timeout=60.0, trust_env=False) as client:
                for attempt in range(2):
                    try:
                        response = client.post(
                            self._upload_url,
                            params={"ext": ext, "ttl": max(60, int(ttl))},
                            headers={
                                "Authorization": f"Bearer {self._token}",
                                "Content-Type": "application/octet-stream",
                            },
                            content=data,
                        )
                        response.raise_for_status()
                        break
                    except httpx.TimeoutException:
                        if attempt == 1:
                            raise
                        logger.warning(
                            "HTTP media relay upload timed out; retrying once"
                        )
        except httpx.HTTPError as exc:
            transient = isinstance(exc, httpx.TransportError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and (exc.response.status_code == 429 or exc.response.status_code >= 500)
            )
            if transient:
                _record_http_relay_transient_failure(self._upload_url, exc)
                raise MediaRelayTransientError(
                    f"HTTP media relay upload temporarily failed: {type(exc).__name__}"
                ) from exc
            raise MediaRelayConfigError(f"HTTP media relay upload failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise MediaRelayConfigError("HTTP media relay returned invalid JSON") from exc
        url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MediaRelayConfigError("HTTP media relay returned no public URL")
        _record_http_relay_success(self._upload_url)
        return url

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


class FailoverMediaRelay(HttpMediaRelay):
    """Use a secondary configured relay only for transient primary failures."""

    def __init__(self, primary: _MediaRelay, fallback: _MediaRelay) -> None:
        self._primary = primary
        self._fallback = fallback

    def upload_bytes(self, data: bytes, *, ext: str = "png", ttl: int = 1800) -> str:
        try:
            return self._primary.upload_bytes(data, ext=ext, ttl=ttl)
        except MediaRelayTransientError as exc:
            logger.warning(
                "Primary media relay is temporarily unavailable; using configured fallback: %s",
                type(exc).__name__,
            )
            return self._fallback.upload_bytes(data, ext=ext, ttl=ttl)

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


def _configured_media_relay() -> AliyunOSSRelay | CloudinaryRelay:
    from novelvideo import config
    from novelvideo.model_gateway_settings import get_effective_media_relay_config

    relay_config = get_effective_media_relay_config(
        env_provider=getattr(config, "MEDIA_RELAY_PROVIDER", ""),
        env_ttl_seconds=getattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800),
        env_endpoint=getattr(config, "OSS_RELAY_ENDPOINT", ""),
        env_bucket=getattr(config, "OSS_RELAY_BUCKET", ""),
        env_access_key_id=getattr(config, "OSS_RELAY_AK", ""),
        env_access_key_secret=getattr(config, "OSS_RELAY_SK", ""),
        env_cloud_name=getattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", ""),
        env_cloudinary_api_key=getattr(config, "CLOUDINARY_RELAY_API_KEY", ""),
        env_cloudinary_api_secret=getattr(config, "CLOUDINARY_RELAY_API_SECRET", ""),
        env_cloudinary_folder=getattr(config, "CLOUDINARY_RELAY_FOLDER", ""),
    )
    provider = relay_config.provider
    if provider == "cloudinary":
        return CloudinaryRelay(
            cloud_name=relay_config.cloud_name,
            api_key=relay_config.cloudinary_api_key,
            api_secret=relay_config.cloudinary_api_secret,
            folder=relay_config.cloudinary_folder,
        )
    if provider != "aliyun_oss":
        raise MediaRelayConfigError(
            f"unsupported MEDIA_RELAY_PROVIDER: {provider or '-'}"
        )
    return AliyunOSSRelay(
        endpoint=relay_config.endpoint,
        bucket_name=relay_config.bucket,
        access_key_id=relay_config.access_key_id,
        access_key_secret=relay_config.access_key_secret,
    )


def get_media_relay() -> (
    AliyunOSSRelay | CloudinaryRelay | HttpMediaRelay | FailoverMediaRelay
):
    """Build the configured media relay.

    The relay is intentionally not cached so tests can monkeypatch config and
    failed or rotated credentials are not hidden behind process-local state.
    """
    http_upload_url = os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "").strip()
    http_token = os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "").strip()
    if http_upload_url and http_token:
        primary = HttpMediaRelay(upload_url=http_upload_url, token=http_token)
        try:
            fallback = _configured_media_relay()
        except MediaRelayConfigError:
            return primary
        return FailoverMediaRelay(primary, fallback)

    if http_upload_url or http_token:
        logger.warning(
            "Ignoring incomplete HTTP media relay configuration; trying the optional "
            "OSS/Cloudinary relay instead"
        )

    try:
        return _configured_media_relay()
    except MediaRelayConfigError as exc:
        raise MediaRelayNotConfiguredError(
            "No media relay is configured; local references may use inline data URLs "
            "when the selected upstream supports them"
        ) from exc


def is_media_relay_configured() -> bool:
    """Return whether a complete optional public media relay is available."""
    http_upload_url = os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_UPLOAD_URL", "").strip()
    http_token = os.environ.get("VILLAGE_CANVAS_MEDIA_RELAY_TOKEN", "").strip()
    if http_upload_url and http_token:
        return True
    try:
        _configured_media_relay()
    except MediaRelayConfigError:
        return False
    return True


def _default_media_relay_ttl_seconds() -> int:
    from novelvideo import config
    from novelvideo.model_gateway_settings import get_effective_media_relay_config

    return get_effective_media_relay_config(
        env_provider=getattr(config, "MEDIA_RELAY_PROVIDER", ""),
        env_ttl_seconds=getattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800),
        env_endpoint=getattr(config, "OSS_RELAY_ENDPOINT", ""),
        env_bucket=getattr(config, "OSS_RELAY_BUCKET", ""),
        env_access_key_id=getattr(config, "OSS_RELAY_AK", ""),
        env_access_key_secret=getattr(config, "OSS_RELAY_SK", ""),
        env_cloud_name=getattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", ""),
        env_cloudinary_api_key=getattr(config, "CLOUDINARY_RELAY_API_KEY", ""),
        env_cloudinary_api_secret=getattr(config, "CLOUDINARY_RELAY_API_SECRET", ""),
        env_cloudinary_folder=getattr(config, "CLOUDINARY_RELAY_FOLDER", ""),
    ).ttl_seconds


def prepare_image_bytes(
    data: bytes,
    *,
    ext: str = "png",
    image_transform: str | None = None,
) -> tuple[bytes, str]:
    """Prepare image bytes for either inline or relayed provider input."""
    return _apply_image_transform(data, ext=ext, image_transform=image_transform)


def build_inline_media_url(
    data: bytes,
    *,
    ext: str = "png",
    image_transform: str | None = None,
) -> str:
    """Build a data URL for providers that accept inline reference media."""
    if not data:
        raise ValueError("cannot inline empty media bytes")
    data, ext = prepare_image_bytes(data, ext=ext, image_transform=image_transform)
    normalized_ext = _normalize_ext(ext)
    content_type = mimetypes.types_map.get(
        f".{normalized_ext}", "application/octet-stream"
    )
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def upload_image_bytes(
    data: bytes,
    *,
    ext: str = "png",
    ttl: int | None = None,
    image_transform: str | None = None,
) -> str:
    return upload_media_bytes(
        data,
        ext=ext,
        ttl=ttl,
        image_transform=image_transform,
    )


def upload_media_bytes(
    data: bytes,
    *,
    ext: str = "png",
    ttl: int | None = None,
    image_transform: str | None = None,
) -> str:
    """Upload image, video, audio, or raw bytes to the configured public relay.

    The image-named compatibility helper above delegates here.  Keeping the
    generic entry point prevents video/audio references from being forced into
    image-only Cloudinary endpoints.
    """
    ttl_seconds = int(ttl if ttl is not None else _default_media_relay_ttl_seconds())
    data, ext = prepare_image_bytes(data, ext=ext, image_transform=image_transform)
    relay = get_media_relay()
    cache_key = _media_relay_cache_key(
        relay,
        data,
        ext=ext,
        image_transform=image_transform,
        ttl_seconds=ttl_seconds,
    )
    cached_url = _get_cached_media_url(cache_key)
    if cached_url:
        return cached_url
    url = relay.upload_bytes(data, ext=ext, ttl=ttl_seconds)
    _cache_media_url(cache_key, url, ttl_seconds=ttl_seconds)
    return url


def upload_image_file(path: str | Path, *, ttl: int | None = None) -> str:
    """Upload a local image file to the relay and return a short-lived URL."""
    ttl_seconds = int(ttl if ttl is not None else _default_media_relay_ttl_seconds())
    return get_media_relay().upload_file(path, ttl=ttl_seconds)


def ensure_image_url(reference: str | Path, *, ttl: int | None = None) -> str:
    """Return a remote URL for an image reference.

    - http/https URLs are already model-visible and are returned unchanged.
    - data:image/...;base64 references are uploaded to the relay.
    - local files are uploaded to the relay.

    This keeps newAPI/HuiMeng image calls from receiving local paths or data URLs
    when the upstream channel requires fetchable URL inputs.
    """
    value = str(reference or "").strip()
    if not value:
        raise ValueError("image reference is empty")
    if _is_remote_url(value):
        return value
    data_url_match = _DATA_IMAGE_URL_RE.match(value)
    if data_url_match:
        ext = _normalize_ext(data_url_match.group("ext"))
        try:
            data = base64.b64decode(data_url_match.group("data"), validate=True)
        except Exception as exc:
            raise ValueError("invalid base64 image data URL") from exc
        return upload_image_bytes(data, ext=ext, ttl=ttl)

    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"image reference is not a URL or local file: {value}")
    return upload_image_file(path, ttl=ttl)


def _apply_image_transform(
    data: bytes,
    *,
    ext: str,
    image_transform: str | None,
) -> tuple[bytes, str]:
    if not image_transform:
        return data, ext
    if image_transform == IMAGE_TRANSFORM_AI_REFERENCE_JPEG:
        return _normalize_ai_reference_image(data, ext=ext)
    raise ValueError(f"unsupported image_transform: {image_transform}")


def _normalize_ai_reference_image(data: bytes, *, ext: str = "png") -> tuple[bytes, str]:
    original_ext = _normalize_ext(ext)

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError

        with Image.open(io.BytesIO(data)) as img:
            original_format = (img.format or original_ext or "").upper() or "UNKNOWN"
            original_mode = img.mode
            original_size = img.size
            img = ImageOps.exif_transpose(img)
            if img.mode in {"RGBA", "LA"} or (
                img.mode == "P" and "transparency" in img.info
            ):
                background = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.convert("RGBA").getchannel("A")
                background.paste(img.convert("RGBA"), mask=alpha)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if max(img.size) > AI_REFERENCE_MAX_EDGE:
                img.thumbnail(
                    (AI_REFERENCE_MAX_EDGE, AI_REFERENCE_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                )

            normalized_size = img.size
            buffer = io.BytesIO()
            img.save(
                buffer,
                format="JPEG",
                quality=AI_REFERENCE_JPEG_QUALITY,
                optimize=True,
            )
            normalized = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info(
            "Village Infinite Canvas API reference image normalize skipped: ext=%s bytes=%d error=%s",
            original_ext,
            len(data),
            exc,
        )
        return data, original_ext

    logger.info(
        "Village Infinite Canvas API reference image normalized: %s %dx%d %s %.1fKB -> "
        "JPEG %dx%d RGB %.1fKB q=%d",
        original_format,
        original_size[0],
        original_size[1],
        original_mode,
        len(data) / 1024,
        normalized_size[0],
        normalized_size[1],
        len(normalized) / 1024,
        AI_REFERENCE_JPEG_QUALITY,
    )
    return normalized, "jpg"


_DATA_IMAGE_URL_RE = re.compile(
    r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)


def _is_remote_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _cloudinary_resource_type(data: bytes, *, ext: str) -> str:
    """Return the Cloudinary upload resource type without trusting one signal.

    Cloudinary treats audio as ``video``.  Strong file signatures take priority
    for extension-less local data URLs; an explicit known extension otherwise
    supplies the type.  Everything else is uploaded as ``raw`` rather than
    being incorrectly sent to the image endpoint.
    """
    detected = _cloudinary_magic_resource_type(data)
    if detected:
        return detected
    normalized_ext = _normalize_ext(ext)
    if normalized_ext in _CLOUDINARY_IMAGE_EXTENSIONS:
        return "image"
    if normalized_ext in _CLOUDINARY_VIDEO_EXTENSIONS:
        return "video"
    return "raw"


def _cloudinary_magic_resource_type(data: bytes) -> str | None:
    header = bytes(data[:32])
    if header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return "image"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image"
    if header.startswith((b"BM", b"II*\x00", b"MM\x00*")):
        return "image"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video"
    if header.startswith((b"\x1aE\xdf\xa3", b"OggS", b"ID3", b"fLaC")):
        return "video"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "video"
    return None


def _cloudinary_signature(
    params: dict[str, str | int], api_secret: str
) -> str:
    """Generate the signed-upload digest required by the Cloudinary Upload API."""
    payload = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
        if value is not None and str(value) != ""
    )
    return hashlib.sha1(f"{payload}{api_secret}".encode("utf-8")).hexdigest()


def _cloudinary_error_detail(
    response: object,
    *,
    api_key: str,
    api_secret: str,
) -> str:
    """Extract a small, credential-free explanation from a Cloudinary error."""
    detail = "provider rejected the upload"
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or detail)
        elif error:
            detail = str(error)
    if detail == "provider rejected the upload":
        detail = str(getattr(response, "text", "") or detail)
    detail = detail.replace(api_key, "[redacted]").replace(api_secret, "[redacted]")
    detail = " ".join(detail.split())[:300]
    return detail or "provider rejected the upload"


def _normalize_ext(ext: str) -> str:
    ext = (ext or "png").strip().lower().lstrip(".")
    if ext in {"jpeg", "pjpeg"}:
        return "jpg"
    if ext == "svg+xml":
        return "svg"
    return ext or "png"
