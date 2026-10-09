"""Dialogue parsing helpers for Seedance 2.0 voice references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from novelvideo.seedance2_i2v.voice_clone import (
    _field_text_values,
    normalize_seedance2_audio_type,
    resolve_beat_dialogue,
)


_SPEAKER_PREFIX_RE = re.compile(
    r"(?P<speaker>[\w\u4e00-\u9fff·]{1,40})"
    r"(?:（(?P<action>[^）]{0,120})）|\((?P<action_ascii>[^)]{0,120})\))?"
    r"\s*[：:]"
)


@dataclass(frozen=True)
class Seedance2SpokenLine:
    speaker: str
    text: str
    action: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _spoken_source(beat: dict[str, Any]) -> tuple[str, bool]:
    """Return source text and whether it came from a typed dialogue field."""

    structured = tuple(
        text
        for key in ("dialogue_text", "spoken_dialogue")
        for text in _field_text_values(beat.get(key))
    )
    if structured:
        return "\n".join(structured), True
    legacy = tuple(
        text
        for key in ("dialogue", "narration_segment", "narration")
        for text in _field_text_values(beat.get(key))
    )
    return "\n".join(legacy), False


def parse_seedance2_spoken_lines(beat: dict[str, Any]) -> list[Seedance2SpokenLine]:
    """Parse dialogue text into speaker/action/text lines.

    Supports literal script lines such as ``角色（动作）：台词``. Falls back to the
    beat-level ``speaker`` when the text is plain dialogue.
    """

    if normalize_seedance2_audio_type(beat) != "dialogue":
        return []

    raw, structured_source = _spoken_source(beat)
    if not raw:
        return []

    # A speaker-prefix line is a structured dialogue syntax only when it came
    # through the dedicated dialogue fields.  Treating a plain narration line
    # such as ``角色：抬头看向窗外`` as speech recreates the original bug.
    matches = list(_SPEAKER_PREFIX_RE.finditer(raw)) if structured_source else []
    lines: list[Seedance2SpokenLine] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        text = raw[start:end].strip(" \t\r\n，,。；;")
        if not text:
            continue
        lines.append(
            Seedance2SpokenLine(
                speaker=_text(match.group("speaker")),
                action=_text(match.group("action") or match.group("action_ascii")),
                text=text,
            )
        )
    if lines:
        return lines

    speaker = _text(beat.get("speaker"))
    text = resolve_beat_dialogue(beat) or ""
    if speaker and text:
        return [Seedance2SpokenLine(speaker=speaker, text=text)]
    return []


def unique_seedance2_dialogue_speakers(beat: dict[str, Any]) -> list[str]:
    """Return dialogue speakers in first-spoken order."""

    seen: set[str] = set()
    speakers: list[str] = []
    for line in parse_seedance2_spoken_lines(beat):
        if line.speaker in seen:
            continue
        seen.add(line.speaker)
        speakers.append(line.speaker)
    return speakers


def speaker_display_name(value: str) -> str:
    text = _text(value)
    if "_" in text:
        return text.split("_", 1)[0]
    return text
