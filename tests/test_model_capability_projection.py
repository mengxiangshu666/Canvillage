from types import SimpleNamespace

from novelvideo.workflow_runtime import model_plan


def test_unresolved_model_projection_is_explicit_and_credential_free() -> None:
    snapshot = model_plan.build_model_capability_projection(
        kind="image",
        model_ref="legacy-image-model",
        upstream_model="legacy-image-model",
        model_label="Legacy image",
    )

    assert snapshot["schema"] == "canvas.model-capability-projection.v1"
    assert snapshot["source"] == "unresolved"
    assert snapshot["verification_status"] == "unverified"
    assert snapshot["runtime_ready"] is None
    assert snapshot["snapshot_hash"]
    assert "api_key" not in str(snapshot).lower()
    assert "base_url" not in str(snapshot).lower()


def test_direct_image_projection_uses_model_center_contract(monkeypatch) -> None:
    direct = SimpleNamespace(
        catalog_id="direct/image-1",
        registry_id="image-1",
        upstream_model="upstream-image",
        label="Image 1",
        protocol="openai-images",
    )
    monkeypatch.setattr(model_plan, "resolve_direct_model", lambda _kind, _ref: direct)
    monkeypatch.setattr(
        model_plan,
        "direct_model_option",
        lambda _model: {
            "id": "direct/image-1",
            "modelId": "upstream-image",
            "label": "Image 1",
            "runtimeReady": True,
            "catalogVerification": "catalog-confirmed",
            "capabilityRevision": "direct-model-contract.v2",
            "supportedModes": ["textToImage", "imageToImage"],
            "inputSlots": ["prompt", "reference_images"],
            "aspectRatioOptions": ["1:1", "16:9"],
            "resolutionOptions": ["1K", "2K"],
            "qualityOptions": ["low", "high"],
            "declaredCapabilities": [
                "supportedModes",
                "aspectRatioOptions",
                "resolutionOptions",
                "qualityOptions",
            ],
            "supportsCustomAspectRatio": True,
            "supportsCustomResolution": False,
            "referenceLimits": {"images": 9},
            "parameterDefaults": {"aspectRatio": "1:1"},
            "protocol": "openai-images",
        },
    )

    snapshot = model_plan.build_model_capability_projection(
        kind="image", model_ref="direct/image-1"
    )

    assert snapshot["model_id"] == "direct/image-1"
    assert snapshot["upstream_model"] == "upstream-image"
    assert snapshot["runtime_ready"] is True
    assert snapshot["verification_status"] == "catalog-confirmed"
    assert snapshot["aspect_ratio_options"] == ["1:1", "16:9"]
    assert snapshot["resolution_options"] == ["1K", "2K"]
    assert snapshot["input_slots"] == ["prompt", "reference_images"]
    assert snapshot["quality_options"] == ["low", "high"]
    assert snapshot["declared_capabilities"] == [
        "supportedModes",
        "aspectRatioOptions",
        "resolutionOptions",
        "qualityOptions",
    ]
    assert snapshot["supports_custom_resolution"] is False
    assert snapshot["snapshot_hash"]


def test_explicit_empty_model_modes_do_not_fall_through_to_alias() -> None:
    from novelvideo.generators.direct_model_capabilities import (
        direct_model_capability_summary,
    )

    summary = direct_model_capability_summary(
        "text",
        "vendor-text-vNext",
        protocol="openai-compatible",
        metadata={"supportedModes": [], "modes": ["chat"]},
    )

    assert summary["supportedModes"] == []
    assert summary["declaredCapabilities"] == ["supportedModes"]
