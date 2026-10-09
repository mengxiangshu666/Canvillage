from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "t113_single_shot_paid_l3.py"
PRODUCTION_RUNNER = (
    ROOT / "scripts" / "acceptance" / "t113_production_8784_runner.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("t113_paid_l3", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


t113 = _load_module()


def _load_production_runner():
    spec = importlib.util.spec_from_file_location(
        "t113_production_runner_for_preflight_test",
        PRODUCTION_RUNNER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


production_runner = _load_production_runner()


def _model(
    model_id: str,
    *,
    protocol: str = "",
    runtime_ready: bool = True,
    default: bool = False,
    model_id_value: str = "",
    label: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    payload = {
        "modelId": model_id,
        "enabled": True,
        "configured": True,
        "runtimeReady": runtime_ready,
    }
    if default:
        payload["default"] = True
    if model_id_value:
        payload["id"] = model_id_value
    if label:
        payload["label"] = label
    if base_url:
        payload["baseUrl"] = base_url
    if protocol:
        payload["protocol"] = protocol
    return payload


def _fetcher(
    *,
    text: dict[str, Any] | None = None,
    image: dict[str, Any] | None = None,
    video: dict[str, Any] | None = None,
):
    models = {
        "text": [
            text
            if text is not None
            else _model(t113.TEXT_MODEL)
        ],
        "image": [
            image
            if image is not None
            else _model(t113.IMAGE_MODEL)
        ],
    }
    video_models = [
        video
        if video is not None
        else _model(t113.VIDEO_MODEL, protocol=t113.VIDEO_PROTOCOL)
    ]

    def fetch(url: str, _timeout: float) -> dict[str, Any]:
        if url.endswith("/healthz"):
            return {"status": "ok"}
        if url.endswith("/version.json"):
            return {"version": "v-test", "buildId": "build-test"}
        if url.endswith("/api/v1/model-gateway/config"):
            return {
                "ok": True,
                "data": {
                    "directModels": models,
                    "directVideoModels": video_models,
                },
            }
        raise AssertionError(f"unexpected URL: {url}")

    return fetch


def test_exact_model_contract_passes_without_starting_work() -> None:
    report = t113.build_preflight(fetcher=_fetcher())

    assert t113.TEXT_MODEL == "deepseek-flash"
    assert report["schema"] == t113.SCHEMA
    assert report["ok"] is True
    assert report["environmentReady"] is True
    assert report["executionRunnerImplemented"] is True
    assert report["executionRunnerConnected"] is True
    assert report["executionRunnerReady"] is True
    assert report["paidProvidersConnected"] is False
    assert report["providerCallsStarted"] is False
    assert report["authorizationRequired"] is True
    assert report["readOnly"] is True
    assert report["failedChecks"] == []
    assert report["blockingReasons"] == ["t113_paid_authorization_required"]
    assert report["plan"]["shots"] == 1
    assert report["plan"]["durationSeconds"] == 5
    assert report["plan"]["providerStartLimits"] == {
        "textRequests": t113.TEXT_REQUEST_LIMIT,
        "imageTaskStarts": t113.IMAGE_TASK_START_LIMIT,
        "videoTaskStarts": t113.VIDEO_TASK_START_LIMIT,
    }
    assert report["plan"]["expectedPaidTaskStarts"] == {"image": 1, "video": 1}
    assert report["plan"]["textRequestsMayExceedOne"] is True
    assert report["plan"]["automaticProviderRetry"] is False


def test_preflight_uses_the_running_default_text_model() -> None:
    text_model = "deepseek/deepseek-v4.1-flash"
    report = t113.build_preflight(
        fetcher=_fetcher(
            text=_model(
                text_model,
                protocol="openai-compatible",
                default=True,
            )
        )
    )

    assert report["ok"] is True
    assert report["plan"]["textModel"] == text_model
    text_check = next(
        item for item in report["checks"] if item["name"] == "text_model"
    )
    assert text_check["ok"] is True
    assert text_check["detail"].startswith(f"model={text_model} ")


def test_wrong_video_protocol_fails_closed() -> None:
    report = t113.build_preflight(
        fetcher=_fetcher(
            video=_model(t113.VIDEO_MODEL, protocol="wrong-video-protocol")
        )
    )

    assert report["ok"] is False
    assert report["environmentReady"] is False
    assert report["failedChecks"] == ["video_model"]
    assert report["providerCallsStarted"] is False


def test_missing_runtime_model_fails_closed() -> None:
    report = t113.build_preflight(
        fetcher=_fetcher(image=_model(t113.IMAGE_MODEL, runtime_ready=False))
    )

    assert report["ok"] is False
    assert report["failedChecks"] == ["image_model"]


def test_allow_flag_without_exact_phrase_fails_before_runner(
    monkeypatch,
    tmp_path,
) -> None:
    original_build_preflight = t113.build_preflight
    monkeypatch.setattr(
        t113,
        "build_preflight",
        lambda **_kwargs: original_build_preflight(fetcher=_fetcher()),
    )
    monkeypatch.setattr(
        t113.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not start without exact authorization")
        ),
    )
    output = tmp_path / "preflight.json"

    exit_code = t113.main(
        [
            "--allow-paid-generation",
            "--authorization",
            "not approved",
            "--output",
            str(output),
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert report["paidGenerationAuthorized"] is False
    assert report["authorizationAccepted"] is False
    assert report["blockingReasons"][0] == "t113_authorization_phrase_mismatch"
    assert report["providerCallsStarted"] is False


def test_text_and_image_budgets_are_unbounded_but_video_stops_at_500() -> None:
    plan = t113.build_plan()

    text_allowed = t113.reserve_provider_start(
        plan, {"textRequests": 2345}, "textRequests"
    )
    image_allowed = t113.reserve_provider_start(
        plan, {"imageTaskStarts": 2345}, "imageTaskStarts"
    )
    video_allowed = t113.reserve_provider_start(
        plan,
        {"videoTaskStarts": t113.VIDEO_TASK_START_LIMIT - 1},
        "videoTaskStarts",
    )
    video_denied = t113.reserve_provider_start(
        plan,
        {"videoTaskStarts": t113.VIDEO_TASK_START_LIMIT},
        "videoTaskStarts",
    )

    assert text_allowed["ok"] is True
    assert text_allowed["limit"] is None
    assert text_allowed["next"] == 2346
    assert image_allowed["ok"] is True
    assert image_allowed["limit"] is None
    assert image_allowed["next"] == 2346
    assert video_allowed["ok"] is True
    assert video_allowed["next"] == t113.VIDEO_TASK_START_LIMIT
    assert video_denied == {
        "ok": False,
        "kind": "videoTaskStarts",
        "counterKey": "videoTaskStarts",
        "limit": t113.VIDEO_TASK_START_LIMIT,
        "current": t113.VIDEO_TASK_START_LIMIT,
        "next": t113.VIDEO_TASK_START_LIMIT,
        "reason": "provider_start_limit_reached",
        "providerCallsStarted": False,
    }


def test_single_sample_expectation_is_separate_from_the_cumulative_video_budget() -> None:
    plan = t113.build_plan()

    assert plan["expectedPaidTaskStarts"] == {"image": 1, "video": 1}
    assert plan["providerStartLimits"]["videoTaskStarts"] == 500
    assert plan["providerStartLimits"]["imageTaskStarts"] is None


def test_invalid_budget_inputs_fail_closed() -> None:
    plan = t113.build_plan()
    plan["providerStartLimits"]["textRequests"] = True

    invalid_limit = t113.reserve_provider_start(plan, {}, "textRequests")
    invalid_counter = t113.reserve_provider_start(
        t113.build_plan(),
        {"textRequests": -1},
        "textRequests",
    )
    unknown_kind = t113.reserve_provider_start(t113.build_plan(), {}, "audio")

    assert invalid_limit["ok"] is False
    assert invalid_limit["reason"] == "provider_start_limit_invalid"
    assert invalid_counter["ok"] is False
    assert invalid_counter["reason"] == "provider_start_counter_invalid"
    assert unknown_kind["ok"] is False
    assert unknown_kind["reason"] == "provider_start_limit_invalid"


def test_text_runtime_probe_is_one_shot_and_reuses_bound_credential() -> None:
    calls: list[tuple[str, dict[str, Any], float]] = []
    text_model = _model(
        t113.TEXT_MODEL,
        protocol="openai-compatible",
        runtime_ready=False,
        model_id_value="agent-test",
        label=t113.TEXT_MODEL,
        base_url="https://text.example/v1",
    )

    def poster(
        url: str,
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((url, body, timeout))
        return {
            "ok": True,
            "data": {
                "ok": True,
                "protocol": "openai-compatible",
                "capabilities": {
                    "runtimeReady": True,
                    "verificationStatus": "contract-resolved",
                },
            },
        }

    report = t113.probe_text_model_runtime(
        fetcher=_fetcher(text=text_model),
        poster=poster,
    )

    assert report["ok"] is True
    assert report["attempted"] is True
    assert report["requestCount"] == 1
    assert len(calls) == 1
    assert calls[0][0].endswith(
        "/api/v1/model-gateway/direct-models/text/probe"
    )
    assert calls[0][1] == {
        "id": "agent-test",
        "label": t113.TEXT_MODEL,
        "modelId": t113.TEXT_MODEL,
        "baseUrl": "https://text.example/v1",
        "protocol": "openai-compatible",
    }
    assert "apiKey" not in calls[0][1]


def test_text_runtime_probe_skips_an_already_ready_model() -> None:
    calls: list[dict[str, Any]] = []

    def poster(
        _url: str,
        body: dict[str, Any],
        _timeout: float,
    ) -> dict[str, Any]:
        calls.append(body)
        raise AssertionError("ready text model must not be probed again")

    report = t113.probe_text_model_runtime(
        fetcher=_fetcher(
            text=_model(
                t113.TEXT_MODEL,
                protocol="openai-compatible",
            )
        ),
        poster=poster,
    )

    assert report["ok"] is True
    assert report["attempted"] is False
    assert report["alreadyReady"] is True
    assert report["requestCount"] == 0
    assert calls == []


def test_text_runtime_probe_transport_failure_is_not_retried() -> None:
    calls: list[dict[str, Any]] = []

    def poster(
        _url: str,
        body: dict[str, Any],
        _timeout: float,
    ) -> dict[str, Any]:
        calls.append(body)
        raise TimeoutError("probe timed out")

    report = t113.probe_text_model_runtime(
        fetcher=_fetcher(
            text=_model(
                t113.TEXT_MODEL,
                protocol="openai-compatible",
                runtime_ready=False,
                model_id_value="agent-test",
                label=t113.TEXT_MODEL,
                base_url="https://text.example/v1",
            )
        ),
        poster=poster,
    )

    assert report["ok"] is False
    assert report["attempted"] is True
    assert report["requestCount"] == 1
    assert report["reason"] == "text_runtime_probe_failed"
    assert "TimeoutError" in report["error"]
    assert len(calls) == 1


def test_production_runner_probes_text_before_preflight(
    monkeypatch,
    tmp_path,
) -> None:
    probe_calls: list[dict[str, Any]] = []
    fake_contract = SimpleNamespace(
        AUTHORIZATION_PHRASE=t113.AUTHORIZATION_PHRASE,
        probe_text_model_runtime=lambda **kwargs: (
            probe_calls.append(kwargs)
            or {
                "ok": False,
                "attempted": True,
                "requestCount": 1,
                "reason": "text_runtime_probe_failed",
            }
        ),
    )
    monkeypatch.setattr(
        production_runner,
        "_load_module",
        lambda _name, _path: fake_contract,
    )
    monkeypatch.setattr(
        production_runner,
        "build_preflight",
        lambda: (_ for _ in ()).throw(
            AssertionError("preflight must wait for the text probe")
        ),
    )

    report = production_runner.run_production_sample(
        authorization=t113.AUTHORIZATION_PHRASE,
        artifact_dir=tmp_path,
    )

    assert len(probe_calls) == 1
    assert probe_calls[0]["api_base"] == production_runner.PRODUCTION_ROOT
    assert report["ok"] is False
    assert report["providerCallsStarted"] is True
    assert report["blockingReasons"] == ["t113_text_runtime_probe_failed"]
