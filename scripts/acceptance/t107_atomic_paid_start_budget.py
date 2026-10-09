from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from novelvideo.workflow_runtime.definitions import (  # noqa: E402
    get_workflow_definition,
)
from novelvideo.workflow_runtime.paid_media_budget import (  # noqa: E402
    reserve_workflow_paid_start,
)
from novelvideo.workflow_runtime.production_authorization import (  # noqa: E402
    PRODUCTION_AUTHORIZATION_SCHEMA,
)
from novelvideo.workflow_runtime.step_contract import (  # noqa: E402
    WorkflowStepExecutionError,
)
from novelvideo.workflow_runtime.store import (  # noqa: E402
    WorkflowRunConflictError,
    WorkflowRunStore,
)


T106_DATABASE = (
    ROOT
    / "workspace"
    / "ui-smoke-t106"
    / "state"
    / "local"
    / "ui_smoke_t091"
    / "workflow_runs.db"
)
EVIDENCE_PATH = (
    ROOT
    / "workspace"
    / "artifacts"
    / "t107-atomic-paid-start-budget"
    / "evidence.json"
)


def _authorization(max_paid_starts: int) -> dict[str, Any]:
    return {
        "schema": PRODUCTION_AUTHORIZATION_SCHEMA,
        "scope": "workflow_run",
        "workflow_id": "freezone-final-film",
        "project_id": "t107-project",
        "canvas_id": "t107-canvas",
        "source_turn_id": "t107-turn",
        "source": "server_turn_grant",
        "max_paid_starts": max_paid_starts,
        "max_shots": 4,
        "max_reference_images": 36,
        "max_duration_seconds": 60,
        "allow_final_film": True,
    }


async def _create_run(
    store: WorkflowRunStore,
    *,
    max_paid_starts: int,
    idempotency_key: str,
) -> dict[str, Any]:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    run, reused = await store.create(
        definition=definition,
        project_id="t107-project",
        canvas_id="t107-canvas",
        run_mode="auto",
        inputs={
            "request": "T-107 atomic paid start budget",
            "auto_generate_paid_media": True,
            "production_authorization": _authorization(max_paid_starts),
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        source_turn_id="t107-turn",
    )
    assert reused is False
    return run


class _SubmitSpy:
    def __init__(self) -> None:
        self.submissions: list[dict[str, str]] = []

    async def reserve_then_submit(
        self,
        store: WorkflowRunStore,
        run: dict[str, Any],
        *,
        step_id: str,
        item_id: str,
        provider_kind: str,
    ) -> tuple[bool, dict[str, Any] | WorkflowStepExecutionError]:
        try:
            reservation = await reserve_workflow_paid_start(
                run,
                state_dir=store.state_dir,
                step_id=step_id,
                item_id=item_id,
                provider_kind=provider_kind,
            )
        except WorkflowStepExecutionError as exc:
            return False, exc
        self.submissions.append(
            {
                "step_id": step_id,
                "item_id": item_id,
                "provider_kind": provider_kind,
                "ordinal": str(reservation["reservation"]["ordinal"]),
            }
        )
        return True, reservation


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_t106_film_budget() -> dict[str, Any]:
    if not T106_DATABASE.is_file():
        raise RuntimeError(
            "T-106 evidence is missing; run "
            "scripts/acceptance/t106_one_turn_one_authorization_browser_film.py"
        )
    connection = sqlite3.connect(f"file:{T106_DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            """
            SELECT id, status, inputs_json, artifacts_json
              FROM canvas_workflow_runs
             ORDER BY created_at DESC
             LIMIT 1
            """
        ).fetchone()
        if run is None:
            raise RuntimeError("T-106 workflow run is missing")
        starts = connection.execute(
            """
            SELECT step_id, item_id, provider_kind, ordinal
              FROM canvas_workflow_paid_starts
             WHERE run_id=?
             ORDER BY ordinal ASC
            """,
            (str(run["id"]),),
        ).fetchall()
    finally:
        connection.close()

    inputs = json.loads(str(run["inputs_json"] or "{}"))
    artifacts = json.loads(str(run["artifacts_json"] or "{}"))
    authorization = (
        inputs.get("production_authorization")
        if isinstance(inputs.get("production_authorization"), dict)
        else {}
    )
    final_film = (
        artifacts.get("final_film")
        if isinstance(artifacts.get("final_film"), dict)
        else {}
    )
    final_artifact = (
        final_film.get("final_compose_artifact")
        if isinstance(final_film.get("final_compose_artifact"), dict)
        else {}
    )
    final_path = Path(str(final_artifact.get("path") or ""))
    ordinals = [int(row["ordinal"]) for row in starts]
    expected_steps = ["storyboard_images", "storyboard_images", "shot_videos"]
    if (
        str(run["status"]) != "completed"
        or int(authorization.get("max_paid_starts") or 0) != 4
        or len(starts) != 3
        or ordinals != [1, 2, 3]
        or [str(row["step_id"]) for row in starts] != expected_steps
        or len({str(row["item_id"]) for row in starts}) != 3
        or str(final_film.get("status") or "") != "completed"
        or not final_path.is_file()
        or _sha256(final_path) != str(final_artifact.get("sha256") or "")
    ):
        raise RuntimeError("T-106 end-to-end paid-start budget evidence is invalid")
    return {
        "run_id": str(run["id"]),
        "status": str(run["status"]),
        "max_paid_starts": int(authorization["max_paid_starts"]),
        "used_paid_starts": len(starts),
        "ordinals": ordinals,
        "steps": [str(row["step_id"]) for row in starts],
        "final_artifact_sha256": str(final_artifact["sha256"]),
        "final_artifact_bytes": final_path.stat().st_size,
    }


async def _run_acceptance() -> dict[str, Any]:
    workspace = ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="t107-budget-", dir=workspace) as raw:
        state_dir = Path(raw)
        store = WorkflowRunStore(state_dir)
        run = await _create_run(
            store,
            max_paid_starts=2,
            idempotency_key="t107-atomic-budget",
        )

        results = await asyncio.gather(
            *(
                store.reserve_paid_start(
                    run["id"],
                    step_id="storyboard_images",
                    item_id=f"batch-{index}",
                    provider_kind="image",
                )
                for index in range(6)
            ),
            return_exceptions=True,
        )
        allowed = [item for item in results if isinstance(item, dict)]
        denied = [
            item
            for item in results
            if isinstance(item, WorkflowRunConflictError)
        ]
        if len(allowed) != 2 or len(denied) != 4:
            raise RuntimeError("atomic concurrency cap did not allow exactly two starts")
        if {item.code for item in denied} != {
            "workflow_paid_start_budget_exceeded"
        }:
            raise RuntimeError("concurrent over-limit rejection used the wrong error")

        replay = await asyncio.gather(
            *(
                store.reserve_paid_start(
                    run["id"],
                    step_id="storyboard_images",
                    item_id=str(allowed[0]["reservation"]["item_id"]),
                    provider_kind="image",
                )
                for _ in range(12)
            ),
            return_exceptions=True,
        )
        if not all(
            isinstance(item, dict) and item.get("reused") is True
            for item in replay
        ):
            raise RuntimeError("same-batch replay did not reuse its reservation")

        restarted = await WorkflowRunStore(state_dir).reserve_paid_start(
            run["id"],
            step_id="storyboard_images",
            item_id=str(allowed[1]["reservation"]["item_id"]),
            provider_kind="image",
        )
        starts = await store.list_paid_starts(run["id"])
        if (
            restarted.get("reused") is not True
            or restarted.get("used") != 2
            or len(starts) != 2
            or [int(item["ordinal"]) for item in starts] != [1, 2]
        ):
            raise RuntimeError("durable restart did not preserve the reservation")

        spy = _SubmitSpy()
        accepted, rejected = await spy.reserve_then_submit(
            restarted_store := WorkflowRunStore(state_dir),
            run,
            step_id="shot_videos",
            item_id="shot_videos:batch-1",
            provider_kind="video",
        )
        if (
            accepted
            or not isinstance(rejected, WorkflowStepExecutionError)
            or rejected.code != "workflow_paid_start_budget_exceeded"
            or rejected.details.get("media_submission_started") is not False
            or spy.submissions
        ):
            raise RuntimeError(
                "over-limit reservation reached a task/provider submission side effect"
            )

        legal_store = WorkflowRunStore(state_dir / "legal")
        legal_run = await _create_run(
            legal_store,
            max_paid_starts=4,
            idempotency_key="t107-legal-film",
        )
        legal_spy = _SubmitSpy()
        legal_batches = (
            ("storyboard_images", "storyboard_images:asset_references:signature", "image"),
            ("storyboard_images", "storyboard_images:storyboard_images:0", "image"),
            ("shot_videos", "shot_videos:shot_videos:0", "video"),
        )
        for step_id, item_id, provider_kind in legal_batches:
            legal_ok, legal_result = await legal_spy.reserve_then_submit(
                legal_store,
                legal_run,
                step_id=step_id,
                item_id=item_id,
                provider_kind=provider_kind,
            )
            if not legal_ok or isinstance(legal_result, WorkflowStepExecutionError):
                raise RuntimeError("legal film batch was rejected inside its budget")
        legal_starts = await legal_store.list_paid_starts(legal_run["id"])

    return {
        "schema": "t107_atomic_paid_start_acceptance.v1",
        "concurrent_candidates": 6,
        "concurrent_allowed": 2,
        "concurrent_rejected": 4,
        "same_batch_replays": 12,
        "same_batch_reused": True,
        "restart_reused": True,
        "over_limit_task_submissions": len(spy.submissions),
        "over_limit_media_submission_started": rejected.details.get(
            "media_submission_started"
        ),
        "legal_batch_starts": len(legal_starts),
        "legal_ordinals": [int(item["ordinal"]) for item in legal_starts],
        "legal_task_submissions": len(legal_spy.submissions),
        "t106_film": _verify_t106_film_budget(),
    }


def main() -> int:
    evidence = asyncio.run(_run_acceptance())
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
