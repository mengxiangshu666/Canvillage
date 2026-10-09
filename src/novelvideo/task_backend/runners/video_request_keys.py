"""单镜视频请求的幂等键与参考位解析。

这些函数原本长在 ``runners/video.py`` 里，那个文件已经越过体积基线。抽出来
只为让巨型文件能继续缩小，行为与原来完全一致。
"""

from __future__ import annotations

from novelvideo.services.video_submission_keys import (
    _coerce_duration_seconds,
    idempotency_content_token,
    single_video_idempotency_key,
    video_media_input_token,
)


def declared_image_reference_limit(backend: object) -> int:
    """Resolve how many ordinary image references the selected model declares.

    A zero result means the model has no spare image-reference slot, so callers
    must leave the request untouched instead of appending a reference that would
    be ignored or would displace a real one.
    """

    try:
        from novelvideo.generators.video.direct_models import (
            resolve_direct_video_model,
        )

        direct_model = resolve_direct_video_model(backend)
        if direct_model is not None:
            return max(
                0, int(direct_model.capability.reference_limits.reference_images)
            )
        from novelvideo.generators.video.builtin_catalog import (
            build_newapi_video_catalog,
        )
        from novelvideo.generators.video_generator import (
            parse_newapi_video_backend,
        )

        model = parse_newapi_video_backend(backend)
        if not model:
            return 0
        snapshot = build_newapi_video_catalog(include_seedance2_variants=True)
        try:
            capability = snapshot.registry.resolve(model)
        except Exception:
            capability = build_newapi_video_catalog(
                models=[model],
                audio_models=[],
                duration_bounds="",
            ).registry.resolve(model)
        return max(0, int(capability.reference_limits.reference_images))
    except Exception:
        return 0


def direct_video_family(backend: object) -> str:
    """Return the registered direct video family, or empty when unknown."""

    try:
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        model = resolve_direct_video_model(str(backend or ""))
    except Exception:  # noqa: BLE001 - discovery failure must not block generation
        return ""
    return str(getattr(getattr(model, "profile", None), "family", "") or "").strip().casefold()


__all__ = [
    "declared_image_reference_limit",
    "direct_video_family",
    "idempotency_content_token",
    "_coerce_duration_seconds",
    "single_video_idempotency_key",
    "video_media_input_token",
]
