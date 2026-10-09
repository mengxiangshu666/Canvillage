"""Model gateway configuration endpoints for CE."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from novelvideo import config as app_config
from novelvideo.generators.video.direct_models import probe_direct_video_model
from novelvideo.generators.video.direct_video_probe import (
    discover_direct_video_models,
)
from novelvideo.model_gateway_settings import (
    MODE_UNIFIED,
    DirectModelClearNotConfirmed,
    build_direct_models_status,
    build_direct_video_models_status,
    build_media_relay_status,
    build_model_gateway_status,
    canonical_direct_model_kind,
    get_effective_media_relay_config,
    is_valid_cloudinary_cloud_name,
    get_bound_direct_model_api_key,
    get_model_gateway_settings,
    get_unified_gateway_environment,
    normalize_direct_model_id,
    normalize_relay_base_url,
    normalize_api_key,
    save_direct_models,
    save_direct_video_models,
    save_media_relay_config,
    save_custom_newapi_gateway,
    save_unified_gateway,
    save_newapi_database_config,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_newapi_provider_channels,
    get_newapi_provider_channel,
    set_model_gateway_mode,
)
from novelvideo.model_gateway_runtime import refresh_model_gateway_runtime
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.storage.media_relay import build_http_media_relay_runtime_status
from novelvideo.newapi_provisioner import (
    build_channel_payload,
    build_provisioner_status,
    create_or_reuse_relay_token,
    ensure_newapi_setup,
    ensure_admin_access_token,
    get_provisioner_config,
    mask_token,
    NewApiSetupCredentials,
    require_provisioner_enabled,
    upsert_channel,
    update_provider_channel_credentials,
)

router = APIRouter(prefix="/model-gateway")


CUSTOM_MEDIA_MODEL_NAMES = {
    "LingShan-G2",
    "LingShan-NB-2",
    "seedance-1.0-pro-fast",
    "seedance-1.5-pro",
    "seedance-2.0",
    "seedance-2.0-fast",
    "happyhorse-1.0",
    "index-tts-2",
    "LingShan-MU-11",
}
OFFICIAL_ONLY_MEDIA_MODEL_NAMES = {
    "seedance-2.0-value",
    "seedance-2.0-fast-value",
}


def require_ce_gateway_management() -> None:
    """Reject CE-local gateway mutations from an EE-composed process."""
    if not is_ce_effective():
        raise PermissionError("model gateway management is only available in CE")
    require_provisioner_enabled()


def require_ce_direct_video_management() -> None:
    """Direct endpoints are local settings, independent from NewAPI provisioning."""
    if not is_ce_effective():
        raise PermissionError("direct video model management is only available in CE")


def require_ce_direct_model_management() -> None:
    """Non-video direct models are local settings, never NewAPI channels."""
    if not is_ce_effective():
        raise PermissionError("direct model management is only available in CE")


class OfficialGatewayBody(BaseModel):
    new_api_api_key: str = Field(alias="newApiApiKey")


class UnifiedGatewayBody(BaseModel):
    new_api_api_key: str = Field(alias="newApiApiKey")


class MediaRelayConfigBody(BaseModel):
    provider: str = "aliyun_oss"
    ttl_seconds: int = Field(default=1800, alias="ttlSeconds")
    endpoint: str | None = None
    bucket: str | None = None
    access_key_id: str | None = Field(default=None, alias="accessKeyId")
    access_key_secret: str | None = Field(default=None, alias="accessKeySecret")
    cloud_name: str | None = Field(default=None, alias="cloudName")
    cloudinary_api_key: str | None = Field(default=None, alias="apiKey")
    cloudinary_api_secret: str | None = Field(default=None, alias="apiSecret")
    cloudinary_folder: str | None = Field(default=None, alias="apiFolder")


class DirectVideoModelBody(BaseModel):
    id: str | None = None
    label: str
    model_id: str = Field(alias="modelId")
    base_url: str = Field(alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    protocol: str | None = "auto"
    enabled: bool = True
    is_default: bool = Field(default=False, alias="isDefault")
    #: 渠道自带的出线合同。留空表示「不带合同」：保存时会沿用已存的那份，
    #: 都没有就留给解析层走源码种子/通用兜底。写坏了在保存处直接 400。
    wire_contract: dict[str, Any] | None = Field(default=None, alias="wireContract")


class DirectVideoModelsBody(BaseModel):
    models: list[DirectVideoModelBody] = Field(default_factory=list)
    confirm_clear: bool = Field(default=False, alias="confirmClear")


#: Canonical chat/image/embedding/audio plus the retired chat aliases.
DirectModelKindPath = Literal["chat", "agent", "text", "vision", "image", "embedding"]


class DirectModelDiscoveryBody(BaseModel):
    id: str | None = None
    base_url: str = Field(alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    protocol: str | None = "auto"


class DirectModelBody(BaseModel):
    id: str | None = None
    label: str
    model_id: str = Field(alias="modelId")
    base_url: str = Field(alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    protocol: str | None = "auto"
    enabled: bool = True
    is_default: bool = Field(default=False, alias="isDefault")
    supported_modes: list[str] | None = Field(default=None, alias="supportedModes")
    # Only meaningful for the unified chat family; ignored by image/audio/embedding.
    supports_tools: bool | None = Field(default=None, alias="supportsTools")
    supports_vision: bool | None = Field(default=None, alias="supportsVision")


class DirectModelsBody(BaseModel):
    models: list[DirectModelBody] = Field(default_factory=list)
    confirm_clear: bool = Field(default=False, alias="confirmClear")


class NewApiDatabaseBody(BaseModel):
    sql_dsn: str | None = Field(default=None, alias="sqlDsn")
    sqlite_path: str | None = Field(default=None, alias="sqlitePath")
    admin_username: str | None = Field(default=None, alias="adminUsername")


class NewApiInitBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    setup_username: str | None = Field(default=None, alias="setupUsername")
    setup_password: str | None = Field(default=None, alias="setupPassword")
    setup_confirm_password: str | None = Field(
        default=None, alias="setupConfirmPassword"
    )
    token_name: str | None = Field(default=None, alias="tokenName")
    group: str = "default"
    unlimited_quota: bool = Field(default=True, alias="unlimitedQuota")
    remain_quota: int = Field(default=0, alias="remainQuota")
    expired_time: int = Field(default=-1, alias="expiredTime")
    reuse_existing: bool = Field(default=True, alias="reuseExisting")


class CreateChannelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str = "ali"
    type: int | None = None
    name: str | None = None
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    model_mapping: dict[str, str] = Field(alias="modelMapping")
    group: str = "default"
    priority: int = 0
    weight: int = 0
    base_url: str | None = Field(default=None, alias="baseUrl")
    test_model: str | None = Field(default=None, alias="testModel")


class ChannelSpec(BaseModel):
    provider: str = "ali"
    type: int | None = None
    name: str | None = None
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    model_mapping: dict[str, str] = Field(alias="modelMapping")
    group: str = "default"
    priority: int = 0
    weight: int = 0
    base_url: str | None = Field(default=None, alias="baseUrl")
    test_model: str | None = Field(default=None, alias="testModel")


class CreateChannelsBatchBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    channels: list[ChannelSpec] = Field(min_length=1)


class ProviderChannelConfigBody(BaseModel):
    provider: str
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    base_url: str | None = Field(default=None, alias="baseUrl")


class SaveProviderChannelsBody(BaseModel):
    channels: list[ProviderChannelConfigBody] = Field(default_factory=list)


class SyncProviderChannelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    base_url: str | None = Field(default=None, alias="baseUrl")


class MediaModelConfigBody(BaseModel):
    provider: str
    upstream_model: str | None = Field(default=None, alias="upstreamModel")


class SaveMediaModelsBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    models: dict[str, MediaModelConfigBody] = Field(default_factory=dict)


class SaveEmbeddingModelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str
    upstream_model: str = Field(alias="upstreamModel")
    dimension: int
    batch_size: int | None = Field(default=None, alias="batchSize")
    send_dimensions: bool = Field(default=True, alias="sendDimensions")


def _permission_error(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc))


def _get_provisioner_config_from_request(
    new_api_base_url: str | None,
    database: NewApiDatabaseBody | None,
):
    return get_provisioner_config(
        new_api_base_url,
        sql_dsn=database.sql_dsn if database else None,
        sqlite_path=database.sqlite_path if database else None,
        admin_username=database.admin_username if database else None,
    )


def _save_request_database_config(
    cfg,
    database: NewApiDatabaseBody | None,
) -> None:
    if database is None:
        return
    save_newapi_database_config(
        sql_dsn=cfg.sql_dsn,
        sqlite_path=cfg.sqlite_path,
        admin_username=cfg.admin_username,
    )


def _setup_credentials_from_request(body: NewApiInitBody) -> NewApiSetupCredentials:
    username = (body.setup_username or "").strip()
    if not username and body.database and body.database.admin_username:
        username = body.database.admin_username.strip()
    return NewApiSetupCredentials(
        username=username,
        password=body.setup_password or "",
        confirm_password=body.setup_confirm_password or "",
        self_use_mode_enabled=True,
        demo_site_enabled=False,
    )


def _build_channel_payload_from_spec(
    spec: ChannelSpec | CreateChannelBody,
) -> dict[str, Any]:
    saved_channel = get_newapi_provider_channel(spec.provider) or {}
    return build_channel_payload(
        provider=spec.provider,
        channel_type=spec.type,
        name=spec.name,
        upstream_key=spec.upstream_key or saved_channel.get("upstreamKey", ""),
        model_mapping=spec.model_mapping,
        group=spec.group,
        priority=spec.priority,
        weight=spec.weight,
        base_url=spec.base_url or saved_channel.get("baseUrl", ""),
        test_model=spec.test_model,
    )


def _build_media_model_channel_specs(
    models: dict[str, MediaModelConfigBody],
) -> tuple[list[ChannelSpec], dict[str, dict[str, str]]]:
    if not models:
        raise ValueError("models must be a non-empty JSON object")

    grouped: dict[str, dict[str, str]] = {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_model, item in models.items():
        model = str(raw_model or "").strip()
        if not model:
            raise ValueError("models contains an empty model name")
        if model in OFFICIAL_ONLY_MEDIA_MODEL_NAMES:
            raise ValueError(f"media model {model} is official-channel only")
        if model not in CUSTOM_MEDIA_MODEL_NAMES:
            raise ValueError(f"unsupported media model: {model}")
        provider = str(item.provider or "").strip().lower()
        if not provider:
            raise ValueError(f"provider is required for media model {model}")
        upstream_model = (item.upstream_model or "").strip() or model
        grouped.setdefault(provider, {})[model] = upstream_model
        normalized[model] = {
            "provider": provider,
            "upstreamModel": "" if upstream_model == model else upstream_model,
        }

    specs = [
        ChannelSpec(provider=provider, modelMapping=mapping)
        for provider, mapping in grouped.items()
    ]
    return specs, normalized


def _build_embedding_model_channel_spec(
    body: SaveEmbeddingModelBody,
) -> tuple[ChannelSpec, dict[str, Any]]:
    provider = str(body.provider or "").strip().lower()
    upstream_model = str(body.upstream_model or "").strip()
    dimension = int(body.dimension)
    batch_size = int(body.batch_size or 0)
    if not provider:
        raise ValueError("provider is required for embedding model")
    if not upstream_model:
        raise ValueError("upstreamModel is required for embedding model")
    if dimension <= 0:
        raise ValueError("dimension must be positive")
    if body.batch_size is not None and batch_size <= 0:
        raise ValueError("batchSize must be positive")
    normalized = {
        "provider": provider,
        "upstreamModel": upstream_model,
        "dimension": dimension,
        # Kept in the response/config schema for compatibility with older
        # clients. Runtime request behavior is controlled by the internal
        # EmbeddingModelSpec, not by this user-supplied field.
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if batch_size > 0:
        normalized["batchSize"] = batch_size
    return (
        ChannelSpec(
            provider=provider,
            modelMapping={"DC-cognee-embedding": upstream_model},
        ),
        normalized,
    )


def _mask_sent_channel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "channel": {
            **payload["channel"],
            "key": mask_token(payload["channel"]["key"]),
        },
    }
def _media_relay_status() -> dict[str, Any]:
    status = build_media_relay_status(
        env_provider=app_config.MEDIA_RELAY_PROVIDER,
        env_ttl_seconds=app_config.MEDIA_RELAY_TTL_SECONDS,
        env_endpoint=app_config.OSS_RELAY_ENDPOINT,
        env_bucket=app_config.OSS_RELAY_BUCKET,
        env_access_key_id=app_config.OSS_RELAY_AK,
        env_access_key_secret=app_config.OSS_RELAY_SK,
        env_cloud_name=app_config.CLOUDINARY_RELAY_CLOUD_NAME,
        env_cloudinary_api_key=app_config.CLOUDINARY_RELAY_API_KEY,
        env_cloudinary_api_secret=app_config.CLOUDINARY_RELAY_API_SECRET,
        env_cloudinary_folder=app_config.CLOUDINARY_RELAY_FOLDER,
    )

    status["httpRuntime"] = build_http_media_relay_runtime_status()
    return status


def _launcher_unified_gateway_base_url() -> str:
    """Use the one portable-launcher endpoint; retain NewAPI as a code alias."""

    return normalize_relay_base_url(
        app_config.VILLAGE_CANVAS_GATEWAY_BASE_URL or app_config.NEWAPI_BASE_URL
    )


@router.get("/config")
async def get_model_gateway_config() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            **build_model_gateway_status(
                official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
                official_api_key=app_config.NEWAPI_API_KEY,
            ),
            "provisioner": build_provisioner_status(),
            "mediaRelay": _media_relay_status(),
            "directVideoModels": build_direct_video_models_status(),
            "directModels": build_direct_models_status(),
        },
    }


@router.post("/official/enable")
async def enable_official_gateway() -> dict[str, Any]:
    """Compatibility alias: activate the unified local gateway, never an external route."""
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    status = build_model_gateway_status(
        official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    if not status["unified"]["configured"]:
        settings = get_model_gateway_settings()
        legacy_key = normalize_api_key(
            settings.get("official_newapi_api_key", "")
            or settings.get("custom_newapi_api_key", "")
            or app_config.VILLAGE_CANVAS_GATEWAY_API_KEY
            or app_config.NEWAPI_API_KEY
        )
        base_url = _launcher_unified_gateway_base_url()
        if not base_url or not legacy_key:
            raise HTTPException(status_code=400, detail="unified gateway is not configured")
        save_unified_gateway(base_url=base_url, api_key=legacy_key, activate=True)
    set_model_gateway_mode(MODE_UNIFIED)
    runtime = refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/official/config")
async def save_official_gateway_config(body: OfficialGatewayBody) -> dict[str, Any]:
    """Compatibility alias: old one-key setup now writes the unified gateway."""
    return await save_unified_gateway_config(UnifiedGatewayBody(newApiApiKey=body.new_api_api_key))


@router.post("/unified/enable")
async def enable_unified_gateway() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    status = build_model_gateway_status(
        official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    if not status["unified"]["configured"]:
        raise HTTPException(status_code=400, detail="unified gateway is not configured")
    set_model_gateway_mode(MODE_UNIFIED)
    runtime = refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/unified/config")
async def save_unified_gateway_config(body: UnifiedGatewayBody) -> dict[str, Any]:
    """Save the one user-facing Key against the launcher-owned HK endpoint."""
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    api_key = normalize_api_key(body.new_api_api_key)
    if not api_key:
        raise HTTPException(status_code=400, detail="newApiApiKey is required")
    base_url = _launcher_unified_gateway_base_url()
    if not base_url:
        env_base_url, _env_api_key = get_unified_gateway_environment()
        base_url = env_base_url
    if not base_url:
        raise HTTPException(status_code=400, detail="VILLAGE_CANVAS_GATEWAY_BASE_URL is required")
    save_unified_gateway(base_url=base_url, api_key=api_key, activate=True)
    runtime = refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/media-relay/config")
async def save_media_relay_settings(body: MediaRelayConfigBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    provider = body.provider.strip().lower()
    if provider not in {"aliyun_oss", "cloudinary"}:
        raise HTTPException(status_code=400, detail="unsupported media relay provider")
    if body.ttl_seconds <= 0:
        raise HTTPException(status_code=400, detail="ttlSeconds must be positive")
    current = get_effective_media_relay_config(
        env_provider=app_config.MEDIA_RELAY_PROVIDER,
        env_ttl_seconds=app_config.MEDIA_RELAY_TTL_SECONDS,
        env_endpoint=app_config.OSS_RELAY_ENDPOINT,
        env_bucket=app_config.OSS_RELAY_BUCKET,
        env_access_key_id=app_config.OSS_RELAY_AK,
        env_access_key_secret=app_config.OSS_RELAY_SK,
        env_cloud_name=app_config.CLOUDINARY_RELAY_CLOUD_NAME,
        env_cloudinary_api_key=app_config.CLOUDINARY_RELAY_API_KEY,
        env_cloudinary_api_secret=app_config.CLOUDINARY_RELAY_API_SECRET,
        env_cloudinary_folder=app_config.CLOUDINARY_RELAY_FOLDER,
    )

    def merge_field(value: str | None, saved: str, *, secret: bool = False) -> str:
        if value is None:
            return saved
        normalized = value.strip()
        if secret and not normalized:
            return saved
        return normalized

    endpoint = merge_field(body.endpoint, current.endpoint)
    bucket = merge_field(body.bucket, current.bucket)
    access_key_id = merge_field(body.access_key_id, current.access_key_id, secret=True)
    access_key_secret = merge_field(
        body.access_key_secret, current.access_key_secret, secret=True
    )
    cloud_name = merge_field(body.cloud_name, current.cloud_name)
    cloudinary_api_key = merge_field(
        body.cloudinary_api_key, current.cloudinary_api_key, secret=True
    )
    cloudinary_api_secret = merge_field(
        body.cloudinary_api_secret, current.cloudinary_api_secret, secret=True
    )
    cloudinary_folder = merge_field(
        body.cloudinary_folder, current.cloudinary_folder
    ).strip("/")
    if provider == "cloudinary":
        if not is_valid_cloudinary_cloud_name(cloud_name):
            raise HTTPException(
                status_code=400,
                detail=(
                    "cloudName must contain only ASCII letters, digits, underscores, "
                    "or hyphens"
                ),
            )
        required = {
            "cloudName": cloud_name,
            "apiKey": cloudinary_api_key,
            "apiSecret": cloudinary_api_secret,
        }
    else:
        required = {
            "endpoint": endpoint,
            "bucket": bucket,
            "accessKeyId": access_key_id,
            "accessKeySecret": access_key_secret,
        }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"missing fields: {', '.join(missing)}"
        )
    save_media_relay_config(
        provider=provider,
        ttl_seconds=body.ttl_seconds,
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        cloud_name=cloud_name,
        cloudinary_api_key=cloudinary_api_key,
        cloudinary_api_secret=cloudinary_api_secret,
        cloudinary_folder=cloudinary_folder,
    )
    return {"ok": True, "data": _media_relay_status()}


def _probe_checked_at(result: Any) -> str:
    """这次检测**真实发生**的时间（UTC），用来区分实时结果和旧缓存。"""

    from datetime import datetime, timezone

    return str(
        (result or {}).get("checkedAt")
        or datetime.now(timezone.utc).isoformat()
    )


@router.post("/direct-video-models")
async def save_direct_video_model_settings(
    body: DirectVideoModelsBody,
) -> dict[str, Any]:
    """Save direct video endpoints without routing them through NewAPI."""
    try:
        require_ce_direct_video_management()
        saved = save_direct_video_models(
            [item.model_dump(by_alias=True) for item in body.models],
            confirm_clear=body.confirm_clear,
        )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except DirectModelClearNotConfirmed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": build_direct_video_models_status(),
        "count": len(saved),
    }


@router.post("/direct-video-models/probe")
async def probe_direct_video_model_settings(
    body: DirectVideoModelBody,
) -> dict[str, Any]:
    """Probe the model catalog and read-only video-task auth; never create video."""
    try:
        require_ce_direct_video_management()
        api_key = normalize_api_key(body.api_key)
        if not api_key and body.id:
            api_key = get_bound_direct_model_api_key(
                "video",
                body.id,
                body.base_url,
            )
        if not api_key:
            raise ValueError("apiKey is required to probe a new direct video model")
        model_id = normalize_direct_model_id("video", body.model_id)
        result = probe_direct_video_model(
            upstream_model=model_id,
            base_url=body.base_url,
            api_key=api_key,
            protocol=str(body.protocol or "auto"),
        )
        capability = result.get("capability")
        if result.get("ok"):
            from novelvideo.generators.video.direct_video_capability_cache import (
                record_capability,
            )

            try:
                catalog_matches = bool(result.get("modelFound"))
                capability_payload = (
                    dict(capability) if isinstance(capability, dict) else {}
                )
                result["capability"] = record_capability(
                    base_url=body.base_url,
                    protocol=str(result.get("protocol") or "openai-video"),
                    upstream_model=model_id,
                    capability={
                        **capability_payload,
                        "verificationStatus": "catalog-confirmed"
                        if catalog_matches
                        else "catalog-mismatch",
                        "verificationStage": "catalog",
                        "probeStatus": "fresh" if catalog_matches else "model-not-found",
                        "modelFound": catalog_matches,
                        "discoveredModelCount": int(
                            result.get("discoveredModelCount") or 0
                        ),
                        "supportedProtocols": result.get("supportedProtocols") or [],
                        "detectedProtocol": result.get("protocol") or "openai-video",
                        "adapterFamily": result.get("adapterFamily"),
                        "adapterConfidence": result.get("adapterConfidence"),
                        "adapterEvidence": result.get("adapterEvidence"),
                        "openapiUrl": result.get("openapiUrl"),
                        "openapiPaths": result.get("openapiPaths") or [],
                        "openapiOperations": result.get("openapiOperations") or [],
                        "openapiSubmitPath": result.get("openapiSubmitPath") or "",
                        "openapiQueryPath": result.get("openapiQueryPath") or "",
                        "credentialValidation": result.get("credentialValidation")
                        or {},
                        "credentialCheckedAt": _probe_checked_at(result),
                    },
                    replace_snapshot=True,
                )
            except OSError:
                result["capabilityCache"] = "unavailable"
        # 探测成功后把本次发现的出线合同写回渠道条目。
        if body.id and result.get("ok") and result.get("modelFound"):
            from novelvideo.model_gateway_settings import (
                persist_probed_wire_contract,
            )

            try:
                persisted = persist_probed_wire_contract(
                    body.id,
                    protocol=result.get("protocol") or body.protocol,
                    runtime_verified=(
                        str(
                            (result.get("capability") or {}).get("verificationStatus")
                            if isinstance(result.get("capability"), dict)
                            else ""
                        ).strip().lower()
                        == "runtime-verified"
                    ),
                )
                if persisted is not None:
                    result["wireContract"] = persisted
            except (OSError, ValueError):
                result["wireContractWrite"] = "unavailable"
        elif not result.get("ok"):
            from novelvideo.generators.video.direct_video_capability_cache import (
                record_capability,
            )

            try:
                result["capability"] = record_capability(
                    base_url=body.base_url,
                    protocol=str(result.get("protocol") or body.protocol or "openai-video"),
                    upstream_model=model_id,
                    capability={
                        "verificationStatus": "probe-failed",
                        "probeStatus": "failed",
                        "modelFound": bool(result.get("modelFound")),
                        "discoveredModelCount": int(
                            result.get("discoveredModelCount") or 0
                        ),
                        "supportedProtocols": result.get("supportedProtocols") or [],
                        "detectedProtocol": result.get("protocol") or "unresolved",
                        "adapterFamily": result.get("adapterFamily"),
                        "adapterConfidence": result.get("adapterConfidence"),
                        "adapterEvidence": result.get("adapterEvidence"),
                        "openapiUrl": result.get("openapiUrl"),
                        "openapiPaths": result.get("openapiPaths") or [],
                        "openapiOperations": result.get("openapiOperations") or [],
                        "openapiSubmitPath": result.get("openapiSubmitPath") or "",
                        "openapiQueryPath": result.get("openapiQueryPath") or "",
                        "credentialValidation": result.get("credentialValidation")
                        or {},
                        "credentialCheckedAt": _probe_checked_at(result),
                        "lastFailure": result.get("error") or "视频模型目录探测失败",
                    },
                    replace_snapshot=True,
                )
            except OSError:
                result["capabilityCache"] = "unavailable"
        return {
            "ok": True,
            "data": result,
        }
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/direct-video-models/discover")
async def discover_direct_video_model_settings(
    body: DirectModelDiscoveryBody,
) -> dict[str, Any]:
    """List upstream video model IDs without saving or invoking a model."""

    try:
        require_ce_direct_video_management()
        api_key = normalize_api_key(body.api_key)
        if not api_key and body.id:
            api_key = get_bound_direct_model_api_key(
                "video",
                body.id,
                body.base_url,
            )
        if not api_key:
            raise ValueError("apiKey is required to read the video model directory")
        return {
            "ok": True,
            "data": discover_direct_video_models(
                base_url=body.base_url,
                api_key=api_key,
                protocol=str(body.protocol or "auto"),
            ),
        }
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/direct-models/{kind}")
async def save_direct_model_settings(
    kind: DirectModelKindPath,
    body: DirectModelsBody,
) -> dict[str, Any]:
    """Persist one direct-model family for every canvas consumer."""
    try:
        require_ce_direct_model_management()
        canonical_direct_model_kind(kind)
        saved = save_direct_models(
            kind,
            [item.model_dump(by_alias=True) for item in body.models],
            confirm_clear=body.confirm_clear,
        )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except DirectModelClearNotConfirmed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": build_direct_models_status()[kind],
        "count": len(saved),
    }


@router.post("/direct-models/{kind}/discover")
async def discover_direct_model_settings(
    kind: DirectModelKindPath,
    body: DirectModelDiscoveryBody,
) -> dict[str, Any]:
    """List upstream model IDs without saving or invoking a model."""

    try:
        require_ce_direct_model_management()
        canonical_direct_model_kind(kind)
        api_key = normalize_api_key(body.api_key)
        if not api_key and body.id:
            api_key = get_bound_direct_model_api_key(
                kind,
                body.id,
                body.base_url,
            )
        if not api_key:
            raise ValueError(f"apiKey is required to read the direct {kind} model directory")
        from novelvideo.generators.direct_model_probe import discover_direct_models

        return {
            "ok": True,
            "data": discover_direct_models(
                base_url=body.base_url,
                api_key=api_key,
                protocol=str(body.protocol or "auto"),
                kind=kind,
            ),
        }
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/direct-models/{kind}/probe")
async def probe_direct_model_settings(
    kind: DirectModelKindPath,
    body: DirectModelBody,
) -> dict[str, Any]:
    """Probe the directory and the bounded runtime contract required by *kind*."""
    try:
        require_ce_direct_model_management()
        canonical_direct_model_kind(kind)
        api_key = normalize_api_key(body.api_key)
        if not api_key and body.id:
            api_key = get_bound_direct_model_api_key(
                kind,
                body.id,
                body.base_url,
            )
        if not api_key:
            raise ValueError(f"apiKey is required to probe a new direct {kind} model")
        from novelvideo.generators.direct_model_capabilities import (
            direct_model_capability_summary,
        )
        from novelvideo.generators.direct_model_probe import probe_direct_model_endpoint

        model_id = normalize_direct_model_id(kind, body.model_id)
        capabilities = direct_model_capability_summary(
            kind,
            model_id,
            protocol=body.protocol,
            base_url=body.base_url,
        )
        result = probe_direct_model_endpoint(
            upstream_model=model_id,
            base_url=body.base_url,
            api_key=api_key,
            protocol=str(body.protocol or "auto"),
            kind=kind,
            declared_capabilities={
                "supportsTools": body.supports_tools,
                "supportsVision": body.supports_vision,
            },
        )
        from novelvideo.generators.direct_model_capability_cache import (
            record_direct_model_capability,
        )

        record_direct_model_capability(
            base_url=body.base_url,
            kind=kind,
            upstream_model=model_id,
            protocol=str(result.get("protocol") or capabilities["protocol"]),
            capability={
                "verificationStatus": result.get("verificationStatus")
                or ("degraded" if not result.get("ok") else "contract-resolved"),
                "detectedProtocol": result.get("detectedProtocol")
                or result.get("protocol")
                or capabilities["protocol"],
                "modelFound": bool(result.get("modelFound")),
                "discoveredModelCount": int(result.get("discoveredModelCount") or 0),
                "modelMetadata": result.get("modelMetadata") or {},
                "probeContractVersion": result.get("probeContractVersion"),
                "responsesProbeStatus": result.get("responsesProbeStatus"),
                "responsesProbeError": result.get("responsesProbeError"),
                "toolCallingVerified": result.get("toolCallingVerified"),
                "chatProbeStatus": result.get("chatProbeStatus"),
                "chatHttpStatus": result.get("chatHttpStatus"),
                "chatFirstTokenLatencyMs": result.get("chatFirstTokenLatencyMs"),
                "chatResponseUsable": result.get("chatResponseUsable"),
                "chatProbeError": result.get("chatProbeError"),
                "streamProbeStatus": result.get("streamProbeStatus"),
                "streamHttpStatus": result.get("streamHttpStatus"),
                "streamFirstEventLatencyMs": result.get("streamFirstEventLatencyMs"),
                "streamResponseUsable": result.get("streamResponseUsable"),
                "streamProbeError": result.get("streamProbeError"),
                "toolProbeStatus": result.get("toolProbeStatus"),
                "toolProbeMode": result.get("toolProbeMode"),
                "toolHttpStatus": result.get("toolHttpStatus"),
                "toolProbeError": result.get("toolProbeError"),
                "visionProbeStatus": result.get("visionProbeStatus"),
                "visionHttpStatus": result.get("visionHttpStatus"),
                "visionResponseUsable": result.get("visionResponseUsable"),
                "visionProbeError": result.get("visionProbeError"),
                "hermesProbeStatus": result.get("hermesProbeStatus"),
                "hermesProbeError": result.get("hermesProbeError"),
                "embeddingProbeStatus": result.get("embeddingProbeStatus"),
                "embeddingHttpStatus": result.get("embeddingHttpStatus"),
                "embeddingResponseUsable": result.get("embeddingResponseUsable"),
                "embeddingProbeLatencyMs": result.get("embeddingProbeLatencyMs"),
                "embeddingDimensions": result.get("embeddingDimensions"),
                "embeddingProbeError": result.get("embeddingProbeError"),
                **({"lastFailure": result.get("error")} if result.get("error") else {}),
            },
        )
        catalog_matches = bool(result.get("modelFound")) or not int(
            result.get("discoveredModelCount") or 0
        )
        detected_capabilities = direct_model_capability_summary(
            kind,
            model_id,
            protocol=str(result.get("detectedProtocol") or result.get("protocol") or body.protocol),
            base_url=body.base_url,
            metadata=(
                result.get("modelMetadata")
                if isinstance(result.get("modelMetadata"), dict)
                else None
            ),
        )
        capabilities = {
            **detected_capabilities,
            "runtimeReady": bool(detected_capabilities.get("runtimeReady"))
            and catalog_matches
            and bool(result.get("ok")),
            "runtime_ready": bool(detected_capabilities.get("runtimeReady"))
            and catalog_matches
            and bool(result.get("ok")),
            "verificationStatus": result.get("verificationStatus")
            or detected_capabilities.get("verificationStatus"),
        }
        result["capabilities"] = capabilities
        result["protocol"] = str(capabilities["protocol"])
        return {"ok": True, "data": result}
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custom/newapi/init")
async def init_custom_newapi(body: NewApiInitBody = NewApiInitBody()) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        setup_status = ensure_newapi_setup(cfg, _setup_credentials_from_request(body))
        admin = ensure_admin_access_token(cfg)
        token_name = (
            body.token_name or cfg.relay_token_name
        ).strip() or cfg.relay_token_name
        token = create_or_reuse_relay_token(
            cfg,
            admin,
            name=token_name,
            group=body.group,
            unlimited_quota=body.unlimited_quota,
            remain_quota=body.remain_quota,
            expired_time=body.expired_time,
            reuse_existing=body.reuse_existing,
        )
        relay_base_url = normalize_relay_base_url(cfg.admin_base_url)
        save_custom_newapi_gateway(
            base_url=relay_base_url,
            api_key=token["key"],
            admin_base_url=cfg.admin_base_url,
            token_name=str(token["name"]),
            token_id=token["tokenId"],
            activate=False,
        )
        save_unified_gateway(
            base_url=relay_base_url,
            api_key=token["key"],
            activate=True,
        )
        _save_request_database_config(cfg, body.database)
        runtime = refresh_model_gateway_runtime()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "mode": "custom",
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "newApiBaseUrl": relay_base_url,
            "adminUserId": admin.admin_user_id,
            "adminUsername": admin.admin_username,
            "adminTokenCreated": admin.token_created,
            "adminTokenPreview": mask_token(admin.access_token),
            "newApiSetup": {
                "initialized": setup_status.initialized,
                "rootInitialized": setup_status.root_initialized,
                "databaseType": setup_status.database_type,
                "setupPerformed": setup_status.setup_performed,
                "alreadyInitialized": setup_status.already_initialized,
            },
            "relayToken": {
                "created": bool(token["created"]),
                "tokenId": token["tokenId"],
                "name": token["name"],
                "keyPreview": token["keyPreview"],
            },
            "database": build_provisioner_status()["database"],
            "effective": build_model_gateway_status(
                official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
                official_api_key=app_config.NEWAPI_API_KEY,
            )["effective"],
            "runtime": runtime,
        },
    }


@router.post("/custom/newapi/provider-channels")
async def save_custom_newapi_provider_channels(
    body: SaveProviderChannelsBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        saved = save_newapi_provider_channels(
            [
                {
                    "provider": channel.provider,
                    "upstreamKey": channel.upstream_key or "",
                    "baseUrl": channel.base_url or "",
                }
                for channel in body.channels
            ]
        )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "channels": [
                {
                    "provider": channel["provider"],
                    "configured": bool(channel["upstreamKey"]),
                    "upstreamKeyPreview": mask_token(channel["upstreamKey"]),
                    "baseUrl": channel["baseUrl"],
                }
                for channel in saved
            ]
        },
    }


@router.post("/custom/newapi/provider-channel/sync")
async def sync_custom_newapi_provider_channel(
    body: SyncProviderChannelBody,
) -> dict[str, Any]:
    provider = str(body.provider or "").strip().lower()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    try:
        require_ce_gateway_management()
        saved_channel = get_newapi_provider_channel(provider) or {}
        upstream_key = (body.upstream_key or "").strip() or saved_channel.get(
            "upstreamKey", ""
        )
        if not upstream_key:
            raise ValueError(f"upstreamKey is required for provider {provider}")
        base_url = (
            body.base_url
            if body.base_url is not None
            else saved_channel.get("baseUrl", "")
        )
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = ensure_admin_access_token(cfg)
        result = update_provider_channel_credentials(
            cfg,
            admin,
            provider=provider,
            upstream_key=upstream_key,
            base_url=base_url,
        )
        saved = []
        if result.get("ok"):
            saved = save_newapi_provider_channels(
                [
                    {
                        "provider": provider,
                        "upstreamKey": upstream_key,
                        "baseUrl": base_url or "",
                    }
                ]
            )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sent_payload = result.get("sentPayload")
    return {
        "ok": result["ok"],
        "data": {
            "provider": provider,
            "channelId": result.get("channelId"),
            "httpStatus": result.get("httpStatus"),
            "newApiResponse": result.get("newApiResponse"),
            "sentPayload": (
                _mask_sent_channel_payload(sent_payload)
                if isinstance(sent_payload, dict) and "channel" in sent_payload
                else sent_payload
            ),
            "savedChannel": next(
                (
                    {
                        "provider": channel["provider"],
                        "configured": bool(channel["upstreamKey"]),
                        "upstreamKeyPreview": mask_token(channel["upstreamKey"]),
                        "baseUrl": channel["baseUrl"],
                    }
                    for channel in saved
                    if channel["provider"] == provider
                ),
                None,
            ),
        },
    }


@router.post("/custom/newapi/channels")
async def create_custom_newapi_channel(body: CreateChannelBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = ensure_admin_access_token(cfg)
        payload = _build_channel_payload_from_spec(body)
        result = upsert_channel(cfg, admin, payload)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": result["ok"],
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "httpStatus": result["httpStatus"],
            "newApiResponse": result["newApiResponse"],
            "action": result.get("action"),
            "channelId": result.get("channelId"),
            "sentPayload": _mask_sent_channel_payload(
                result.get("sentPayload") or payload
            ),
        },
    }


@router.post("/custom/newapi/channels/batch")
async def create_custom_newapi_channels_batch(
    body: CreateChannelsBatchBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = ensure_admin_access_token(cfg)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    results: list[dict[str, Any]] = []
    for index, channel in enumerate(body.channels):
        try:
            payload = _build_channel_payload_from_spec(channel)
            result = upsert_channel(cfg, admin, payload)
            item: dict[str, Any] = {
                "index": index,
                "ok": result["ok"],
                "httpStatus": result["httpStatus"],
                "newApiResponse": result["newApiResponse"],
                "action": result.get("action"),
                "channelId": result.get("channelId"),
                "sentPayload": _mask_sent_channel_payload(
                    result.get("sentPayload") or payload
                ),
            }
            if not result["ok"]:
                item["error"] = "NewAPI rejected channel creation"
            results.append(item)
        except Exception as exc:
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "error": str(exc),
                }
            )

    succeeded = sum(1 for item in results if item["ok"])
    failed = len(results) - succeeded
    return {
        "ok": failed == 0,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        },
    }


@router.post("/custom/newapi/embedding-model")
async def save_custom_newapi_embedding_model(
    body: SaveEmbeddingModelBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        spec, normalized_model = _build_embedding_model_channel_spec(body)
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = ensure_admin_access_token(cfg)
        payload = _build_channel_payload_from_spec(spec)
        result = upsert_channel(cfg, admin, payload)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    item: dict[str, Any] = {
        "provider": spec.provider,
        "ok": result["ok"],
        "httpStatus": result["httpStatus"],
        "newApiResponse": result["newApiResponse"],
        "action": result.get("action"),
        "channelId": result.get("channelId"),
        "sentPayload": _mask_sent_channel_payload(result.get("sentPayload") or payload),
    }
    if not result["ok"]:
        response = result.get("newApiResponse")
        message = ""
        if isinstance(response, dict):
            message = str(
                response.get("message") or response.get("error") or ""
            ).strip()
        item["error"] = message or "NewAPI rejected embedding model channel update"
        return {
            "ok": False,
            "data": {
                "newApiAdminBaseUrl": cfg.admin_base_url,
                "embeddingModel": {},
                "result": item,
            },
        }

    saved = save_newapi_embedding_model_config(
        provider=normalized_model["provider"],
        upstream_model=normalized_model["upstreamModel"],
        dimension=normalized_model["dimension"],
        batch_size=normalized_model.get("batchSize"),
        send_dimensions=normalized_model["sendDimensions"],
    )
    return {
        "ok": True,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "embeddingModel": saved,
            "result": item,
        },
    }


@router.post("/custom/newapi/media-models")
async def save_custom_newapi_media_models(body: SaveMediaModelsBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        specs, normalized_models = _build_media_model_channel_specs(body.models)
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = ensure_admin_access_token(cfg)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    results: list[dict[str, Any]] = []
    for index, channel in enumerate(specs):
        try:
            payload = _build_channel_payload_from_spec(channel)
            result = upsert_channel(cfg, admin, payload)
            item: dict[str, Any] = {
                "index": index,
                "provider": channel.provider,
                "ok": result["ok"],
                "httpStatus": result["httpStatus"],
                "newApiResponse": result["newApiResponse"],
                "action": result.get("action"),
                "channelId": result.get("channelId"),
                "sentPayload": _mask_sent_channel_payload(
                    result.get("sentPayload") or payload
                ),
            }
            if not result["ok"]:
                response = result.get("newApiResponse")
                message = ""
                if isinstance(response, dict):
                    message = str(
                        response.get("message") or response.get("error") or ""
                    ).strip()
                item["error"] = message or "NewAPI rejected media model channel update"
            results.append(item)
        except Exception as exc:
            results.append(
                {
                    "index": index,
                    "provider": channel.provider,
                    "ok": False,
                    "error": str(exc),
                }
            )

    succeeded = sum(1 for item in results if item["ok"])
    failed = len(results) - succeeded
    if failed == 0:
        save_newapi_media_model_mappings(normalized_models)

    return {
        "ok": failed == 0,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "models": normalized_models if failed == 0 else {},
            "results": results,
        },
    }
