"""Preflight the real Hermes/provider execution bridge without calling it.

The report deliberately separates environment readiness from bridge
implementation readiness.  A configured model catalog or a live 8784 process
is not evidence that the script-node target chain can execute real provider
work yet.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
DEFAULT_API_BASE = "http://127.0.0.1:8784"
DEFAULT_ARTIFACT = WORKSPACE / "artifacts" / "real-bridge" / "latest.json"
SCHEMA = "t111_real_execution_bridge.v1"
T106_ROOT = WORKSPACE / "ui-smoke-t106"
T106_STATE = T106_ROOT / "state"
T106_PROJECT_STATE = T106_STATE / "local" / "ui_smoke_t091"
PLAYWRIGHT_NODE_MODULES = (
    WORKSPACE / "ui-smoke-t091" / "browser-runner" / "node_modules"
)
T106_PREFLIGHT = (
    ROOT
    / "scripts"
    / "acceptance"
    / "t106_one_turn_one_authorization_browser_film.py"
)
T106_BROWSER = (
    ROOT
    / "scripts"
    / "acceptance"
    / "t106_one_turn_one_authorization_browser_film.cjs"
)
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return payload


def _bool_field(item: dict[str, Any], *names: str) -> bool:
    for name in names:
        value = item.get(name)
        if isinstance(value, bool):
            return value
    return False


def _model_summary(
    items: list[dict[str, Any]],
    *,
    require_tools: bool = False,
) -> dict[str, Any]:
    enabled = [item for item in items if _bool_field(item, "enabled")]
    configured = [item for item in items if _bool_field(item, "configured")]
    runtime_ready = [
        item
        for item in enabled
        if _bool_field(item, "runtimeReady", "runtime_ready")
    ]
    has_usable = any("usable" in item for item in items)
    has_tools = any("supportsTools" in item for item in items)
    usable_items = [item for item in runtime_ready if _bool_field(item, "usable")]
    tool_ready = [item for item in runtime_ready if _bool_field(item, "supportsTools")]
    eligible = tool_ready if require_tools else runtime_ready
    return {
        "total": len(items),
        "enabled": len(enabled),
        "configured": len(configured),
        "runtimeReady": len(runtime_ready),
        "usable": len(usable_items) if has_usable else None,
        "toolReady": len(tool_ready) if has_tools else None,
        "requiredReady": bool(eligible),
    }


def summarize_models(gateway_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    direct_models = gateway_data.get("directModels")
    direct_models = direct_models if isinstance(direct_models, dict) else {}
    video_models = gateway_data.get("directVideoModels")
    video_models = video_models if isinstance(video_models, list) else []
    summary: dict[str, dict[str, Any]] = {}
    for kind, require_tools in (
        ("agent", True),
        ("text", False),
        ("image", False),
    ):
        items = direct_models.get(kind)
        items = items if isinstance(items, list) else []
        summary[kind] = _model_summary(
            [item for item in items if isinstance(item, dict)],
            require_tools=require_tools,
        )
    summary["video"] = _model_summary(
        [item for item in video_models if isinstance(item, dict)]
    )
    return summary


def _path_check(name: str, path: Path, *, directory: bool = False) -> dict[str, Any]:
    exists = path.is_dir() if directory else path.is_file()
    return {
        "name": name,
        "ok": bool(exists),
        "path": str(path),
        "expected": "directory" if directory else "file",
    }


def _loopback_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def collect_environment() -> dict[str, Any]:
    node = shutil.which("node")
    checks = [
        _path_check("bundled_ffmpeg", FFMPEG),
        _path_check("bundled_ffprobe", FFPROBE),
        _path_check("t106_preflight", T106_PREFLIGHT),
        _path_check("t106_browser_driver", T106_BROWSER),
        _path_check("playwright_node_modules", PLAYWRIGHT_NODE_MODULES, directory=True),
        _path_check("t106_isolated_state", T106_STATE, directory=True),
        _path_check("t106_project_state", T106_PROJECT_STATE, directory=True),
        _path_check(
            "t106_workflow_database",
            T106_PROJECT_STATE / "workflow_runs.db",
        ),
        {
            "name": "node_runtime",
            "ok": bool(node),
            "path": str(node or ""),
            "expected": "executable",
        },
        {
            "name": "isolated_api_port",
            "ok": _loopback_port_available(8792),
            "path": "127.0.0.1:8792",
            "expected": "available",
        },
        {
            "name": "isolated_vite_port",
            "ok": _loopback_port_available(5192),
            "path": "127.0.0.1:5192",
            "expected": "available",
        },
    ]
    return {
        "checks": checks,
        "ffmpeg": str(FFMPEG),
        "ffprobe": str(FFPROBE),
        "node": str(node or ""),
        "playwrightNodeModules": str(PLAYWRIGHT_NODE_MODULES),
        "targetState": str(T106_STATE),
        "targetProjectState": str(T106_PROJECT_STATE),
        "targetProjectKind": "isolated_t106",
        "targetScriptNodeId": "script-a",
    }


def _collect_api(
    api_base: str,
    *,
    fetcher: Callable[[str, float], dict[str, Any]],
    timeout: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    base = api_base.rstrip("/")
    endpoints = {
        "health": f"{base}/healthz",
        "version": f"{base}/version.json",
        "openapi": f"{base}/openapi.json",
        "gateway": f"{base}/api/v1/model-gateway/config",
        "engines": f"{base}/api/v1/chat/engines",
    }
    payloads: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, url in endpoints.items():
        try:
            payloads[name] = fetcher(url, timeout)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            payloads[name] = None
            errors[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return payloads, errors


def _api_checks(payloads: dict[str, Any], errors: dict[str, str]) -> list[dict[str, Any]]:
    health = payloads.get("health") if isinstance(payloads.get("health"), dict) else {}
    version = payloads.get("version") if isinstance(payloads.get("version"), dict) else {}
    openapi = payloads.get("openapi") if isinstance(payloads.get("openapi"), dict) else {}
    gateway = payloads.get("gateway") if isinstance(payloads.get("gateway"), dict) else {}
    engines = payloads.get("engines") if isinstance(payloads.get("engines"), dict) else {}
    paths = openapi.get("paths") if isinstance(openapi.get("paths"), dict) else {}
    engine_data = engines.get("data") if isinstance(engines.get("data"), dict) else {}
    engine_items = engine_data.get("engines")
    engine_items = engine_items if isinstance(engine_items, list) else []
    hermes_available = any(
        isinstance(item, dict)
        and str(item.get("id") or "") == "village"
        and item.get("available") is True
        for item in engine_items
    )
    gateway_ok = gateway.get("ok") is True and isinstance(gateway.get("data"), dict)
    return [
        {
            "name": "api_health",
            "ok": health.get("status") == "ok",
            "detail": errors.get("health") or str(health.get("status") or "missing"),
        },
        {
            "name": "api_version",
            "ok": bool(version.get("buildId")),
            "detail": errors.get("version") or str(version.get("buildId") or "missing"),
        },
        {
            "name": "api_openapi",
            "ok": len(paths) > 0,
            "detail": errors.get("openapi") or f"paths={len(paths)}",
        },
        {
            "name": "model_gateway_config",
            "ok": gateway_ok,
            "detail": errors.get("gateway") or f"ok={gateway.get('ok')}",
        },
        {
            "name": "hermes_engine",
            "ok": hermes_available,
            "detail": errors.get("engines") or f"village_available={hermes_available}",
        },
    ]


def _model_checks(models: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": f"{kind}_model",
            "ok": bool(models.get(kind, {}).get("requiredReady")),
            "detail": json.dumps(models.get(kind, {}), sort_keys=True),
        }
        for kind in ("agent", "text", "image", "video")
    ]


def _serializable_report(
    *,
    api_base: str,
    payloads: dict[str, Any],
    errors: dict[str, str],
    environment: dict[str, Any],
) -> dict[str, Any]:
    gateway = payloads.get("gateway") if isinstance(payloads.get("gateway"), dict) else {}
    gateway_data = gateway.get("data") if isinstance(gateway.get("data"), dict) else {}
    version = payloads.get("version") if isinstance(payloads.get("version"), dict) else {}
    openapi = payloads.get("openapi") if isinstance(payloads.get("openapi"), dict) else {}
    paths = openapi.get("paths") if isinstance(openapi.get("paths"), dict) else {}
    models = summarize_models(gateway_data)
    checks = [
        *_api_checks(payloads, errors),
        *_model_checks(models),
        *environment.get("checks", []),
    ]
    failed = [str(item["name"]) for item in checks if item.get("ok") is not True]
    environment_ready = not failed
    blocking_reasons = [
        f"preflight_failed:{name}"
        for name in failed
    ]
    if environment_ready:
        blocking_reasons.append("real_execution_adapter_not_connected")
    return {
        "schema": SCHEMA,
        "generatedAt": _stamp(),
        "ok": environment_ready,
        "environmentReady": environment_ready,
        "executionBridgeConnected": False,
        "providerCallsStarted": False,
        "readOnly": True,
        "apiBase": api_base.rstrip("/"),
        "api": {
            "health": (
                payloads.get("health", {}).get("status")
                if isinstance(payloads.get("health"), dict)
                else None
            ),
            "version": version.get("version"),
            "buildId": version.get("buildId"),
            "openapiPathCount": len(paths),
            "errors": errors,
        },
        "models": models,
        "runtime": {
            key: environment.get(key)
            for key in (
                "ffmpeg",
                "ffprobe",
                "node",
                "playwrightNodeModules",
            )
        },
        "target": {
            key: environment.get(key)
            for key in (
                "targetState",
                "targetProjectState",
                "targetProjectKind",
                "targetScriptNodeId",
            )
        },
        "checks": checks,
        "failedChecks": failed,
        "blockingReasons": blocking_reasons,
    }


def build_report(
    api_base: str = DEFAULT_API_BASE,
    *,
    timeout: float = 5.0,
    fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
) -> dict[str, Any]:
    payloads, errors = _collect_api(api_base, fetcher=fetcher, timeout=timeout)
    return _serializable_report(
        api_base=api_base,
        payloads=payloads,
        errors=errors,
        environment=collect_environment(),
    )


def _write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    report = build_report(
        args.api_base,
        timeout=args.timeout_seconds,
    )
    _write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["environmentReady"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
