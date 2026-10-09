"""Model-neutral contracts for direct text-to-speech execution.

The model center may discover a provider-specific field name, but execution
must only use it after the upstream catalog explicitly declares a voice
reference slot.  This module is deliberately free of provider/model names so
the same contract can be reused by canvas nodes, WorkflowRun and Beat audio.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


VOICE_REFERENCE_SLOT = "voice_reference"
_ALLOWED_TRANSPORTS = frozenset({"url", "data_url", "base64"})
_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_RESERVED_FIELDS = frozenset({"model", "input", "response_format", "metadata"})


class AudioModelContractError(ValueError):
    """Structured preflight error raised before a paid upstream request."""

    code = "AUDIO_MODEL_CONTRACT_INVALID"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


class AudioModeNotSupportedError(AudioModelContractError):
    code = "AUDIO_MODE_NOT_SUPPORTED"


class AudioVoiceReferenceUnsupportedError(AudioModelContractError):
    code = "AUDIO_VOICE_REFERENCE_UNSUPPORTED"


class AudioVoiceReferenceLimitError(AudioModelContractError):
    code = "AUDIO_VOICE_REFERENCE_LIMIT_INVALID"


class AudioVoiceReferenceMappingError(AudioModelContractError):
    code = "AUDIO_VOICE_REFERENCE_MAPPING_INVALID"


class AudioVoiceReferenceTransportError(AudioModelContractError):
    code = "AUDIO_VOICE_REFERENCE_TRANSPORT_INVALID"


@dataclass(frozen=True, slots=True)
class CompiledAudioSpeechContract:
    mode: str
    input_slots: tuple[str, ...]
    reference_audio_limit: int
    voice_reference_field: str = ""
    voice_reference_transport: str = ""
    parameter_defaults: dict[str, Any] = field(default_factory=dict)
    provider_mapping: dict[str, Any] = field(default_factory=dict)

    @property
    def accepts_voice_reference(self) -> bool:
        return (
            VOICE_REFERENCE_SLOT in self.input_slots
            and self.reference_audio_limit > 0
            and bool(self.voice_reference_field)
        )


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _as_slots(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _mode_limits(value: Any, mode: str) -> int | None:
    if not isinstance(value, Mapping):
        return None
    # New contracts use {text_to_speech: {audio: 1}}.  Accept the flat
    # {audio: 1} form for compatibility with early capability snapshots.
    candidate = value.get(mode)
    if isinstance(candidate, Mapping):
        candidate = _first(candidate, "audio", "audio_reference", "reference_audio")
    if candidate is None:
        candidate = _first(value, "audio", "audio_reference", "reference_audio")
    if isinstance(candidate, Mapping):
        candidate = _first(candidate, "limit", "max", "count")
    try:
        number = int(candidate)
    except (TypeError, ValueError):
        return None
    return number


def _provider_mapping(capability: Mapping[str, Any]) -> dict[str, Any]:
    raw = _first(capability, "providerMapping", "provider_mapping", "mapping")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _voice_mapping(provider_mapping: Mapping[str, Any]) -> dict[str, Any]:
    raw = _first(provider_mapping, VOICE_REFERENCE_SLOT, "voiceReference", "voice_reference")
    return dict(raw) if isinstance(raw, Mapping) else {}


def compile_audio_speech_contract(
    capability: Mapping[str, Any] | None,
    *,
    mode: str = "text_to_speech",
) -> CompiledAudioSpeechContract:
    """Compile one explicit, browser-safe capability envelope.

    Empty metadata means text-only speech.  It never grants an inferred voice
    clone slot merely because the protocol happens to be OpenAI-compatible.
    """

    payload = dict(capability or {})
    normalized_mode = str(mode or "text_to_speech").strip() or "text_to_speech"
    modes = _as_slots(_first(payload, "supportedModes", "supported_modes", "modeType", "mode_type"))
    if modes and normalized_mode not in modes:
        raise AudioModeNotSupportedError(
            f"音频模型不支持当前模式：{normalized_mode}",
            details={"mode": normalized_mode, "supported_modes": list(modes)},
        )

    slots = _as_slots(_first(payload, "inputSlots", "input_slots"))
    limits_raw = _first(payload, "referenceLimits", "reference_limits")
    limit = _mode_limits(limits_raw, normalized_mode)
    if limit is None:
        limit = 0
    if limit < 0:
        raise AudioVoiceReferenceLimitError(
            "音频模型的参考声线数量不能为负数",
            details={"mode": normalized_mode, "limit": limit},
        )

    mapping = _provider_mapping(payload)
    voice_mapping = _voice_mapping(mapping)
    field_name = str(_first(voice_mapping, "field", "parameter", "key") or "").strip()
    transport = str(_first(voice_mapping, "transport", "encoding") or "").strip().lower()
    if VOICE_REFERENCE_SLOT in slots and limit > 0:
        if not field_name:
            raise AudioVoiceReferenceMappingError(
                "模型声明了参考声线，但未声明 providerMapping.voice_reference.field",
                details={"mode": normalized_mode},
            )
        if not _FIELD_RE.fullmatch(field_name) or field_name in _RESERVED_FIELDS:
            raise AudioVoiceReferenceMappingError(
                "providerMapping.voice_reference.field 无效",
                details={"field": field_name},
            )
        if transport not in _ALLOWED_TRANSPORTS:
            raise AudioVoiceReferenceTransportError(
                "providerMapping.voice_reference.transport 无效",
                details={"transport": transport},
            )

    defaults = _first(payload, "parameterDefaults", "parameter_defaults")
    return CompiledAudioSpeechContract(
        mode=normalized_mode,
        input_slots=slots,
        reference_audio_limit=limit,
        voice_reference_field=field_name if VOICE_REFERENCE_SLOT in slots and limit > 0 else "",
        voice_reference_transport=transport if VOICE_REFERENCE_SLOT in slots and limit > 0 else "",
        parameter_defaults=dict(defaults) if isinstance(defaults, Mapping) else {},
        provider_mapping=mapping,
    )


def audio_model_accepts_voice_reference(
    capability: Mapping[str, Any] | None,
    *,
    mode: str = "text_to_speech",
) -> bool:
    return compile_audio_speech_contract(capability, mode=mode).accepts_voice_reference


def audio_reference_limit_for_mode(
    capability: Mapping[str, Any] | None,
    *,
    mode: str = "text_to_speech",
) -> int:
    return compile_audio_speech_contract(capability, mode=mode).reference_audio_limit


def encode_audio_reference(path: str | Path, transport: str) -> str:
    """Encode a local reference according to the compiled provider contract."""

    audio_path = Path(path)
    if not audio_path.exists() or not audio_path.is_file():
        raise AudioVoiceReferenceMappingError(f"参考声线文件不存在：{audio_path}")
    normalized = str(transport or "").strip().lower()
    if normalized not in _ALLOWED_TRANSPORTS:
        raise AudioVoiceReferenceTransportError(
            "参考声线传输类型不受支持",
            details={"transport": normalized},
        )
    raw = audio_path.read_bytes()
    if normalized == "base64":
        return base64.b64encode(raw).decode("ascii")
    if normalized == "data_url":
        mime = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    # Existing relay helper enforces the project-wide size cap and returns a
    # provider-readable data URL for local files.  Providers that call the
    # transport "url" can still consume that opaque reference.
    from novelvideo.seedance2_i2v.voice_clone import build_reference_audio_url

    return build_reference_audio_url(audio_path)


def compile_voice_reference_field(
    capability: Mapping[str, Any] | None,
    path: str | Path,
    *,
    mode: str = "text_to_speech",
) -> tuple[CompiledAudioSpeechContract, str]:
    contract = compile_audio_speech_contract(capability, mode=mode)
    if not contract.accepts_voice_reference:
        raise AudioVoiceReferenceUnsupportedError(
            "当前音频模型未声明可用的参考声线能力，请切换支持参考声线的模型",
            details={
                "mode": contract.mode,
                "input_slots": list(contract.input_slots),
                "reference_audio_limit": contract.reference_audio_limit,
            },
        )
    return contract, encode_audio_reference(path, contract.voice_reference_transport)


__all__ = [
    "AudioModeNotSupportedError",
    "AudioModelContractError",
    "AudioVoiceReferenceLimitError",
    "AudioVoiceReferenceMappingError",
    "AudioVoiceReferenceTransportError",
    "AudioVoiceReferenceUnsupportedError",
    "CompiledAudioSpeechContract",
    "audio_model_accepts_voice_reference",
    "audio_reference_limit_for_mode",
    "compile_audio_speech_contract",
    "compile_voice_reference_field",
    "encode_audio_reference",
]
