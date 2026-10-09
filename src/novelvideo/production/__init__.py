"""Canonical production registry shared by the main pipeline and Freezone."""

from .registry import ProductionRegistry
from .metadata import (
    PRODUCTION_METADATA_KEY,
    PRODUCTION_METADATA_SCHEMA,
    default_production_metadata,
    normalize_production_metadata,
    stamp_production_metadata,
    validate_production_metadata,
)
from .shot_contract import (
    SHOT_CONTRACT_SCHEMA,
    build_shot_contract,
    decode_shot_contract,
    serialize_shot_contract,
    shot_contract_ready,
    validate_shot_contract,
)
from .cinematic_contract import (
    CINEMATIC_AUDIT_SCHEMA,
    CINEMATIC_CONTRACT_SCHEMA,
    CINEMATIC_QUALITY_GATES,
    CINEMATIC_REVISION_PREFIX,
    audit_cinematic_contracts,
    build_cinematic_contract,
    cinematic_prompt_lines,
    compute_cinematic_revision,
)
from .emotion_direction import (
    EMOTION_DIRECTION_REVISION_PREFIX,
    EMOTION_DIRECTION_SCHEMA,
    build_emotion_direction,
    compile_emotion_direction,
    validate_emotion_direction,
)
from .cost_receipt import (
    PRODUCTION_COST_RECEIPT_SCHEMA,
    attach_production_cost_receipt_assets,
    build_production_cost_receipt,
    finalize_production_cost_receipt,
    project_production_cost_receipt,
    summarize_production_cost_receipts,
    with_production_cost_receipt,
)
from .pipeline_contract import (
    PIPELINE_CONTRACT_REVISION_PREFIX,
    PIPELINE_CONTRACT_SCHEMA,
    PIPELINE_STAGES,
    ProductionPipelineContractError,
    ProductionStageSpec,
    compile_production_pipeline_contract,
    compute_pipeline_contract_revision,
    validate_production_pipeline_contract,
)
from .schemas import (
    CanvasProjectionCreate,
    ProductionEntityCreate,
    WorkVersionCreate,
)

# ``evidence_graph`` reaches ``novelvideo.workflow_runtime`` and with it the
# numpy/PIL-backed ``novelvideo.generators`` package.  Importing it eagerly here
# made every lightweight consumer pay that cost -- including
# ``novelvideo.production.asset_passport``, which the Hermes plugin imports on
# the first ``canvas.snapshot`` call inside a worker tool thread.  On Windows
# that first import wedged the worker for the whole 420 s tool deadline, so the
# two names are re-exported lazily instead (PEP 562).
_LAZY_EXPORTS = {
    "EVIDENCE_GRAPH_SCHEMA": ".evidence_graph",
    "build_production_evidence_graph": ".evidence_graph",
}


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)

__all__ = [
    "CanvasProjectionCreate",
    "ProductionEntityCreate",
    "ProductionRegistry",
    "WorkVersionCreate",
    "PRODUCTION_METADATA_KEY",
    "PRODUCTION_METADATA_SCHEMA",
    "default_production_metadata",
    "normalize_production_metadata",
    "stamp_production_metadata",
    "validate_production_metadata",
    "EVIDENCE_GRAPH_SCHEMA",
    "build_production_evidence_graph",
    "SHOT_CONTRACT_SCHEMA",
    "build_shot_contract",
    "decode_shot_contract",
    "serialize_shot_contract",
    "shot_contract_ready",
    "validate_shot_contract",
    "CINEMATIC_AUDIT_SCHEMA",
    "CINEMATIC_CONTRACT_SCHEMA",
    "CINEMATIC_QUALITY_GATES",
    "CINEMATIC_REVISION_PREFIX",
    "audit_cinematic_contracts",
    "build_cinematic_contract",
    "cinematic_prompt_lines",
    "compute_cinematic_revision",
    "EMOTION_DIRECTION_REVISION_PREFIX",
    "EMOTION_DIRECTION_SCHEMA",
    "build_emotion_direction",
    "compile_emotion_direction",
    "validate_emotion_direction",
    "PRODUCTION_COST_RECEIPT_SCHEMA",
    "attach_production_cost_receipt_assets",
    "build_production_cost_receipt",
    "finalize_production_cost_receipt",
    "project_production_cost_receipt",
    "summarize_production_cost_receipts",
    "with_production_cost_receipt",
    "PIPELINE_CONTRACT_REVISION_PREFIX",
    "PIPELINE_CONTRACT_SCHEMA",
    "PIPELINE_STAGES",
    "ProductionPipelineContractError",
    "ProductionStageSpec",
    "compile_production_pipeline_contract",
    "compute_pipeline_contract_revision",
    "validate_production_pipeline_contract",
]
