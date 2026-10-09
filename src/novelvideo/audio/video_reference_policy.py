"""Model-neutral selection of audio references for video generation.

The policy deliberately knows only the semantic contract published by a model
discovery record.  Provider names, workflow node ids and transport field names
stay in the adapter layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from novelvideo.seedance2_i2v.voice_clone import (
    beat_audio_path,
    narrator_reference_audio_path,
    resolve_dialogue_reference_audio,
)


_SEMANTICS = frozenset(
    {"driving_audio", "voice_profile", "soundtrack", "audio_prompt"}
)


def normalize_audio_input_semantics(value: object) -> tuple[str, ...]:
    """Return stable, de-duplicated semantic names from a capability record."""

    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        return ()
    result: list[str] = []
    for item in values:
        semantic = str(item or "").strip().casefold().replace("-", "_")
        if semantic in _SEMANTICS and semantic not in result:
            result.append(semantic)
    return tuple(result)


def capability_audio_input_semantics(capability: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Read the additive semantic field while accepting both API spellings."""

    if not isinstance(capability, Mapping):
        return ()
    raw = capability.get("audioInputSemantics")
    if raw is None:
        raw = capability.get("audio_input_semantics")
    return normalize_audio_input_semantics(raw)


def capability_audio_reference_limit(
    capability: Mapping[str, Any] | None,
    *,
    mode: str = "",
) -> int:
    """Read an audio-reference limit from either flat or mode-scoped shapes."""

    if not isinstance(capability, Mapping):
        return 0
    raw = capability.get("referenceLimits")
    if raw is None:
        raw = capability.get("reference_limits")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(raw, Mapping):
        normalized_mode = str(mode or "").strip().casefold()
        nested_limits = [value for value in raw.values() if isinstance(value, Mapping)]
        selected = next(
            (
                value
                for key, value in raw.items()
                if str(key).strip().casefold() == normalized_mode
                and isinstance(value, Mapping)
            ),
            None,
        )
        if normalized_mode and selected is not None:
            candidates.append(selected)
        elif normalized_mode and nested_limits:
            candidates = []
        else:
            candidates.append(raw)
            candidates.extend(nested_limits)
    best = 0
    for item in candidates:
        for key in ("audio", "referenceAudios", "reference_audios"):
            try:
                best = max(best, int(item.get(key) or 0))
            except (TypeError, ValueError):
                continue
    return best


def _existing(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path if path.is_file() and path.stat().st_size > 0 else None
    except OSError:
        return None


async def resolve_video_audio_references(
    *,
    beat: Mapping[str, Any],
    store: Any,
    episode: int | None = None,
    semantics: Iterable[str] = (),
    audio_limit: int | None = None,
    narrator_stored_path: str = "",
) -> list[dict[str, str]]:
    """Select at most the contract-allowed audio references for one Beat.

    A generated Beat audio file is preferred for ``driving_audio`` and
    ``soundtrack``.  ``voice_profile`` resolves the speaker/narrator sample.
    Unknown or absent semantics intentionally produce no references.
    """

    normalized = normalize_audio_input_semantics(tuple(semantics))
    if not normalized:
        return []
    audio_type = str(beat.get("audio_type") or "").strip().casefold()
    text = str(
        beat.get("narration_segment")
        or beat.get("dialogue")
        or beat.get("narration")
        or ""
    ).strip()
    if audio_type in {"silence", "action"} or not text:
        return []
    try:
        resolved_episode = int(
            episode
            if episode is not None
            else beat.get("episode_number") or beat.get("episode") or 0
        )
        beat_num = int(beat.get("beat_number") or 0)
    except (TypeError, ValueError):
        return []
    if resolved_episode <= 0 or beat_num <= 0:
        return []

    project_dir = Path(store.project_dir)
    refs: list[dict[str, str]] = []
    beat_audio = _existing(beat_audio_path(project_dir, resolved_episode, beat_num))

    # One complete driving track is preferable to mixing a voice sample into
    # the same request.  This also keeps the behavior deterministic when a
    # model advertises several compatible semantic labels.
    if any(item in normalized for item in ("driving_audio", "soundtrack")) and beat_audio:
        role = "driving_audio" if "driving_audio" in normalized else "soundtrack"
        refs.append({"type": "audio", "path": str(beat_audio), "role": role})
    elif "voice_profile" in normalized:
        speaker = str(beat.get("speaker") or "").strip()
        resolved = await resolve_dialogue_reference_audio(dict(beat), store) if speaker else None
        voice_path = resolved[0] if resolved else None
        if not voice_path and not speaker:
            voice_path = narrator_reference_audio_path(project_dir, narrator_stored_path)
        voice_path = _existing(voice_path)
        if voice_path:
            refs.append({"type": "audio", "path": str(voice_path), "role": "voice_profile"})

    if audio_limit is not None:
        try:
            limit = max(0, int(audio_limit))
        except (TypeError, ValueError):
            limit = 0
        refs = refs[:limit]
    return refs


__all__ = [
    "capability_audio_input_semantics",
    "capability_audio_reference_limit",
    "normalize_audio_input_semantics",
    "resolve_video_audio_references",
]
