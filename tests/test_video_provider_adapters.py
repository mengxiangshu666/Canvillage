from __future__ import annotations

from novelvideo.generators.video.video_provider_adapters import (
    VideoProtocolFamily,
    get_video_adapter_contract,
    infer_video_protocol_family,
    normalize_video_protocol_family,
)
from novelvideo.generators.video.video_openapi_discovery import discover_video_openapi
from novelvideo.generators.video.direct_video_probe import probe_direct_video_model
from novelvideo.generators.video.direct_video_probe import discover_direct_video_models
from novelvideo.generators.video.runtime_contract import (
    GENERIC_VIDEO_PROTOCOL_FAMILIES,
    is_generic_video_adapter,
    normalize_adapter_family,
)


def test_non_openai_protocol_families_have_distinct_lifecycle_contracts() -> None:
    replicate = get_video_adapter_contract("replicate")
    fal = get_video_adapter_contract("fal")
    vertex = get_video_adapter_contract("vertex")
    comfy = get_video_adapter_contract("comfyui")

    assert replicate.family is VideoProtocolFamily.PREDICTION
    assert replicate.submit_path == "/v1/predictions"
    assert replicate.query_path_template == "/v1/predictions/{task_id}"
    assert fal.family is VideoProtocolFamily.QUEUE
    assert fal.polling_style == "queue"
    assert vertex.family is VideoProtocolFamily.LONG_RUNNING_OPERATION
    assert "predictLongRunning" in vertex.submit_path
    assert comfy.family is VideoProtocolFamily.WORKFLOW
    assert comfy.submit_path == "/prompt"


def test_adapter_contracts_normalize_paths_and_parse_lifecycle_shapes() -> None:
    replicate = get_video_adapter_contract(VideoProtocolFamily.PREDICTION)
    assert replicate.submit_url("https://relay.example/api") == (
        "https://relay.example/api/v1/predictions"
    )
    assert replicate.query_url("https://relay.example/api", "pred-1") == (
        "https://relay.example/api/v1/predictions/pred-1"
    )
    payload = {
        "id": "pred-1",
        "status": "succeeded",
        "output": ["https://media.example/video.mp4"],
    }
    assert replicate.extract_task_id(payload) == "pred-1"
    assert replicate.extract_status(payload) == "succeeded"
    assert replicate.extract_result(payload) == payload["output"]

    operation = get_video_adapter_contract("google-veo")
    assert operation.extract_status({"done": False}) == "running"
    assert operation.extract_status({"done": True, "response": {}}) == "completed"
    assert operation.extract_status({"done": True, "error": {"message": "quota"}}) == "failed"


def test_protocol_family_inference_prefers_explicit_and_openapi_evidence() -> None:
    explicit = infer_video_protocol_family(protocol_hint="replicate")
    assert explicit.family is VideoProtocolFamily.PREDICTION
    assert explicit.confidence >= 0.9

    queue = infer_video_protocol_family(
        openapi_paths=(
            "/queue/{model}/requests",
            "/queue/{model}/requests/{request_id}/status",
        )
    )
    assert queue.family is VideoProtocolFamily.QUEUE
    assert queue.resolved is True

    operation = infer_video_protocol_family(
        openapi_paths=(
            "/v1/{model}:predictLongRunning",
            "/v1/{name=operations/**}",
        )
    )
    assert operation.family is VideoProtocolFamily.LONG_RUNNING_OPERATION

    workflow = infer_video_protocol_family(
        openapi_paths=("/prompt", "/history", "/history/{prompt_id}")
    )
    assert workflow.family is VideoProtocolFamily.WORKFLOW


def test_unknown_protocol_does_not_get_promoted_to_openai() -> None:
    evidence = infer_video_protocol_family(model_id="provider-video-v1")
    assert evidence.family is VideoProtocolFamily.UNKNOWN
    assert normalize_video_protocol_family("totally-custom") is VideoProtocolFamily.UNKNOWN


def test_runtime_contract_has_one_generic_adapter_selection_boundary() -> None:
    assert GENERIC_VIDEO_PROTOCOL_FAMILIES == {
        "prediction",
        "queue",
        "long-running-operation",
        "workflow",
        "autodl-comfyui",
    }
    assert normalize_adapter_family("replicate") == "prediction"
    assert is_generic_video_adapter("fal") is True
    assert is_generic_video_adapter("openai-video") is False
    assert normalize_adapter_family("totally-custom") == ""


def test_openapi_discovery_is_bounded_and_extracts_only_paths(monkeypatch) -> None:
    import httpx

    seen: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            seen.append(url)
            if url.endswith("/v1/openapi.json"):
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={
                        "openapi": "3.1.0",
                        "paths": {
                            "/queue/{model}/requests": {
                                "post": {
                                    "operationId": "submitQueue",
                                    "requestBody": {
                                        "content": {"application/json": {"schema": {}}}
                                    },
                                    "responses": {
                                        "200": {"content": {"application/json": {}}}
                                    },
                                }
                            },
                            "/queue/{model}/requests/{id}/status": {},
                        },
                    },
                )
            return httpx.Response(
                404,
                request=httpx.Request("GET", url),
                json={"detail": "not found"},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = discover_video_openapi(
        base_url="https://relay.example/v1",
        api_key="fixture-key",
    )

    assert result["found"] is True
    assert result["version"] == "3.1.0"
    assert result["paths"] == [
        "/queue/{model}/requests",
        "/queue/{model}/requests/{id}/status",
    ]
    assert result["operations"][0]["operationId"] == "submitQueue"
    assert result["operations"][0]["requestContentTypes"] == ["application/json"]
    assert seen[0] == "https://relay.example/v1/openapi.json"


def test_openapi_discovery_excludes_unrelated_site_routes(monkeypatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            if url.endswith("/v1/openapi.json"):
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={
                        "openapi": "3.1.0",
                        "paths": {
                            "/v1/videos": {"post": {"operationId": "createVideo"}},
                            "/api/admin/videos/{task_id}": {"get": {}},
                            "/api/admin/accounts": {"get": {}},
                            "/v1/models": {"get": {}},
                        },
                    },
                )
            return httpx.Response(
                404,
                request=httpx.Request("GET", url),
                json={"detail": "not found"},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = discover_video_openapi(
        base_url="https://relay.example/v1",
        api_key="fixture-key",
    )

    assert result["paths"] == ["/v1/videos"]
    assert result["operations"] == [
        {
            "path": "/v1/videos",
            "method": "post",
            "operationId": "createVideo",
            "requestContentTypes": [],
            "responseContentTypes": [],
            "tags": [],
        }
    ]


def test_probe_can_resolve_queue_family_from_openapi_without_media_submit(monkeypatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            if url.endswith("/models"):
                payload = {"data": [{"id": "queue-video-v1"}]}
            elif url.endswith("/openapi.json"):
                payload = {
                    "openapi": "3.1.0",
                    "paths": {
                        "/queue/{model}/requests": {},
                        "/queue/{model}/requests/{request_id}/status": {},
                    },
                }
            else:
                return httpx.Response(
                    404,
                    request=httpx.Request("GET", url),
                    json={"detail": "not found"},
                )
            return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = probe_direct_video_model(
        upstream_model="queue-video-v1",
        base_url="https://relay.example/v1",
        api_key="fixture-key",
    )

    assert result["modelFound"] is True
    assert result["adapterFamily"] == "queue"
    assert result["adapterConfidence"] >= 0.9
    assert result["openapiPaths"] == [
        "/queue/{model}/requests",
        "/queue/{model}/requests/{request_id}/status",
    ]


def test_model_directory_listing_does_not_probe_openapi_after_catalog_read(monkeypatch) -> None:
    import httpx

    requested: list[str] = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            requested.append(url)
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"data": [{"id": "video-v1"}]},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = discover_direct_video_models(
        base_url="https://relay.example/v1",
        api_key="fixture-key",
    )

    assert result["ok"] is True
    assert requested == ["https://relay.example/v1/models"]
