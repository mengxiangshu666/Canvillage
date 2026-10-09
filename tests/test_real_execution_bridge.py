from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "t111_real_execution_bridge.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("t111_real_execution_bridge", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_module()


def _payloads() -> dict[str, object]:
    return {
        "health": {"status": "ok"},
        "version": {"version": "v1.0.0", "buildId": "build-1"},
        "openapi": {"paths": {"/api/v1/test": {}}},
        "gateway": {
            "ok": True,
            "data": {
                "directModels": {
                    "agent": [
                        {
                            "enabled": True,
                            "configured": True,
                            "runtimeReady": True,
                            "supportsTools": True,
                            "usable": True,
                            "apiKeyPreview": "sk-secret-agent",
                        }
                    ],
                    "text": [
                        {
                            "enabled": True,
                            "configured": True,
                            "runtimeReady": True,
                            "supportsTools": True,
                            "usable": True,
                            "apiKeyPreview": "sk-secret-text",
                        }
                    ],
                    "image": [
                        {
                            "enabled": True,
                            "configured": True,
                            "runtimeReady": True,
                            "apiKeyPreview": "sk-secret-image",
                        }
                    ],
                },
                "directVideoModels": [
                    {
                        "enabled": True,
                        "configured": True,
                        "runtimeReady": True,
                        "apiKeyPreview": "sk-secret-video",
                    }
                ],
            },
        },
        "engines": {
            "data": {
                "engines": [
                    {"id": "village", "available": True},
                    {"id": "other", "available": False},
                ]
            }
        },
    }


def _environment(*, ready: bool = True) -> dict[str, object]:
    return {
        "checks": [
            {
                "name": "bundled_ffmpeg",
                "ok": ready,
                "path": "ffmpeg.exe",
                "expected": "file",
            }
        ],
        "ffmpeg": "ffmpeg.exe",
        "ffprobe": "ffprobe.exe",
        "node": "node.exe",
        "playwrightNodeModules": "node_modules",
        "targetState": "state",
        "targetProjectState": "project-state",
        "targetProjectKind": "isolated_t106",
        "targetScriptNodeId": "script-a",
    }


def test_model_summary_counts_runtime_capabilities() -> None:
    gateway = _payloads()["gateway"]
    assert isinstance(gateway, dict)
    data = gateway["data"]
    assert isinstance(data, dict)

    models = bridge.summarize_models(data)

    assert models["agent"] == {
        "total": 1,
        "enabled": 1,
        "configured": 1,
        "runtimeReady": 1,
        "usable": 1,
        "toolReady": 1,
        "requiredReady": True,
    }
    assert models["image"]["requiredReady"] is True
    assert models["image"]["usable"] is None
    assert models["image"]["toolReady"] is None
    assert models["video"]["runtimeReady"] == 1
    assert models["video"]["usable"] is None
    assert models["video"]["toolReady"] is None


def test_environment_ready_is_not_execution_bridge_ready() -> None:
    report = bridge._serializable_report(
        api_base="http://127.0.0.1:8784",
        payloads=_payloads(),
        errors={},
        environment=_environment(),
    )

    assert report["schema"] == bridge.SCHEMA
    assert report["ok"] is True
    assert report["environmentReady"] is True
    assert report["executionBridgeConnected"] is False
    assert report["providerCallsStarted"] is False
    assert report["blockingReasons"] == ["real_execution_adapter_not_connected"]


def test_failed_environment_check_fails_closed() -> None:
    report = bridge._serializable_report(
        api_base="http://127.0.0.1:8784",
        payloads=_payloads(),
        errors={},
        environment=_environment(ready=False),
    )

    assert report["ok"] is False
    assert report["environmentReady"] is False
    assert report["executionBridgeConnected"] is False
    assert report["failedChecks"] == ["bundled_ffmpeg"]
    assert report["blockingReasons"] == ["preflight_failed:bundled_ffmpeg"]


def test_report_does_not_emit_provider_credentials() -> None:
    report = bridge._serializable_report(
        api_base="http://127.0.0.1:8784",
        payloads=_payloads(),
        errors={},
        environment=_environment(),
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert "sk-secret" not in serialized
    assert "apiKeyPreview" not in serialized
    assert "baseUrl" not in serialized
