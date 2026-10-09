"""Declarative video model capability catalog (not generator dispatch)."""

from .capabilities import (
    FallbackPolicy,
    Lifecycle,
    ModelCapability,
    NativeAudio,
    ReferenceLimits,
    VideoMode,
)
from .builtin_catalog import (
    NEWAPI_DISABLED_VIDEO_MODELS,
    NEWAPI_MAINLINE_SEEDANCE2_MODELS,
    NEWAPI_VIDEO_BACKEND_PREFIX,
    NEWAPI_VIDEO_DISPLAY_LABELS,
    PROMPT_HUBS_UPSTREAM_VIDEO_MODELS,
    NewApiVideoCatalogSnapshot,
    build_newapi_video_catalog,
    newapi_video_backend_options_from_catalog,
    normalize_newapi_video_model_id,
    resolve_newapi_video_upstream_model,
)
from .catalog import (
    SCHEMA_VERSION,
    DuplicateModelKeyError,
    UnknownVideoModelError,
    VideoModelCatalogError,
    VideoModelRegistry,
    VideoModelUnavailableError,
)
from .prompt_compiler import (
    CompiledVideoRequest,
    VideoRequestPreflightError,
    compile_video_request,
)
from .request import (
    ReferenceKind,
    ReferenceRole,
    VideoGenerationRequest,
    VideoReference,
)
from .video_provider_adapters import (
    VideoAdapterEvidence,
    VideoEndpointContract,
    VideoProtocolFamily,
    get_video_adapter_contract,
    infer_video_protocol_family,
)
from .generic_video_adapter import GenericVideoAdapterError, GenericVideoAdapterGenerator
from .runtime_contract import (
    GENERIC_VIDEO_PROTOCOL_FAMILIES,
    is_generic_video_adapter,
)

__all__ = [
    "CompiledVideoRequest",
    "DuplicateModelKeyError",
    "FallbackPolicy",
    "Lifecycle",
    "ModelCapability",
    "NEWAPI_DISABLED_VIDEO_MODELS",
    "NEWAPI_MAINLINE_SEEDANCE2_MODELS",
    "NEWAPI_VIDEO_BACKEND_PREFIX",
    "NEWAPI_VIDEO_DISPLAY_LABELS",
    "PROMPT_HUBS_UPSTREAM_VIDEO_MODELS",
    "NativeAudio",
    "NewApiVideoCatalogSnapshot",
    "ReferenceKind",
    "ReferenceLimits",
    "ReferenceRole",
    "SCHEMA_VERSION",
    "UnknownVideoModelError",
    "VideoGenerationRequest",
    "VideoMode",
    "VideoModelCatalogError",
    "VideoModelRegistry",
    "VideoModelUnavailableError",
    "VideoReference",
    "VideoAdapterEvidence",
    "VideoEndpointContract",
    "VideoProtocolFamily",
    "VideoRequestPreflightError",
    "build_newapi_video_catalog",
    "compile_video_request",
    "newapi_video_backend_options_from_catalog",
    "normalize_newapi_video_model_id",
    "resolve_newapi_video_upstream_model",
    "get_video_adapter_contract",
    "infer_video_protocol_family",
    "GenericVideoAdapterError",
    "GenericVideoAdapterGenerator",
    "GENERIC_VIDEO_PROTOCOL_FAMILIES",
    "is_generic_video_adapter",
]
