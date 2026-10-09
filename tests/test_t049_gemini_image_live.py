from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "t049_gemini_image_live.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("t049_gemini_image_live", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_runner_is_inert_without_exact_authorization(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "build_preflight",
        lambda _model_id: {
            "ok": False,
            "blockingReasons": ["t049_paid_authorization_required"],
        },
    )

    assert module.main(
        [
            "--allow-paid-generation",
            "--authorization",
            "not approved",
            "--evidence",
            str(tmp_path / "t049-test.json"),
        ]
    ) == 2


def test_authorization_phrase_names_one_no_retry_call() -> None:
    module = _load_module()

    assert "调用一次" in module.AUTHORIZATION_PHRASE
    assert "generateContent" in module.AUTHORIZATION_PHRASE
    assert "禁止自动重试" in module.AUTHORIZATION_PHRASE


def test_execute_once_disables_retry_fallback_and_calls_generator_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    from PIL import Image

    from novelvideo.generators import direct_image_models

    output = tmp_path / "gemini.png"
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, format="PNG")
    model = SimpleNamespace(
        registry_id="image-test",
        catalog_id="catalog-test",
        upstream_model="gemini-3-pro-image",
        protocol="gemini-image",
        base_url="https://img.example/v1beta",
        enabled=True,
    )
    calls: list[dict[str, Any]] = []

    async def fake_generate_direct_image(**kwargs: Any) -> Path:
        calls.append(kwargs)
        return output

    monkeypatch.setattr(
        direct_image_models,
        "resolve_direct_image_model",
        lambda _model_id: model,
    )
    monkeypatch.setattr(
        direct_image_models,
        "generate_direct_image",
        fake_generate_direct_image,
    )
    monkeypatch.setattr(module, "_apply_runtime_env", lambda: None)
    monkeypatch.setenv(direct_image_models.DIRECT_IMAGE_SAFETY_RETRY_ENV, "1")
    monkeypatch.setenv(
        direct_image_models.DIRECT_IMAGE_FALLBACK_ENV,
        "image-secondary",
    )
    monkeypatch.setattr(
        direct_image_models,
        "DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS",
        3,
    )

    report = asyncio.run(
        module._execute_once(
            model_id="direct/image-test",
            prompt="test prompt",
            output_path=output,
        )
    )

    assert len(calls) == 1
    assert calls[0]["output_path"] == output
    assert calls[0]["aspect_ratio"] == "16:9"
    assert calls[0]["image_size"] == "1K"
    assert os.environ[direct_image_models.DIRECT_IMAGE_SAFETY_RETRY_ENV] == "0"
    assert direct_image_models.DIRECT_IMAGE_FALLBACK_ENV not in os.environ
    assert direct_image_models.DIRECT_IMAGE_TRANSIENT_MAX_ATTEMPTS == 1
    assert report["automatic_retry_enabled"] is False
    assert report["artifact"]["format"] == "PNG"
    assert report["artifact"]["width"] == 2
    assert report["artifact"]["height"] == 2
