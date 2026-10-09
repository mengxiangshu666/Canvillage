"""Preflight or explicitly execute the one-shot T-113 paid L3 contract.

This entry point is intentionally inert by default. It reads the running API
and model catalog, verifies the exact T-113 models, and stops before creating a
project, canvas, WorkflowRun, or provider task.

Passing ``--allow-paid-generation`` is not sufficient by itself: the exact
``--authorization`` phrase must also be present before the real runner starts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_BASE = "http://127.0.0.1:8784"
DEFAULT_ARTIFACT = (
    ROOT / "workspace" / "artifacts" / "t113-single-shot-paid-l3" / "preflight.json"
)
DEFAULT_EXECUTION_ARTIFACT = (
    ROOT / "workspace" / "artifacts" / "t113-single-shot-paid-l3" / "execution.json"
)
RUNNER = ROOT / "scripts" / "acceptance" / "t113_paid_sample_runner.py"
PRODUCTION_RUNNER = (
    ROOT / "scripts" / "acceptance" / "t113_production_8784_runner.py"
)
SCHEMA = "t113_single_shot_paid_l3_preflight.v1"
AUTHORIZATION_PHRASE = (
    "批准 T-113：一个结构化回合；文本和图片按需使用；"
    "视频生成总数最多 500 次；禁止自动重试；"
    "临时项目 / 画布用完清理。同时允许启动本地 8784。"
)

#: 旧计划的回退值；运行版已经有可用的默认文本模型时，预检会优先采用它。
TEXT_MODEL = "deepseek-flash"
TEXT_MODEL_ENV = "T113_TEXT_MODEL"
IMAGE_MODEL = "gpt-image-2.5-sunburst"
VIDEO_MODEL = "MiniMax-H3"
VIDEO_PROTOCOL = "minimax-video-v2"
TEXT_REQUEST_LIMIT = None
IMAGE_TASK_START_LIMIT = None
VIDEO_TASK_START_LIMIT = 500
EXPECTED_IMAGE_TASK_STARTS = 1
EXPECTED_VIDEO_TASK_STARTS = 1

_COUNTER_KEYS = {
    "textRequests": "textRequests",
    "imageTaskStarts": "imageTaskStarts",
    "videoTaskStarts": "videoTaskStarts",
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return payload


def _post_json(
    url: str,
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return payload


def _check(
    name: str,
    ok: bool,
    detail: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "required": bool(required),
        "detail": detail,
    }


def _bool_field(item: dict[str, Any], *names: str) -> bool:
    for name in names:
        value = item.get(name)
        if isinstance(value, bool):
            return value
    return False


def _find_model(
    items: Any,
    *,
    model_id: str,
    protocol: str = "",
) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("modelId") or "") != model_id:
            continue
        if protocol and str(item.get("protocol") or "") != protocol:
            continue
        return item
    return None


def _resolve_text_model(items: Any) -> dict[str, Any] | None:
    """Resolve the running text model instead of trusting a baked-in ID.

    The operator may replace the gateway's text model without changing the
    acceptance plan.  Prefer an explicit T-113 override, then the catalog's
    default row, then a runtime-ready DeepSeek row, and finally the first
    enabled text row so a failed readiness check still reports the real model.
    """

    if not isinstance(items, list):
        return None
    rows = [item for item in items if isinstance(item, dict)]
    if not rows:
        return None

    preferred = str(os.environ.get(TEXT_MODEL_ENV) or "").strip()
    if preferred:
        for item in rows:
            if str(item.get("modelId") or "").strip() == preferred:
                return item

    def _ready(item: dict[str, Any]) -> bool:
        return (
            _bool_field(item, "enabled")
            and _bool_field(item, "configured")
            and _bool_field(item, "runtimeReady", "runtime_ready")
        )

    ready = [item for item in rows if _ready(item)]
    for item in ready:
        if item.get("default") is True:
            return item
    for item in ready:
        if "deepseek" in str(item.get("modelId") or "").casefold():
            return item
    if ready:
        return ready[0]

    for item in rows:
        if item.get("default") is True:
            return item
    for item in rows:
        if "deepseek" in str(item.get("modelId") or "").casefold():
            return item
    return rows[0]


def _model_check(
    name: str,
    item: dict[str, Any] | None,
    *,
    expected_model: str,
    expected_protocol: str = "",
) -> dict[str, Any]:
    if item is None:
        protocol = f" protocol={expected_protocol}" if expected_protocol else ""
        return _check(
            name,
            False,
            f"model={expected_model}{protocol} missing",
        )
    enabled = _bool_field(item, "enabled")
    configured = _bool_field(item, "configured")
    runtime_ready = _bool_field(item, "runtimeReady", "runtime_ready")
    actual_protocol = str(item.get("protocol") or "")
    protocol_ok = not expected_protocol or actual_protocol == expected_protocol
    detail = (
        f"model={expected_model} enabled={enabled} configured={configured} "
        f"runtimeReady={runtime_ready} protocol={actual_protocol or 'n/a'}"
    )
    return _check(
        name,
        enabled and configured and runtime_ready and protocol_ok,
        detail,
    )


def _read_api(
    api_base: str,
    *,
    timeout: float,
    fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
) -> tuple[dict[str, Any], dict[str, str]]:
    base = api_base.rstrip("/")
    urls = {
        "health": f"{base}/healthz",
        "version": f"{base}/version.json",
        "gateway": f"{base}/api/v1/model-gateway/config",
    }
    payloads: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, url in urls.items():
        try:
            payloads[name] = fetcher(url, timeout)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            payloads[name] = None
            errors[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return payloads, errors


def build_plan(text_model: str | None = None) -> dict[str, Any]:
    return {
        "workflowId": "freezone-final-film",
        "runMode": "auto",
        "shots": 1,
        "durationSeconds": 5,
        "textModel": str(
            text_model
            or os.environ.get(TEXT_MODEL_ENV)
            or TEXT_MODEL
        ).strip(),
        "imageModel": IMAGE_MODEL,
        "imageSize": "1K",
        "imageAspectRatio": "16:9",
        "videoModel": VIDEO_MODEL,
        "videoProtocol": VIDEO_PROTOCOL,
        "videoMode": "imageToVideo",
        "videoResolution": "768p",
        "videoAspectRatio": "16:9",
        "providerStartLimits": {
            "textRequests": TEXT_REQUEST_LIMIT,
            "imageTaskStarts": IMAGE_TASK_START_LIMIT,
            "videoTaskStarts": VIDEO_TASK_START_LIMIT,
        },
        "expectedPaidTaskStarts": {
            "image": EXPECTED_IMAGE_TASK_STARTS,
            "video": EXPECTED_VIDEO_TASK_STARTS,
        },
        "textRequestsMayExceedOne": True,
        "automaticProviderRetry": False,
        "temporaryProjectPrefix": "t113_l3_",
        "cleanupRequired": True,
    }


def probe_text_model_runtime(
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 10.0,
    fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
    poster: Callable[
        [str, dict[str, Any], float],
        dict[str, Any],
    ] = _post_json,
) -> dict[str, Any]:
    """Run at most one authorized text-model contract probe.

    The direct-model probe endpoint reuses the stored credential bound to the
    model id and base URL. This function never receives or submits an API key.
    It is intentionally inert when the target model is already runtime-ready.
    """

    base = api_base.rstrip("/")
    payloads, errors = _read_api(api_base, timeout=timeout, fetcher=fetcher)
    gateway = payloads.get("gateway") if isinstance(payloads.get("gateway"), dict) else {}
    gateway_data = gateway.get("data") if isinstance(gateway.get("data"), dict) else {}
    direct_models = (
        gateway_data.get("directModels")
        if isinstance(gateway_data.get("directModels"), dict)
        else {}
    )
    text_item = _resolve_text_model(direct_models.get("text"))
    text_model = str(
        (text_item or {}).get("modelId")
        or os.environ.get(TEXT_MODEL_ENV)
        or TEXT_MODEL
    ).strip()
    report: dict[str, Any] = {
        "ok": False,
        "attempted": False,
        "requestCount": 0,
        "alreadyReady": False,
        "modelId": text_model,
        "runtimeReady": False,
        "verificationStatus": "",
        "reason": "",
        "error": errors.get("gateway") or "",
    }
    if text_item is None:
        report["reason"] = "text_model_missing"
        return report

    report["runtimeReady"] = _bool_field(
        text_item,
        "runtimeReady",
        "runtime_ready",
    )
    report["verificationStatus"] = str(text_item.get("verificationStatus") or "")
    if not _bool_field(text_item, "enabled") or not _bool_field(
        text_item,
        "configured",
    ):
        report["reason"] = "text_model_not_enabled_or_configured"
        return report
    if report["runtimeReady"]:
        report["ok"] = True
        report["alreadyReady"] = True
        return report

    required = {
        "id": str(text_item.get("id") or "").strip(),
        "label": str(text_item.get("label") or text_model).strip(),
        "modelId": str(text_item.get("modelId") or "").strip(),
        "baseUrl": str(text_item.get("baseUrl") or "").strip(),
        "protocol": str(text_item.get("protocol") or "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        report["reason"] = "text_model_probe_fields_missing"
        report["error"] = ",".join(missing)
        return report

    report["attempted"] = True
    report["requestCount"] = 1
    try:
        payload = poster(
            f"{base}/api/v1/model-gateway/direct-models/text/probe",
            required,
            timeout,
        )
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        report["reason"] = "text_runtime_probe_failed"
        report["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        return report

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    capabilities = (
        data.get("capabilities")
        if isinstance(data.get("capabilities"), dict)
        else {}
    )
    report["runtimeReady"] = bool(
        capabilities.get("runtimeReady")
        or capabilities.get("runtime_ready")
        or data.get("runtimeReady")
        or data.get("runtime_ready")
    )
    report["verificationStatus"] = str(
        data.get("verificationStatus")
        or capabilities.get("verificationStatus")
        or ""
    )
    result_ok = payload.get("ok") is True and data.get("ok") is True
    if not result_ok or not report["runtimeReady"]:
        report["reason"] = "text_runtime_probe_failed"
        report["error"] = str(
            data.get("error")
            or data.get("chatProbeError")
            or data.get("streamProbeError")
            or "runtime contract not ready"
        )[:500]
        return report

    report["ok"] = True
    return report


def reserve_provider_start(
    plan: dict[str, Any],
    counters: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Decide whether one provider request/task start may be attempted.

    The caller must invoke this before forwarding the request and persist the
    returned counter value before doing so. This helper is deliberately pure:
    an in-process counter alone is not a budget.
    """

    counter_key = _COUNTER_KEYS.get(str(kind or ""))
    limits = plan.get("providerStartLimits")
    limits = limits if isinstance(limits, dict) else {}
    if not counter_key or counter_key not in limits:
        return {
            "ok": False,
            "kind": str(kind or ""),
            "reason": "provider_start_limit_invalid",
            "providerCallsStarted": False,
        }
    limit = limits.get(counter_key)
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        return {
            "ok": False,
            "kind": str(kind or ""),
            "reason": "provider_start_limit_invalid",
            "providerCallsStarted": False,
        }
    current = counters.get(counter_key, 0)
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        return {
            "ok": False,
            "kind": str(kind or ""),
            "limit": limit,
            "current": current,
            "reason": "provider_start_counter_invalid",
            "providerCallsStarted": False,
        }
    if limit is not None and current >= limit:
        return {
            "ok": False,
            "kind": str(kind or ""),
            "counterKey": counter_key,
            "limit": limit,
            "current": current,
            "next": current,
            "reason": "provider_start_limit_reached",
            "providerCallsStarted": False,
        }
    return {
        "ok": True,
        "kind": str(kind or ""),
        "counterKey": counter_key,
        "limit": limit,
        "current": current,
        "next": current + 1,
        "reason": "",
        "providerCallsStarted": False,
    }


def build_preflight(
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 10.0,
    fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
) -> dict[str, Any]:
    payloads, errors = _read_api(api_base, timeout=timeout, fetcher=fetcher)
    health = payloads.get("health") if isinstance(payloads.get("health"), dict) else {}
    version = payloads.get("version") if isinstance(payloads.get("version"), dict) else {}
    gateway = payloads.get("gateway") if isinstance(payloads.get("gateway"), dict) else {}
    gateway_data = gateway.get("data") if isinstance(gateway.get("data"), dict) else {}
    direct_models = (
        gateway_data.get("directModels")
        if isinstance(gateway_data.get("directModels"), dict)
        else {}
    )
    video_models = (
        gateway_data.get("directVideoModels")
        if isinstance(gateway_data.get("directVideoModels"), list)
        else []
    )

    text_item = _resolve_text_model(direct_models.get("text"))
    text_model = str(
        (text_item or {}).get("modelId")
        or os.environ.get(TEXT_MODEL_ENV)
        or TEXT_MODEL
    ).strip()
    image_item = _find_model(direct_models.get("image"), model_id=IMAGE_MODEL)
    video_item = _find_model(
        video_models,
        model_id=VIDEO_MODEL,
        protocol=VIDEO_PROTOCOL,
    )
    checks = [
        _check(
            "api_health",
            health.get("status") == "ok",
            errors.get("health") or str(health.get("status") or "missing"),
        ),
        _check(
            "api_version",
            bool(version.get("buildId")),
            errors.get("version") or str(version.get("buildId") or "missing"),
        ),
        _check(
            "model_gateway",
            gateway.get("ok") is True and bool(gateway_data),
            errors.get("gateway") or f"ok={gateway.get('ok')}",
        ),
        _model_check(
            "text_model",
            text_item,
            expected_model=text_model,
        ),
        _model_check(
            "image_model",
            image_item,
            expected_model=IMAGE_MODEL,
        ),
        _model_check(
            "video_model",
            video_item,
            expected_model=VIDEO_MODEL,
            expected_protocol=VIDEO_PROTOCOL,
        ),
    ]
    failed = [
        str(item["name"])
        for item in checks
        if item.get("required") is True and item.get("ok") is not True
    ]
    environment_ready = not failed
    blocking_reasons = [f"preflight_failed:{name}" for name in failed]
    if environment_ready:
        blocking_reasons.append("t113_paid_authorization_required")
    return {
        "schema": SCHEMA,
        "generatedAt": _stamp(),
        "ok": environment_ready,
        "environmentReady": environment_ready,
        "executionRunnerImplemented": True,
        "executionRunnerConnected": True,
        "executionRunnerReady": environment_ready,
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "authorizationRequired": True,
        "readOnly": True,
        "apiBase": api_base.rstrip("/"),
        "api": {
            "health": health.get("status"),
            "version": version.get("version"),
            "buildId": version.get("buildId"),
            "errors": errors,
        },
        "plan": build_plan(text_model),
        "authorizationPhrase": AUTHORIZATION_PHRASE,
        "checks": checks,
        "failedChecks": failed,
        "blockingReasons": blocking_reasons,
    }


def _write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument(
        "--execution-output",
        type=Path,
        default=DEFAULT_EXECUTION_ARTIFACT,
    )
    parser.add_argument("--authorization", default="")
    parser.add_argument(
        "--allow-paid-generation",
        action="store_true",
        help=(
            "allow the real runner only when --authorization exactly matches "
            "the T-113 approval phrase"
        ),
    )
    parser.add_argument(
        "--production-8784",
        action="store_true",
        help=(
            "run against the live 8784 process with a temporary t113_l3_ "
            "project instead of the local-provider harness"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.allow_paid_generation and (
        str(args.authorization or "").strip() != AUTHORIZATION_PHRASE
    ):
        report = build_preflight(
            api_base=str(args.api_base),
            timeout=float(args.timeout_seconds),
        )
        report["paidGenerationAuthorized"] = False
        report["authorizationAccepted"] = False
        report["authorizedExecutionBlocked"] = True
        report["blockingReasons"].insert(
            0,
            "t113_authorization_phrase_mismatch",
        )
        _write_report(report, Path(args.output))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if args.allow_paid_generation:
        runner = PRODUCTION_RUNNER if args.production_8784 else RUNNER
        completed = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--authorization",
                str(args.authorization),
                "--output",
                str(args.execution_output),
            ],
            cwd=ROOT,
            check=False,
        )
        return int(completed.returncode)

    report = build_preflight(
        api_base=str(args.api_base),
        timeout=float(args.timeout_seconds),
    )
    report["paidGenerationAuthorized"] = False
    _write_report(report, Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
