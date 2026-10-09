from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI

from novelvideo import config
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes._freezone_parts import _freezone_routes_video_audio as video_routes
from novelvideo.generators.video import direct_models
from novelvideo.generators.video.direct_video_capability_cache import record_capability
from novelvideo.generators.video.newapi import NewApiVideoGenerator
from novelvideo.project_context import ProjectContext
from novelvideo.workflow_runtime import model_plan as workflow_model_plan


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_uvicorn(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive():
        if time.monotonic() >= deadline:
            raise TimeoutError("isolated uvicorn did not start")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("isolated uvicorn stopped before startup")
    return server, thread, f"http://127.0.0.1:{port}"


def _stop_uvicorn(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("isolated uvicorn did not stop")


class _CaptureHandler(BaseHTTPRequestHandler):
    server: "_CaptureServer"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        self.server.requests.append(
            {
                "path": self.path,
                "payload": payload,
                "authorization": self.headers.get("Authorization"),
            }
        )
        body = json.dumps(
            {
                "error": {
                    "message": "rq004 local capture only",
                    "type": "intentional_test_error",
                }
            }
        ).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _CaptureServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CaptureHandler)
        self.requests: list[dict[str, Any]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1"


def _start_capture() -> tuple[_CaptureServer, threading.Thread]:
    server = _CaptureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_capture(server: _CaptureServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("capture upstream did not stop")


def _build_app(
    ctx: ProjectContext,
    *,
    queue_calls: list[dict[str, Any]],
) -> tuple[FastAPI, list[Any]]:
    async def resolve_project(*_args: object, **_kwargs: object) -> tuple[Any, ...]:
        return (
            ctx,
            ctx.requester_username,
            ctx.project_name,
            ctx.output_dir,
            str(ctx.output_dir),
        )

    async def canonical_asset_library(*_args: object, **_kwargs: object) -> tuple[Any, list[Any]]:
        return SimpleNamespace(), []

    async def forbidden_enqueue(**kwargs: Any) -> None:
        queue_calls.append(kwargs)
        raise AssertionError("RQ-004 preflight reached the queue")

    app = FastAPI()
    app.include_router(video_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    patches = [
        patch.object(video_routes, "_resolve_freezone_project", resolve_project),
        patch.object(
            video_routes,
            "assert_freezone_video_generation_enabled",
            lambda: None,
        ),
        patch.object(
            video_routes,
            "resolve_freezone_video_backend",
            lambda model: str(model or "direct_rq004-a"),
        ),
        patch.object(
            video_routes,
            "_canonical_asset_library",
            canonical_asset_library,
        ),
        patch.object(
            video_routes,
            "build_freezone_video_prompt",
            lambda **kwargs: str(kwargs.get("user_prompt") or ""),
        ),
        patch.object(video_routes, "_new_job_id", lambda: "job-rq004"),
        patch.object(
            video_routes,
            "_enqueue_freezone_background_job",
            forbidden_enqueue,
        ),
    ]
    for item in patches:
        item.start()
    return app, patches


def _close_app(patches: list[Any]) -> None:
    for item in reversed(patches):
        item.stop()


def _fixture_registry(
    capture_a: _CaptureServer, capture_b: _CaptureServer
) -> list[dict[str, Any]]:
    return [
        {
            "id": "rq004-a",
            "label": "RQ004 A",
            "modelId": "operator-video-a",
            "baseUrl": capture_a.base_url,
            "apiKey": "rq004-a-key",
            "requestedProtocol": "openai-video",
            "protocol": "openai-video",
            "enabled": True,
            "isDefault": True,
        },
        {
            "id": "rq004-b",
            "label": "RQ004 B",
            "modelId": "operator-video-b",
            "baseUrl": capture_b.base_url,
            "apiKey": "rq004-b-key",
            "requestedProtocol": "openai-video",
            "protocol": "openai-video",
            "enabled": True,
            "isDefault": False,
        },
    ]


def _seed_registry(capture_a: _CaptureServer, capture_b: _CaptureServer) -> list[Any]:
    registry_patch = patch.object(
        direct_models,
        "get_direct_video_models",
        lambda: [dict(item) for item in _fixture_registry(capture_a, capture_b)],
    )
    registry_patch.start()
    record_capability(
        base_url=capture_a.base_url,
        protocol="openai-video",
        upstream_model="operator-video-a",
        capability={
            "source": "upstream",
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": [
                "modes",
                "aspectRatios",
                "resolutionOptions",
                "durationOptions",
            ],
            "modes": ["textToVideo"],
            "aspectRatios": ["16:9"],
            "resolutionOptions": ["1080p"],
            "durationOptions": [5, 10],
            "nativeAudio": "unsupported",
        },
    )
    record_capability(
        base_url=capture_b.base_url,
        protocol="openai-video",
        upstream_model="operator-video-b",
        capability={
            "source": "upstream",
            "verificationStatus": "runtime-verified",
            "modelFound": True,
            "discoveredModelCount": 1,
            "declaredCapabilities": [
                "modes",
                "aspectRatios",
                "resolutionOptions",
                "durationOptions",
            ],
            "modes": ["imageToVideo"],
            "aspectRatios": ["9:16"],
            "resolutionOptions": ["720p"],
            "durationOptions": [4],
            "nativeAudio": "unsupported",
        },
    )
    return [registry_patch]


def _model_by_backend(models: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    return next(item for item in models if item.get("id") == backend)


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rq004-capability-") as raw_dir:
        root = Path(raw_dir)
        state_dir = root / "state"
        output_dir = root / "output"
        runtime_dir = root / "runtime"
        for path in (state_dir, output_dir, runtime_dir):
            path.mkdir(parents=True)

        previous_state_dir = config.STATE_DIR
        config.STATE_DIR = str(state_dir)
        capture_a, thread_a = _start_capture()
        capture_b, thread_b = _start_capture()
        queue_calls: list[dict[str, Any]] = []
        app: FastAPI | None = None
        patches: list[Any] = []
        try:
            patches.extend(_seed_registry(capture_a, capture_b))
            ctx = ProjectContext(
                project_id="proj-rq004",
                project_name="demo",
                owner_type="user",
                owner_id="user-1",
                owner_username="alice",
                requester_user_id="user-1",
                requester_username="alice",
                requester_principals=(("user", "user-1"),),
                effective_role="editor",
                home_node_id="local",
                output_dir=output_dir,
                state_dir=state_dir,
                runtime_dir=runtime_dir,
                is_home_node=True,
            )
            app, app_patches = _build_app(ctx, queue_calls=queue_calls)
            patches.extend(app_patches)
            server, thread, base_url = _start_uvicorn(app)
            try:
                with httpx.Client(base_url=base_url, timeout=10) as client:
                    response = client.get(
                        "/api/v1/projects/demo/freezone/video/models"
                    )
                    response.raise_for_status()
                    models = response.json()["data"]
            finally:
                _stop_uvicorn(server, thread)

            model_a = _model_by_backend(models, "direct_rq004-a")
            model_b = _model_by_backend(models, "direct_rq004-b")
            assert model_a["capabilitySource"] == "upstream"
            assert model_b["capabilitySource"] == "upstream"
            assert model_a["capabilityRevision"] != model_b["capabilityRevision"]
            assert model_a["aspectRatioOptions"] == ["16:9"]
            assert model_b["aspectRatioOptions"] == ["9:16"]
            assert model_a["resolutionOptions"] == ["1080p"]
            assert model_b["resolutionOptions"] == ["720p"]
            assert model_a["durationOptions"] == [5, 10]
            assert model_b["durationOptions"] == [4]

            snapshot = workflow_model_plan.build_model_plan_snapshot(
                {"video": model_a["id"]}
            )
            binding = snapshot["bindings"]["video"]
            assert binding["capability_revision"] == model_a["capabilityRevision"]

            selected = {
                "mode": "textToVideo",
                "aspect_ratio": model_a["aspectRatioOptions"][0],
                "resolution": model_a["resolutionOptions"][0],
                "duration_seconds": model_a["durationOptions"][-1],
                "generate_audio": False,
            }
            compiled = workflow_model_plan.compile_snapshot_video_parameters(
                snapshot,
                selected,
                strict_explicit=True,
            )
            assert compiled["aspect_ratio"] == selected["aspect_ratio"]
            assert compiled["resolution"] == selected["resolution"]
            assert compiled["duration_seconds"] == selected["duration_seconds"]

            generator = NewApiVideoGenerator(
                api_key="rq004-a-key",
                endpoint=capture_a.base_url,
                model="operator-video-a",
                resolution=compiled["resolution"],
                protocol="openai-video",
                create_path="/video/generations",
                preserve_upstream_model=True,
                allowed_durations=tuple(model_a["durationOptions"]),
                allowed_aspect_ratios=tuple(model_a["aspectRatioOptions"]),
                duration_parameter_enabled=True,
                aspect_ratio_parameter_enabled=True,
                resolution_parameter_enabled=True,
            )
            payload = asyncio.run(
                generator._build_newapi_video_v1_payload(
                    image_path="",
                    last_frame_path=None,
                    prompt="rq004 capability mapping",
                    duration=float(compiled["duration_seconds"]),
                    ratio=str(compiled["aspect_ratio"]),
                    references=(),
                    mode=str(compiled["mode"]),
                    log=lambda _message: None,
                )
            )
            try:
                asyncio.run(
                    generator._post_json(
                        f"{capture_a.base_url}/video/generations",
                        payload,
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError("local capture upstream must reject the request")

            assert len(capture_a.requests) == 1
            assert capture_b.requests == []
            sent = capture_a.requests[0]["payload"]
            assert sent["duration_seconds"] == compiled["duration_seconds"]
            assert sent["resolution"] == compiled["resolution"]
            assert sent["aspect_ratio"] == compiled["aspect_ratio"]
            assert sent["model"] == "operator-video-a"

            app, invalid_patches = _build_app(ctx, queue_calls=queue_calls)
            patches.extend(invalid_patches)
            server, thread, base_url = _start_uvicorn(app)
            try:
                with httpx.Client(base_url=base_url, timeout=10) as client:
                    invalid = client.post(
                        "/api/v1/projects/demo/freezone/video/gen",
                        json={
                            "prompt": "rq004 unsupported capability",
                            "model": model_a["id"],
                            "gen_mode": "textToVideo",
                            "aspect_ratio": "2.39:1",
                            "resolution": "4k",
                            "duration_seconds": 99,
                            "generate_audio": False,
                        },
                    )
            finally:
                _stop_uvicorn(server, thread)

            assert invalid.status_code == 400, invalid.text
            detail = invalid.json()["detail"]
            assert detail["code"] == "video_capability_contract_invalid"
            issues = detail["issues"]
            issue_codes = {item["code"] for item in issues}
            assert {
                "unsupported_aspect_ratio",
                "unsupported_resolution",
                "unsupported_duration",
            }.issubset(issue_codes)
            assert all(item["modelId"] == model_a["id"] for item in issues)
            assert all(
                item["capabilityRevision"] == model_a["capabilityRevision"]
                for item in issues
            )
            assert queue_calls == []

            return {
                "schema": "rq004_video_capability_mapping_live_http.v1",
                "channel_revisions": {
                    "direct_rq004-a": model_a["capabilityRevision"],
                    "direct_rq004-b": model_b["capabilityRevision"],
                },
                "picker_options": {
                    "direct_rq004-a": {
                        "aspect_ratio": model_a["aspectRatioOptions"],
                        "resolution": model_a["resolutionOptions"],
                        "duration": model_a["durationOptions"],
                    },
                    "direct_rq004-b": {
                        "aspect_ratio": model_b["aspectRatioOptions"],
                        "resolution": model_b["resolutionOptions"],
                        "duration": model_b["durationOptions"],
                    },
                },
                "workflow_revision": binding["capability_revision"],
                "compiled_selection": compiled,
                "captured_request": sent,
                "unsupported_issue_codes": sorted(issue_codes),
                "unsupported_issue_identity": {
                    "modelId": issues[0]["modelId"],
                    "capabilityRevision": issues[0]["capabilityRevision"],
                },
                "queue_calls": len(queue_calls),
                "paid_provider_calls": 0,
                "channel_b_capture_calls": len(capture_b.requests),
            }
        finally:
            _close_app(patches)
            _stop_capture(capture_a, thread_a)
            _stop_capture(capture_b, thread_b)
            config.STATE_DIR = previous_state_dir


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
