from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.workflow_runtime.production_plan import build_production_plan
from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
)
from novelvideo.workflow_runtime.store import WorkflowRunStore


def script_contract_payload(
    *,
    rows: list[Mapping[str, Any]] | None = None,
    result_signature: str = "b" * 64,
) -> dict[str, Any]:
    """Build the minimal completed script fact needed by plan fixtures."""

    normalized_rows = [
        dict(row)
        for row in (
            rows
            or [
                {
                    "shot_id": "shot-1",
                    "shot_no": 1,
                    "duration": 4,
                    "shot": "中景",
                    "shot_prompt": "测试镜头一，电影感。",
                    "video_motion_prompt": "镜头缓慢推进。",
                }
            ]
        )
    ]
    return {
        "schema": "workflow_freezone_script_artifact.v1",
        "kind": "freezone_script_contract",
        "status": "completed",
        "rows": normalized_rows,
        "asset_ledger": build_script_asset_ledger(normalized_rows),
        "contract_report": {
            "rows_fingerprint": "a" * 64,
            "blocking_count": 0,
        },
        "result_signature": result_signature,
    }


def install_production_plan(
    run: dict[str, Any],
    *,
    script_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a valid script and matching plan to an in-memory run."""

    artifacts = run.setdefault("artifacts", {})
    artifacts["script_contract"] = (
        dict(script_payload)
        if isinstance(script_payload, Mapping)
        else script_contract_payload()
    )
    plan = build_production_plan(run)
    artifacts["production_plan"] = plan
    return run


async def complete_script_and_production_plan(
    store: WorkflowRunStore,
    run: dict[str, Any],
    *,
    prefix: str,
    script_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance a persisted run through script and the zero-cost plan stage."""

    payload = (
        dict(script_payload)
        if isinstance(script_payload, Mapping)
        else script_contract_payload()
    )
    for step_id, step_payload in (
        ("script_contract", None),
        ("script_contract", payload),
        ("production_plan", None),
        ("production_plan", build_production_plan(
            {
                **run,
                "artifacts": {
                    **run.get("artifacts", {}),
                    "script_contract": payload,
                },
            }
        )),
    ):
        event_type = "step_started" if step_payload is None else "step_completed"
        run, applied = await store.record_event(
            run["id"],
            event_id=f"{prefix}:{step_id}:{event_type}",
            event_type=event_type,
            step_id=step_id,
            success=True if event_type == "step_completed" else None,
            payload=step_payload,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
    return run
