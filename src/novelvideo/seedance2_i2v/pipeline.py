"""Seedance 2.0 video generation input preparation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any

from novelvideo.generators.video_generator import ShotReference
from novelvideo.seedance2_i2v.character_voice_storage import probe_voice_sample_duration_seconds
from novelvideo.seedance2_i2v.assets import (
    Seedance2ResolvedAsset,
    apply_prompt_audio_selection,
    append_seedance2_user_reference_assets,
    build_seedance2_project_assets,
    selected_reference_paths,
)
from novelvideo.seedance2_i2v.models import (
    Seedance2I2VMode,
    Seedance2VideoConfig,
    dump_seedance2_config,
    normalize_seedance2_generation_mode,
    parse_seedance2_config,
)
from novelvideo.seedance2_i2v.voice_clone import (
    normalize_seedance2_audio_type,
    resolve_beat_dialogue,
)

SEEDANCE2_HUIMENG_BACKEND = "huimeng_seedance-2.0-fast"
SEEDANCE2_NEWAPI_BACKEND = "newapi_seedance-2.0-fast"
MAX_SEEDANCE2_REFERENCE_AUDIOS = 3
MAX_SEEDANCE2_REFERENCE_AUDIO_TOTAL_SECONDS = 15.0


def sync_seedance2_prompt_from_director(
    beat: dict[str, Any],
    prompt: object,
    *,
    force: bool = False,
    config: Seedance2VideoConfig | None = None,
) -> str | None:
    """Synchronize a director motion prompt into Seedance's final prompt.

    ``video_prompt`` and ``seedance2_config_json.final_prompt`` are two
    persisted views of the same motion intent. Older projects can have an
    empty or stale Seedance value, so the runtime may adopt the director
    prompt. Explicitly manual/edited prompts remain authoritative; a panel
    generated prompt is preserved during ordinary submission and is only
    replaced by an explicit global-director run (``force=True``).
    """
    if not isinstance(beat, dict):
        return None
    candidate = str(prompt or "").strip()
    if not candidate or "seedance2_config_json" not in beat:
        return None

    config = config or parse_seedance2_config(beat.get("seedance2_config_json"))
    source = str(config.prompt_source or "").strip().casefold()
    if source in {"manual", "edited"}:
        return None
    if source == "generated" and not force:
        return None
    current = str(config.final_prompt or "").strip()
    if current == candidate:
        return dump_seedance2_config(config)

    config.final_prompt = candidate
    config.prompt_source = "director_generated"
    # The hash describes Seedance composer inputs, not the director's motion
    # string. Clearing it prevents the panel from claiming that this
    # independently optimized prompt is still backed by an old composer hash.
    config.prompt_inputs_hash = ""
    saved_json = dump_seedance2_config(config)
    beat["seedance2_config_json"] = saved_json
    return saved_json


@dataclass(frozen=True)
class Seedance2PreparedGeneration:
    prompt: str
    seedance2_config_json: str
    duration: int
    mode: Seedance2I2VMode
    image_path: str | None
    last_frame_path: str | None
    references: list[ShotReference]
    assets: list[Seedance2ResolvedAsset]


@dataclass(frozen=True)
class Seedance2VideoPrereqError:
    beat_number: int
    key: str
    label: str
    media_type: str
    path: str
    reason: str


#: Seedance 2 家族共用同一套首尾帧/多模态参考准备。2.5 与 2.0 的请求合同相同
#: （只有时长上限不同），只认 2.0 会让 2.5 静默退回单首帧路径。
_SEEDANCE2_MODEL_PREFIX = "seedance-2"


def is_huimeng_seedance2_backend(backend: str | None) -> bool:
    value = str(backend or "").strip()
    if value in {SEEDANCE2_HUIMENG_BACKEND, SEEDANCE2_NEWAPI_BACKEND}:
        return True
    for prefix in ("huimeng_", "huimengi_", "newapi_"):
        if value.startswith(prefix):
            return value[len(prefix) :].strip().startswith(_SEEDANCE2_MODEL_PREFIX)
    return False


def _unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _references_from_paths(
    *,
    image_paths: list[str],
    audio_paths: list[str],
) -> list[ShotReference]:
    references: list[ShotReference] = []
    for index, path in enumerate(image_paths, start=1):
        references.append(ShotReference("image", path, f"图片{index}"))
    for index, path in enumerate(audio_paths, start=1):
        references.append(ShotReference("audio", path, f"音频{index}"))
    return references


def _request_reference_kind(reference: object) -> str:
    """Read a typed or mapping reference without forcing remote URLs through Path."""

    if isinstance(reference, dict):
        raw = reference.get("type") or reference.get("kind") or "image"
    else:
        raw = getattr(reference, "type", None) or getattr(reference, "kind", "image")
    raw = getattr(raw, "value", raw)
    value = str(raw or "image").strip().lower()
    return value if value in {"image", "video", "audio"} else "image"


def _request_reference_path(reference: object) -> str:
    if isinstance(reference, (str, Path)):
        return str(reference).strip()
    if isinstance(reference, dict):
        values = (reference.get("path"), reference.get("url"), reference.get("uri"))
    else:
        values = (
            getattr(reference, "path", None),
            getattr(reference, "url", None),
            getattr(reference, "uri", None),
        )
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _request_reference_role(reference: object) -> str:
    if isinstance(reference, dict):
        value = reference.get("role") or ""
    else:
        value = getattr(reference, "role", "") or ""
    return str(value).strip().lower()


def _normalized_request_references(
    references: list[ShotReference] | None,
) -> list[ShotReference]:
    """Keep request-scoped references typed while preserving URL/URI values."""

    normalized: list[ShotReference] = []
    seen: set[tuple[str, str]] = set()
    for reference in references or []:
        path = _request_reference_path(reference)
        if not path:
            continue
        kind = _request_reference_kind(reference)
        role = _request_reference_role(reference)
        marker = (kind, path)
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(ShotReference(kind, path, role))
    return normalized


def _request_images(references: list[ShotReference]) -> list[ShotReference]:
    return [
        reference
        for reference in references
        if _request_reference_kind(reference) == "image"
        and _request_reference_path(reference)
    ]


def _request_image_for_role(
    references: list[ShotReference],
    *tokens: str,
) -> ShotReference | None:
    for reference in references:
        role = _request_reference_role(reference)
        if any(token in role for token in tokens):
            return reference
    return None


def _is_all_reference_mode(value: object) -> bool:
    """Return whether a canvas mode has the full multimodal-reference contract."""

    token = re.sub(
        r"[^a-z0-9]+",
        "",
        str(getattr(value, "value", value) or "").casefold(),
    )
    return token in {"allreference", "referencetovideo", "multimodalreference"}


def _request_reference_kinds_for_mode(value: object) -> set[str]:
    """Limit request media to the selected canvas mode's provider contract."""

    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return {"image", "video", "audio"}
    token = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    if token in {"imagereference"}:
        return {"image"}
    if token in {"videoedit"}:
        return {"video"}
    if token in {"allreference", "referencetovideo", "multimodalreference"}:
        return {"image", "video", "audio"}
    return {"image", "video", "audio"}


def _asset_missing_reason(asset: Seedance2ResolvedAsset) -> str:
    if not getattr(asset, "required", True) and not asset.selected:
        return ""
    if not asset.exists:
        return "missing"
    if asset.validation_error:
        return asset.validation_error
    return ""


def _selected_audio_assets(assets: list[Seedance2ResolvedAsset]) -> list[Seedance2ResolvedAsset]:
    return [
        asset
        for asset in assets
        if asset.media_type == "audio"
        and asset.selected
        and asset.request_field == "reference_audios"
    ]


def _validate_reference_audio_request(audio_paths: list[str]) -> None:
    if len(audio_paths) > MAX_SEEDANCE2_REFERENCE_AUDIOS:
        raise ValueError("Seedance2 参考音频最多 3 段")

    total_duration = 0.0
    measured = False
    for path in audio_paths:
        try:
            total_duration += probe_voice_sample_duration_seconds(path)
            measured = True
        except ValueError:
            continue
    if measured and total_duration > MAX_SEEDANCE2_REFERENCE_AUDIO_TOTAL_SECONDS:
        raise ValueError(
            "Seedance2 参考音频总时长超过 15 秒，" "请回到角色工作台把参考声线裁剪到 3-5 秒"
        )


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _validate_dialogue_final_prompt(
    *,
    beat: dict[str, Any],
    final_prompt: str,
    assets: list[Seedance2ResolvedAsset],
    request_references: list[ShotReference] | None = None,
    validate_reference_audio: bool = True,
) -> None:
    if normalize_seedance2_audio_type(beat) != "dialogue":
        return

    # Dialogue is an independent payload.  The visual prompt only needs to
    # describe the speaking performance; requiring the literal line here was
    # what forced narration into ``prompt`` and made the provider read it.
    if not resolve_beat_dialogue(beat):
        return

    if not validate_reference_audio:
        return

    # A node-selected voice reference is already explicit. It does not have
    # to be rewritten as a persisted ``音频N`` label before it reaches the
    # provider, so let request-scoped audio satisfy the voice contract.
    if any(
        _request_reference_kind(reference) == "audio"
        and _request_reference_path(reference)
        for reference in request_references or []
    ):
        return

    selected_voice_assets = _selected_audio_assets(assets)
    if not selected_voice_assets:
        raise ValueError("Seedance2 对白缺少可用的角色参考声线")


def collect_seedance2_video_prereq_errors(
    *,
    project_output: str | Path,
    episode: int,
    beats: list[dict[str, Any]],
    characters: list[Any] | None = None,
    prop_menu: list[Any] | None = None,
) -> list[Seedance2VideoPrereqError]:
    """Return missing/invalid Seedance 2.0 project references before video generation."""

    project_output = Path(project_output)
    errors: list[Seedance2VideoPrereqError] = []
    for index, beat in enumerate(beats):
        config = parse_seedance2_config(beat.get("seedance2_config_json"))
        next_beat = beats[index + 1] if index + 1 < len(beats) else None
        assets = build_seedance2_project_assets(
            project_output=project_output,
            episode=episode,
            beat=beat,
            mode=config.mode,
            next_beat=next_beat,
            characters=characters,
            prop_menu=prop_menu,
        )
        final_prompt = str(config.final_prompt or "").strip()
        append_seedance2_user_reference_assets(
            assets,
            reference_image_paths=list(config.reference_image_paths),
            reference_audio_paths=list(config.reference_audio_paths),
        )
        assets = apply_prompt_audio_selection(assets, final_prompt)
        beat_number = int(beat.get("beat_number") or index + 1)
        selected_audio_assets = _selected_audio_assets(assets)
        if len(selected_audio_assets) > MAX_SEEDANCE2_REFERENCE_AUDIOS:
            errors.append(
                Seedance2VideoPrereqError(
                    beat_number=beat_number,
                    key="reference_audios",
                    label="Seedance2 参考音频最多 3 段，请减少同一 Beat 的 speaker 或合并台词",
                    media_type="audio",
                    path="",
                    reason="reference_audio_count_exceeded",
                )
            )
            continue
        for asset in assets:
            reason = _asset_missing_reason(asset)
            if not reason:
                continue
            errors.append(
                Seedance2VideoPrereqError(
                    beat_number=beat_number,
                    key=asset.key,
                    label=f"{asset.label}（{asset.note}）" if asset.note else asset.label,
                    media_type=asset.media_type,
                    path=str(asset.path),
                    reason=reason,
                )
            )
    return errors


async def prepare_seedance2_generation_inputs(
    *,
    project_output: str | Path,
    episode: int,
    beat: dict[str, Any],
    video_mode: str,
    prompt: str,
    duration: float,
    gen_mode: str | None = None,
    request_references: list[ShotReference] | None = None,
    resolution: str = "720p",
    ratio: str = "9:16",
    next_beat: dict[str, Any] | None = None,
    characters: list[Any] | None = None,
    prop_menu: list[Any] | None = None,
) -> Seedance2PreparedGeneration:
    """Prepare prompt, config, and media references for one Seedance 2.0 beat."""

    project_output = Path(project_output)
    config = parse_seedance2_config(beat.get("seedance2_config_json"))
    normalized_request_references = _normalized_request_references(request_references)
    request_images = _request_images(normalized_request_references)

    # A selected canvas mode is authoritative and must be applied before
    # resolving project assets.  The local ``keyframe`` sentinel remains the
    # only legacy value that changes persisted mode when no explicit provider
    # mode was selected.
    explicit_mode = normalize_seedance2_generation_mode(gen_mode)
    if explicit_mode is not None:
        config.mode = explicit_mode
    elif video_mode == "keyframe" and config.mode != Seedance2I2VMode.FIRST_LAST_FRAME:
        config.mode = Seedance2I2VMode.FIRST_LAST_FRAME

    assets = build_seedance2_project_assets(
        project_output=project_output,
        episode=episode,
        beat=beat,
        mode=config.mode,
        next_beat=next_beat,
        characters=characters,
        prop_menu=prop_menu,
    )
    if config.mode != Seedance2I2VMode.TEXT_TO_VIDEO:
        append_seedance2_user_reference_assets(
            assets,
            reference_image_paths=list(config.reference_image_paths),
            reference_audio_paths=list(config.reference_audio_paths),
        )

    # A provider's default 4s configuration is not a creative decision.  Use
    # the director's source-material duration until the user explicitly edits
    # this beat in the video panel.
    requested_duration = config.duration if config.duration_user_set else duration
    target_duration = max(1, int(math.ceil(float(requested_duration or 0))))
    config.duration = target_duration
    config.resolution = resolution or config.resolution
    config.ratio = ratio or config.ratio

    incoming_prompt = str(prompt or "").strip()
    # A global director writes the canonical Beat motion into ``video_prompt``
    # while Seedance keeps a separate ``final_prompt`` field. Adopt that
    # director value for legacy/empty configs at the shared preparation
    # boundary, so every Seedance transport receives the same prompt.
    synced_json = sync_seedance2_prompt_from_director(
        beat,
        incoming_prompt,
        force=False,
        config=config,
    )
    if synced_json:
        config = parse_seedance2_config(synced_json)

    final_prompt = str(config.final_prompt or incoming_prompt or "").strip()
    if not final_prompt:
        beat_number = int(beat.get("beat_number") or 0)
        prefix = f"Beat {beat_number} " if beat_number else ""
        raise ValueError(
            f"{prefix}Seedance 2.0 最终提示词为空，请先在 Seedance 2.0 Prompt 面板生成或填写最终提示词"
        )
    config.final_prompt = final_prompt
    assets = apply_prompt_audio_selection(assets, final_prompt)

    auto_images = selected_reference_paths(assets, "reference_images")
    # Only the full ``allReference`` contract consumes audio.  Canvas modes
    # such as imageToVideo, firstLastFrame, imageReference, and videoEdit may
    # share the enum's multimodal preparation path, but must not inherit voice
    # references from the beat.
    allowed_request_kinds = _request_reference_kinds_for_mode(gen_mode)
    reference_audio_enabled = (
        config.mode == Seedance2I2VMode.MULTIMODAL_REFERENCE
        and "audio" in allowed_request_kinds
        # Empty mode values are how older callers represent an omitted
        # provider mode.  Keep the config-selected multimodal contract in
        # that case instead of silently dropping its audio references.
        and (not str(gen_mode or "").strip() or _is_all_reference_mode(gen_mode))
    )
    auto_audios = (
        selected_reference_paths(assets, "reference_audios")
        if reference_audio_enabled
        else []
    )
    config.reference_image_paths = (
        _unique_paths(auto_images)
        if config.mode != Seedance2I2VMode.TEXT_TO_VIDEO
        else []
    )
    config.reference_audio_paths = (
        _unique_paths(auto_audios)
        if config.mode != Seedance2I2VMode.TEXT_TO_VIDEO
        else []
    )
    request_audio_paths = (
        [
            _request_reference_path(reference)
            for reference in normalized_request_references
            if _request_reference_kind(reference) == "audio"
        ]
        if reference_audio_enabled
        else []
    )
    if reference_audio_enabled:
        _validate_reference_audio_request(
            _unique_paths([*config.reference_audio_paths, *request_audio_paths])
        )

    _validate_dialogue_final_prompt(
        beat=beat,
        final_prompt=final_prompt,
        assets=assets,
        request_references=normalized_request_references,
        validate_reference_audio=reference_audio_enabled,
    )

    image_path: str | None = None
    last_frame_path: str | None = None
    references: list[ShotReference] = []

    if config.mode == Seedance2I2VMode.FIRST_FRAME:
        first_frames = selected_reference_paths(assets, "image_url")
        request_first = _request_image_for_role(
            request_images, "首帧", "first_frame", "firstframe", "first"
        )
        image_path = (
            _request_reference_path(request_first)
            if request_first is not None
            else _request_reference_path(request_images[0])
            if request_images
            else first_frames[0]
            if first_frames
            else None
        )
    elif config.mode == Seedance2I2VMode.FIRST_LAST_FRAME:
        first_frames = selected_reference_paths(assets, "first_frame_image")
        last_frames = selected_reference_paths(assets, "last_frame_image")
        request_first = _request_image_for_role(
            request_images, "首帧", "first_frame", "firstframe", "first"
        )
        request_last = _request_image_for_role(
            request_images, "尾帧", "last_frame", "lastframe", "last"
        )
        unassigned_images = [
            reference
            for reference in request_images
            if reference is not request_first and reference is not request_last
        ]
        if request_first is None and request_last is None and request_images:
            request_first = request_images[0]
            request_last = request_images[1] if len(request_images) > 1 else None
        elif request_first is None and unassigned_images:
            # A lone ``尾帧`` reference must not be reused as the first frame;
            # let the persisted/local first frame fill the missing endpoint.
            request_first = unassigned_images[0]
        elif request_last is None and unassigned_images:
            request_last = unassigned_images[0]
        image_path = (
            _request_reference_path(request_first)
            if request_first is not None
            else first_frames[0]
            if first_frames
            else None
        )
        last_frame_path = (
            _request_reference_path(request_last)
            if request_last is not None
            else last_frames[0]
            if last_frames
            else None
        )
    else:
        references = _references_from_paths(
            image_paths=config.reference_image_paths,
            audio_paths=config.reference_audio_paths,
        )
        if config.mode == Seedance2I2VMode.MULTIMODAL_REFERENCE:
            references.extend(
                reference
                for reference in normalized_request_references
                if _request_reference_kind(reference) in allowed_request_kinds
            )

    return Seedance2PreparedGeneration(
        prompt=final_prompt,
        seedance2_config_json=dump_seedance2_config(config),
        duration=target_duration,
        mode=config.mode,
        image_path=image_path,
        last_frame_path=last_frame_path,
        references=references,
        assets=assets,
    )
