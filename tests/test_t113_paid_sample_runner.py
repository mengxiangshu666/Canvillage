from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "scripts" / "acceptance"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_module(
    "t113_paid_sample_runner",
    ACCEPTANCE / "t113_paid_sample_runner.py",
)
contract = _load_module(
    "t113_paid_sample_contract_for_runner",
    ACCEPTANCE / "t113_single_shot_paid_l3.py",
)
hollywood_fixture = _load_module(
    "hollywood_60s_fixture",
    ACCEPTANCE / "hollywood_60s_fixture.py",
)


def _valid_run() -> dict[str, Any]:
    return {
        "id": "wfr_paid_l3",
        "workflow_id": "freezone-final-film",
        "status": "completed",
        "artifacts": {},
        "inputs": {
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "quality": "low",
            "video_resolution": "768p",
            "video_duration_seconds": 5,
            "output_resolution": "1366x768",
        },
    }


def _valid_task(
    *,
    task_id: str,
    task_type: str,
    job_id: str,
    media_kind: str,
    provider_task_id: str = "",
) -> dict[str, Any]:
    task_key = f"task:{task_type}:project:project-1:0:{job_id}"
    result = {"job_id": job_id}
    if provider_task_id:
        result["provider_task_id"] = provider_task_id
    cost_receipt = {
        "schema": "production_cost_receipt.v1",
        "project_id": "project-1",
        "task_id": task_id,
        "media_kind": media_kind,
        "quantity": 1,
        "started_at": "2026-09-19T00:00:00Z",
        "completed_at": "2026-09-19T00:00:01Z",
        "duration_ms": 1000,
        "result_status": "completed",
        "retry_count": 0,
    }
    return {
        "task_id": task_id,
        "task_type": task_type,
        "job_id": job_id,
        "task_key": task_key,
        "status": "completed",
        "result": result,
        "task_acceptance_receipt": {
            "schema": "task_acceptance_receipt.v1",
            "receipt_id": f"accept:{task_id}",
            "task_id": task_id,
            "task_key": task_key,
            "task_type": task_type,
            "project_id": "project-1",
            "status": "accepted",
            "run_id": "wfr_paid_l3",
        },
        "production_cost_receipt": cost_receipt,
        "metadata": {
            "provider_task_id": provider_task_id,
            "task_acceptance_receipt": {
                "schema": "task_acceptance_receipt.v1",
                "receipt_id": f"accept:{task_id}",
                "task_id": task_id,
                "task_key": task_key,
                "task_type": task_type,
                "project_id": "project-1",
                "status": "accepted",
                "run_id": "wfr_paid_l3",
            },
            "production_cost_receipt": {
                **cost_receipt,
                "actual_cost": {},
                "wasted_cost": {},
            },
        },
    }


def _valid_browser_evidence() -> dict[str, Any]:
    return {
        "task_submissions": [
            _valid_task(
                task_id="image-task",
                task_type="freezone_gen",
                job_id="image",
                media_kind="image",
            ),
            _valid_task(
                task_id="video-task",
                task_type="freezone_video_gen",
                job_id="video",
                media_kind="video",
                provider_task_id="provider-video-1",
            ),
            {"task_type": "compose_episode", "job_id": ""},
        ],
        "browser_errors": [],
        "workflow_canvas_writes": [],
        "upstream_media_requests": [
            {
                "method": "POST",
                "path": "/v1/images/generations",
                "model": "gpt-image-2.5-sunburst",
                "aspect_ratio": "16:9",
                "size": "1024x576",
                "image_size": "1K",
            },
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "model": "MiniMax-H3",
                "resolution": "768P",
                "ratio": "adaptive",
                "duration": 5,
                "content_roles": ["first_frame"],
                "media_urls": [
                    {
                        "type": "image_url",
                        "role": "first_frame",
                        "scheme": "https",
                        "host": "res.cloudinary.com",
                    }
                ],
            },
            {
                "method": "GET",
                "path": "/v2/query/video_generation/t112-local-video-1",
            },
        ],
    }


def test_authorization_mismatch_stops_before_provider_config_or_servers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_capture(_state_dir: Path) -> dict[str, Any]:
        raise AssertionError("provider config must not be read without authorization")

    monkeypatch.setattr(runner, "capture_provider_config", fail_capture)

    report = runner.run_paid_sample(
        authorization="not the approved phrase",
        state_dir=tmp_path,
    )

    assert report["ok"] is False
    assert report["providerCallsStarted"] is False
    assert report["paidProvidersConnected"] is False
    assert report["blockingReasons"] == ["t113_authorization_phrase_mismatch"]


def test_exact_authorization_still_stops_when_runner_preflight_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_capture(_state_dir: Path) -> dict[str, Any]:
        raise AssertionError("provider config must not be read before runner preflight")

    monkeypatch.setattr(runner, "capture_provider_config", fail_capture)
    monkeypatch.setattr(
        runner,
        "build_runner_preflight",
        lambda: {
            "ok": False,
            "blockingReasons": ["runner_preflight_failed:chrome"],
        },
    )

    report = runner.run_paid_sample(
        authorization=contract.AUTHORIZATION_PHRASE,
        state_dir=tmp_path,
    )

    assert report["ok"] is False
    assert report["providerCallsStarted"] is False
    assert report["blockingReasons"] == ["runner_preflight_failed:chrome"]


def test_runner_preflight_does_not_require_retired_hermes_cli(
    monkeypatch,
) -> None:
    path_type = type(runner.CHROME)
    monkeypatch.setattr(path_type, "is_file", lambda self: True)
    monkeypatch.setattr(
        path_type,
        "is_dir",
        lambda self: True,
    )
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"C:/tools/{name}.exe")

    report = runner.build_runner_preflight()

    assert report["ok"] is True
    assert "hermes_cli" not in report["failedChecks"]
    assert "hermes_cli" not in {item["name"] for item in report["checks"]}
    assert report["blockingReasons"] == ["t113_paid_authorization_required"]


def test_t113_owns_the_retired_t100_script_fixture() -> None:
    row = runner.build_t113_script_row(runner.build_t113_base_script_row())

    assert row["shot_no"] == 1
    assert row["duration"] == 5
    assert row["character_1"] == "无"
    assert row["scene_tags"] == "无"
    assert "[时长：5s]" in row["video_motion_prompt"]


def test_hollywood_fixture_is_a_valid_twelve_shot_sixty_second_contract() -> None:
    from novelvideo.freezone.script_contract import validate_script_rows

    base = runner.build_t113_script_row(runner.build_t113_base_script_row())
    rows = hollywood_fixture.build_hollywood_60_rows(base)
    report = validate_script_rows(rows).as_dict()

    assert len(rows) == 12
    assert sum(int(row["duration"]) for row in rows) == 60
    assert report["blocking_count"] == 0


def test_execution_validation_accepts_one_image_and_one_video_task() -> None:
    validation = runner._validate_execution(
        browser_evidence=_valid_browser_evidence(),
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is True
    assert validation["violations"] == []
    assert validation["textRequests"] == 3
    assert validation["taskTypes"] == [
        "freezone_gen",
        "freezone_video_gen",
        "compose_episode",
    ]
    assert validation["mediaRequests"]["image_start_paths"] == [
        "/v1/images/generations"
    ]
    assert validation["mediaRequests"]["video_start_paths"] == [
        "/v2/video_generation"
    ]


def test_run_budget_delta_excludes_prior_authorized_samples() -> None:
    delta = runner._budget_counter_delta(
        {
            "counters": {
                "textRequests": 7,
                "imageTaskStarts": 3,
                "videoTaskStarts": 12,
            }
        },
        {
            "counters": {
                "textRequests": 12,
                "imageTaskStarts": 4,
                "videoTaskStarts": 13,
            }
        },
    )

    assert delta["counters"] == {
        "textRequests": 5,
        "imageTaskStarts": 1,
        "videoTaskStarts": 1,
    }


def test_seed_t113_models_persists_real_chat_runtime_probe(
    monkeypatch,
) -> None:
    from novelvideo.generators import direct_model_capability_cache
    from novelvideo.generators import direct_model_probe
    from novelvideo.generators.video import direct_video_capability_cache
    from novelvideo import model_gateway_settings

    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        model_gateway_settings,
        "save_direct_models",
        lambda kind, rows, **kwargs: calls.append(
            ("save_direct_models", {"kind": kind, "rows": rows, **kwargs})
        ),
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "save_direct_video_models",
        lambda rows, **kwargs: calls.append(
            ("save_direct_video_models", {"rows": rows, **kwargs})
        ),
    )
    monkeypatch.setattr(
        direct_model_probe,
        "probe_direct_model_endpoint",
        lambda **kwargs: calls.append(("probe", kwargs))
        or {
            "ok": True,
            "protocol": "openai-compatible",
            "probeContractVersion": 1,
            "verificationStatus": "runtime-verified",
            "detectedProtocol": "openai-compatible",
            "modelFound": True,
            "discoveredModelCount": 2,
            "chatProbeStatus": "passed",
            "chatHttpStatus": 200,
            "chatFirstTokenLatencyMs": 12,
            "chatResponseUsable": True,
            "streamProbeStatus": "passed",
            "streamHttpStatus": 200,
            "streamFirstEventLatencyMs": 10,
            "streamResponseUsable": True,
        },
    )
    monkeypatch.setattr(
        direct_model_capability_cache,
        "record_direct_model_capability",
        lambda **kwargs: calls.append(("record_chat", kwargs)) or kwargs,
    )
    monkeypatch.setattr(
        direct_video_capability_cache,
        "record_capability",
        lambda **kwargs: calls.append(("record_video", kwargs)) or kwargs,
    )

    cluster = SimpleNamespace(
        text=SimpleNamespace(base_url="http://127.0.0.1:41001"),
        image=SimpleNamespace(base_url="http://127.0.0.1:41002"),
        video=SimpleNamespace(base_url="http://127.0.0.1:41003"),
    )
    configs = {
        "text": {
            "modelId": "deepseek-flash",
            "baseUrl": "https://text.example/v1",
            "apiKey": "text-key",
        },
        "image": {
            "modelId": "gpt-image-2.5-sunburst",
            "baseUrl": "https://image.example/v1",
            "apiKey": "image-key",
        },
        "video": {
            "modelId": "MiniMax-H3",
            "baseUrl": "https://video.example/v1",
            "apiKey": "video-key",
        },
    }

    result = runner._seed_t113_models(cluster, configs)

    probe_call = next(payload for name, payload in calls if name == "probe")
    assert probe_call["base_url"] == "https://text.example/v1"
    assert probe_call["kind"] == "chat"
    save_chat_call = next(
        payload
        for name, payload in calls
        if name == "save_direct_models" and payload["kind"] == "chat"
    )
    assert save_chat_call["rows"][0]["baseUrl"] == "https://text.example/v1"
    record_call = next(payload for name, payload in calls if name == "record_chat")
    assert record_call["base_url"] == "https://text.example/v1"
    assert record_call["capability"]["chatProbeStatus"] == "passed"
    assert record_call["capability"]["streamProbeStatus"] == "passed"
    assert record_call["capability"]["chatResponseUsable"] is True
    assert record_call["capability"]["streamResponseUsable"] is True
    assert result["streamProbeStatus"] == "passed"


def test_execution_validation_accepts_provider_compiled_image_dimensions() -> None:
    evidence = _valid_browser_evidence()
    image_request = evidence["upstream_media_requests"][0]
    image_request.pop("aspect_ratio")
    image_request.pop("image_size")
    image_request["size"] = "1088x608"

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is True
    assert validation["violations"] == []


def test_execution_validation_rejects_a_wrong_provider_image_ratio() -> None:
    evidence = _valid_browser_evidence()
    image_request = evidence["upstream_media_requests"][0]
    image_request.pop("aspect_ratio")
    image_request.pop("image_size")
    image_request["size"] = "1024x1024"

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert "image_provider_aspect_ratio" in validation["violations"]


def test_execution_validation_rejects_non_https_first_frame() -> None:
    evidence = _valid_browser_evidence()
    evidence["upstream_media_requests"][1]["media_urls"][0]["scheme"] = "http"

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert "video_provider_first_frame_not_https" in validation["violations"]


def test_execution_validation_rejects_budget_and_duplicate_media_starts() -> None:
    evidence = _valid_browser_evidence()
    evidence["task_submissions"] = [
        _valid_task(
            task_id="image-task-1",
            task_type="freezone_gen",
            job_id="image-1",
            media_kind="image",
        ),
        _valid_task(
            task_id="image-task-2",
            task_type="freezone_gen",
            job_id="image-2",
            media_kind="image",
        ),
        _valid_task(
            task_id="video-task",
            task_type="freezone_video_gen",
            job_id="video",
            media_kind="video",
            provider_task_id="provider-video-1",
        ),
        {"task_type": "compose_episode", "job_id": ""},
    ]
    evidence["browser_errors"] = ["boom"]

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 0,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert validation["violations"] == [
        "text_request_budget",
        "image_task_start_count",
        "browser_errors",
    ]


def test_execution_validation_rejects_missing_task_acceptance_receipt() -> None:
    evidence = _valid_browser_evidence()
    evidence["task_submissions"][0].pop("task_acceptance_receipt")

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert "image_task_acceptance_receipt" in validation["violations"]


def test_execution_validation_requires_explicit_cost_fields() -> None:
    evidence = _valid_browser_evidence()
    evidence["task_submissions"][1]["metadata"]["production_cost_receipt"].pop(
        "actual_cost"
    )

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert "video_actual_cost_field" in validation["violations"]


def test_execution_validation_requires_video_provider_task_id() -> None:
    evidence = _valid_browser_evidence()
    evidence["task_submissions"][1]["result"].pop("provider_task_id")
    evidence["task_submissions"][1]["metadata"].pop("provider_task_id")

    validation = runner._validate_execution(
        browser_evidence=evidence,
        run=_valid_run(),
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        contract=contract,
    )

    assert validation["ok"] is False
    assert "video_provider_task_id" in validation["violations"]


def test_cleanup_failure_is_a_validation_violation() -> None:
    validation = {"ok": True, "violations": []}

    runner._apply_cleanup_violation(
        validation,
        {"remaining": True, "error": "PermissionError: in use"},
    )

    assert validation == {
        "ok": False,
        "violations": ["temp_site_cleanup_failed"],
    }


def test_video_start_failure_validation_proves_one_forwarded_attempt() -> None:
    validation = runner._validate_video_start_failure(
        adapter_exception=RuntimeError("workflow stopped"),
        run={
            "workflow_id": "freezone-final-film",
            "status": "failed",
            "error_code": "workflow_shot_video_failed",
            "next_action": "recover:retry_failed_items:shot_videos",
            "artifacts": {
                "shot_videos": {
                    "status": "failed",
                    "failed_items": [{"job_id": "video-1"}],
                }
            },
        },
        browser_failure_evidence={
            "schema": "t112_real_execution_adapter_failure.v1",
            "browserRequests": [{"request": "one structured turn"}],
        },
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        proxy_snapshot=[
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 502,
                "budgetReserved": True,
            }
        ],
        upstream_snapshot=[
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 502,
            }
        ],
        contract=contract,
    )

    assert validation["ok"] is True
    assert validation["violations"] == []
    assert validation["upstreamVideoStarts"] == 1
    assert validation["proxyForwardedVideoStarts"] == 1


def test_video_start_failure_validation_rejects_second_upstream_start() -> None:
    validation = runner._validate_video_start_failure(
        adapter_exception=RuntimeError("workflow stopped"),
        run={
            "workflow_id": "freezone-final-film",
            "status": "failed",
            "error_code": "workflow_shot_video_failed",
            "next_action": "recover:retry_failed_items:shot_videos",
            "artifacts": {
                "shot_videos": {
                    "status": "failed",
                    "failed_items": [{"job_id": "video-1"}],
                }
            },
        },
        browser_failure_evidence={
            "schema": "t112_real_execution_adapter_failure.v1",
            "browserRequests": [{"request": "one structured turn"}],
        },
        budget={
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 1,
            }
        },
        proxy_snapshot=[
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 502,
                "budgetReserved": True,
            }
        ],
        upstream_snapshot=[
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 502,
            },
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 200,
            },
        ],
        contract=contract,
    )

    assert validation["ok"] is False
    assert "upstream_video_start_count" in validation["violations"]


def _valid_recovery_validation_inputs() -> dict[str, Any]:
    run_id = "wfr_recovery"
    failed_item_id = "shot_videos:shot:1:abcdef123456"
    return {
        "browser_evidence": {
            "schema": "t112_real_execution_adapter_recovery_chain.v1",
            "recovery_chain": {
                "same_run_id": True,
                "failed_item_ids": [failed_item_id],
                "failed_run": {
                    "id": run_id,
                    "status": "failed",
                    "error_code": "workflow_shot_video_failed",
                    "next_action": "recover:retry_failed_items:shot_videos",
                },
            },
            "browser_structured_requests": [
                {},
                {
                    "workflow_runtime": {"workflow_run_id": run_id},
                    "task_authorization": {"max_paid_starts": 1},
                },
            ],
            "browser_errors": [],
            "workflow_canvas_writes": [],
        },
        "run": {
            "id": run_id,
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "workflow_id": "freezone-final-film",
            "status": "completed",
            "artifacts": {
                "shot_videos": {
                    "status": "completed",
                    "item_states": {
                        failed_item_id: {
                            "id": failed_item_id,
                            "status": "completed",
                        }
                    },
                    "videos": [{"item_id": failed_item_id}],
                }
            },
        },
        "final_film": {
            "sha256": "same",
            "declared_sha256": "same",
            "size_bytes": 1,
            "width": 640,
            "height": 360,
            "duration_seconds": 5.0,
        },
        "budget": {
            "counters": {
                "textRequests": 3,
                "imageTaskStarts": 1,
                "videoTaskStarts": 2,
            }
        },
        "contract": contract,
        "proxy_snapshot": [
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 502,
                "budgetReserved": True,
            },
            {
                "method": "POST",
                "path": "/v2/video_generation",
                "status": 200,
                "budgetReserved": True,
            },
        ],
        "upstream_snapshot": [
            {"method": "POST", "path": "/v2/video_generation", "status": 502},
            {"method": "POST", "path": "/v2/video_generation", "status": 200},
        ],
        "grant_state": {
            "use_count": 5,
            "grants": [
                {
                    "max_starts": 1,
                    "used_starts": 1,
                    "use_count": 1,
                    "project_id": "project-1",
                    "canvas_id": "canvas-1",
                },
                {
                    "max_starts": 4,
                    "used_starts": 4,
                    "use_count": 4,
                    "project_id": "project-1",
                    "canvas_id": "canvas-1",
                },
            ],
        },
    }


def test_recovery_validation_accepts_initial_and_exact_recovery_grants() -> None:
    validation = runner._validate_recovery_execution(
        **_valid_recovery_validation_inputs()
    )

    assert validation["ok"] is True
    assert validation["violations"] == []


def test_recovery_validation_rejects_more_than_one_recovery_start() -> None:
    inputs = _valid_recovery_validation_inputs()
    inputs["grant_state"]["grants"][0]["used_starts"] = 2
    inputs["grant_state"]["grants"][0]["use_count"] = 2
    inputs["grant_state"]["use_count"] = 6

    validation = runner._validate_recovery_execution(**inputs)

    assert validation["ok"] is False
    assert "paid_grant_consumption" in validation["violations"]


def test_media_relay_settings_copy_satisfies_runtime_settings_schema(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    connection = runner.sqlite3.connect(str(source))
    try:
        connection.execute(
            """
            CREATE TABLE runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO runtime_settings (key, value, updated_at) VALUES (?, ?, ?)",
            [
                ("media_relay_provider", "cloudinary", "2026-09-19T00:00:00Z"),
                (
                    "cloudinary_relay_cloud_name",
                    "village-canvas",
                    "2026-09-19T00:00:00Z",
                ),
                (
                    "cloudinary_relay_api_key",
                    "api-key",
                    "2026-09-19T00:00:00Z",
                ),
                (
                    "cloudinary_relay_api_secret",
                    "api-secret",
                    "2026-09-19T00:00:00Z",
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    result = runner._copy_production_media_relay_settings(
        tmp_path / "destination-state",
        source_database=source,
    )

    assert result["copied"] is True
    destination = tmp_path / "destination-state" / "local" / "settings.db"
    connection = runner.sqlite3.connect(str(destination))
    try:
        rows = connection.execute(
            "SELECT key, value, updated_at FROM runtime_settings ORDER BY key"
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 4
    assert all(row[2] for row in rows)


def test_paid_runner_returns_structured_failure_when_adapter_raises(
    monkeypatch,
    tmp_path: Path,
) -> None:
    real_load_module = runner._load_module

    def fake_load_module(name: str, path: Path):
        if name == "t112_real_execution_adapter_for_t113":
            return SimpleNamespace(
                T100=SimpleNamespace(_script_row=lambda: {}),
                _run_adapter=lambda **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("provider execution failed")
                ),
            )
        return real_load_module(name, path)

    monkeypatch.setattr(runner, "_load_module", fake_load_module)
    monkeypatch.setattr(
        runner,
        "build_runner_preflight",
        lambda: {"ok": True, "blockingReasons": []},
    )
    monkeypatch.setattr(
        runner,
        "capture_provider_config",
        lambda _state_dir: {
            role: {
                "baseUrl": f"https://{role}.invalid/v1",
                "apiKey": "test-key",
                "modelId": f"{role}-model",
            }
            for role in ("text", "image", "video")
        },
    )
    monkeypatch.setattr(runner, "_latest_run", lambda: {})
    monkeypatch.setattr(
        runner,
        "_safe_cleanup_temp_site",
        lambda: {
            "path": str(tmp_path),
            "removed": True,
            "remaining": False,
            "error": "",
        },
    )
    monkeypatch.setattr(
        runner,
        "production_media_relay_status",
        lambda _database: {
            "configured": True,
            "provider": "cloudinary",
            "ttlSeconds": 1800,
            "keyCount": 4,
        },
    )
    monkeypatch.setenv("T112_MAX_PAID_STARTS", "0")

    report = runner.run_paid_sample(
        authorization=contract.AUTHORIZATION_PHRASE,
        state_dir=tmp_path,
        artifact_dir=tmp_path / "artifacts",
    )

    assert report["ok"] is False
    assert report["paidProvidersConnected"] is True
    assert report["providerCallsStarted"] is False
    assert report["validation"]["violations"] == ["execution_exception"]
    assert report["failure"]["type"] == "RuntimeError"
    assert report["failure"]["message"] == "provider execution failed"
