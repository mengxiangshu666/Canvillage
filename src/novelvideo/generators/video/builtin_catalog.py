"""Built-in NewAPI video catalog with legacy environment overlays.

This module is a compatibility boundary: it describes the models exposed by the
existing NewAPI configuration without changing generator dispatch or provider
payloads. Environment values are read at snapshot construction time so tests and
long-running processes keep the legacy monkeypatch/reload behavior.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Mapping, Sequence

from .capabilities import (
    FallbackPolicy,
    Lifecycle,
    ModelCapability,
    NativeAudio,
    ReferenceLimits,
    VideoMode,
)
from .catalog import VideoModelRegistry

CAPABILITY_CONTRACT_REVISION = "newapi-capabilities.v2"
CATALOG_BASE_REVISION = "newapi-builtin-2026-07-18"
NEWAPI_VIDEO_BACKEND_PREFIX = "newapi_"
NEWAPI_MAINLINE_SEEDANCE2_MODELS = (
    "seedance-2.0",
    "seedance-2.0-fast",
    "seedance-2.0-value",
    "seedance-2.0-fast-value",
)
NEWAPI_DISABLED_VIDEO_MODELS = frozenset(
    {
        "grok-video-channel",
        "kling-3.0",
        "kling-3.0-pro",
        "kling-3.0-omni",
        "kling-3.0-omni-ref",
    }
)
PROMPT_HUBS_UPSTREAM_VIDEO_MODELS: Mapping[str, str] = MappingProxyType(
    {
        "sd2.0-full-933-face": "sd2.0满血933卡脸版",
        "sd2.0-pro-full-9img-face": "sd2.0 pro 满血九图卡脸版",
        "sd2.0fast-full-933-face": "sd2.0fast满血933卡脸版",
    }
)
_PROMPT_HUBS_INTERNAL_VIDEO_MODELS = MappingProxyType(
    {value: key for key, value in PROMPT_HUBS_UPSTREAM_VIDEO_MODELS.items()}
)
NEWAPI_VIDEO_DISPLAY_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "village-canvas-video": "视频路由：NewAPI 极速主力",
        "seedance-1.0-pro-fast": "Seedance1.0 Pro Fast",
        "seedance-1.5-pro": "Seedance1.5 Pro",
        "seedance-2.0": "Seedance2.0",
        "seedance-2.0-fast": "Seedance2.0 Fast",
        "jimeng-seedance-2.0-fast": "Wokey · 即梦 Seedance 2.0 Fast（720P 极速主力）",
        "jimeng-seedance-2.5": "Wokey · 即梦 Seedance 2.5（高质量长镜头）",
        "s-videos-f-933-fast-480-2": "卡藏 · 933 Fast（480P 多参考人脸一致性）",
        "mini-h3": "卡藏 · Mini H3（2K 文生视频）",
        "kling-v3-omni-v2v-create": "卡藏 · Kling V3 Omni（源视频重绘）",
        "seedance-2.0-value": "Seedance2.0 Value",
        "seedance-2.0-fast-value": "Seedance2.0 Fast Value",
        "seedance-2.5": "Seedance2.5（网关已验证）",
        "seedance-2-5": "Seedance2.5（网关已验证）",
        "happyhorse-1.0": "HappyHorse 1.0",
        "minimax-hailuo-2.3": "MiniMax Hailuo 2.3",
        "grok-video-channel": "Grok Video Channel",
        "firefly-seedance2-480p": "Firefly Seedance2 480P",
        "firefly-seedance2-fast-480p": "Firefly Seedance2 Fast 480P",
        "firefly-seedance2-fast-720p": "Firefly Seedance2 Fast 720P",
        "kling-3.0": "Kling 3.0",
        "kling-3.0-pro": "Kling 3.0 Pro",
        "kling-3.0-omni": "Kling 3.0 Omni",
        "kling-3.0-omni-ref": "Kling 3.0 Omni Ref",
        "runway-gen4.5": "Runway Gen4.5",
        "sora-2-flex": "Sora 2 Flex",
        "sora-2-pro-flex": "Sora 2 Pro Flex",
        "veo-3.1-fast-flex": "Veo 3.1 Fast Flex",
        "veo-3.1-flex": "Veo 3.1 Flex",
        "veo-3.1-ref-flex": "Veo 3.1 Ref Flex",
        "sd2.0-pro": "SD2.0 Pro",
        "sd2.0-full-933-face": "SD2.0 满血 933 卡脸版",
        "sd2.0-pro-full-9img-face": "SD2.0 Pro 满血九图卡脸版",
        "sd2.0-1080p-4k-pro": "SD2.0 1080P/4K Pro",
        "sd2.0-fast-15s": "SD2.0 Fast 15s",
        "sd2.0-720p-4img-pro": "SD2.0 720P 4图 Pro",
        "sd2.0-15s": "SD2.0 15s",
        "sd2.0-720p-mini": "SD2.0 720P Mini",
        "sd2.0-720p-4img-mini": "SD2.0 720P 4图 Mini",
        "sd2.0fast-full-933-face": "SD2.0 Fast 满血 933 卡脸版",
        "sd2.0-720p-4img-fast": "SD2.0 720P 4图 Fast",
        "sd2.0-720p-fast": "SD2.0 720P Fast",
        "sd2.0-720p-pro": "SD2.0 720P Pro",
    }
)

# These are the only user-facing Wokey routes.  The capability declaration is
# deliberately local: gateway discovery alone does not describe the media-input
# contract required to render a safe canvas node.
WOKEY_JIMENG_VIDEO_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[int, int]]] = (
    MappingProxyType(
        {
            "jimeng-seedance-2.0-fast": (("720p",), (4, 15)),
            "jimeng-seedance-2.5": (("480p", "720p"), (4, 30)),
        }
    )
)

KACANG_933_FAST_MODEL = "s-videos-f-933-fast-480-2"
KACANG_MINI_H3_MODEL = "mini-h3"
KACANG_KLING_V3_OMNI_V2V_MODEL = "kling-v3-omni-v2v-create"

# The upstream's capital ``S`` is significant.  Keep the local model key
# lowercase so it passes catalog validation, then restore the provider's exact
# identifier only at the gateway boundary.
KACANG_UPSTREAM_MODEL_IDS: Mapping[str, str] = MappingProxyType(
    {KACANG_933_FAST_MODEL: "S-videos-f-933-fast-480-2"}
)

LEGACY_NEWAPI_VIDEO_MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "wokey-seedance-2.0-fast": "jimeng-seedance-2.0-fast",
        "wokey-seedance-2.0": "jimeng-seedance-2.0-fast",
        "wokey-seedance-2.0-mini": "jimeng-seedance-2.0-fast",
        "wokey-seedance-2.0-fast-vip": "jimeng-seedance-2.0-fast",
        "wokey-seedance-2.0-vip": "jimeng-seedance-2.0-fast",
        "wokey-seedance-2.5": "jimeng-seedance-2.5",
        "s-videos-f-933-fast-480-2": KACANG_933_FAST_MODEL,
    }
)

# These entries existed in saved selector lists before the dedicated 933
# channel was introduced.  Apply them only while rebuilding a legacy catalog;
# an explicit ASCII model ID must always keep its own upstream identity.
LEGACY_CATALOG_VIDEO_MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "sd2.0-full-933-face": KACANG_933_FAST_MODEL,
        "sd2.0-pro-full-9img-face": KACANG_933_FAST_MODEL,
        "sd2.0fast-full-933-face": KACANG_933_FAST_MODEL,
    }
)


def normalize_newapi_video_model_id(model: str) -> str:
    """Normalize old selector values to the five canonical local catalog ids."""

    clean = str(model or "").strip()
    return LEGACY_NEWAPI_VIDEO_MODEL_ALIASES.get(
        clean.casefold(), _PROMPT_HUBS_INTERNAL_VIDEO_MODELS.get(clean, clean)
    )


def _normalize_newapi_video_catalog_model_id(model: str) -> str:
    """Migrate deprecated saved selector values without rewriting direct IDs."""
    normalized = normalize_newapi_video_model_id(model)
    return LEGACY_CATALOG_VIDEO_MODEL_ALIASES.get(normalized.casefold(), normalized)


def resolve_newapi_video_upstream_model(model: str) -> str:
    """Return the gateway model id, keeping legacy display aliases local.

    The public gateway catalog exposes the ASCII ids (for example
    ``sd2.0-full-933-face``). The Chinese names in
    :data:`PROMPT_HUBS_UPSTREAM_VIDEO_MODELS` are historical local aliases;
    sending those display strings upstream produces ``model_not_found``.
    """

    clean = normalize_newapi_video_model_id(model)
    return KACANG_UPSTREAM_MODEL_IDS.get(clean, clean)


@dataclass(frozen=True, slots=True)
class NewApiVideoCatalogSnapshot:
    """One immutable view of built-ins after applying legacy config overlays."""

    registry: VideoModelRegistry
    labels: Mapping[str, str]
    configured_duration_bounds: Mapping[str, tuple[int, int]]

    def backend_options(self) -> dict[str, str]:
        return {
            f"{NEWAPI_VIDEO_BACKEND_PREFIX}{item.model_id}": self.labels[item.model_id]
            for item in self.registry.list_enabled()
        }


def parse_duration_bounds(raw: str) -> dict[str, tuple[int, int]]:
    """Parse the permissive legacy ``model:min-max`` environment format."""

    bounds: dict[str, tuple[int, int]] = {}
    for item in str(raw or "").split(","):
        entry = item.strip()
        if not entry or ":" not in entry or "-" not in entry:
            continue
        model, raw_bounds = entry.split(":", 1)
        raw_min, raw_max = raw_bounds.split("-", 1)
        try:
            min_seconds = int(raw_min.strip())
            max_seconds = int(raw_max.strip())
        except ValueError:
            continue
        model = model.strip()
        if model and min_seconds > 0 and max_seconds >= min_seconds:
            bounds[model] = (min_seconds, max_seconds)
    return bounds


def build_newapi_video_catalog(
    *,
    models: Sequence[str] | None = None,
    audio_models: Sequence[str] | None = None,
    duration_bounds: str | None = None,
    include_seedance2_variants: bool = False,
) -> NewApiVideoCatalogSnapshot:
    """Build a registry while preserving legacy model ordering and overrides."""

    if models is None or audio_models is None or duration_bounds is None:
        from novelvideo import config

        if models is None:
            models = config.NEWAPI_VIDEO_MODELS
        if audio_models is None:
            audio_models = config.NEWAPI_VIDEO_AUDIO_MODELS
        if duration_bounds is None:
            duration_bounds = config.NEWAPI_VIDEO_DURATION_BOUNDS

    ordered_models = _ordered_unique(
        [_normalize_newapi_video_catalog_model_id(model) for model in models]
    )
    if include_seedance2_variants:
        ordered_models.extend(
            model
            for model in NEWAPI_MAINLINE_SEEDANCE2_MODELS
            if model not in ordered_models
        )

    audio_set = {str(model).strip() for model in audio_models if str(model).strip()}
    configured_bounds = parse_duration_bounds(duration_bounds)
    revision = _snapshot_revision(ordered_models, audio_set, str(duration_bounds))
    capabilities = tuple(
        _capability_for_model(
            model,
            enabled=model not in NEWAPI_DISABLED_VIDEO_MODELS,
            native_audio=(
                NativeAudio.OPTIONAL if model in audio_set else NativeAudio.UNSUPPORTED
            ),
            duration=configured_bounds.get(model),
            catalog_revision=revision,
        )
        for model in ordered_models
    )
    labels = MappingProxyType(
        {
            model: NEWAPI_VIDEO_DISPLAY_LABELS.get(model, model)
            for model in ordered_models
        }
    )
    return NewApiVideoCatalogSnapshot(
        registry=VideoModelRegistry(capabilities, catalog_revision=revision),
        labels=labels,
        configured_duration_bounds=MappingProxyType(configured_bounds),
    )


def newapi_video_backend_options_from_catalog(
    *, include_seedance2_variants: bool = False
) -> dict[str, str]:
    """Return the exact legacy backend option shape from a catalog snapshot."""

    return build_newapi_video_catalog(
        include_seedance2_variants=include_seedance2_variants
    ).backend_options()


def _ordered_unique(models: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    for raw_model in models:
        model = str(raw_model).strip()
        if model and model not in ordered:
            ordered.append(model)
    return ordered


def _snapshot_revision(
    models: Sequence[str], audio_models: set[str], bounds: str
) -> str:
    overlay = "\n".join(
        (
            CAPABILITY_CONTRACT_REVISION,
            *models,
            "--audio--",
            *sorted(audio_models),
            "--duration--",
            bounds,
        )
    )
    digest = hashlib.sha256(overlay.encode("utf-8")).hexdigest()[:12]
    return f"{CATALOG_BASE_REVISION}+{digest}"


def _capability_for_model(
    model: str,
    *,
    enabled: bool,
    native_audio: NativeAudio,
    duration: tuple[int, int] | None,
    catalog_revision: str,
) -> ModelCapability:
    is_village_canvas_routed = model == "village-canvas-video"
    is_wokey_jimeng_seedance2 = model in WOKEY_JIMENG_VIDEO_SPECS
    is_kacang_933 = model == KACANG_933_FAST_MODEL
    is_kacang_mini_h3 = model == KACANG_MINI_H3_MODEL
    is_kacang_kling_v2v = model == KACANG_KLING_V3_OMNI_V2V_MODEL
    is_happyhorse = model == "happyhorse-1.0"
    is_minimax_hailuo = model == "minimax-hailuo-2.3"
    is_grok = model == "grok-video-channel"
    is_firefly_seedance2 = model.startswith("firefly-seedance2-")
    is_kling_30 = model.startswith("kling-3.0")
    is_runway = model.startswith("runway-gen4.5")
    is_sora = model.startswith("sora-2")
    is_veo = model.startswith("veo-3.1")
    is_prompt_hubs_sd = model.startswith("sd2")
    if is_village_canvas_routed:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
        )
        resolution = ("480p", "720p")
        aspect = ("16:9", "9:16", "1:1")
        references = ReferenceLimits(input_images=1)
        default_duration = (4, 15)
        vendor = "village-canvas-route"
    elif is_wokey_jimeng_seedance2:
        resolution, default_duration = WOKEY_JIMENG_VIDEO_SPECS[model]
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.FIRST_LAST_FRAME,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        aspect = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
        references = ReferenceLimits(
            input_images=2,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        )
        vendor = "wokey-jimeng"
    elif is_kacang_933:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        resolution = ("480p",)
        aspect = ("16:9", "9:16", "1:1")
        references = ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        )
        default_duration = (4, 15)
        vendor = "kacang"
    elif is_kacang_mini_h3:
        modes = (VideoMode.TEXT_TO_VIDEO,)
        resolution = ("2k",)
        aspect = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
        references = ReferenceLimits()
        default_duration = (5, 15)
        vendor = "kacang"
    elif is_kacang_kling_v2v:
        modes = (VideoMode.REFERENCE_TO_VIDEO,)
        resolution = ("720p",)
        aspect = ("16:9", "9:16")
        references = ReferenceLimits(
            reference_images=9,
            reference_videos=1,
            reference_audios=1,
        )
        default_duration = (3, 15)
        vendor = "kacang"
    elif is_firefly_seedance2:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        resolution = ("480p",) if model.endswith("-480p") else ("720p",)
        aspect = ("16:9", "9:16", "1:1")
        references = ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=3,
            reference_audios=3,
        )
        default_duration = (4, 15)
        vendor = "firefly"
    elif is_kling_30 or is_runway or is_sora or is_veo:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        resolution = ("720p",) if is_runway else ("720p", "1080p")
        aspect = ("16:9", "9:16", "1:1") if is_runway else ("16:9", "9:16")
        ref_images = (
            3 if model.endswith("-ref-flex") or model.endswith("-omni-ref") else 1
        )
        references = ReferenceLimits(
            input_images=1,
            reference_images=ref_images,
        )
        if is_kling_30 and model in {"kling-3.0", "kling-3.0-pro"}:
            default_duration = (15, 15)
        elif is_kling_30 and model.endswith("-omni-ref"):
            default_duration = (5, 15)
        elif is_kling_30:
            default_duration = (5, 10)
        elif is_runway:
            default_duration = (5, 10)
        elif is_sora:
            default_duration = (4, 12)
        else:
            default_duration = (4, 8)
        vendor = (
            "kling"
            if is_kling_30
            else "runway"
            if is_runway
            else "openai"
            if is_sora
            else "google"
        )
    elif is_prompt_hubs_sd:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        if "1080p-4k" in model:
            resolution = ("1080p", "2160p")
        elif "15s" in model or "满血" in model or "full" in model:
            resolution = ("480p", "720p", "1080p")
        elif "720p" in model or model == "sd2.0-pro":
            resolution = ("480p", "720p")
        else:
            resolution = ("720p",)
        aspect = ("16:9", "9:16")
        is_four_image = "4img" in model
        references = ReferenceLimits(
            input_images=1,
            reference_images=4 if is_four_image else 9,
            reference_videos=3,
            reference_audios=1 if is_four_image else 3,
        )
        default_duration = (15, 15) if "15s" in model else (4, 15)
        vendor = "seedance"
    elif is_happyhorse:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        resolution = ("720p", "1080p")
        aspect = ("16:9", "9:16", "1:1", "4:3", "3:4")
        references = ReferenceLimits(
            input_images=1,
            reference_images=9,
            reference_videos=1,
        )
        default_duration = (1, 15)
        vendor = "happyhorse"
    elif is_minimax_hailuo:
        modes = (VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO)
        resolution = ("768p", "1080p")
        aspect = ("16:9", "9:16", "1:1")
        references = ReferenceLimits(input_images=1)
        default_duration = (6, 10)
        vendor = "minimax"
    elif is_grok:
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            VideoMode.REFERENCE_TO_VIDEO,
        )
        resolution = ("720p", "480p")
        aspect = ("16:9", "9:16", "1:1", "2:3", "3:2")
        references = ReferenceLimits(input_images=1, reference_images=7)
        default_duration = (6, 30)
        vendor = "xai"
    else:
        is_seedance = model.startswith("seedance-")
        is_seedance2 = model.startswith("seedance-2.0") or model in {
            "seedance-2.5",
            "seedance-2-5",
        }
        modes = (
            VideoMode.TEXT_TO_VIDEO,
            VideoMode.IMAGE_TO_VIDEO,
            *((VideoMode.FIRST_LAST_FRAME,) if is_seedance else ()),
            *((VideoMode.REFERENCE_TO_VIDEO,) if is_seedance2 else ()),
        )
        resolution = {
            "seedance-1.5-pro": ("480p", "720p", "1080p"),
            "seedance-2.0-fast": ("480p", "720p"),
            "seedance-2.0": ("480p", "720p", "1080p"),
            "seedance-2.0-value": ("720p", "1080p"),
            "seedance-2.0-fast-value": ("720p", "1080p"),
            # A direct 2.5 option is only added by authenticated gateway
            # discovery; keep its UI contract conservative until the relay
            # publishes richer resolution/reference metadata.
            "seedance-2.5": ("480p", "720p", "1080p"),
            "seedance-2-5": ("480p", "720p", "1080p"),
        }.get(model, ("720p",))
        aspect = ("16:9", "9:16")
        references = ReferenceLimits(
            input_images=2 if is_seedance else 1,
            reference_images=9 if is_seedance2 else 0,
            reference_videos=3 if is_seedance2 else 0,
            reference_audios=3 if is_seedance2 else 0,
        )
        default_duration = (
            (4, 30) if model in {"seedance-2.5", "seedance-2-5"} else (2, 12)
        )
        vendor = "bytedance" if model.startswith("seedance-") else "unknown"

    minimum, maximum = duration or default_duration
    return ModelCapability(
        model_id=model,
        provider="newapi",
        model_vendor=vendor,
        adapter="newapi",
        upstream_model=resolve_newapi_video_upstream_model(model),
        aliases=(),
        modes=modes,
        duration=tuple(range(minimum, maximum + 1)),
        resolution=resolution,
        aspect=aspect,
        native_audio=native_audio,
        reference_limits=references,
        return_last_frame=model.startswith("seedance-2.0")
        or model in {"seedance-2.5", "seedance-2-5"},
        prompt_profile=model,
        enabled=enabled,
        lifecycle=Lifecycle.ACTIVE,
        catalog_revision=catalog_revision,
        model_revision=f"{model}@{CAPABILITY_CONTRACT_REVISION}",
        pricing_revision="external-credit-table",
        data_policy_revision="provider-contract-unknown",
        fallback_policy=FallbackPolicy.FORBIDDEN,
        last_verified=date(2026, 8, 2)
        if is_wokey_jimeng_seedance2
        else date(2026, 7, 18),
    )
