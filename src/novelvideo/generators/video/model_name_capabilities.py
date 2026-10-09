"""Parse structured capability hints out of relay-facing model names.

Relay stations publish video models whose *names* carry the capability
contract, because ``GET /models`` itself only returns ``id``/``object`` (plus
``supported_endpoint_types`` on NewAPI).  A model called ``S-2.5-10图-内置过脸``
is declaring, in its name, that it accepts ten reference images and performs
face handling internally.

The built-in profile table in :mod:`.direct_video_profiles` can only match the
vendor/version part of such a name, so a name that carries capability segments
was previously served by the generic fallback — the canvas then advertised
``1`` input image and no reference slots while the upstream accepted ten.

This module turns those name segments into capability facts.  It is pure (no
I/O, no side effects) and deliberately conservative:

* Only *explicitly written* numbers become limits.  ``10图`` means ten;
  a name without a count segment contributes nothing and the caller keeps
  whatever it already had.
* A parsed limit never silently drops below the declared baseline, and an
  unparsable name yields no hints at all rather than a guess.
* English vendor names (``sd2.0-480p``, ``minnow-h3``) must not be disturbed:
  the numeric segments are anchored to the CJK classifier characters, so a
  version number can never be read as a reference-image count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

__all__ = [
    "ModelNameCapabilities",
    "parse_model_name_capabilities",
    "MODEL_NAME_CAPABILITY_REVISION",
]


MODEL_NAME_CAPABILITY_REVISION = "relay-model-name.v1"

# Bounds mirror the protocol-wide sanity ceiling used by the transport layer
# (``_reference_limits`` clamps provider declarations to 100) so a typo in a
# relay model name cannot publish an absurd picker.
_MAX_REFERENCE_IMAGES = 100
_MAX_REFERENCE_VIDEOS = 100
_MAX_REFERENCE_AUDIOS = 100

# ``10图`` / ``10 张图`` / ``支持10张参考图`` / ``10图参考``.
# The count must be followed by a CJK classifier — that anchor is what keeps
# ``sd2.0`` and ``minimax-h3`` from being read as capabilities.  The classifier
# may also follow a leading ``参考`` (``10张参考图``), so the optional tail is
# allowed to appear on either side of it.
_REFERENCE_IMAGE_RE = re.compile(
    r"(\d{1,3})\s*(?:张|幅|帧)?\s*(?:参考)?\s*图(?![a-zA-Z])",
)

# ``3视频`` / ``3个视频`` / ``10秒视频`` is explicitly NOT matched: the count
# must be followed by the clip classifier, not by a duration unit.
_REFERENCE_VIDEO_RE = re.compile(r"(\d{1,3})\s*(?:段|个|条)?\s*视频(?![a-zA-Z])")

# ``3音频`` / ``3条音频`` / ``3个音频``.
_REFERENCE_AUDIO_RE = re.compile(r"(\d{1,3})\s*(?:段|个|条)?\s*音频(?![a-zA-Z])")

# ``30秒`` / ``15s`` are duration, not references.  Recognized so a name like
# ``S-2.5-30秒-10图`` keeps both facts and they cannot be confused.
_FIXED_DURATION_RE = re.compile(r"(?:固定|定长)?\s*(\d{1,3})\s*(?:秒|s)(?![a-zA-Z0-9])")

# ``内置过脸`` / ``过脸`` / ``换脸`` / ``人脸`` — the relay's wording for a model
# that performs face-preserving handling upstream.  Kept as a boolean fact:
# the canvas has no per-model face parameter, so this only annotates the model
# and must never turn into a fabricated transport field.
_FACE_HANDLING_RE = re.compile(r"(?:内置)?\s*(?:过脸|换脸)|人脸(?:保持|一致|稳定|处理)")

# ``全能参考`` / ``多图参考`` / ``参考图`` / ``多参考`` / ``多图`` — an explicit
# statement that the model accepts references, which is what makes
# ``reference_to_video`` legal for it.  The count-free forms (``多图``,
# ``多音频``) appear in display names where the relay never writes a number.
_REFERENCE_MODE_RE = re.compile(
    r"全能参考|多图参考|参考图|多参考|全参考|多图|多视频|多音频|reference",
    re.IGNORECASE,
)

# ``首尾帧`` / ``首帧`` explicit declarations.
_FIRST_LAST_FRAME_RE = re.compile(r"首尾帧|首末帧")
_FIRST_FRAME_RE = re.compile(r"首帧")

# Segments that only ever mean "this is a video model"; they carry no
# capability and are stripped before any numeric scan so they cannot create
# false positives.
_NOISE_SEGMENT_RE = re.compile(
    r"^(?:视频|模型|版|飞|极速|快速|高速|标准|专业|尊享|满血|普通|正式|测试|预览)$"
)


@dataclass(frozen=True, slots=True)
class ModelNameCapabilities:
    """Capability facts explicitly written into a relay model name.

    Every field defaults to ``None``/``False``, meaning "the name did not say".
    A caller must treat ``None`` as *unknown* and keep its existing value —
    never as zero.
    """

    reference_images: int | None = None
    reference_videos: int | None = None
    reference_audios: int | None = None
    fixed_duration_seconds: int | None = None
    supports_face_handling: bool = False
    declares_reference_mode: bool = False
    declares_first_last_frame: bool = False
    declares_first_frame: bool = False
    matched_segments: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.matched_segments


def _clamp(value: int, maximum: int) -> int:
    return max(0, min(value, maximum))


def _first_group(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def parse_model_name_capabilities(model_name: object) -> ModelNameCapabilities:
    """Extract capability facts written into a relay-facing model name.

    Returns an empty :class:`ModelNameCapabilities` when the name declares
    nothing, so callers can keep their previous behaviour unchanged.
    """

    raw = str(model_name or "").strip()
    if not raw:
        return ModelNameCapabilities()

    segments: list[str] = []
    for segment in _split_segments(raw):
        cleaned = _NOISE_SEGMENT_RE.sub("", segment).strip()
        if cleaned:
            segments.append(cleaned)
    haystack = " ".join(segments) if segments else raw

    reference_images = _first_group(_REFERENCE_IMAGE_RE, haystack)
    reference_videos = _first_group(_REFERENCE_VIDEO_RE, haystack)
    reference_audios = _first_group(_REFERENCE_AUDIO_RE, haystack)
    fixed_duration = _first_group(_FIXED_DURATION_RE, haystack)

    supports_face_handling = bool(_FACE_HANDLING_RE.search(haystack))
    declares_reference_mode = bool(_REFERENCE_MODE_RE.search(haystack))
    declares_first_last_frame = bool(_FIRST_LAST_FRAME_RE.search(haystack))
    declares_first_frame = bool(_FIRST_FRAME_RE.search(haystack))

    matched: list[str] = []
    if reference_images is not None:
        matched.append("referenceImages")
    if reference_videos is not None:
        matched.append("referenceVideos")
    if reference_audios is not None:
        matched.append("referenceAudios")
    if fixed_duration is not None:
        matched.append("fixedDuration")
    if supports_face_handling:
        matched.append("faceHandling")
    if declares_reference_mode:
        matched.append("referenceMode")
    if declares_first_last_frame:
        matched.append("firstLastFrame")
    if declares_first_frame:
        matched.append("firstFrame")

    if not matched:
        return ModelNameCapabilities()

    # A name that declares a reference count is also declaring that the model
    # accepts references at all, even without the words ``全能参考``.
    if (
        reference_images is not None
        or reference_videos is not None
        or reference_audios is not None
    ):
        declares_reference_mode = True
        if "referenceMode" not in matched:
            matched.append("referenceMode")

    return ModelNameCapabilities(
        reference_images=(
            _clamp(reference_images, _MAX_REFERENCE_IMAGES)
            if reference_images is not None
            else None
        ),
        reference_videos=(
            _clamp(reference_videos, _MAX_REFERENCE_VIDEOS)
            if reference_videos is not None
            else None
        ),
        reference_audios=(
            _clamp(reference_audios, _MAX_REFERENCE_AUDIOS)
            if reference_audios is not None
            else None
        ),
        fixed_duration_seconds=fixed_duration,
        supports_face_handling=supports_face_handling,
        declares_reference_mode=declares_reference_mode,
        declares_first_last_frame=declares_first_last_frame,
        declares_first_frame=declares_first_frame,
        matched_segments=tuple(matched),
    )


def _split_segments(model_name: str) -> Iterable[str]:
    """Split a relay model name on its structural separators.

    Relay names use ``-``/``_``/spaces between capability segments
    (``S-2.5-10图-内置过脸``).  Splitting first keeps each pattern anchored to
    one segment, so a count cannot drift across a separator boundary.
    """

    return (part for part in re.split(r"[-_/\s]+", model_name) if part.strip())
