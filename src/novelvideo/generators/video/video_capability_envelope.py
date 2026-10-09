"""Lossless, provider-neutral video capability envelopes.

The public video node only needs a small set of common controls, but a model
catalog can expose many more fields.  This module keeps those fields in a
deterministic shape so discovery, cache, API and transport can share one
contract without teaching every layer about every provider.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable, Mapping


CAPABILITY_ENVELOPE_VERSION = 1

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization)",
    re.IGNORECASE,
)
_PARAMETER_CONTAINER_KEYS = (
    "parameters",
    "parameterSchema",
    "parameter_schema",
    "inputSchema",
    "input_schema",
    "requestSchema",
    "request_schema",
    "schema",
    "properties",
)
_CAPABILITY_KEY_HINTS = (
    "mode",
    "resolution",
    "size",
    "aspect",
    "ratio",
    "duration",
    "audio",
    "image",
    "video",
    "reference",
    "format",
    "quality",
    "seed",
    "frame",
    "motion",
    "camera",
    "style",
    "prompt",
    "negative",
    "steps",
    "strength",
    "guidance",
    "fps",
)
_KNOWN_NON_PARAMETER_KEYS = {
    "id",
    "name",
    "model",
    "modelId",
    "model_id",
    "displayName",
    "display_name",
    "description",
    "created",
    "createdAt",
    "updatedAt",
    "ownedBy",
    "owned_by",
    "object",
    "type",
    "provider",
    "providerId",
    "provider_id",
    "metadata",
    "capabilities",
    "video",
    # Legacy normalized capability names are already projected into the
    # public model shape and should not reappear as opaque provider fields.
    "input_modalities",
    "inputModalities",
    "sizeOptions",
    "size_slots",
    "sizeSlots",
    "sizeField",
    "size_field",
    "resolutionOptions",
    "resolution_options",
    "aspectRatios",
    "aspect_ratios",
    "durationRange",
    "duration_range",
    "minDuration",
    "min_duration",
    "maxDuration",
    "max_duration",
    "supportsAudio",
    "supports_audio",
    "nativeAudio",
    "native_audio",
    "referenceLimits",
    "reference_limits",
    "declaredCapabilities",
    "declared_capabilities",
    "verificationStatus",
    "verification_status",
    "verificationStage",
    "verification_stage",
    "source",
}

_CANONICAL_ALIASES: dict[str, str] = {
    "mode": "mode",
    "gen_mode": "mode",
    "genmode": "mode",
    "generation_mode": "mode",
    "generationmode": "mode",
    "generate_mode": "mode",
    "generatemode": "mode",
    "duration": "duration",
    "duration_seconds": "duration",
    "durationseconds": "duration",
    "seconds": "duration",
    "resolution": "resolution",
    "output_resolution": "resolution",
    "outputresolution": "resolution",
    "size": "size",
    "size_slot": "size",
    "sizeslot": "size",
    "aspect_ratio": "aspectRatio",
    "aspectratio": "aspectRatio",
    "ratio": "aspectRatio",
    "generate_audio": "generateAudio",
    "generateaudio": "generateAudio",
    "audio": "generateAudio",
    "output_format": "outputFormat",
    "outputformat": "outputFormat",
    "format": "outputFormat",
    "images": "images",
    "image": "images",
    "reference_images": "images",
    "referenceimages": "images",
    "first_frame": "firstFrame",
    "firstframe": "firstFrame",
    "first_frame_path": "firstFrame",
    "firstframepath": "firstFrame",
    "last_frame": "lastFrame",
    "lastframe": "lastFrame",
    "last_frame_path": "lastFrame",
    "lastframepath": "lastFrame",
    "videos": "videos",
    "reference_videos": "videos",
    "referencevideos": "videos",
    "audios": "audios",
    "reference_audios": "audios",
    "referenceaudios": "audios",
}

_COMMON_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "mode": ("mode", "gen_mode", "genMode", "generation_mode", "generationMode"),
    "duration": ("duration", "duration_seconds", "durationSeconds", "seconds"),
    "resolution": ("resolution", "output_resolution", "outputResolution", "video_resolution"),
    "size": ("size", "size_slot", "sizeSlot"),
    "aspectRatio": ("aspect_ratio", "aspectRatio", "ratio"),
    "generateAudio": ("generate_audio", "generateAudio", "audio"),
    "firstFrame": (
        "first_frame",
        "firstFrame",
        "first_frame_path",
        "firstFramePath",
    ),
    "lastFrame": (
        "last_frame",
        "lastFrame",
        "last_frame_path",
        "lastFramePath",
    ),
    "images": ("image", "images", "reference_images", "referenceImages"),
    "videos": ("videos", "reference_videos", "referenceVideos"),
    "audios": ("audios", "reference_audios", "referenceAudios"),
}


def canonical_parameter_key(value: object) -> str:
    """Return a stable UI key while retaining provider keys in the mapping."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    return _CANONICAL_ALIASES.get(raw.casefold(), _CANONICAL_ALIASES.get(compact, raw))


def _json_safe(value: object, *, depth: int = 0) -> object:
    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:100]]
    return str(value)


def _infer_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple, set)):
        return "array"
    return "string"


@dataclass(frozen=True, slots=True)
class VideoCapabilityParameter:
    key: str
    provider_key: str
    type: str = "string"
    required: bool = False
    default: object = None
    enum: tuple[object, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    label: str = ""
    description: str = ""
    advanced: bool = True
    source: str = "catalog"

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "providerKey": self.provider_key,
            "provider_key": self.provider_key,
            "type": self.type,
            "required": self.required,
            "advanced": self.advanced,
            "source": self.source,
        }
        if self.default is not None:
            result["default"] = _json_safe(self.default)
        if self.enum:
            result["enum"] = [_json_safe(item) for item in self.enum]
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.step is not None:
            result["step"] = self.step
        if self.label:
            result["label"] = self.label
        if self.description:
            result["description"] = self.description
        return result


def _metadata_sources(entry: Mapping[str, Any], capability: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [entry]
    for key in ("metadata", "capabilities", "video"):
        nested = entry.get(key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    # OpenAPI discovery stores sanitized operation summaries alongside the
    # catalog entry.  Their request-schema properties are provider metadata,
    # not a second execution path; include them so newly connected endpoints
    # get the same parameter envelope as model catalogs.
    for key in ("openapiOperations", "openapi_operations"):
        operations = entry.get(key)
        if isinstance(operations, list):
            sources.extend(
                item for item in operations if isinstance(item, Mapping)
            )
    # ``capability`` is the normalized legacy projection.  It contains fields
    # such as ``modes`` and ``durationRange`` that are already represented by
    # the public contract; treating it as a raw source would duplicate those
    # fields into ``opaque``.  Raw provider declarations are always read from
    # the entry and its metadata containers above.
    del capability
    return sources


def _parameter_definition(raw: object) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {"default": raw}


def _iter_schema_properties(
    schema: Mapping[str, Any], source: str
) -> Iterable[tuple[str, Mapping[str, Any], str]]:
    """Yield JSON-Schema properties without exposing schema bookkeeping keys."""
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return
    required_values = schema.get("required")
    required = {
        str(item).strip()
        for item in required_values
        if str(item).strip()
    } if isinstance(required_values, (list, tuple, set)) else set()
    for key, raw in properties.items():
        definition = dict(_parameter_definition(raw))
        if str(key) in required and "required" not in definition:
            definition["required"] = True
        yield str(key), definition, source


def _iter_parameter_definitions(sources: Iterable[Mapping[str, Any]]) -> Iterable[tuple[str, Mapping[str, Any], str]]:
    for source in sources:
        for container_key in _PARAMETER_CONTAINER_KEYS:
            container = source.get(container_key)
            if isinstance(container, Mapping):
                if container_key in {
                    "inputSchema",
                    "input_schema",
                    "requestSchema",
                    "request_schema",
                    "parameterSchema",
                    "parameter_schema",
                    "schema",
                } and isinstance(container.get("properties"), Mapping):
                    yield from _iter_schema_properties(container, container_key)
                    continue
                for key, raw in container.items():
                    yield str(key), _parameter_definition(raw), container_key
            elif isinstance(container, list):
                for raw in container:
                    if not isinstance(raw, Mapping):
                        continue
                    key = raw.get("key") or raw.get("name") or raw.get("id")
                    if key:
                        yield str(key), raw, container_key
        # Some providers publish common controls directly on the model entry.
        for key, raw in source.items():
            if key in _KNOWN_NON_PARAMETER_KEYS or _SENSITIVE_KEY.search(key):
                continue
            canonical = canonical_parameter_key(key)
            if canonical in {"mode", "duration", "resolution", "size", "aspectRatio", "generateAudio", "firstFrame", "lastFrame", "outputFormat", "images", "videos", "audios"}:
                yield key, {"default": raw}, "direct-field"


def _make_parameter(key: str, raw: Mapping[str, Any], source: str) -> VideoCapabilityParameter | None:
    provider_key = str(
        raw.get("providerKey")
        or raw.get("provider_key")
        or raw.get("providerField")
        or raw.get("provider_field")
        or raw.get("x-provider-key")
        or raw.get("x_provider_key")
        or raw.get("name")
        or key
    ).strip()
    canonical = canonical_parameter_key(key)
    if not canonical or not provider_key or _SENSITIVE_KEY.search(provider_key):
        return None
    enum_raw = raw.get("enum")
    if enum_raw is None:
        enum_raw = raw.get("options") or raw.get("values")
    enum = tuple(enum_raw) if isinstance(enum_raw, (list, tuple, set)) else ()
    default = raw.get("default")
    type_name = str(raw.get("type") or raw.get("valueType") or "").strip().lower()
    if type_name in {"int", "integer"}:
        type_name = "integer"
    elif type_name in {"float", "double", "decimal"}:
        type_name = "number"
    elif type_name not in {"string", "number", "integer", "boolean", "object", "array"}:
        type_name = _infer_type(default if default is not None else (enum[0] if enum else ""))

    def number(name: str) -> float | None:
        value = raw.get(name)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return VideoCapabilityParameter(
        key=canonical,
        provider_key=provider_key,
        type=type_name,
        required=bool(raw.get("required", False)),
        default=_json_safe(default),
        enum=tuple(_json_safe(item) for item in enum),
        minimum=number("minimum") if number("minimum") is not None else number("min"),
        maximum=number("maximum") if number("maximum") is not None else number("max"),
        step=number("step") or number("multipleOf"),
        label=str(raw.get("label") or raw.get("title") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        advanced=bool(raw.get("advanced", canonical not in {"mode", "duration", "resolution", "size", "aspectRatio", "generateAudio", "firstFrame", "lastFrame", "images", "videos", "audios"})),
        source=source,
    )


def build_video_capability_envelope(
    entry: Mapping[str, Any], *, capability: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build a JSON-safe envelope from a raw catalog entry and normalized capability."""

    sources = _metadata_sources(entry, capability)
    parameters: dict[str, VideoCapabilityParameter] = {}
    mapping: dict[str, str] = {}
    for source in sources:
        for mapping_key in ("providerMapping", "provider_mapping", "mapping"):
            raw_mapping = source.get(mapping_key)
            if not isinstance(raw_mapping, Mapping):
                continue
            for raw_key, raw_value in raw_mapping.items():
                canonical = canonical_parameter_key(raw_key)
                provider_key = str(raw_value or "").strip()
                if canonical and provider_key and not _SENSITIVE_KEY.search(provider_key):
                    mapping.setdefault(canonical, provider_key)
    for key, raw, source in _iter_parameter_definitions(sources):
        parameter = _make_parameter(key, raw, source)
        if parameter is None:
            continue
        # The first declaration wins, making output stable when a provider
        # mirrors the same schema under metadata/capabilities/video.
        parameters.setdefault(parameter.key, parameter)
        mapping.setdefault(parameter.key, parameter.provider_key)

    # An explicit provider mapping is authoritative over a schema-local
    # ``providerKey``. Keep the parameter descriptor and mapping synchronized
    # so the UI and transport expose the same field name.
    for key, provider_key in mapping.items():
        parameter = parameters.get(key)
        if parameter is not None and parameter.provider_key != provider_key:
            parameters[key] = replace(parameter, provider_key=provider_key)

    opaque: list[dict[str, object]] = []
    seen_opaque: set[str] = set()
    for source_index, source in enumerate(sources):
        for key, value in source.items():
            if key in _KNOWN_NON_PARAMETER_KEYS or _SENSITIVE_KEY.search(key):
                continue
            canonical = canonical_parameter_key(key)
            if canonical in parameters or key in mapping.values():
                continue
            lowered = key.casefold()
            if not any(hint in lowered for hint in _CAPABILITY_KEY_HINTS):
                continue
            marker = f"{source_index}:{key}"
            if marker in seen_opaque:
                continue
            seen_opaque.add(marker)
            opaque.append({"key": key, "value": _json_safe(value), "source": "catalog"})

    media_inputs: list[dict[str, object]] = []
    for parameter in parameters.values():
        if parameter.key in {"images", "videos", "audios"}:
            media_inputs.append({"key": parameter.key, "providerKey": parameter.provider_key, "type": parameter.key[:-1]})

    return {
        "capabilityEnvelopeVersion": CAPABILITY_ENVELOPE_VERSION,
        "parameters": [item.to_dict() for item in parameters.values()],
        "providerMapping": mapping,
        "provider_mapping": mapping,
        "mapping": mapping,
        "mediaInputs": media_inputs,
        "media_inputs": media_inputs,
        "opaque": opaque,
    }


def compile_video_provider_parameters(
    values: Mapping[str, object] | None, mapping: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Map user-facing parameter keys to provider keys without dropping unknowns."""

    result: dict[str, object] = {}
    provider_mapping = {str(key): str(value) for key, value in (mapping or {}).items() if str(key).strip() and str(value).strip()}
    for key, value in (values or {}).items():
        raw_key = str(key).strip()
        if not raw_key or _SENSITIVE_KEY.search(raw_key):
            continue
        canonical = canonical_parameter_key(raw_key)
        provider_key = provider_mapping.get(canonical) or provider_mapping.get(raw_key) or raw_key
        if _SENSITIVE_KEY.search(provider_key):
            continue
        result[provider_key] = _json_safe(value)
    return result


def apply_video_common_parameter_mapping(
    container: Mapping[str, object] | dict[str, object],
    values: Mapping[str, object] | None,
    mapping: Mapping[str, object] | None,
) -> dict[str, object]:
    """Replace common canonical fields with their discovered provider names.

    The canvas owns friendly names such as ``duration`` and ``aspect_ratio``;
    providers are free to call those fields ``seconds`` or ``ratio``.  Apply
    only an explicit catalog mapping and remove the canonical aliases when a
    different provider key is selected, preventing duplicate/conflicting
    values in strict request validators.
    """

    result = dict(container)
    provider_mapping = {
        canonical_parameter_key(key): str(value).strip()
        for key, value in (mapping or {}).items()
        if canonical_parameter_key(key) and str(value).strip()
    }
    for raw_key, value in (values or {}).items():
        canonical = canonical_parameter_key(raw_key)
        if canonical not in _COMMON_PARAMETER_ALIASES:
            continue
        provider_key = provider_mapping.get(canonical, "")
        if not provider_key:
            continue
        lowered = provider_key.casefold()
        for prefix in ("input.", "payload.", "request."):
            if lowered.startswith(prefix):
                provider_key = provider_key[len(prefix) :].strip()
                break
        if not provider_key:
            continue
        for alias in _COMMON_PARAMETER_ALIASES[canonical]:
            result.pop(alias, None)
        result[provider_key] = _json_safe(value)
    return result


__all__ = [
    "CAPABILITY_ENVELOPE_VERSION",
    "VideoCapabilityParameter",
    "build_video_capability_envelope",
    "canonical_parameter_key",
    "apply_video_common_parameter_mapping",
    "compile_video_provider_parameters",
]
