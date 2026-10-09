"""Celery runners for video-generation tasks."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from novelvideo.media_binaries import bundled_media_binary
from novelvideo.project_context import ProjectContext
from novelvideo.production.shot_contract import (
    SEAM_CONTINUOUS,
    shot_handoff_seam,
    shot_incoming_seam,
)
from novelvideo.task_backend.cancel import (
    TaskTimedOut,
    await_envelope_with_cancel_watch,
    raise_if_envelope_cancel_requested,
    remaining_timeout_seconds,
)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.runners.canvas_media import (
    commit_media_result_to_canvas,
    media_file_metadata,
)
from novelvideo.task_backend.runners.dialogue_audio_support import mux_dialogue_audio
from novelvideo.task_backend.runners.video_request_keys import (
    _coerce_duration_seconds,
    declared_image_reference_limit,
    direct_video_family,
    single_video_idempotency_key,
    video_media_input_token,
)
from novelvideo.task_backend.runners.video_compose_support import (
    append_delivery_loudness as _append_delivery_loudness,
    build_transition_subtitle_entries as _build_transition_subtitle_entries,
    compose_color_params as _compose_color_params, compose_video_encoder_args as _compose_video_encoder_args,
    escape_ffmpeg_subtitle_path as _escape_ffmpeg_subtitle_path,
    ffmpeg_xfade_transition as _ffmpeg_xfade_transition,
    select_compose_video_encoder as _select_compose_video_encoder,
)
from novelvideo.task_backend.subprocesses import run_project_subprocess
from novelvideo.services.canvas_assets import ensure_freezone_dirs
from novelvideo.task_backend.runners.video_history import _append_freezone_video_node_history
from novelvideo.services.video_generation_source import persist_video_generation_source, video_prompt_digest
from novelvideo.services.video_generation_request import build_video_generation_request, video_execution_arguments, video_generation_request, video_generation_request_matches
from novelvideo.services.task_failures import classify_video_pending
from novelvideo.services.video_tasks import (
    probe_video_duration,
    probe_video_size,
    run_freezone_video_gen as run_freezone_video_job,
    strip_unrequested_video_audio,
)
from novelvideo.task_state import get_task_manager


logger = logging.getLogger(__name__)


def _log(manager, ctx: ProjectContext, envelope: dict[str, Any], message: str) -> None:
    manager.update_progress_for_project(
        ctx,
        str(envelope["task_type"]),
        int(envelope.get("episode") or 0),
        beat_num=envelope.get("beat_num"),
        scope=envelope.get("scope"),
        current_task=message,
        logs=[message],
    )


def _resolve_video_aspect_ratio(value: object, frame_path: object) -> str:
    """Follow the first frame when an I2V task has no fixed ratio."""
    requested = str(value or "").strip()
    if requested and requested.lower() != "auto":
        return requested
    return "adaptive" if str(frame_path or "").strip() else "9:16"


# Batch episode payloads can come from the legacy workbench (``keyframe`` /
# ``first_frame``) or from a Canvas video node (camelCase and snake_case
# capability labels).  Keep the provider-facing spelling separate from the
# local ``keyframe`` sentinel used to select the next beat's first frame.
_BATCH_VIDEO_MODE_ALIASES: dict[str, str] = {
    "texttovideo": "textToVideo",
    "text_to_video": "textToVideo",
    "t2v": "textToVideo",
    "imagetovideo": "imageToVideo",
    "image_to_video": "imageToVideo",
    "i2v": "imageToVideo",
    "firstframe": "imageToVideo",
    "firstlastframe": "firstLastFrame",
    "first_last_frame": "firstLastFrame",
    "firstlast": "firstLastFrame",
    "first_last": "firstLastFrame",
    "keyframe": "firstLastFrame",
    "flf": "firstLastFrame",
    "allreference": "allReference",
    "all_reference": "allReference",
    "referencetovideo": "allReference",
    "reference_to_video": "allReference",
    "multimodalreference": "allReference",
    "multimodal_reference": "allReference",
    "imagereference": "imageReference",
    "image_reference": "imageReference",
    "videoedit": "videoEdit",
    "video_edit": "videoEdit",
}


def _normalize_batch_video_mode(value: object) -> str:
    """Normalize Canvas/legacy mode aliases without guessing unknown modes."""

    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return ""
    token = raw.casefold().replace("-", "_").replace(" ", "")
    if not token:
        return ""
    return _BATCH_VIDEO_MODE_ALIASES.get(token, raw)


def _batch_mode_uses_last_frame(value: object) -> bool:
    return _normalize_batch_video_mode(value) == "firstLastFrame"


def _contract_handoff_seam(beat: object) -> str:
    """Return the shot contract's declared seam to the next shot.

    Only ``continuous`` handoffs may become automatic first/last-frame requests.
    A keyframe request is an exclusive two-image contract, so turning every shot
    into a keyframe would silently drop identity/scene references and audio.
    """

    return shot_handoff_seam(beat)


def _beat_has_dialogue_track(beat: object) -> bool:
    """Whether this beat carries a spoken line that needs the audio channel."""

    if not isinstance(beat, dict):
        return False
    if str(beat.get("dialogue_text") or "").strip():
        return True
    if beat.get("spoken_dialogue"):
        return True
    return str(beat.get("audio_type") or "").strip().casefold() == "dialogue"


def _auto_keyframe_eligible(
    beat: object,
    *,
    contract_ready: bool,
    keyframe_supported: bool,
    has_next_frame: bool,
) -> bool:
    """Automatic first/last-frame eligibility derived from the shot contract.

    A keyframe request is an exclusive two-image contract (endpoints only), so
    it is reserved for ``continuous`` seams that do not carry a spoken line:
    hard cuts and dialogue beats keep first-frame mode together with their
    identity/scene reference and audio channels.
    """

    if not (contract_ready and keyframe_supported and has_next_frame):
        return False
    if _contract_handoff_seam(beat) != "continuous":
        return False
    return not _beat_has_dialogue_track(beat)


#: 多参考模型上的「落点参考」：下一镜首帧就是本镜的结束状态（合同已保证）。
SEAM_LANDING_ROLE = "落点参考"


def _append_seam_landing_reference(
    references: list[Any],
    *,
    beat: object,
    landing_path: object,
    image_limit: int,
    generation_mode: object,
    backend: object,
    prompt: str,
    reference_factory: Callable[..., Any],
) -> tuple[list[Any], str]:
    """Anchor a continuous seam with the next shot's first frame.

    Models without a first/last-frame mode can only be told where a shot should
    land through an ordinary reference image; the contract already guarantees
    that the next shot starts exactly at this shot's declared end state. Hard
    cuts are never anchored, and the prompt names the image by index so the
    extra reference is not mistaken for a subject/scene reference.

    """

    if _contract_handoff_seam(beat) != "continuous":
        return references, prompt
    if _normalize_batch_video_mode(generation_mode) not in {
        "",
        "allReference",
        "imageReference",
    }:
        return references, prompt
    declared_modes = _direct_canvas_modes(backend)
    if declared_modes and not (declared_modes & {"allReference", "imageReference"}):
        return references, prompt
    if direct_video_family(backend) in _FIRST_FRAME_MIXING_REJECTED_FAMILIES:
        return references, prompt
    path = str(landing_path or "").strip()
    if not path or not Path(path).is_file() or image_limit <= 0:
        return references, prompt
    if any(_runner_reference_path(reference) == path for reference in references):
        return references, prompt
    image_count = sum(
        1 for reference in references if _runner_reference_kind(reference) == "image"
    )
    if image_count + 1 > image_limit:
        return references, prompt
    anchored = [*references, reference_factory("image", path, SEAM_LANDING_ROLE)]
    prompt = (
        f"{prompt} 参考图第{image_count + 1}张是本镜的落点目标（下一镜起始画面），"
        "只用来确定结束构图，不要提前执行下一镜的动作。"
    ).strip()
    return anchored, prompt


def _direct_canvas_modes(backend: object) -> frozenset[str]:
    """Return the canvas modes a direct video backend declares for itself.

    An unresolvable capability returns an empty set so callers stay on the
    historical request shape instead of guessing a mode the endpoint may reject.
    """

    try:
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        model = resolve_direct_video_model(str(backend or ""))
    except Exception:  # noqa: BLE001 - 能力查询失败不得阻断生成
        return frozenset()
    if model is None:
        return frozenset()
    declared = getattr(getattr(model, "profile", None), "exact_canvas_modes", ())
    return frozenset(str(item) for item in declared or ())


_FIRST_FRAME_MIXING_REJECTED_FAMILIES = frozenset({"minimax-h3"})
def _prefer_seam_reference_mode(
    *,
    beat: object,
    generation_mode: object,
    backend: object,
    frame_path: object,
    landing_path: object,
) -> str:
    """Promote a continuous seam's first-frame request to multi-reference.

    ``imageToVideo`` is a strict one-image transport: every ordinary reference
    (identity images included) is dropped, so a model without a first/last-frame
    mode has no way to state where the shot must land.  Only the untouched
    legacy default is promoted; an explicit operator mode, a hard cut, a missing
    landing frame, or a backend without the multi-reference mode all keep the
    historical request shape.
    """

    if _normalize_batch_video_mode(generation_mode) != "imageToVideo":
        return ""
    if _contract_handoff_seam(beat) != "continuous":
        return ""
    frame = str(frame_path or "").strip()
    landing = str(landing_path or "").strip()
    if not frame or not landing or not Path(landing).is_file():
        return ""
    if "allReference" not in _direct_canvas_modes(backend):
        return ""
    return "allReference"


_BATCH_VIDEO_FIELD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The canonical names on ``single_config`` match what the single-shot
    # runner consumes.  Keep this list bounded so arbitrary beat metadata never
    # becomes a constructor argument or provider payload by accident.
    (
        "gen_mode",
        (
            "gen_mode",
            "genMode",
            "generation_mode",
            "generationMode",
            "mode",
        ),
    ),
    (
        "references",
        (
            "references",
            "reference_items",
            "referenceItems",
            "referenceInputs",
            "reference_inputs",
        ),
    ),
    (
        "video_duration",
        ("video_duration", "videoDuration", "duration_seconds", "durationSeconds", "duration"),
    ),
    ("last_frame_path", ("last_frame_path", "lastFramePath")),
    ("resolution", ("resolution",)),
    ("ratio", ("ratio", "aspect_ratio", "aspectRatio")),
    ("parameters", ("parameters",)),
    ("provider_mapping", ("provider_mapping", "providerMapping", "mapping")),
    ("size", ("size",)),
    ("size_field", ("size_field", "sizeField")),
    ("generate_audio", ("generate_audio", "generateAudio")),
    (
        "generate_audio_explicit",
        (
            "generate_audio_explicit",
            "generateAudioExplicit",
            "generate_audio_user_set",
            "generateAudioUserSet",
        ),
    ),
    ("dialogue_text", ("dialogue_text", "dialogueText")),
    ("spoken_dialogue", ("spoken_dialogue", "spokenDialogue")),
    ("audio_type", ("audio_type", "audioType")),
    ("speaker", ("speaker",)),
    (
        "native_audio_strategy",
        ("native_audio_strategy", "nativeAudioStrategy"),
    ),
    ("audio_asset_ref", ("audio_asset_ref", "audioAssetRef")),
    ("audio_setting", ("audio_setting", "audioSetting")),
    ("auto_video_dispatch", ("auto_video_dispatch", "autoVideoDispatch")),
    ("style", ("style",)),
    ("seedance2_config", ("seedance2_config", "seedance2Config")),
    (
        "seedance2_config_json",
        ("seedance2_config_json", "seedance2ConfigJson"),
    ),
    ("workflow", ("workflow",)),
    ("workflow_input_rules", ("workflow_input_rules", "workflowInputRules")),
    ("resolution_mappings", ("resolution_mappings", "resolutionMappings")),
    ("opaque", ("opaque", "capability_opaque", "capabilityOpaque")),
)


def _batch_video_value_is_present(value: object) -> bool:
    """Treat empty scalar values as absent while preserving explicit containers."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _promote_batch_video_fields(beat: object) -> dict[str, object]:
    """Copy only request-scoped video fields from a beat into ``single_config``.

    Beats persisted by older workbenches keep these values at the top level;
    Canvas nodes may nest them below ``video_config``.  Top-level values win,
    including explicit ``False`` and empty containers.  The returned mapping
    is a shallow copy so the caller never mutates the stored beat.
    """

    if not isinstance(beat, dict):
        return {}
    containers: list[dict[str, object]] = [beat]
    for key in ("video_config", "videoConfig"):
        nested = beat.get(key)
        if isinstance(nested, dict):
            containers.append(nested)

    promoted: dict[str, object] = {}
    for canonical, aliases in _BATCH_VIDEO_FIELD_ALIASES:
        found = False
        for container in containers:
            for alias in aliases:
                if alias not in container:
                    continue
                value = container[alias]
                # A top-level null/blank means "not configured" and permits a
                # nested legacy value; empty lists/dicts remain meaningful.
                if not _batch_video_value_is_present(value):
                    continue
                promoted[canonical] = value
                found = True
                break
            if found:
                break
    return promoted


def _batch_video_references(value: object) -> list[object]:
    """Normalize the bounded reference field into the runner's list shape."""

    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (str, os.PathLike)) and str(value).strip():
        return [str(value).strip()]
    return []


def _batch_reference_count(references: object, kind: str) -> int:
    expected = str(kind or "").strip().lower()
    return sum(
        1
        for reference in _batch_video_references(references)
        if _runner_reference_kind(reference) == expected
        and _runner_reference_path(reference)
    )


def _batch_has_first_last_media(
    references: object,
    *,
    image_path: object = None,
) -> bool:
    """Return whether explicit first/last mode has two deterministic images."""

    image_references = [
        reference
        for reference in _batch_video_references(references)
        if _runner_reference_kind(reference) == "image"
        and _runner_reference_path(reference)
    ]
    if len(image_references) >= 2:
        return True
    first_path = str(image_path or "").strip()
    if first_path and image_references:
        return _runner_reference_path(image_references[0]) != first_path
    if not image_references:
        return False
    role = _runner_reference_role(image_references[0])
    if any(token in role for token in ("尾帧", "last", "last_frame", "lastframe")):
        return True
    return False


def _runner_reference_value(reference: object, name: str, default: object = "") -> object:
    if isinstance(reference, dict):
        return reference.get(name, default)
    return getattr(reference, name, default)


def _runner_reference_kind(reference: object) -> str:
    raw = _runner_reference_value(reference, "type", "") or _runner_reference_value(
        reference, "kind", "image"
    )
    raw = getattr(raw, "value", raw)
    value = str(raw or "image").strip().lower()
    return value if value in {"image", "video", "audio"} else "image"


def _runner_reference_path(reference: object) -> str:
    if isinstance(reference, (str, os.PathLike)):
        return str(reference).strip()
    for name in ("path", "url", "uri"):
        value = _runner_reference_value(reference, name, "")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _runner_reference_role(reference: object) -> str:
    return str(_runner_reference_value(reference, "role", "") or "").strip().lower()


def _filter_runner_media_for_mode(
    mode: object,
    *,
    image_path: object,
    last_frame_path: object,
    references: object,
) -> tuple[str | None, str | None, list[object]]:
    """Build the request media view before dispatch and adapters.

    Explicit Canvas modes are authoritative.  Omitted/legacy values keep the
    original media collection so existing episode behavior remains unchanged.
    """

    normalized = _normalize_batch_video_mode(mode)
    if normalized not in {
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "allReference",
        "imageReference",
        "videoEdit",
    }:
        return (
            str(image_path).strip() if image_path else None,
            str(last_frame_path).strip() if last_frame_path else None,
            _batch_video_references(references),
        )

    source_references = _batch_video_references(references)
    image_references = [
        reference
        for reference in source_references
        if _runner_reference_kind(reference) == "image"
        and _runner_reference_path(reference)
    ]

    def first_image() -> str:
        direct = str(image_path or "").strip()
        if direct:
            return direct
        for reference in image_references:
            if any(
                token in _runner_reference_role(reference)
                for token in ("首帧", "first", "first_frame", "firstframe")
            ):
                return _runner_reference_path(reference)
        return _runner_reference_path(image_references[0]) if image_references else ""

    def last_image() -> str:
        direct = str(last_frame_path or "").strip()
        if direct:
            return direct
        for reference in image_references:
            if any(
                token in _runner_reference_role(reference)
                for token in ("尾帧", "last", "last_frame", "lastframe")
            ):
                return _runner_reference_path(reference)
        return _runner_reference_path(image_references[1]) if len(image_references) > 1 else ""

    if normalized == "textToVideo":
        return None, None, []
    if normalized == "imageToVideo":
        return first_image() or None, None, []
    if normalized == "firstLastFrame":
        return first_image() or None, last_image() or None, []
    if normalized == "imageReference":
        selected: list[object] = []
        seen: set[str] = set()
        # ``last_frame_path`` is a local transition sentinel, not an image
        # reference.  A stale尾帧 must not silently become a second reference
        # when the node explicitly selected imageReference.
        for path in (image_path,):
            value = str(path or "").strip()
            if value and value not in seen:
                selected.append({"type": "image", "path": value, "role": "图片参考"})
                seen.add(value)
        for reference in image_references:
            path = _runner_reference_path(reference)
            if path and path not in seen:
                selected.append(reference)
                seen.add(path)
        return None, None, selected
    if normalized == "videoEdit":
        return (
            None,
            None,
            [
                reference
                for reference in source_references
                if _runner_reference_kind(reference) == "video"
                and _runner_reference_path(reference)
            ],
        )
    # allReference accepts standalone frame arguments as ordinary image
    # references. Keep the direct paths as well; the downstream adapters use
    # the same deterministic promotion and de-duplicate before relay.
    return (
        str(image_path).strip() if image_path else None,
        str(last_frame_path).strip() if last_frame_path else None,
        source_references,
    )


def _ratio_value(value: object) -> float | None:
    """Parse the common ``w:h``/``wxh`` ratio forms used by model contracts."""
    text = str(value or "").strip().lower().replace("×", "x")
    parts = text.split(":", 1) if ":" in text else text.split("x", 1)
    if len(parts) != 2:
        return None
    try:
        width, height = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    return width / height if width > 0 and height > 0 else None


def _frame_ratio(frame_path: object) -> float | None:
    """Read a local first-frame ratio without decoding the full image."""
    path = Path(str(frame_path or "").strip())
    if not path.is_file():
        return None
    try:
        from novelvideo.seedance2_i2v.assets import read_seedance2_image_size

        size = read_seedance2_image_size(path)
    except (OSError, ValueError, TypeError):
        return None
    if not size:
        return None
    width, height = size
    return float(width) / float(height) if width > 0 and height > 0 else None


def _validate_local_video_frame_contract(
    paths: object,
    frame_path: object,
    *,
    label: str,
) -> None:
    """Reject an explicitly non-single-frame local input before paid dispatch.

    Remote/data URLs are already owned by the provider and cannot be inspected
    by the local sidecar contract.  Legacy local images without a sidecar stay
    compatible because ``PathResolver`` treats them as valid.
    """
    raw_path = str(frame_path or "").strip()
    if not raw_path or raw_path.startswith(("http://", "https://", "data:")):
        return
    candidate = Path(raw_path)
    validator = getattr(paths, "validate_single_frame_contract", None)
    if not callable(validator):
        return
    valid, reason = validator(candidate)
    if not valid:
        raise RuntimeError(f"{label}不满足单格图像合同: {reason} ({candidate})")


def _nearest_ratio(options: tuple[str, ...], target: float | None) -> str:
    if not options:
        return ""
    if target is None or target <= 0:
        return options[0]
    scored = [
        (abs((_ratio_value(option) or target) - target), index, option)
        for index, option in enumerate(options)
    ]
    return min(scored)[2]


def _resolve_direct_video_defaults(
    backend: object,
    *,
    resolution: object,
    ratio: object,
    frame_path: object,
) -> tuple[str, str]:
    """Resolve omitted legacy node values against the selected direct model.

    The episode workbench predates discovered direct-model contracts and may
    omit both fields.  In that case the old global ``720p``/``adaptive``
    defaults are not transport-safe for models such as AutoDL H3, whose
    contract uses orientation-qualified resolutions and fixed ratios.
    """
    from novelvideo.generators.video.direct_models import (
        direct_video_model_option,
        resolve_direct_video_model,
    )

    model = resolve_direct_video_model(str(backend or ""))
    if model is None:
        return str(resolution or "").strip(), str(ratio or "").strip()

    option = direct_video_model_option(model)
    profile = model.profile
    resolution_options = tuple(
        str(item).strip()
        for item in (
            option.get("runtimeResolutionOptions")
            or option.get("resolutionOptions")
            or profile.resolution
        )
        if str(item).strip()
    )
    aspect_options = tuple(
        str(item).strip()
        for item in (option.get("aspectRatioOptions") or profile.aspect)
        if str(item).strip()
    )

    requested_ratio = str(ratio or "").strip()
    ratio_is_auto = requested_ratio.casefold() in {"", "auto", "adaptive"}
    target_ratio = _frame_ratio(frame_path) if ratio_is_auto else _ratio_value(requested_ratio)
    if requested_ratio and not ratio_is_auto:
        if requested_ratio.casefold() in {item.casefold() for item in aspect_options}:
            resolved_ratio = requested_ratio
        elif profile.supports_custom_aspect_ratio and target_ratio is not None:
            resolved_ratio = requested_ratio
        else:
            resolved_ratio = _nearest_ratio(aspect_options, target_ratio)
    elif aspect_options:
        resolved_ratio = _nearest_ratio(aspect_options, target_ratio)
    else:
        # An explicitly empty upstream aspect enum remains an omitted field.
        resolved_ratio = ""

    requested_resolution = str(resolution or "").strip()
    normalized_resolution = requested_resolution.casefold().replace("×", "x")
    resolution_by_case = {item.casefold(): item for item in resolution_options}
    resolved_resolution = resolution_by_case.get(normalized_resolution, "")
    if not resolved_resolution:
        # AutoDL commonly exposes ``480p竖``/``480p横`` while older canvas
        # nodes send the canonical height only.  Preserve that height and use
        # the resolved ratio to select its orientation.
        quality_match = re.fullmatch(r"([1-9][0-9]{2,5})p", normalized_resolution)
        if quality_match:
            prefix = f"{quality_match.group(1)}p"
            candidates = tuple(
                item
                for item in resolution_options
                if item.casefold().startswith(prefix.casefold())
            )
            if candidates:
                vertical = (_ratio_value(resolved_ratio) or 1.0) < 1.0
                oriented = tuple(
                    item
                    for item in candidates
                    if ("竖" in item) == vertical
                )
                resolved_resolution = (oriented or candidates)[0]
    if not resolved_resolution:
        defaults = option.get("parameterDefaults") or option.get("parameter_defaults")
        default_value = defaults.get("resolution") if isinstance(defaults, dict) else None
        if default_value:
            resolved_resolution = resolution_by_case.get(str(default_value).casefold(), str(default_value))
    if not resolved_resolution and resolution_options:
        resolved_resolution = resolution_options[0]
    return resolved_resolution, resolved_ratio


def _apply_direct_video_resolution(
    generate_kwargs: dict[str, Any],
    *,
    backend: object,
    resolution: object,
) -> None:
    """Carry the resolved direct-model resolution into the request call."""
    from novelvideo.generators.video.direct_models import is_direct_video_backend

    if is_direct_video_backend(str(backend or "")) and str(resolution or "").strip():
        generate_kwargs["resolution"] = str(resolution).strip()


def _runner_audio_value(
    config: dict[str, Any],
    beat: object,
    *names: str,
) -> object:
    """Read a semantic audio field from promoted config or the legacy Beat."""

    for source in (config, beat):
        if not isinstance(source, dict):
            continue
        for name in names:
            if name in source:
                return source[name]
    return None


def _resolve_runner_native_audio_preference(
    config: dict[str, Any],
    beat: object,
    backend: object,
) -> bool:
    """Apply the shared semantic audio contract before a direct adapter starts.

    Explicit canvas choices win. Legacy semantic fields remain a fallback;
    spoken dialogue alone does not disable the video's native speech.
    """

    from novelvideo.generators.video.direct_models import resolve_direct_video_model
    from novelvideo.services.video_request_contract import (
        extract_spoken_dialogue,
        resolve_video_audio_preference,
    )

    audio_type = _runner_audio_value(config, beat, "audio_type", "audioType")
    dialogue_text = _runner_audio_value(config, beat, "dialogue_text", "dialogueText")
    spoken_dialogue = _runner_audio_value(config, beat, "spoken_dialogue", "spokenDialogue")
    native_audio_strategy = _runner_audio_value(
        config,
        beat,
        "native_audio_strategy",
        "nativeAudioStrategy",
    )
    audio_asset_ref = _runner_audio_value(config, beat, "audio_asset_ref", "audioAssetRef")
    requested = config.get("generate_audio", True)
    explicit_value = _runner_audio_value(
        config,
        beat,
        "generate_audio_explicit",
        "generateAudioExplicit",
        "generate_audio_user_set",
        "generateAudioUserSet",
    )
    requested_explicit = None if explicit_value is None else bool(explicit_value)
    direct_model = resolve_direct_video_model(str(backend or ""))
    native_audio = getattr(getattr(direct_model, "capability", None), "native_audio", None)
    return resolve_video_audio_preference(
        requested=requested,
        requested_explicit=requested_explicit,
        audio_type=audio_type,
        native_audio=getattr(native_audio, "value", native_audio or "optional"),
        native_audio_strategy=native_audio_strategy,
        has_spoken_dialogue=bool(
            extract_spoken_dialogue(dialogue_text=spoken_dialogue or dialogue_text or "")
        ),
        has_external_audio=bool(str(audio_asset_ref or "").strip()),
    )


def _beat_requests_silent_native_audio(beat: object) -> bool:
    """Compatibility predicate for callers that only need silence/action."""

    if not isinstance(beat, dict):
        return False
    from novelvideo.services.video_request_contract import (
        explicit_audio_type_requests_silence,
    )

    return explicit_audio_type_requests_silence(
        beat.get("audio_type") or beat.get("audioType")
    )


def _resolve_video_failover_backend(primary_backend: str, error: object) -> str:
    """Return an explicitly enabled NewAPI failover for channel errors.

    A cross-model fallback can silently change both quality and billing (for
    example Firefly 480p -> Seedance 720p).  It is therefore opt-in instead of
    being activated merely by a stale ``VILLAGE_CANVAS_VIDEO_FAILOVER_BACKEND`` env.
    """

    message = str(error or "").lower()
    if not any(
        marker in message for marker in ("model_not_found", "no available channel")
    ):
        return ""
    allow_cross_model = (
        os.environ.get("VILLAGE_CANVAS_VIDEO_ALLOW_CROSS_MODEL_FAILOVER", "").strip().lower()
    )
    if allow_cross_model not in {"1", "true", "yes", "on"}:
        return ""
    fallback = os.environ.get("VILLAGE_CANVAS_VIDEO_FAILOVER_BACKEND", "").strip()
    if (
        not fallback
        or fallback == primary_backend
        or not primary_backend.startswith("newapi_")
        or not fallback.startswith("newapi_")
    ):
        return ""
    return fallback


def _mp4_top_level_atom_offsets(path: Path) -> dict[str, int]:
    """Return offsets of top-level MP4 atoms without loading the whole file."""
    offsets: dict[str, int] = {}
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            while offset + 8 <= size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    break
                atom_size = int.from_bytes(header[:4], "big")
                atom_type = header[4:8].decode("latin1", errors="ignore")
                if atom_type and atom_type not in offsets:
                    offsets[atom_type] = offset
                header_size = 8
                if atom_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        break
                    atom_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif atom_size == 0:
                    atom_size = size - offset
                if atom_size < header_size:
                    break
                offset += atom_size
    except OSError:
        return {}
    return offsets


def _mp4_moov_before_mdat(path: Path) -> bool:
    atoms = _mp4_top_level_atom_offsets(path)
    moov = atoms.get("moov", -1)
    mdat = atoms.get("mdat", -1)
    return moov >= 0 and mdat >= 0 and moov < mdat


def _ensure_faststart_mp4(
    video_path: Path,
    *,
    on_log,
    timeout_seconds: int | None,
) -> bool:
    """Move MP4 metadata to the front so browser preview starts immediately."""
    if video_path.suffix.lower() != ".mp4" or not _is_nonempty_file(video_path):
        return False
    if _mp4_moov_before_mdat(video_path):
        return False
    ffmpeg = bundled_media_binary("ffmpeg") or shutil.which("ffmpeg")
    if not ffmpeg:
        on_log("未找到 ffmpeg，跳过 MP4 faststart 优化")
        return False

    tmp_path = video_path.with_name(
        f"{video_path.stem}.faststart.tmp{video_path.suffix}"
    )
    try:
        result = run_project_subprocess(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0 or not _is_nonempty_file(tmp_path):
            excerpt = _ffmpeg_error_excerpt(result)
            on_log(f"MP4 faststart 优化失败，保留原文件: {excerpt or 'ffmpeg failed'}")
            return False
        tmp_path.replace(video_path)
        on_log("已优化 MP4 faststart，前端视频预览可更快起播")
        return True
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _ensure_video_preview_frame(video_path: Path) -> Path | None:
    """Create one deterministic local preview frame for a completed video."""
    if not _is_nonempty_file(video_path):
        return None
    ffmpeg = bundled_media_binary("ffmpeg")
    if not ffmpeg:
        return None
    preview_path = video_path.with_suffix(".preview.jpg")
    if _is_nonempty_file(preview_path):
        return preview_path
    temp_path = preview_path.with_name(f"{preview_path.stem}.tmp{preview_path.suffix}")
    try:
        result = run_project_subprocess(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-ss",
                "2",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(temp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not _is_nonempty_file(temp_path):
            return None
        os.replace(temp_path, preview_path)
        return preview_path
    except (OSError, RuntimeError):
        return None
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_video_last_frame(video_path: Path, target_path: Path) -> Path | None:
    """Extract a completed video's true final frame into ``target_path``.

    ``-sseof`` seeks to the tail and ``reverse`` re-orders the decoded tail so
    the first emitted frame is the video's real last frame; one ffmpeg pass
    writes it directly, without decoding the whole file.
    """
    if not _is_nonempty_file(video_path):
        return None
    ffmpeg = bundled_media_binary("ffmpeg")
    if not ffmpeg:
        return None
    temp_path = target_path.with_name(f"{target_path.stem}.tmp{target_path.suffix}")
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result = run_project_subprocess(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-sseof",
                "-0.5",
                "-i",
                str(video_path),
                "-vf",
                "reverse",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(temp_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 or not _is_nonempty_file(temp_path):
            return None
        os.replace(temp_path, target_path)
        return target_path
    except (OSError, RuntimeError):
        return None
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_relay_frame_for_next_beat(
    *,
    paths: object,
    beat: object,
    beat_num: int,
    video_path: Path,
    next_beat: object,
    on_log: Callable[[str], None],
) -> Path | None:
    """Hand this shot's real last frame to the next continuous shot.

    Only a ``continuous`` hand-off consumes a tail frame; hard cuts keep their
    own planned first frame.  The relay sidecar is keyed to ``video_path``'s
    signature, so regenerating this shot invalidates the relay automatically.
    """
    if shot_handoff_seam(beat) != SEAM_CONTINUOUS:
        return None
    frame_for_video = getattr(paths, "frame", None)
    relay_frame = getattr(paths, "relay_frame", None)
    write_relay = getattr(paths, "write_relay_frame", None)
    if not (callable(relay_frame) and callable(write_relay)):
        return None
    has_next_beat = isinstance(next_beat, dict) or (
        callable(frame_for_video) and frame_for_video(beat_num + 1).exists()
    )
    if not has_next_beat:
        return None
    extracted = _ensure_video_last_frame(video_path, relay_frame(beat_num + 1))
    if extracted is None:
        on_log(f"尾帧接力：Beat {beat_num} 成片未抽出可用尾帧，下一镜保持规划首帧")
        return None
    write_relay(beat_num + 1, source_video=video_path)
    on_log(
        f"尾帧接力：Beat {beat_num} 的真实尾帧已写入 Beat {beat_num + 1} 首帧接力位"
    )
    return extracted


async def _run_single_video_async(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    task_type = "single_video"
    episode = int(envelope.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or 0)
    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    output_dir = str(payload.get("output_dir") or ctx.output_dir)

    manager = get_task_manager()
    _log(manager, ctx, envelope, f"开始生成 Beat {beat_num} 视频")

    from novelvideo.generators.video_generator import (
        ShotReference,
        create_video_generator,
    )
    from novelvideo.seedance2_i2v.pipeline import is_huimeng_seedance2_backend
    from novelvideo.utils.path_resolver import PathResolver

    raw_beat = config.get("beat")
    beat = dict(raw_beat) if isinstance(raw_beat, dict) else {}
    # Batch and workflow callers may promote semantic audio fields beside the
    # historical Beat object. Keep one merged request-local view for prompt
    # normalization and Seedance preparation without mutating stored beats.
    for canonical, aliases in (
        ("dialogue_text", ("dialogue_text", "dialogueText")),
        ("spoken_dialogue", ("spoken_dialogue", "spokenDialogue")),
        ("audio_type", ("audio_type", "audioType")),
        ("speaker", ("speaker",)),
        ("native_audio_strategy", ("native_audio_strategy", "nativeAudioStrategy")),
        ("audio_asset_ref", ("audio_asset_ref", "audioAssetRef")),
        (
            "generate_audio_explicit",
            (
                "generate_audio_explicit",
                "generateAudioExplicit",
                "generate_audio_user_set",
                "generateAudioUserSet",
            ),
        ),
    ):
        if canonical in beat:
            continue
        for alias in aliases:
            if alias in config:
                beat[canonical] = config[alias]
                break
    frame_path = config.get("frame_path")
    video_mode = config.get("video_mode", "first_frame")
    # Preserve only an explicitly selected provider mode.  ``video_mode`` is
    # also used as the local first-frame/keyframe preparation sentinel, so its
    # default ``first_frame`` value must not become an implicit transport mode
    # and change legacy text/image inference.
    explicit_generation_mode = str(config.get("gen_mode") or "").strip()
    if explicit_generation_mode:
        generation_mode = explicit_generation_mode
    elif _normalize_batch_video_mode(video_mode) in {
        "textToVideo",
        "imageToVideo",
        "firstLastFrame",
        "allReference",
        "imageReference",
        "videoEdit",
    }:
        generation_mode = _normalize_batch_video_mode(video_mode)
    else:
        generation_mode = ""
    prompt = config.get("prompt", "")
    from novelvideo.project_config import load_project_config
    from novelvideo.styles.project_style import (
        apply_style_snapshot_to_prompt,
        build_project_style_snapshot,
    )

    project_config = load_project_config(ctx.owner_username, ctx.project_name)
    style_snapshot = build_project_style_snapshot(
        config.get("style") or project_config.get("visual_style"),
        username=ctx.owner_username,
        project=ctx.project_name,
        project_dir=output_dir,
        video_model=config.get("video_backend"),
    )
    prompt = apply_style_snapshot_to_prompt(prompt, style_snapshot, modality="video")
    video_duration = config.get("video_duration", 5.0)
    backend_str = config.get("video_backend", "comfyui")
    from novelvideo.services.video_request_contract import (
        compile_and_enforce_native_video_provider_prompt,
        prepare_video_submission,
    )
    prompt_normalization = prepare_video_submission(
        prompt,
        duration_seconds=float(video_duration or 5.0),
        dialogue_text=beat.get("dialogue_text", ""),
        spoken_dialogue=beat.get("spoken_dialogue", ()),
        audio_type=beat.get("audio_type", ""),
        speaker=beat.get("speaker", ""),
    ).normalization
    prompt = prompt_normalization.visual_prompt
    if prompt_normalization.spoken_dialogue:
        beat["spoken_dialogue"] = list(prompt_normalization.spoken_dialogue)
        beat["dialogue_text"] = prompt_normalization.dialogue_text
        if not str(beat.get("audio_type") or "").strip():
            beat["audio_type"] = "dialogue"
    last_frame_path = config.get("last_frame_path")
    seedance2_config = (
        config.get("seedance2_config")
        or config.get("seedance2_config_json")
        or beat.get("seedance2_config_json")
    )
    is_seedance2_backend = is_huimeng_seedance2_backend(backend_str)
    is_wokey_jimeng_backend = str(backend_str or "").strip().lower().startswith(
        "newapi_jimeng-seedance-"
    )

    request_references = [
        ShotReference(
            _runner_reference_kind(item),
            _runner_reference_path(item),
            _runner_reference_role(item),
        )
        for item in _batch_video_references(config.get("references"))
        if _runner_reference_path(item)
    ]

    paths = PathResolver(output_dir, episode)
    videos_dir = paths.videos_dir()
    videos_dir.mkdir(parents=True, exist_ok=True)
    video_path = paths.video(beat_num)

    def on_log(msg: str) -> None:
        _log(manager, ctx, envelope, msg)

    def on_progress(value: float) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            progress=value,
            current_task=f"生成 Beat {beat_num} 视频",
        )

    # 只有用户没显式选模式时才允许升级（路由/批处理各自声明）。升级必须发生在
    # 显式媒体合同之前：``imageToVideo`` 会先清空全部参考图，升级晚了就只剩空表。
    if config.get("seam_mode_promotion"):
        promoted_mode = _prefer_seam_reference_mode(
            beat=beat,
            generation_mode=generation_mode,
            backend=backend_str,
            frame_path=frame_path,
            landing_path=paths.first_frame_for_video(
                beat_num + 1,
                use_director_render=bool(config.get("use_director_render")),
            ),
        )
        if promoted_mode:
            on_log(
                "连续接缝：本镜升级为多图参考请求（首帧 + 身份参考 + 落点参考）"
            )
            generation_mode = promoted_mode

    if (
        video_mode == "keyframe"
        and last_frame_path
        and not is_seedance2_backend
        and not is_wokey_jimeng_backend
    ):
        video_duration = 5.0

    seedance2_references = []
    if is_seedance2_backend:
        from novelvideo.seedance2_i2v.models import Seedance2I2VMode
        from novelvideo.seedance2_i2v.pipeline import (
            prepare_seedance2_generation_inputs,
        )

        prepared = await prepare_seedance2_generation_inputs(
            project_output=output_dir,
            episode=episode,
            beat={**beat, "seedance2_config_json": seedance2_config or "{}"},
            next_beat=config.get("next_beat"),
            video_mode=video_mode,
            gen_mode=generation_mode or None,
            request_references=request_references,
            prompt=prompt,
            duration=video_duration,
            resolution=(
                str(config["resolution"])
                if config.get("resolution") is not None
                else None
            ),
            ratio=str(config["ratio"]) if config.get("ratio") is not None else None,
            prop_menu=config.get("prop_menu"),
        )
        prompt = prepared.prompt
        video_duration = prepared.duration
        frame_path = prepared.image_path
        # 准备阶段可能因为拿不到下一镜而解析不出尾帧；此时保留请求里已经带上的
        # 尾帧，而不是覆盖成空——首尾帧请求不允许静默降级成单首帧。
        last_frame_path = prepared.last_frame_path or last_frame_path
        seedance2_config = prepared.seedance2_config_json
        seedance2_references = prepared.references
        video_mode = (
            "keyframe"
            if prepared.mode == Seedance2I2VMode.FIRST_LAST_FRAME
            else "first_frame"
        )

    model_references = list(seedance2_references)
    # Single-shot canvas requests can carry references outside the persisted
    # Seedance config.  Keep them in the same typed list so explicit modes are
    # not reduced to the project's auto-discovered assets.
    if not is_seedance2_backend:
        model_references = request_references
    elif request_references:
        seen_references = {
            (
                _runner_reference_kind(reference),
                _runner_reference_path(reference),
            )
            for reference in model_references
        }
        for reference in request_references:
            marker = (
                _runner_reference_kind(reference),
                _runner_reference_path(reference),
            )
            if marker not in seen_references:
                model_references.append(reference)
                seen_references.add(marker)

    # A node-selected frame is authoritative for explicit frame contracts.
    # The Seedance preparation also discovers the episode's local frame, so
    # leave the local tail only when a one-image request still needs it as the
    # second endpoint.  Reference-only modes use only the selected media kind
    # when the node supplied one, instead of leaking auto-discovered assets.
    request_image_references = [
        reference
        for reference in request_references
        if _runner_reference_kind(reference) == "image"
        and _runner_reference_path(reference)
    ]
    if generation_mode == "imageToVideo" and request_image_references:
        frame_path = None
    elif generation_mode == "firstLastFrame" and len(request_image_references) >= 2:
        # With two explicit endpoints the node references are authoritative.
        # A one-image request may carry only a tail (or only a head); preserve
        # the other endpoint prepared from the local episode frame.
        frame_path = None
        last_frame_path = None
    elif generation_mode == "imageReference" and request_image_references:
        model_references = request_image_references
    elif generation_mode == "videoEdit":
        request_video_references = [
            reference
            for reference in request_references
            if _runner_reference_kind(reference) == "video"
            and _runner_reference_path(reference)
        ]
        if request_video_references:
            model_references = request_video_references

    # Apply the same explicit media contract used by the provider adapters
    frame_path, last_frame_path, model_references = _filter_runner_media_for_mode(
        generation_mode,
        image_path=frame_path,
        last_frame_path=last_frame_path,
        references=model_references,
    )

    gen_kwargs: dict[str, Any] = {}
    # 非 seedance2 后端（含 seedance-1.5-pro）的清晰度走构造参数透传；
    # seedance2 的清晰度在 prepare 阶段并入 seedance2_config，无需在此重复。
    from novelvideo import config as app_config
    from novelvideo.generators.video_dispatcher import choose_video_backend_for_task
    from novelvideo.generators.video.direct_models import is_direct_video_backend

    single_resolution = str(
        config.get("resolution") or app_config.NEWAPI_VIDEO_RESOLUTION
    )
    direct_video_ratio = ""
    auto_dispatch_raw = config.get("auto_video_dispatch")
    auto_dispatch = (
        None
        if auto_dispatch_raw is None
        else str(auto_dispatch_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    if not is_seedance2_backend:
        decision = choose_video_backend_for_task(
            backend_str,
            duration=float(video_duration or 5.0),
            resolution=single_resolution,
            references=model_references,
            has_first_frame=bool(frame_path),
            auto_enabled=auto_dispatch,
        )
        if decision.changed:
            on_log(
                "自动调度视频模型: "
                f"{backend_str} -> {decision.backend} "
                f"(resolution={decision.resolution}, {decision.reason})"
            )
        backend_str = decision.backend
        single_resolution = decision.resolution
        is_seedance2_backend = is_huimeng_seedance2_backend(backend_str)
    if single_resolution and not is_seedance2_backend and not is_direct_video_backend(backend_str):
        gen_kwargs["resolution"] = single_resolution

    if isinstance(config.get("parameters"), dict):
        gen_kwargs["parameters"] = dict(config["parameters"])
    if isinstance(config.get("provider_mapping"), dict):
        gen_kwargs["provider_mapping"] = dict(config["provider_mapping"])
    # Discovered workflow metadata is request-scoped only for the generic
    # adapter.  Legacy constructors do not accept these arguments and already
    # obtain their capability contract from the model registry.
    if is_direct_video_backend(backend_str):
        from novelvideo.generators.video.direct_models import resolve_direct_video_model
        from novelvideo.generators.video.runtime_contract import is_generic_video_adapter

        direct_model = resolve_direct_video_model(backend_str)
        if direct_model is not None and is_generic_video_adapter(direct_model.adapter_family):
            for key in ("workflow", "workflow_input_rules", "resolution_mappings"):
                value = config.get(key)
                if value is not None:
                    gen_kwargs[key] = value
    if config.get("size"):
        gen_kwargs.setdefault("parameters", {})
        gen_kwargs["parameters"].setdefault("size", config["size"])
    if config.get("size_field"):
        gen_kwargs.setdefault("provider_mapping", {})
        gen_kwargs["provider_mapping"].setdefault("size", config["size_field"])

    if not (is_seedance2_backend and last_frame_path):
        landing_path = paths.first_frame_for_video(
            beat_num + 1,
            use_director_render=bool(config.get("use_director_render")),
        )
        anchored_references, anchored_prompt = _append_seam_landing_reference(
            model_references,
            beat=beat,
            landing_path=landing_path,
            image_limit=declared_image_reference_limit(backend_str),
            generation_mode=generation_mode,
            backend=backend_str,
            prompt=prompt,
            reference_factory=ShotReference,
        )
        if len(anchored_references) > len(model_references):
            model_references = anchored_references
            prompt = anchored_prompt
            on_log("已注入连续接缝落点参考图: 下一镜起始画面作为结束构图目标")

    # The direct registry is the source of truth for omitted legacy values.
    if is_direct_video_backend(backend_str):
        single_resolution, direct_video_ratio = _resolve_direct_video_defaults(
            backend_str,
            resolution=single_resolution,
            ratio=config.get("ratio"),
            frame_path=frame_path,
        )
        if single_resolution:
            gen_kwargs["resolution"] = single_resolution
        gen_kwargs["generate_audio"] = _resolve_runner_native_audio_preference(
            config,
            beat,
            backend_str,
        )
        prompt = await compile_and_enforce_native_video_provider_prompt(
            backend_str,
            prompt,
            prompt_normalization,
            speaker=str(_runner_audio_value(config, beat, "speaker") or ""),
            generate_audio=bool(gen_kwargs.get("generate_audio")),
            beat=beat,
            on_log=on_log,
            audio_type=beat.get("audio_type", ""),
            dialogue_text=beat.get("dialogue_text", ""),
        )
    # Validate the final local inputs immediately before provider dispatch.
    _validate_local_video_frame_contract(paths, frame_path, label="首帧")
    if last_frame_path:
        _validate_local_video_frame_contract(paths, last_frame_path, label="尾帧")

    request_media_inputs = [
        video_media_input_token(frame_path, kind="image", role="first_frame"),
        video_media_input_token(last_frame_path, kind="image", role="last_frame"),
        *[video_media_input_token(_runner_reference_path(reference), kind=_runner_reference_kind(reference), role=_runner_reference_role(reference)) for reference in (model_references or [])],
    ]
    generate_kwargs = {
        "image_path": frame_path,
        "prompt": prompt,
        "output_path": video_path.as_posix(),
        "aspect_ratio": direct_video_ratio
        or _resolve_video_aspect_ratio(config.get("ratio"), frame_path),
        "duration": video_duration,
        "on_log": on_log,
        "on_progress": on_progress,
        "last_frame_path": last_frame_path,
        "gen_mode": generation_mode or None,
        "project_output_dir": output_dir,
        "episode": episode,
        "beat_num": beat_num,
        "task_type": task_type,
        "idempotency_key": single_video_idempotency_key(
            scope=envelope.get("scope")
            or f"single_video:{ctx.project_id}:{episode}:{beat_num}",
            prompt=prompt,
            duration=video_duration,
            generation_mode=generation_mode,
            generate_audio=bool(gen_kwargs.get("generate_audio")),
            media_inputs=request_media_inputs,
        ),
    }
    # Generic direct adapters receive request-scoped controls on ``generate``.
    # Passing resolution only to the constructor is not sufficient because the
    # adapter's generate signature has its own legacy ``720p`` default.
    _apply_direct_video_resolution(
        generate_kwargs,
        backend=backend_str,
        resolution=single_resolution,
    )
    if model_references:
        generate_kwargs["references"] = model_references
    if config.get("audio_setting"):
        generate_kwargs["audio_setting"] = str(config["audio_setting"])
    if is_seedance2_backend:
        generate_kwargs["seedance2_config"] = seedance2_config

    from novelvideo.services.video_dispatch import dispatch_video_generation

    failover_resolution = os.environ.get(
        "VILLAGE_CANVAS_VIDEO_FAILOVER_RESOLUTION", ""
    ).strip()
    outcome = await dispatch_video_generation(
        create_generator=create_video_generator,
        backend=backend_str,
        generator_kwargs=gen_kwargs,
        generate_kwargs=generate_kwargs,
        fallback_selector=lambda primary_result: _resolve_video_failover_backend(
            backend_str, primary_result.error
        ),
        fallback_generator_kwargs=(
            {"resolution": failover_resolution} if failover_resolution else None
        ),
        on_fallback=lambda selected: on_log(
            "主视频模型暂时无可用渠道，自动切换到 "
            f"{selected.removeprefix('newapi_')}"
        ),
    )
    result = outcome.result
    effective_backend = outcome.effective_backend
    failover_from = outcome.fallback_from
    if result.status.value != "done":
        error = RuntimeError(result.error or "视频生成失败")
        error_metadata = getattr(result, "error_metadata", None)
        if isinstance(error_metadata, dict):
            setattr(error, "provider_error_metadata", dict(error_metadata))
        raise error

    from novelvideo.services.video_request_contract import (
        should_strip_unrequested_native_audio,
    )

    audio_type = _runner_audio_value(config, beat, "audio_type", "audioType")
    native_audio_strategy = _runner_audio_value(
        config,
        beat,
        "native_audio_strategy",
        "nativeAudioStrategy",
    )
    dialogue_text = _runner_audio_value(config, beat, "dialogue_text", "dialogueText")
    spoken_dialogue = _runner_audio_value(config, beat, "spoken_dialogue", "spokenDialogue")
    audio_asset_ref = _runner_audio_value(config, beat, "audio_asset_ref", "audioAssetRef")
    requested_native_audio = config.get("generate_audio", False)
    explicit_audio_value = _runner_audio_value(
        config,
        beat,
        "generate_audio_explicit",
        "generateAudioExplicit",
        "generate_audio_user_set",
        "generateAudioUserSet",
    )
    requested_audio_explicit = (
        None if explicit_audio_value is None else bool(explicit_audio_value)
    )
    if should_strip_unrequested_native_audio(
        requested=requested_native_audio,
        requested_explicit=requested_audio_explicit,
        audio_type=audio_type,
        native_audio_strategy=native_audio_strategy,
        has_spoken_dialogue=bool(str(dialogue_text or "").strip() or spoken_dialogue),
        has_external_audio=bool(str(audio_asset_ref or "").strip()),
    ):
        await strip_unrequested_video_audio(video_path, on_log=on_log)

    _ensure_faststart_mp4(
        Path(video_path),
        on_log=on_log,
        timeout_seconds=remaining_timeout_seconds(envelope, default_seconds=120),
    )

    # 真尾帧接力：把本镜成片的真实最后一帧交给下一镜当首帧。写在成片定稿之后，
    # 批处理按 Beat 顺序推进，下一镜解析首帧时就能拿到这一帧。
    _write_relay_frame_for_next_beat(
        paths=paths,
        beat=beat,
        beat_num=beat_num,
        video_path=Path(video_path),
        next_beat=config.get("next_beat"),
        on_log=on_log,
    )

    video_pool_id = None
    try:
        from novelvideo.generators.video_pool_indexer import add_video_to_pool

        entry = add_video_to_pool(
            videos_ep_dir=videos_dir,
            episode=episode,
            beat_num=beat_num,
            source_video_path=Path(video_path),
            duration=video_duration,
            video_mode=video_mode,
            backend=effective_backend,
            prompt=prompt,
        )
        video_pool_id = entry.id
    except Exception as exc:  # noqa: BLE001
        on_log(f"添加到视频池失败 (非致命): {exc}")

    task_result = {
        "video_path": video_path.as_posix(),
        "beat_num": beat_num,
        "video_pool_id": video_pool_id,
        "video_backend": effective_backend,
    }
    if failover_from:
        task_result["failover_from"] = failover_from
    provider_task_id = getattr(result, "provider_task_id", None) or getattr(
        result, "task_id", None
    )
    if provider_task_id:
        task_result["provider_task_id"] = provider_task_id
    if result.last_frame_path:
        task_result["last_frame_path"] = result.last_frame_path
    if result.last_frame_url:
        task_result["last_frame_url"] = result.last_frame_url
    from novelvideo.styles.project_style import write_artifact_style_evidence

    write_artifact_style_evidence(video_path, style_snapshot)
    return task_result


def run_single_video(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_single_video_async(envelope, ctx),
            envelope,
            task_type="single_video",
        )
    )


register_project_task_runner("single_video", run_single_video)


def _audio_duration(
    audio_path: Path, *, timeout_seconds: int | None = 30
) -> float | None:
    if not audio_path.exists():
        return None
    import subprocess

    try:
        result = run_project_subprocess(
            [
                bundled_media_binary("ffprobe") or "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TaskTimedOut(timeout_seconds=timeout_seconds) from exc
    try:
        return float(result.stdout.strip())
    except Exception:
        return None


def _video_has_audio_stream(
    video_path: Path, *, timeout_seconds: int | None = 30
) -> bool:
    if not video_path.exists():
        return False
    import subprocess

    try:
        result = run_project_subprocess(
            [
                bundled_media_binary("ffprobe") or "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TaskTimedOut(timeout_seconds=timeout_seconds) from exc
    return result.returncode == 0 and bool(result.stdout.strip())


async def _run_video_generation_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.manual_shots import resolve_generation_video_duration
    from novelvideo.utils.path_resolver import PathResolver

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    beats = list(payload.get("beats") or [])
    video_backend = str(payload.get("video_backend") or "").strip()
    if not video_backend:
        raise RuntimeError(
            "批量视频生成缺少 video_backend；请先选择可用的视频模型，"
            "测试或演示占位运行必须显式传 video_backend=mock。"
        )
    resolution = str(payload.get("resolution") or "720p")
    ratio = str(payload.get("ratio") or "9:16")
    prop_menu = payload.get("prop_menu")
    use_director_render = bool(payload.get("use_director_render"))
    manager = get_task_manager()
    paths = PathResolver(output_dir, episode)
    generated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for index, beat in enumerate(beats):
        beat_num = int(beat.get("beat_number") or index + 1)
        manager.update_progress_for_project(
            ctx,
            "video_generation",
            episode,
            progress=index / max(1, len(beats)),
            current_task=f"生成 Beat {beat_num} 视频...",
        )
        promoted_fields = _promote_batch_video_fields(beat)
        explicit_mode_value = (
            beat.get("gen_mode")
            or beat.get("genMode")
            or beat.get("generation_mode")
            or beat.get("generationMode")
            or promoted_fields.get("gen_mode")
        )
        if explicit_mode_value is not None and not str(explicit_mode_value).strip():
            explicit_mode_value = None
        raw_mode_value = explicit_mode_value or beat.get("video_mode")
        raw_video_mode = str(raw_mode_value or "first_frame").strip()
        normalized_mode = _normalize_batch_video_mode(raw_video_mode)
        canvas_modes = {
            "textToVideo",
            "imageToVideo",
            "firstLastFrame",
            "allReference",
            "imageReference",
            "videoEdit",
        }
        if explicit_mode_value and normalized_mode not in canvas_modes:
            error = f"Beat {beat_num} 视频模式 {raw_video_mode!r} 不在画布能力合同中"
            failed.append({"beat_num": beat_num, "error": error})
            manager.update_progress_for_project(
                ctx,
                "video_generation",
                episode,
                logs=[error],
            )
            continue
        if explicit_mode_value or normalized_mode in {
            "textToVideo",
            "imageToVideo",
            "firstLastFrame",
            "allReference",
            "imageReference",
            "videoEdit",
        }:
            generation_mode = normalized_mode or raw_video_mode
        else:
            # The historical ``first_frame`` value means ordinary I2V
            # inference and must stay omitted at the transport boundary.
            generation_mode = ""
        uses_last_frame = _batch_mode_uses_last_frame(raw_video_mode)
        legacy_keyframe_mode = (
            not explicit_mode_value and raw_video_mode.casefold() == "keyframe"
        )
        candidate_references = promoted_fields.get("references")
        candidate_image_count = _batch_reference_count(candidate_references, "image")
        candidate_video_count = _batch_reference_count(candidate_references, "video")

        # Explicit text-to-video has no local frame prerequisite.  Every other
        # batch mode keeps the historical single-frame validation before queue.
        # 连续接缝优先吃上一镜成片的真实尾帧；批处理按 Beat 顺序 await，
        # 所以上一镜的 relay 在本镜解析首帧前一定已经落盘。
        frame_path = None
        local_frame = paths.first_frame_for_video(
            beat_num,
            use_director_render=use_director_render,
            prefer_relay=shot_incoming_seam(beat) == SEAM_CONTINUOUS,
        )
        local_frame_exists = local_frame.exists()
        frame_path = local_frame if local_frame_exists else None
        frame_mode = generation_mode in {"imageToVideo", "firstLastFrame"}
        reference_only_modes = {
            "allReference",
            "imageReference",
            "videoEdit",
        }
        reference_only_mode = generation_mode in reference_only_modes
        # Reference-only modes have their own media prerequisites.  Do this
        # before invoking the single-shot runner so a batch cannot report a
        # queued-looking success for a request that has no legal input.
        reference_contract_missing = (
            generation_mode == "imageReference"
            and not (local_frame_exists or candidate_image_count > 0)
        ) or (
            generation_mode == "videoEdit" and candidate_video_count < 1
        ) or (
            generation_mode == "allReference"
            and not (
                local_frame_exists
                or candidate_image_count > 0
                or candidate_video_count > 0
            )
        )
        if reference_contract_missing:
            error = f"Beat {beat_num} {generation_mode} 缺少所需参考素材"
            failed.append({"beat_num": beat_num, "error": error})
            manager.update_progress_for_project(
                ctx,
                "video_generation",
                episode,
                logs=[error],
            )
            continue
        reference_backed = (
            generation_mode == "imageToVideo" and candidate_image_count >= 1
        ) or (
            generation_mode == "firstLastFrame" and candidate_image_count >= 2
        )
        required_local_frame = (
            generation_mode not in {"textToVideo", *reference_only_modes}
            and not reference_backed
        )
        if not local_frame_exists and required_local_frame:
            manager.update_progress_for_project(
                ctx,
                "video_generation",
                episode,
                logs=[f"Beat {beat_num} 缺少首帧或模式所需参考图，跳过: {local_frame}"],
            )
            failed.append(
                {
                    "beat_num": beat_num,
                    "error": f"Beat {beat_num} 缺少首帧或模式所需参考图",
                }
            )
            continue
        if frame_path is not None and (not reference_only_mode or frame_mode):
            validate_frame = getattr(paths, "validate_single_frame_contract", None)
            frame_valid, frame_reason = (
                validate_frame(frame_path)
                if callable(validate_frame)
                else (True, "legacy")
            )
            if not frame_valid:
                failed.append(
                    {
                        "beat_num": beat_num,
                        "error": f"首帧不满足单格图像合同: {frame_reason}",
                    }
                )
                manager.update_progress_for_project(
                    ctx,
                    "video_generation",
                    episode,
                    logs=[
                        f"Beat {beat_num} 首帧不满足单格图像合同，跳过视频生成: "
                        f"{frame_reason} ({frame_path})"
                    ],
                )
                continue

        local_mode = normalized_mode or raw_video_mode or "first_frame"
        # ``keyframe`` is the runner's local sentinel.  Keep the canonical
        # Canvas label in ``gen_mode`` so generic and legacy adapters can map
        # it to their own wire contract without losing the first/last-frame
        # intent.
        video_mode = "keyframe" if uses_last_frame else local_mode
        prompt = str(
            beat.get("keyframe_prompt")
            if uses_last_frame
            else beat.get("video_prompt") or ""
        )
        audio_path = paths.audio(beat_num)
        duration = resolve_generation_video_duration(
            beat,
            _audio_duration(
                audio_path,
                timeout_seconds=remaining_timeout_seconds(envelope, default_seconds=30),
            ),
        )
        last_frame_path = (
            str(promoted_fields.get("last_frame_path") or "").strip() or None
        )
        if uses_last_frame:
            if not last_frame_path:
                next_frame = paths.first_frame_for_video(
                    beat_num + 1,
                    use_director_render=use_director_render,
                )
                if next_frame.exists():
                    last_frame_path = str(next_frame)
            if not last_frame_path and generation_mode == "firstLastFrame":
                # An explicit first/last contract must not silently degrade to
                # imageToVideo. Reuse two declared image references when they
                # exist; otherwise fail this beat before provider submission.
                if not _batch_has_first_last_media(
                    candidate_references,
                    image_path=frame_path,
                ):
                    failed.append(
                        {
                            "beat_num": beat_num,
                            "error": "firstLastFrame requires first and last frame images",
                        }
                    )
                    manager.update_progress_for_project(
                        ctx,
                        "video_generation",
                        episode,
                        logs=[
                            f"Beat {beat_num} 缺少尾帧或第二张图片参考，保持 firstLastFrame 并跳过"
                        ],
                    )
                    continue
            elif not last_frame_path and not legacy_keyframe_mode:
                failed.append(
                    {
                        "beat_num": beat_num,
                        "error": "firstLastFrame requires first and last frame images",
                    }
                )
                continue
            elif not last_frame_path:
                video_mode = "first_frame"
                generation_mode = "imageToVideo"
                prompt = str(beat.get("video_prompt") or "")

        single_config = {
            "beat": beat,
            "next_beat": beats[index + 1] if index + 1 < len(beats) else None,
            "frame_path": str(frame_path) if frame_path is not None else None,
            "video_mode": video_mode,
            "prompt": prompt,
            "video_duration": duration,
            "video_backend": video_backend,
            "last_frame_path": last_frame_path,
            "resolution": resolution,
            "ratio": ratio,
            "prop_menu": prop_menu,
        }
        # Canvas beats may carry provider-specific controls at the top level or
        # under ``videoConfig``.  Promote only the bounded allowlist so batch
        # dispatch has the same contract as the single-shot route without
        # leaking arbitrary beat metadata into a provider request.
        if promoted_fields:
            single_config.update(promoted_fields)
        if "references" in promoted_fields:
            single_config["references"] = _batch_video_references(
                promoted_fields["references"]
            )
        if generation_mode:
            single_config["gen_mode"] = generation_mode
        if not explicit_mode_value:
            # 与单镜路由同一开关：只有用户没显式选模式才允许连续接缝升级为
            # 多图参考请求。
            single_config["seam_mode_promotion"] = True

        single_envelope = {
            "task_type": "single_video",
            "episode": episode,
            "beat_num": beat_num,
            "payload": {
                "output_dir": output_dir,
                "config": single_config,
            },
        }
        try:
            generated.append(await _run_single_video_async(single_envelope, ctx))
        except (TaskTimedOut, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 - keep later beats usable
            error_text = str(exc) or exc.__class__.__name__
            failed.append({"beat_num": beat_num, "error": error_text})
            manager.update_progress_for_project(
                ctx,
                "video_generation",
                episode,
                logs=[f"Beat {beat_num} 视频生成失败，继续处理后续 Beat: {error_text}"],
                current_task=f"Beat {beat_num} 视频生成失败，继续后续任务",
            )
            continue

    if not generated and failed:
        first_error = failed[0]["error"]
        raise RuntimeError(f"全部视频生成失败: {first_error}")

    return {
        "generated": len(generated),
        "failed": len(failed),
        "items": generated,
        "errors": failed,
    }


def run_video_generation(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_video_generation_async(envelope, ctx),
            envelope,
            task_type="video_generation",
        )
    )


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _ffmpeg_error_excerpt(result: Any) -> str:
    text = str(getattr(result, "stderr", "") or "").strip()
    if len(text) <= 1000:
        return text
    # FFmpeg puts the decisive error near the end after its build banner and
    # stream discovery output.
    return f"{text[:220]} ... {text[-760:]}"


_DEFAULT_TRANSITION_DURATION_SECONDS = 0.35
_TRANSITION_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "crossfade",
        ("crossfade", "acrossfade", "交叉淡化", "交叉溶解", "交叉叠化"),
    ),
    ("dissolve", ("dissolve", "溶解", "叠化")),
    ("fade", ("fade", "淡入", "淡出", "淡化")),
)


def _positive_transition_duration(value: object) -> float | None:
    try:
        duration = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _beat_edit_duration(beat: object) -> float | None:
    if not isinstance(beat, dict):
        return None
    for field in (
        "target_duration_seconds",
        "duration_seconds",
        "generation_duration_seconds",
    ):
        duration = _positive_transition_duration(beat.get(field))
        if duration is not None:
            return duration
    return None


def _explicit_transition_kind(beat: object) -> tuple[str, str] | None:
    if not isinstance(beat, dict):
        return None
    transition = beat.get("transition")
    if isinstance(transition, dict):
        transition = transition.get("kind") or transition.get("type") or ""
    for field, value in (("transition", transition), ("cut_reason", beat.get("cut_reason"))):
        text = str(value or "").strip().casefold()
        if not text:
            continue
        for kind, tokens in _TRANSITION_TOKENS:
            if any(token.casefold() in text for token in tokens):
                return kind, field
    return None


def resolve_transition_plan(
    previous_beat: object | None,
    current_beat: object | None,
    *,
    default_duration_seconds: float = _DEFAULT_TRANSITION_DURATION_SECONDS,
) -> dict[str, object]:
    """Resolve one deterministic boundary transition for the final edit.

    Hard cuts remain the default.  A transition is enabled only when the
    current beat explicitly names ``dissolve``, ``fade`` or ``crossfade`` in
    its transition/cut-reason field.  The duration is bounded by the shorter
    adjacent edit duration so a future overlapping compositor cannot consume
    either whole clip.
    """

    explicit = _explicit_transition_kind(current_beat)
    if explicit is None:
        return {
            "transition_kind": "hard_cut",
            "duration_seconds": 0.0,
            "reason": "default",
            "audio_transition_kind": "hard_cut",
        }

    kind, source_field = explicit
    requested_duration = None
    if isinstance(current_beat, dict):
        transition = current_beat.get("transition")
        if isinstance(transition, dict):
            requested_duration = transition.get("duration_seconds") or transition.get(
                "duration"
            )
        if requested_duration is None:
            requested_duration = current_beat.get("transition_duration_seconds")
    requested = _positive_transition_duration(requested_duration)
    if requested is None:
        requested = _positive_transition_duration(default_duration_seconds)
    if requested is None:
        requested = _DEFAULT_TRANSITION_DURATION_SECONDS

    adjacent_durations = [
        duration
        for duration in (_beat_edit_duration(previous_beat), _beat_edit_duration(current_beat))
        if duration is not None
    ]
    if adjacent_durations:
        requested = min(requested, min(adjacent_durations))

    return {
        "transition_kind": kind,
        "duration_seconds": round(requested, 3),
        "reason": f"current_beat.{source_field}",
        # Every visual overlap needs the same audio overlap.  ``acrossfade``
        # is the FFmpeg audio primitive for fade, dissolve, and crossfade.
        "audio_transition_kind": "acrossfade"
        if kind in {"crossfade", "dissolve", "fade"}
        else "hard_cut",
    }


def _bound_transition_plan(
    plan: dict[str, object],
    previous_duration: float,
    current_duration: float,
) -> dict[str, object]:
    """Bound one transition against the durations of the rendered clips.

    Beat metadata is a planning hint and can differ from the actual clip after
    audio-driven trimming.  The compositor must never ask ``xfade`` or
    ``acrossfade`` to consume more than either adjacent clip.  A non-positive
    result is represented as a hard cut so the graph stays valid.
    """

    kind = str(plan.get("transition_kind") or "hard_cut").strip().casefold()
    if kind not in {"fade", "dissolve", "crossfade"}:
        return {
            "transition_kind": "hard_cut",
            "duration_seconds": 0.0,
            "reason": plan.get("reason") or "default",
            "audio_transition_kind": "hard_cut",
        }
    try:
        requested = float(plan.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        requested = 0.0
    adjacent = [
        value
        for value in (previous_duration, current_duration)
        if isinstance(value, (int, float)) and value > 0
    ]
    bounded = min([requested, *adjacent]) if requested > 0 and adjacent else 0.0
    # FFmpeg rejects a zero-length xfade.  A tiny requested duration is not a
    # meaningful transition, so keep that boundary as an ordinary hard cut.
    if bounded <= 0.001:
        return {
            "transition_kind": "hard_cut",
            "duration_seconds": 0.0,
            "reason": f"{plan.get('reason') or 'explicit'}:duration_too_short",
            "audio_transition_kind": "hard_cut",
        }
    return {
        "transition_kind": kind,
        "duration_seconds": round(bounded, 3),
        "reason": plan.get("reason") or "explicit",
        "audio_transition_kind": "acrossfade",
    }


def _resolve_compose_media_path(
    output_dir: Path,
    default_path: Path,
    beat: dict[str, Any],
    *,
    media_kind: str,
) -> Path:
    """Resolve an optional per-beat media override inside the project.

    Workflow-produced media is stored in the Freezone job layout rather than
    the legacy ``videos/beats`` layout.  The compose runner remains compatible
    with legacy Beat rows while accepting an explicit local path from the
    WorkflowRun bridge.  URLs and paths outside the project are rejected
    before FFmpeg starts.
    """

    keys = (
        ("video_path", "videoPath", "video", "output_path", "outputPath", "path")
        if media_kind == "video"
        else ("audio_path", "audioPath", "audio", "output_path", "outputPath", "path")
    )
    raw = next((beat.get(key) for key in keys if beat.get(key) not in (None, "")), None)
    if raw in (None, ""):
        return default_path
    value = str(raw).strip()
    if "://" in value:
        raise RuntimeError(f"Beat 媒体路径不支持远程 URL: {media_kind}")
    candidate = Path(value)
    output_root = output_dir.resolve()
    # A leading slash in a workflow artifact is project-relative, not a drive
    # root.  Preserve real Windows drive paths for the containment check below.
    if candidate.is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", value):
        candidate = output_root / value.lstrip("/\\")
    elif not candidate.is_absolute():
        candidate = output_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError as exc:
        raise RuntimeError(f"Beat 媒体路径越出项目目录: {media_kind}") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise RuntimeError(f"Beat {media_kind} 产物缺失或为空: {resolved}")
    return resolved


def _compose_beat_spoken_dialogue(beat: dict[str, Any]) -> tuple[str, ...]:
    """Resolve only actual speech for compose's independent-audio gate.

    Structured ``dialogue_text``/``spoken_dialogue`` values are authoritative
    and may be unquoted.  Legacy ``dialogue``/``narration_segment``/``narration``
    fields are treated as visual prose unless they contain explicit quotes.
    This keeps an old beat labelled ``dialogue`` from turning an action
    description into a false TTS requirement.
    """

    from novelvideo.services.video_request_contract import extract_spoken_dialogue

    structured: list[object] = []
    for key in ("spoken_dialogue", "spokenDialogue", "dialogue_text", "dialogueText"):
        value = beat.get(key)
        if isinstance(value, (list, tuple)):
            structured.extend(value)
        elif value not in (None, ""):
            structured.append(value)
    if structured:
        return extract_spoken_dialogue("", dialogue_text=structured)

    legacy: list[str] = []
    for key in ("dialogue", "narration_segment", "narration"):
        value = beat.get(key)
        if value not in (None, ""):
            legacy.append(str(value))
    return extract_spoken_dialogue(" ".join(legacy))


def run_compose_episode(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    import os
    import subprocess
    import tempfile
    import uuid

    from novelvideo.export import episode_export
    from novelvideo.manual_shots import (
        build_subtitle_timing_entries,
        resolve_composition_window,
        sort_beats_for_display,
    )
    from novelvideo.utils.path_resolver import PathResolver
    from novelvideo.services.delivery_fps import project_delivery_fps_receipt, resolve_delivery_fps

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    style_snapshot = dict(payload.get("style_snapshot") or {})
    if not str(style_snapshot.get("fingerprint") or "").strip():
        from novelvideo.project_config import (
            load_project_config,
            load_project_config_from_state_dir,
        )
        from novelvideo.styles.project_style import build_project_style_snapshot

        owner_username = str(getattr(ctx, "owner_username", "") or "local")
        project_name = str(getattr(ctx, "project_name", "") or "project")
        state_dir = getattr(ctx, "state_dir", None)
        if state_dir is not None:
            project_config = load_project_config_from_state_dir(
                state_dir,
                username=owner_username,
                project=project_name,
            )
        else:
            project_config = load_project_config(owner_username, project_name)
        style_snapshot = build_project_style_snapshot(
            project_config.get("visual_style"),
            username=owner_username,
            project=project_name,
            project_dir=output_dir,
            video_model=project_config.get("video_backend"),
        )
    beats = sort_beats_for_display(list(payload.get("beats") or []))
    resolution = str(payload.get("resolution") or "720x1280")
    add_subtitles = bool(payload.get("add_subtitles"))
    add_bgm = bool(payload.get("add_bgm"))
    delivery_fps = project_delivery_fps_receipt(payload.get("delivery_fps"))
    if delivery_fps is None:
        delivery_fps = resolve_delivery_fps(requested_fps=payload.get("fps"))
    if not isinstance(delivery_fps, dict):
        raise RuntimeError("compose_episode 无法解析交付帧率")
    target_fps = int(delivery_fps["fps"])
    manager = get_task_manager()
    paths = PathResolver(output_dir, episode)
    final_dir = Path(output_dir) / "videos" / "episodes"
    final_dir.mkdir(parents=True, exist_ok=True)
    output_path = final_dir / f"ep{episode:03d}_final.mp4"
    staged_output_path = final_dir / (
        f"ep{episode:03d}_final.partial-{uuid.uuid4().hex}.mp4"
    )

    def check_cancel() -> None:
        raise_if_envelope_cancel_requested(
            envelope,
            task_type="compose_episode",
            episode=episode,
        )

    def subprocess_timeout(default_seconds: int) -> int | None:
        return remaining_timeout_seconds(envelope, default_seconds=default_seconds)

    def run_checked(cmd: list[str], *, default_timeout_seconds: int):
        try:
            return run_project_subprocess(
                cmd,
                envelope=envelope,
                capture_output=True,
                text=True,
                timeout=subprocess_timeout(default_timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskTimedOut(
                timeout_seconds=int(envelope.get("__timeout_seconds") or 30 * 60)
            ) from exc

    try:
        target_width, target_height = map(int, resolution.split("x"))
    except Exception:
        target_width, target_height = 720, 1280

    positive_beats: list[dict] = []
    for beat in beats:
        try:
            beat_num = int(beat.get("beat_number") or 0)
        except (TypeError, ValueError):
            continue
        if beat_num > 0:
            positive_beats.append(beat)
    if not positive_beats:
        raise RuntimeError("没有可合成的正数 Beat")

    invalid_video_beats: list[int] = []
    for beat in positive_beats:
        beat_num = int(beat["beat_number"])
        try:
            video_path = _resolve_compose_media_path(
                Path(output_dir),
                paths.video(beat_num),
                beat,
                media_kind="video",
            )
        except RuntimeError:
            invalid_video_beats.append(beat_num)
            continue
        if not _is_nonempty_file(video_path):
            invalid_video_beats.append(beat_num)
    if invalid_video_beats:
        beat_list = ", ".join(str(beat_num) for beat_num in invalid_video_beats)
        raise RuntimeError(f"Beat {beat_list} 的 MP4 缺失或为空")

    # A dialogue subtitle without its own generated voice is a false-positive
    # deliverable: the source video may contain ambience, but that is not the
    # character line.  Stop before ffmpeg so a partial/previous final remains
    # untouched and the missing beats can be regenerated explicitly.
    #
    # 例外：视频模型在同一遍里生成原生音轨时（MiniMax H3 的
    # native_audio=required），对白由模型直接渲染，不存在可外挂的配音 MP3。
    # 这类运行携带 allow_native_audio_fallback，闸门改为逐 Beat 检查视频是否
    # 真有内置音轨，避免把带声成片误判成无声对白。
    allow_native_audio_fallback = bool(payload.get("allow_native_audio_fallback"))
    if not allow_native_audio_fallback:
        missing_dialogue_audio = []
        for beat in positive_beats:
            audio_type = str(beat.get("audio_type") or "").strip().lower()
            spoken_dialogue = _compose_beat_spoken_dialogue(beat)
            if audio_type == "dialogue" and spoken_dialogue:
                beat_num = int(beat["beat_number"])
                try:
                    audio_path = _resolve_compose_media_path(
                        Path(output_dir),
                        paths.audio(beat_num),
                        beat,
                        media_kind="audio",
                    )
                except RuntimeError:
                    audio_path = paths.audio(beat_num)
                if not _is_nonempty_file(audio_path):
                    missing_dialogue_audio.append(beat_num)
        if missing_dialogue_audio:
            beat_list = ", ".join(
                str(beat_num) for beat_num in missing_dialogue_audio
            )
            raise RuntimeError(
                f"Beat {beat_list} 标记为 dialogue 但缺少独立音频；"
                "请先完成对应角色声线生成，再合成成片"
            )

    bgm_path: Path | None = None
    if add_bgm:
        raw_bgm_path = str(payload.get("bgm_path") or "").strip()
        if not raw_bgm_path:
            raise RuntimeError("add_bgm=true 但未提供 bgm_path，合成已阻止")
        bgm_path = Path(raw_bgm_path).expanduser()
        if not bgm_path.is_absolute():
            bgm_path = Path(output_dir) / bgm_path
        bgm_path = bgm_path.resolve()
        if not _is_nonempty_file(bgm_path):
            raise RuntimeError(f"bgm_path 不存在或为空，合成已阻止: {bgm_path}")

    subtitles_applied = False
    bgm_applied = False
    try:
        video_encoder = _select_compose_video_encoder()
        with tempfile.TemporaryDirectory(
            prefix=f"village-canvas-compose-ep{episode:03d}-"
        ) as tmp:
            tmp_dir = Path(tmp)
            video_clips: list[Path] = []
            clip_durations: list[float] = []
            duration_by_beat: dict[int, float | None] = {}
            transition_hint_requested = any(
                _explicit_transition_kind(beat) is not None
                for beat in positive_beats[1:]
            )

            for index, beat in enumerate(positive_beats):
                check_cancel()
                beat_num = int(beat["beat_number"])
                video_path = _resolve_compose_media_path(
                    Path(output_dir),
                    paths.video(beat_num),
                    beat,
                    media_kind="video",
                )
                audio_path = _resolve_compose_media_path(
                    Path(output_dir),
                    paths.audio(beat_num),
                    beat,
                    media_kind="audio",
                )
                clip_path = tmp_dir / f"beat_{beat_num:04d}.mp4"
                audio_duration = _audio_duration(
                    audio_path,
                    timeout_seconds=subprocess_timeout(30),
                )
                trim_in_seconds, final_duration_seconds = resolve_composition_window(
                    beat,
                    audio_duration,
                )
                manager.update_progress_for_project(
                    ctx,
                    "compose_episode",
                    episode,
                    progress=index / max(1, len(positive_beats)),
                    current_task=f"合成 Beat {beat_num}...",
                )
                cmd = [bundled_media_binary("ffmpeg") or "ffmpeg", "-y"]
                if trim_in_seconds > 0:
                    cmd.extend(["-ss", f"{trim_in_seconds:.3f}"])
                cmd.extend(["-i", str(video_path)])
                has_embedded_audio = False
                if _is_nonempty_file(audio_path):
                    cmd.extend(["-i", str(audio_path)])
                    cmd.extend(
                        [
                            "-map",
                            "0:v:0",
                            "-map",
                            "1:a:0",
                            *_compose_video_encoder_args(video_encoder),
                            "-c:a",
                            "aac",
                            "-b:a",
                            "128k",
                            "-pix_fmt",
                            "yuv420p",
                            "-shortest",
                        ]
                    )
                    manager.update_progress_for_project(
                        ctx,
                        "compose_episode",
                        episode,
                        logs=[f"Beat {beat_num} 使用独立音频: {audio_path.name}"],
                    )
                else:
                    has_embedded_audio = _video_has_audio_stream(
                        video_path,
                        timeout_seconds=subprocess_timeout(30),
                    )
                    check_cancel()
                if has_embedded_audio:
                    cmd.extend(
                        [
                            "-map",
                            "0:v:0",
                            "-map",
                            "0:a:0",
                            *_compose_video_encoder_args(video_encoder),
                            "-c:a",
                            "aac",
                            "-b:a",
                            "128k",
                            "-pix_fmt",
                            "yuv420p",
                            "-shortest",
                        ]
                    )
                    manager.update_progress_for_project(
                        ctx,
                        "compose_episode",
                        episode,
                        logs=[f"Beat {beat_num} 使用视频内置音轨"],
                    )
                elif not _is_nonempty_file(audio_path):
                    cmd.extend(
                        [
                            "-f",
                            "lavfi",
                            "-i",
                            "anullsrc=r=44100:cl=stereo",
                            "-map",
                            "0:v:0",
                            "-map",
                            "1:a:0",
                            *_compose_video_encoder_args(video_encoder),
                            "-c:a",
                            "aac",
                            "-b:a",
                            "128k",
                            "-pix_fmt",
                            "yuv420p",
                            "-shortest",
                        ]
                    )
                if (
                    allow_native_audio_fallback
                    and not has_embedded_audio
                    and not _is_nonempty_file(audio_path)
                    and str(beat.get("audio_type") or "").strip().lower()
                    == "dialogue"
                    and _compose_beat_spoken_dialogue(beat)
                ):
                    raise RuntimeError(
                        f"Beat {beat_num} 标记为 dialogue，但既没有独立配音音频，"
                        "视频本身也没有内置音轨；请先完成对应角色声线生成，"
                        "再合成成片"
                    )
                # ``-t`` applies to the output clip: generated source media
                # may be 4–5s long while the final reaction/insert is 1–2s.
                cmd.extend(["-t", f"{final_duration_seconds:.3f}", str(clip_path)])
                result = run_checked(cmd, default_timeout_seconds=30 * 60)
                check_cancel()
                if result.returncode != 0:
                    raise RuntimeError(
                        f"Beat {beat_num} 转码失败: {_ffmpeg_error_excerpt(result)}"
                    )
                if not _is_nonempty_file(clip_path):
                    raise RuntimeError(f"Beat {beat_num} 转码未产出非空 MP4")
                video_clips.append(clip_path)
                measured_clip_duration = None
                if transition_hint_requested:
                    measured_clip_duration = _audio_duration(
                        clip_path,
                        timeout_seconds=subprocess_timeout(30),
                    )
                clip_duration = (
                    measured_clip_duration
                    if measured_clip_duration is not None and measured_clip_duration > 0
                    else final_duration_seconds
                )
                clip_durations.append(float(clip_duration))
                if add_subtitles:
                    duration_by_beat[id(beat)] = (
                        float(clip_duration)
                        if transition_hint_requested
                        else _audio_duration(
                            clip_path,
                            timeout_seconds=subprocess_timeout(30),
                        )
                    )
                    check_cancel()

            transition_plans: list[dict[str, object]] = [
                {
                    "transition_kind": "hard_cut",
                    "duration_seconds": 0.0,
                    "reason": "first_beat",
                    "audio_transition_kind": "hard_cut",
                }
            ]
            for index in range(1, len(positive_beats)):
                requested_plan = resolve_transition_plan(
                    positive_beats[index - 1], positive_beats[index]
                )
                plan = _bound_transition_plan(
                    requested_plan,
                    clip_durations[index - 1],
                    clip_durations[index],
                )
                transition_plans.append(plan)
                if plan["transition_kind"] != "hard_cut":
                    manager.update_progress_for_project(
                        ctx,
                        "compose_episode",
                        episode,
                        logs=[
                            "Beat "
                            f"{int(positive_beats[index - 1]['beat_number'])} -> "
                            f"{int(positive_beats[index]['beat_number'])} "
                            f"启用 {plan['transition_kind']} "
                            f"({plan['duration_seconds']}s)"
                        ],
                    )
            transition_active = any(
                str(plan.get("transition_kind") or "") != "hard_cut"
                for plan in transition_plans
            )

            subtitle_path: Path | None = None
            if add_subtitles:
                if transition_active:
                    subtitle_entries = _build_transition_subtitle_entries(
                        positive_beats,
                        clip_durations,
                        transition_plans,
                    )
                else:
                    subtitle_entries = build_subtitle_timing_entries(
                        positive_beats,
                        duration_lookup=lambda beat: duration_by_beat.get(id(beat)),
                    )
                if subtitle_entries:
                    subtitle_content = episode_export.build_srt_content_from_entries(
                        subtitle_entries
                    )
                    subtitle_path = tmp_dir / f"ep{episode:03d}.srt"
                    subtitle_path.write_text(subtitle_content, encoding="utf-8")
                    if not _is_nonempty_file(subtitle_path):
                        raise RuntimeError("字幕文件生成失败")
                    subtitles_applied = True

            check_cancel()
            cmd = [bundled_media_binary("ffmpeg") or "ffmpeg", "-y"]
            for clip in video_clips:
                cmd.extend(["-i", str(clip)])
            if bgm_path is not None:
                cmd.extend(["-stream_loop", "-1", "-i", str(bgm_path)])

            filter_parts: list[str] = []
            for index in range(len(video_clips)):
                video_filter = (
                    f"[{index}:v]scale={target_width}:{target_height}:"
                    f"force_original_aspect_ratio=decrease,"
                    f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"fps={target_fps},setsar=1,format=yuv420p,{_compose_color_params()}"
                )
                audio_filter = f"[{index}:a]aresample=44100"
                if transition_active:
                    # xfade/acrossfade require timestamps starting at zero and
                    # stable stream parameters across every input.
                    video_filter += ",setpts=PTS-STARTPTS"
                    audio_filter += (
                        ",aformat=sample_fmts=fltp:sample_rates=44100:"
                        "channel_layouts=stereo,asetpts=PTS-STARTPTS"
                    )
                filter_parts.append(f"{video_filter}[v{index}]")
                filter_parts.append(f"{audio_filter}[a{index}]")

            if not transition_active:
                concat_inputs = "".join(
                    f"[v{index}][a{index}]" for index in range(len(video_clips))
                )
                filter_parts.append(
                    f"{concat_inputs}concat=n={len(video_clips)}:v=1:a=1[concatv][concata]"
                )
                video_label = "concatv"
                audio_label = "concata"
            else:
                video_label = "v0"
                audio_label = "a0"
                for index in range(1, len(video_clips)):
                    plan = transition_plans[index]
                    kind = str(plan.get("transition_kind") or "hard_cut")
                    next_video_label = f"vjoin{index}"
                    next_audio_label = f"ajoin{index}"
                    if kind == "hard_cut":
                        filter_parts.append(
                            f"[{video_label}][v{index}]concat=n=2:v=1:a=0"
                            f"[{next_video_label}]"
                        )
                        filter_parts.append(
                            f"[{audio_label}][a{index}]concat=n=2:v=0:a=1"
                            f"[{next_audio_label}]"
                        )
                    else:
                        duration = float(plan["duration_seconds"])
                        offset = max(0.0, sum(clip_durations[:index]) - sum(
                            float(
                                transition_plans[j].get("duration_seconds") or 0.0
                            )
                            for j in range(1, index)
                        ) - duration)
                        xfade_kind = _ffmpeg_xfade_transition(kind)
                        filter_parts.append(
                            f"[{video_label}][v{index}]xfade="
                            f"transition={xfade_kind}:duration={duration:.3f}:"
                            f"offset={offset:.3f}[{next_video_label}]"
                        )
                        filter_parts.append(
                            f"[{audio_label}][a{index}]acrossfade="
                            f"d={duration:.3f}:c1=tri:c2=tri[{next_audio_label}]"
                        )
                    video_label = next_video_label
                    audio_label = next_audio_label

            video_output = f"[{video_label}]"
            if subtitle_path is not None:
                subtitle_filter_path = _escape_ffmpeg_subtitle_path(subtitle_path)
                filter_parts.append(
                    f"{video_output}subtitles=filename='{subtitle_filter_path}':"
                    "force_style='FontName=Microsoft YaHei,FontSize=24,"
                    "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2'"
                    "[outv]"
                )
                video_output = "[outv]"

            audio_output = f"[{audio_label}]"
            if bgm_path is not None:
                bgm_input_index = len(video_clips)
                filter_parts.append(
                    f"[{bgm_input_index}:a]aresample=44100,volume=0.18[bgm]"
                )
                filter_parts.append(
                    f"{audio_output}[bgm]"
                    "amix=inputs=2:duration=first:dropout_transition=2[outa]"
                )
                audio_output = "[outa]"
                bgm_applied = True

            audio_output = _append_delivery_loudness(filter_parts, audio_output)
            cmd.extend(
                [
                    "-filter_complex",
                    ";".join(filter_parts),
                    "-map",
                    video_output,
                    "-map",
                    audio_output,
                    *_compose_video_encoder_args(video_encoder),
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(target_fps),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(staged_output_path),
                ]
            )
            result = run_checked(cmd, default_timeout_seconds=30 * 60)
            check_cancel()
            if result.returncode != 0:
                raise RuntimeError(f"拼接失败: {_ffmpeg_error_excerpt(result)}")
            if not _is_nonempty_file(staged_output_path):
                raise RuntimeError("拼接未产出非空最终 MP4")
            os.replace(staged_output_path, output_path)
            from novelvideo.styles.project_style import write_artifact_style_evidence

            write_artifact_style_evidence(output_path, style_snapshot)
    finally:
        try:
            staged_output_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "video_path": output_path.as_posix(),
        "add_subtitles_requested": add_subtitles,
        "add_bgm_requested": add_bgm,
        "subtitles_applied": subtitles_applied,
        "bgm_applied": bgm_applied,
        "fps": target_fps,
        "delivery_fps": delivery_fps,
    }


register_project_task_runner("compose_episode", run_compose_episode)


async def _run_global_optimize_video_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.agents.global_video_optimizer import (
        get_global_video_optimizer,
        lint_motion_prompt_with_review,
        normalize_motion_prompt_text,
        prepare_global_optimizer_input,
        resolve_video_prompt_frame_path,
        resolve_video_strategy_capabilities,
        sanitize_video_prompt_marker_colors,
    )
    from novelvideo.production.shot_contract import shot_contract_ready
    from novelvideo.cognee import CogneeStore
    from novelvideo.seedance2_i2v.pipeline import (
        is_huimeng_seedance2_backend,
        sync_seedance2_prompt_from_director,
    )
    from novelvideo.utils.path_resolver import PathResolver

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beats = list(payload.get("beats") or [])
    characters = list(payload.get("characters") or [])
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    language = str(payload.get("language") or "zh")
    from novelvideo.styles.project_style import apply_style_snapshot_to_prompt

    style_snapshot = dict(payload.get("style_snapshot") or {})
    manager = get_task_manager()

    def log(message: str, *, progress: float | None = None) -> None:
        manager.update_progress_for_project(
            ctx,
            "global_optimize_video",
            episode,
            progress=progress,
            current_task=message,
            logs=[message],
        )

    # The prompt optimizer remains first-frame by default, but it must not
    # erase an explicitly prepared keyframe beat when the selected model
    # declares first/last-frame support.
    video_backend = str(payload.get("video_backend") or "").strip()
    if not video_backend:
        try:
            from novelvideo.project_config import load_project_config

            project_config = load_project_config(
                ctx.owner_username,
                ctx.project_name,
            )
            video_backend = str(project_config.get("video_backend") or "").strip()
        except Exception:
            video_backend = ""
    supported_strategy_modes, capability_contract_known = (
        resolve_video_strategy_capabilities(video_backend)
    )
    log(
        "开始全局视频提示词优化（默认 first_frame；按能力合同保留 keyframe）...",
        progress=0.02,
    )
    if capability_contract_known:
        log(
            "视频模式能力合同："
            f"{video_backend or '未命名模型'} -> "
            f"{', '.join(sorted(supported_strategy_modes)) or '未声明可用模式'}"
        )
    else:
        log("视频模式能力合同未解析，保持兼容默认 first_frame，不猜测 keyframe")
    store = CogneeStore(
        ctx.owner_project_label,
        output_dir=output_dir,
        state_dir=str(ctx.state_dir),
    )
    try:
        await store.initialize()
        await store.load_graph_state()

        start_frame_paths, color_map, _total_beats = prepare_global_optimizer_input(
            beats=beats,
            characters=characters,
            output_dir=output_dir,
            episode=episode,
            project=ctx.project_name,
        )
        if not start_frame_paths:
            raise RuntimeError("找不到视频起始帧网格或草图网格，请先生成渲染帧或草图")

        resolver = PathResolver(output_dir, episode)
        optimizer = get_global_video_optimizer()
        sorted_beats = sorted(beats, key=lambda beat: beat.get("beat_number", 0))
        # 连续性总开关：合同为空时 keyframe 准入恒假，镜头配方与确定性回退提示词
        # 也只能靠剧本原文猜，于是每个 Beat 各自为战。整集一次补齐合同，失败只降级。
        from novelvideo.agents.beat_shot_contracts import ensure_beat_shot_contracts

        contract_plan = await ensure_beat_shot_contracts(
            beats=sorted_beats,
            episode=episode,
            store=store,
            on_log=log,
        )
        if contract_plan.contracts:
            log(
                f"镜头合同就绪：{len(contract_plan.contracts)}/{len(sorted_beats)} 个 Beat"
                f"（来源：{contract_plan.source}，落库：{contract_plan.persisted}）"
            )
        updated_count = 0
        failure_messages: list[str] = []
        prev_prompt = None

        for index, beat in enumerate(sorted_beats):
            beat_num = int(beat.get("beat_number") or 0)
            log(
                f"Beat {beat_num}/{len(sorted_beats)}: 生成视频提示词...",
                progress=0.2 + 0.7 * index / max(1, len(sorted_beats)),
            )
            prompt_frame_path, source_kind = resolve_video_prompt_frame_path(
                resolver, beat_num, beat
            )
            if prompt_frame_path is None:
                log(f"Beat {beat_num}: 无视频首帧或草图帧，跳过")
                continue
            if source_kind == "sketches":
                log(f"Beat {beat_num}: 视频首帧缺失，回退使用草图帧")

            original_video_mode = str(beat.get("video_mode") or "").strip()
            requested_mode = original_video_mode or "first_frame"
            requested_mode = requested_mode.casefold().replace("-", "_")
            requested_keyframe = requested_mode in {
                "keyframe",
                "first_last_frame",
                "firstlastframe",
                "first_last",
                "flf",
            }
            next_frame_path, _next_source_kind = resolve_video_prompt_frame_path(
                resolver, beat_num + 1
            )
            next_beat = sorted_beats[index + 1] if index < len(sorted_beats) - 1 else None
            contract_ready = shot_contract_ready(
                beat.get("shot_contract") or beat.get("shot_contract_json")
            )
            auto_keyframe = _auto_keyframe_eligible(
                beat,
                contract_ready=contract_ready,
                keyframe_supported="keyframe" in supported_strategy_modes,
                has_next_frame=next_frame_path is not None,
            )
            keyframe_eligible = requested_keyframe or auto_keyframe
            if requested_keyframe:
                existing_keyframe_prompt = str(
                    beat.get("keyframe_prompt") or ""
                ).strip()
                if (
                    "keyframe" in supported_strategy_modes
                    and existing_keyframe_prompt
                    and next_frame_path is not None
                ):
                    # This beat already has the expensive/semantic transition
                    # prompt. Keep it byte-for-byte and let the video runner
                    # submit the same first/last-frame contract later.
                    beat["video_mode"] = "keyframe"
                    beat["keyframe_prompt"] = existing_keyframe_prompt
                    updated_count += 1
                    prev_prompt = existing_keyframe_prompt
                    log(f"Beat {beat_num}: 保留已有 keyframe 首尾帧提示词")
                    continue

                if (
                    "keyframe" in supported_strategy_modes
                    and next_frame_path is not None
                ):
                    # Let the optimizer create the missing transition prompt
                    # from the same contract; do not downgrade merely because
                    # an earlier run did not persist keyframe_prompt.
                    log(f"Beat {beat_num}: 缺少 keyframe 提示词，按合同重新生成首尾帧过渡")
                else:
                    reason = (
                    "模型未声明 first/last-frame"
                    if "keyframe" not in supported_strategy_modes
                    else "缺少 keyframe_prompt 或下一镜首帧"
                    )
                    log(f"Beat {beat_num}: keyframe 不满足合同，回退 first_frame（{reason}）")
                    beat["video_mode"] = "first_frame"
                    beat["keyframe_prompt"] = None
                    keyframe_eligible = False

            # Each beat is persisted immediately below.  On a retry after a
            # late relay failure, keep already-completed prompts and resume at
            # the first missing beat instead of paying for the whole episode
            # again.
            existing_prompt = normalize_motion_prompt_text(
                beat.get("video_prompt") or ""
            )
            if (
                existing_prompt
                and str(beat.get("video_mode") or "") == "first_frame"
                and not keyframe_eligible
            ):
                existing_prompt = sanitize_video_prompt_marker_colors(
                    existing_prompt,
                    color_map,
                )
                styled_prompt = apply_style_snapshot_to_prompt(
                    existing_prompt, style_snapshot, modality="video"
                )
                prompt_issues = await lint_motion_prompt_with_review(
                    styled_prompt, current_beat=beat, next_beat=next_beat, log=log
                )
                if not prompt_issues:
                    seedance_sync = None
                    if is_huimeng_seedance2_backend(video_backend):
                        seedance_sync = sync_seedance2_prompt_from_director(
                            beat,
                            styled_prompt,
                            force=True,
                        )
                    update_fields: dict[str, Any] = {
                        "video_mode": "first_frame",
                        "video_prompt": styled_prompt,
                        "keyframe_prompt": None,
                    }
                    if seedance_sync:
                        update_fields["seedance2_config_json"] = seedance_sync
                    if styled_prompt != existing_prompt or seedance_sync:
                        beat["video_prompt"] = styled_prompt
                        await store.update_beat_asset(
                            episode_number=episode,
                            beat_number=beat_num,
                            **update_fields,
                        )
                    existing_prompt = styled_prompt
                    updated_count += 1
                    prev_prompt = existing_prompt
                    continue
                log(
                    f"Beat {beat_num}: 已有视频提示词未通过质量门 "
                    f"({','.join(prompt_issues)})，重新按镜头配方生成"
                )

            prev_beat = sorted_beats[index - 1] if index > 0 else None
            try:
                result = await optimizer.optimize_single_beat(
                    beat=beat,
                    # Keep the legacy keyword for optimizer/API compatibility;
                    # the value is the actual video start frame when present.
                    sketch_image_path=str(prompt_frame_path),
                    character_color_map=color_map,
                    language=language,
                    prev_beat=prev_beat,
                    next_beat=next_beat,
                    prev_prompt=prev_prompt,
                    total_beats=len(sorted_beats),
                    style_prompt=str(style_snapshot.get("script_prompt") or ""),
                    supported_strategy_modes=supported_strategy_modes,
                    allow_keyframe=keyframe_eligible,
                )
                prompt = sanitize_video_prompt_marker_colors(
                    result["prompt"],
                    color_map,
                )
                prompt = apply_style_snapshot_to_prompt(
                    prompt, style_snapshot, modality="video"
                )
                # The optimizer emits a first-frame motion prompt, but it must
                # not erase a node's explicit provider mode (image-to-video,
                # video-edit, etc.). Keyframe fallback above remains the only
                # intentional mode downgrade.
                result_mode = str(result.get("video_mode") or "first_frame").strip()
                persisted_video_mode = (
                    "keyframe"
                    if result_mode == "keyframe" and keyframe_eligible
                    else (
                        "first_frame"
                        if requested_keyframe or auto_keyframe or not original_video_mode
                        else original_video_mode
                    )
                )
                beat["video_mode"] = persisted_video_mode
                if persisted_video_mode == "keyframe":
                    beat["keyframe_prompt"] = prompt
                    beat["video_prompt"] = ""
                else:
                    beat["video_prompt"] = prompt
                    beat["keyframe_prompt"] = None
                update_fields: dict[str, Any] = {
                    "video_mode": persisted_video_mode,
                    "video_prompt": beat.get("video_prompt") or "",
                    "keyframe_prompt": beat.get("keyframe_prompt"),
                }
                if is_huimeng_seedance2_backend(video_backend):
                    seedance_sync = sync_seedance2_prompt_from_director(
                        beat,
                        prompt,
                        force=True,
                    )
                    if seedance_sync:
                        update_fields["seedance2_config_json"] = seedance_sync
                await store.update_beat_asset(
                    episode_number=episode,
                    beat_number=beat_num,
                    **update_fields,
                )
                updated_count += 1
                prev_prompt = prompt
            except Exception as exc:  # noqa: BLE001
                failure_messages.append(f"Beat {beat_num}: {exc}")
                log(f"Beat {beat_num}: 生成失败 ({exc})")

        if updated_count == 0:
            error = f"全局优化失败：0/{len(sorted_beats)} 个 Beat 生成成功"
            if failure_messages:
                error = f"{error}；最后错误：{failure_messages[-1]}"
            raise RuntimeError(error)

        from novelvideo.styles.project_style import write_stage_style_evidence

        write_stage_style_evidence(
            output_dir,
            "video_prompts",
            style_snapshot,
            episode=episode,
        )
        log(
            f"全局优化完成：成功更新 {updated_count}/{len(sorted_beats)} 个 Beat",
            progress=1.0,
        )
        return {"optimized": updated_count, "beats": beats}
    finally:
        await store.close()


def run_global_optimize_video(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_global_optimize_video_async(envelope, ctx),
            envelope,
            task_type="global_optimize_video",
        )
    )


register_project_task_runner("global_optimize_video", run_global_optimize_video)


async def _run_freezone_video_gen_async(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.shared.provider_cost import provider_cost_event_fields

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    manager = get_task_manager()
    task_type = "freezone_video_gen"
    task_scope = str(envelope.get("scope") or job_id)
    run_task_id = str(envelope.get("__run_task_id") or "").strip() or None
    provider_state: dict[str, object] = {}
    existing_state = manager.get_task_for_project(
        ctx,
        task_type,
        0,
        scope=task_scope,
    )
    existing_metadata = dict(existing_state.metadata or {}) if existing_state else {}
    provider_state.update({key: existing_metadata[key] for key in ("actual_cost", "cost_source") if existing_metadata.get(key)})
    resume_provider_task_id = str(
        payload.get("resume_provider_task_id")
        or existing_metadata.get("provider_task_id")
        or existing_metadata.get("newapi_task_id")
        or ""
    ).strip()
    source_digest = str(payload.get("execution_prompt_sha256") or "")
    if resume_provider_task_id:
        source_digest = source_digest or str(existing_metadata.get("execution_prompt_sha256") or "")
    else:
        source_digest = source_digest or video_prompt_digest(payload.get("prompt"))
    recorded_request = payload.get("generation_request", existing_metadata.get("generation_request"))
    generation_request = (video_generation_request(recorded_request) or recorded_request) if resume_provider_task_id else None

    def update_task(
        *,
        progress: float | None = None,
        current_task: str | None = None,
        logs: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            0,
            scope=task_scope,
            progress=progress,
            current_task=current_task,
            logs=logs,
            metadata=metadata,
            expected_task_id=run_task_id,
        )

    def on_log(message: str) -> None:
        clean_message = str(message or "").strip()
        if clean_message:
            update_task(current_task=clean_message, logs=[clean_message])

    def on_progress(value: float) -> None:
        try:
            normalized = min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return
        # Leave the final 5% to local download validation, history write and
        # task completion.  The frontend therefore never has to fake 96%.
        update_task(progress=0.10 + normalized * 0.85)

    def on_task_event(event: dict[str, object]) -> None:
        stage = str(event.get("stage") or "").strip()
        provider_task_id = str(event.get("provider_task_id") or "").strip()
        provider_request_id = str(event.get("provider_request_id") or "").strip()
        idempotency_key = str(event.get("idempotency_key") or "").strip()
        provider_model = str(event.get("model") or "").strip()
        upstream_status = str(event.get("upstream_status") or "").strip()
        preview_url = str(event.get("preview_url") or "").strip()
        error_code = str(event.get("error_code") or "").strip()
        endpoint_class = str(event.get("endpoint_class") or "").strip()
        verification_stage = str(event.get("verification_stage") or "").strip()
        suggested_action = str(event.get("suggested_action") or "").strip()
        retryable = event.get("retryable")
        request_contract = event.get("request_contract")
        if provider_task_id:
            provider_state["provider_task_id"] = provider_task_id
        if provider_request_id:
            provider_state["provider_request_id"] = provider_request_id
        if provider_model:
            provider_state["provider_model"] = provider_model
        if preview_url:
            provider_state["provider_preview_url"] = preview_url
        provider_state.update(provider_cost_event_fields(event))

        stage_messages = {
            "submitted": "上游视频任务已提交，正在等待生成",
            "resuming": "检测到已提交的上游视频任务，正在恢复",
            "polling": "上游视频生成中",
            "query_retry": "上游状态查询短暂中断，正在自动重试",
            "upstream_completed": "上游视频已生成完成，正在准备下载",
            "downloading": "正在下载上游已完成的视频",
            "downloaded": "视频已落盘，正在归档到画布",
            "download_retry_required": "视频已生成完成，本地下载失败，正在等待恢复",
            "submit_result_unknown": "视频提交结果待确认，等待恢复，不会重复生成",
            "upstream_failed": "上游视频任务返回失败",
            "timeout": "上游视频任务轮询超时",
        }
        current_task = stage_messages.get(stage, "正在处理视频任务")
        if stage == "polling" and upstream_status:
            current_task = f"上游视频生成中（{upstream_status}）"
        metadata: dict[str, object] = {
            "provider": "newapi",
            "provider_backend": str(payload.get("backend") or "").strip() or None,
            "provider_stage": stage or "unknown",
            "provider_task_id": provider_task_id or None,
            "provider_request_id": provider_request_id or None,
            "idempotency_key": idempotency_key or None,
            "provider_model": existing_metadata.get("provider_model") if resume_provider_task_id else provider_model or None,
            "upstream_status": upstream_status or None,
            "provider_preview_url": provider_state.get("provider_preview_url") or None,
        }
        metadata.update(provider_cost_event_fields(event))
        if error_code:
            metadata["error_code"] = error_code[:120]
        if endpoint_class:
            metadata["endpoint_class"] = endpoint_class[:120]
        if verification_stage:
            metadata["verification_stage"] = verification_stage[:80]
        if suggested_action:
            metadata["suggested_action"] = suggested_action[:300]
        if isinstance(retryable, bool):
            metadata["retryable"] = retryable
        if isinstance(request_contract, dict):
            safe_contract: dict[str, object] = {}
            response_keys = request_contract.get("response_keys")
            if isinstance(response_keys, list):
                safe_contract["response_keys"] = [str(key)[:80] for key in response_keys[:24]]
            for key in (
                "response_code",
                "response_data_type",
                "response_message",
                "response_message_present",
            ):
                value = request_contract.get(key)
                if key == "response_message_present":
                    if isinstance(value, bool):
                        safe_contract[key] = value
                elif value not in (None, ""):
                    safe_contract[key] = str(value)[:80]
            response_data_keys = request_contract.get("response_data_keys")
            if isinstance(response_data_keys, list):
                safe_contract["response_data_keys"] = [
                    str(key)[:80] for key in response_data_keys[:24]
                ]
            if safe_contract:
                metadata["request_contract"] = safe_contract
        update_task(
            current_task=current_task,
            logs=[current_task],
            metadata=metadata,
        )

    manager.update_progress_for_project(
        ctx,
        task_type,
        0,
        scope=task_scope,
        progress=0.08,
        current_task=(
            "检测到已提交的上游视频任务，准备恢复"
            if resume_provider_task_id
            else "准备提交视频任务"
        ),
        logs=["开始 freezone 视频生成"],
        metadata={
            "provider": "newapi" if resume_provider_task_id else "",
            "provider_backend": str(payload.get("backend") or "").strip() or None,
            "provider_task_id": resume_provider_task_id or None,
            "provider_stage": "resuming" if resume_provider_task_id else "preparing",
            "execution_prompt_sha256": source_digest,
            # Keep a credential-free request snapshot with the task so a
            # later recovery can restore the canvas binding and presentation
            # settings without replaying the original submit request.
            "canvas_id": str(payload.get("canvas_id") or "").strip() or None,
            "node_id": str(payload.get("node_id") or "").strip() or None,
            "model_id": str(payload.get("model_id") or "").strip() or None,
            "gen_mode": str(payload.get("gen_mode") or "").strip() or None,
            "aspect_ratio": str(payload.get("aspect_ratio") or "").strip() or None,
            "resolution": str(payload.get("resolution") or "").strip() or None,
            "duration_seconds": payload.get("duration_seconds"),
            "generate_audio": bool(payload.get("generate_audio")),
            "requested_generate_audio": payload.get("requested_generate_audio"),
            "generate_audio_explicit": bool(payload["generate_audio_explicit"]) if payload.get("generate_audio_explicit") is not None else None,
            "dialogue_text": str(payload.get("dialogue_text") or ""),
            "spoken_dialogue": list(payload.get("spoken_dialogue") or [])
            if isinstance(payload.get("spoken_dialogue"), (list, tuple))
            else [],
            "audio_type": str(payload.get("audio_type") or "").strip() or None,
            "speaker": str(payload.get("speaker") or "").strip() or None,
            "native_audio_strategy": str(payload.get("native_audio_strategy") or "").strip() or None,
            "audio_asset_ref": str(payload.get("audio_asset_ref") or "").strip() or None,
            "parameters": dict(payload.get("parameters") or {})
            if isinstance(payload.get("parameters"), dict)
            else {},
            "provider_mapping": dict(
                payload.get("provider_mapping") or payload.get("providerMapping") or {}
            )
            if isinstance(payload.get("provider_mapping") or payload.get("providerMapping"), dict)
            else {},
            "opaque": list(payload.get("opaque") or [])
            if isinstance(payload.get("opaque"), list)
            else [],
            "media_inputs": list(payload.get("media_inputs") or payload.get("mediaInputs") or [])
            if isinstance(payload.get("media_inputs") or payload.get("mediaInputs"), list)
            else [],
            "size": str(payload.get("size") or "").strip() or None,
            "size_field": str(payload.get("size_field") or payload.get("sizeField") or "").strip() or None,
            "human_review": bool(payload.get("human_review")),
            "scene_optimize": str(payload.get("scene_optimize") or "").strip() or None,
        },
        expected_task_id=run_task_id,
    )

    try:
        job_arguments = dict(
            **video_execution_arguments(payload),
            project_dir=project_dir,
            job_id=job_id,
            on_log=on_log,
            on_progress=on_progress,
            on_task_event=on_task_event,
            resume_provider_task_id=resume_provider_task_id or None,
        )
        if not resume_provider_task_id:
            generation_request = build_video_generation_request(job_arguments)
        if "expected_generation_request" in payload and not video_generation_request_matches(generation_request, payload["expected_generation_request"]):
            raise ValueError("视频参考素材或生成设置已变化，拒绝执行旧提交，请重新出这一镜")
        update_task(metadata={"generation_request": generation_request})
        out_path = await run_freezone_video_job(**job_arguments)
    except Exception as exc:
        if classify_video_pending(exc) is not None:
            # Provider recovery states are handled by the task core. Do not
            # write a false terminal node-history failure for either state.
            raise
        _append_freezone_video_node_history(
            ctx=ctx,
            project_dir=project_dir,
            payload=payload,
            job_id=job_id,
            error=str(exc),
        )
        raise

    # 已明确提供的对白混音失败必须让逐镜任务失败，不能静默交付无声视频。
    dialogue_audio = await mux_dialogue_audio(
        output_path=out_path,
        dialogue_audio=payload.get("dialogue_audio"),
        project_dir=project_dir,
        envelope=envelope,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
        **({"dialogue_audio": dialogue_audio} if dialogue_audio else {}),
        **{key: provider_state[key] for key in ("actual_cost", "cost_source") if provider_state.get(key)},
    }
    media_metadata: dict[str, object] = {}
    result["video_generation_source"] = persist_video_generation_source(
        out_path, output_url=result["output_url"], job_id=job_id, prompt_digest=source_digest,
        generation_request=generation_request,
        provider_model=(existing_metadata if resume_provider_task_id else provider_state).get("provider_model"),
    )
    try:
        width, height = await probe_video_size(str(out_path))
        media_metadata.update(
            {
                "width": width,
                "height": height,
                "widthPx": width,
                "heightPx": height,
            }
        )
        result.update(media_metadata)
    except Exception as exc:
        # A provider result remains usable when ffprobe is unavailable or the
        # fixture is not a parseable video. The browser will still hydrate
        # dimensions from the media element later.
        logger.debug("video result metadata probe skipped for %s: %s", job_id, exc)
    try:
        # Probed separately so a container ffprobe can size but not time (or the
        # reverse) still contributes whichever half it did report.
        media_metadata["durationSec"] = round(await probe_video_duration(str(out_path)), 3)
    except Exception as exc:
        logger.debug("video duration probe skipped for %s: %s", job_id, exc)
    media_metadata.update(media_file_metadata(out_path))
    result.update(media_metadata)
    media_metadata["video_generation_source"] = result["video_generation_source"]
    # 「只能真，不能骗人」：出片实测 vs 任务请求对账。不符就写进结果并推一条
    # 任务日志，让“请求 Xs / 实得 Ys”留在案上，方便带任务号找渠道方举证。
    requested_seconds = _coerce_duration_seconds(payload.get("duration_seconds"))
    actual_seconds = media_metadata.get("durationSec")
    if (
        requested_seconds is not None
        and isinstance(actual_seconds, (int, float))
        and not isinstance(actual_seconds, bool)
        and float(actual_seconds) > 0
    ):
        actual_value = float(actual_seconds)
        matches = abs(actual_value - requested_seconds) <= 0.5
        result["durationCheck"] = {
            "requestedSeconds": requested_seconds,
            "actualSeconds": round(actual_value, 3),
            "match": matches,
        }
        if not matches:
            on_log(
                f"时长对账不符：请求 {requested_seconds:g}s，实得 {actual_value:.2f}s；"
                "结果已如实记录，可携任务号向渠道方举证。"
            )
    commit_payload = dict(payload)
    preview_path = _ensure_video_preview_frame(out_path)
    if preview_path is not None:
        preview_rel = preview_path.relative_to(project_dir).as_posix()
        preview_url = make_static_url_for_context(
            ctx,
            preview_rel,
            local_path=preview_path,
        )
        result["preview_url"] = preview_url
        commit_payload["preview_url"] = preview_url
    if provider_state.get("provider_task_id") or resume_provider_task_id:
        result["provider_task_id"] = (
            provider_state.get("provider_task_id") or resume_provider_task_id
        )
    if provider_state.get("provider_request_id"):
        result["provider_request_id"] = provider_state["provider_request_id"]
    history_record = _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        job_id=job_id,
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    if payload.get("canvas_commit_mode") == "workflow_artifact":
        result["canvas_commit"] = {
            "status": "skipped",
            "reason_code": "workflow_artifact_only",
            "message": "逐镜视频归 WorkflowRun 产物管理，未绑定真实画布节点。",
        }
    else:
        canvas_receipt = commit_media_result_to_canvas(
            ctx=ctx,
            payload=commit_payload,
            task_type=task_type,
            job_id=job_id,
            output_url=result["output_url"],
            media_type="video",
            media_metadata=media_metadata,
            local_path=out_path,
        )
        if canvas_receipt:
            result["canvas_receipt"] = canvas_receipt
    return result


def run_freezone_video_gen(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_freezone_video_gen_async(envelope, ctx),
            envelope,
            task_type="freezone_video_gen",
        )
    )


register_project_task_runner("freezone_video_gen", run_freezone_video_gen)
