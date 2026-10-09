from novelvideo.workflow_runtime.definitions import (
    get_workflow_definition,
    list_workflow_definitions,
    validate_workflow_definition,
)
from novelvideo.workflow_runtime.execution_semantics import (
    WorkflowExecutionSemantics,
    semantics_from_state,
    validate_execution_semantics,
)
from novelvideo.workflow_runtime import executor as workflow_executor


def test_workflow_definitions_publish_explicit_execution_semantics():
    for definition in list_workflow_definitions():
        assert validate_workflow_definition(definition, workflow_executor.HANDLERS) == ()
        for step in definition.steps:
            semantics = step.execution_semantics
            assert semantics.to_dict()["protocol_version"] == "workflow.execution-semantics/v1"
            assert step.to_dict()["execution_semantics"]["failure_stage"]
    media = get_workflow_definition("one-click-film").steps[4].execution_semantics
    assert media.side_effect == "paid_generation"
    assert media.retry_safety == "idempotency_key_required"
    assert media.result_lookup == "provider_receipt"
    assert media.recovery_mode == "reconcile"


def test_paid_generation_contract_rejects_missing_reconciliation():
    errors = validate_execution_semantics(
        WorkflowExecutionSemantics(
            side_effect="paid_generation",
            retry_safety="idempotency_key_required",
            idempotency="runtime_node",
            result_lookup="idempotency_key",
            recovery_mode="reconcile",
        ),
        step_id="media",
    )
    assert "provider receipt" in " ".join(errors)


def test_legacy_state_gets_conservative_semantics():
    semantics = semantics_from_state({"writes_canvas": True, "execution_mode": "atomic"})
    assert semantics.retry_safety == "idempotency_key_required"
    assert semantics.recovery_mode == "reconcile"
    assert semantics.result_lookup == "idempotency_key"

