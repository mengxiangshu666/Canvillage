from __future__ import annotations

import json
from argparse import Namespace

import pytest

from novelvideo.canvas_cli import (
    WRITE_COMMANDS,
    CanvasApi,
    CanvasCliError,
    _api_body,
    _api_path,
    _api_query,
    _apply,
    _context,
    _generate,
    _handle,
    _model_summary,
    _node_command,
    _node_summary,
    _parser,
    _validate_video_command,
    _wait_task,
)


class FakeApi:
    def __init__(self) -> None:
        self.revision = 4
        self.posts: list[tuple[str, dict]] = []

    def get(self, path: str, **_kwargs):
        if path.endswith("/tasks"):
            return {"ok": True, "data": []}
        if path.endswith("/workflow-runs"):
            return {"ok": True, "data": []}
        if path == "/model-gateway/config":
            return {
                "ok": True,
                "data": {
                    "directModels": {
                        "agent": [
                            {
                                "id": "agent-1",
                                "modelId": "agent-model",
                                "runtimeReady": True,
                            }
                        ]
                    },
                    "directVideoModels": [
                        {
                            "id": "video-1",
                            "modelId": "video-model",
                            "aspectRatioOptions": ["9:16"],
                        }
                    ],
                },
            }
        return {
            "ok": True,
            "data": {
                "revision": self.revision,
                "nodes": [{"id": "node-1", "type": "textAnnotationNode"}],
                "edges": [],
            },
        }

    def post(self, path: str, *, body: dict):
        self.posts.append((path, body))
        self.revision += 1
        return {
            "ok": True,
            "data": {
                "command_id": body["command_id"],
                "canvas_revision": self.revision,
                "created_node_ids": ["node-2"],
            },
        }


def _args(**overrides):
    values = {
        "project": "project-1",
        "canvas": "canvas-1",
        "expected_revision": None,
        "command_id": None,
        "source_turn_id": None,
        "dry_run": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_apply_uses_revision_and_verifies_authoritative_receipt():
    api = FakeApi()
    result = _apply(
        api,
        _args(),
        [
            {
                "type": "create_canvas_node",
                "node_type": "textAnnotationNode",
                "text": "角色",
            }
        ],
    )

    assert result["verified"] is True
    assert result["before_revision"] == 4
    assert result["after_revision"] == 5
    assert api.posts[0][1]["expected_canvas_revision"] == 4
    assert api.posts[0][1]["command_id"].startswith("cli-command:")


def test_apply_dry_run_does_not_call_write_api():
    api = FakeApi()
    result = _apply(
        api,
        _args(dry_run=True),
        [{"type": "move_node", "node_id": "node-1", "x": 10, "y": 20}],
    )

    assert result["dry_run"] is True
    assert api.posts == []
    assert result["envelope"]["expected_canvas_revision"] == 4


def test_node_commands_are_real_canvas_command_shapes():
    assert _node_command(
        Namespace(action="node-create-text", text="场景", x=1, y=2)
    ) == {
        "type": "create_canvas_node",
        "node_type": "textAnnotationNode",
        "text": "场景",
        "x": 1,
        "y": 2,
    }
    assert _node_command(
        Namespace(
            action="node-create-video",
            label="镜头",
            prompt="推进",
            model="video-1",
            x=0,
            y=0,
            aspect_ratio="9:16",
            duration=8,
            quality="1080P",
            mode="textToVideo",
            generate_audio=True,
            count=1,
        )
    ) == {
        "type": "create_video_prompt_node",
        "display_name": "镜头",
        "prompt": "推进",
        "model": "video-1",
        "x": 0,
        "y": 0,
        "aspect_ratio": "9:16",
        "duration_sec": 8,
        "video_quality": "1080P",
        "generation_mode": "textToVideo",
        "generate_audio": True,
        "count": 1,
    }


def test_model_summary_keeps_capabilities_without_credentials():
    summary = _model_summary(
        {
            "directModels": {
                "agent": [
                    {
                        "id": "agent-1",
                        "modelId": "a",
                        "apiKey": "secret",
                        "runtimeReady": True,
                    }
                ]
            },
            "directVideoModels": [
                {"id": "video-1", "modelId": "v", "nativeAudio": "optional"}
            ],
        }
    )

    assert summary["count"] == 2
    assert all("apiKey" not in row for row in summary["models"])
    assert summary["models"][-1]["native_audio"] == "optional"


def test_context_is_compact_and_video_contract_blocks_unsupported_values():
    api = FakeApi()
    context = _context(api, _args())

    assert context["nodes"] == [
        {
            "id": "node-1",
            "type": "textAnnotationNode",
            "position": None,
            "parentId": None,
            "data": {},
        }
    ]
    with pytest.raises(CanvasCliError, match="不支持尺寸比例"):
        _validate_video_command(
            api,
            _args(),
            {
                "type": "create_video_prompt_node",
                "model": "video-model",
                "aspect_ratio": "16:9",
            },
        )


class EmptyVideoCapabilityApi(FakeApi):
    def get(self, path: str, **kwargs):
        if path == "/model-gateway/config":
            return {
                "ok": True,
                "data": {
                    "directVideoModels": [
                        {
                            "id": "video-empty",
                            "modelId": "video-empty-model",
                            "supportedModes": [],
                            "aspectRatioOptions": [],
                            "resolutionOptions": [],
                        }
                    ]
                },
            }
        return super().get(path, **kwargs)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("generation_mode", "textToVideo", "不支持模式"),
        ("aspect_ratio", "16:9", "不支持尺寸比例"),
        ("video_quality", "720P", "不支持清晰度"),
    ],
)
def test_video_cli_does_not_treat_explicit_empty_capabilities_as_unspecified(
    field: str,
    value: str,
    message: str,
):
    with pytest.raises(CanvasCliError, match=message):
        _validate_video_command(
            EmptyVideoCapabilityApi(),
            _args(),
            {
                "type": "create_video_prompt_node",
                "model": "video-empty-model",
                field: value,
            },
        )


def _parsed(*argv: str):
    return _parser().parse_args(list(argv))


def test_api_path_expands_placeholders_and_strips_the_version_prefix():
    args = _parsed("--project", "p-1", "--canvas", "c-1", "api", "get", "/x")

    assert (
        _api_path(args, "/projects/{project}/freezone/canvases/{canvas_id}/viewport")
        == "/projects/p-1/freezone/canvases/c-1/viewport"
    )
    assert (
        _api_path(args, "/api/v1/projects/{project}/freezone/canvases/{canvas}")
        == "/projects/p-1/freezone/canvases/c-1"
    )
    assert _api_path(args, "projects/{project}") == "/projects/p-1"


@pytest.mark.parametrize(
    "value",
    ["http://evil.example.com/x", "https://evil.example.com/x", ""],
)
def test_api_path_rejects_absolute_urls_and_empty_paths(value: str):
    args = _parsed("--project", "p-1", "--canvas", "c-1", "api", "get", "/x")

    with pytest.raises(CanvasCliError):
        _api_path(args, value)


def test_api_query_and_body_accept_inline_json_and_files(tmp_path):
    assert _api_query(["a=1", "b=two=2"]) == {"a": "1", "b": "two=2"}
    with pytest.raises(CanvasCliError, match="key=value"):
        _api_query(["broken"])

    assert _api_body('{"a": 1}') == {"a": 1}
    assert _api_body(None) is None

    source = tmp_path / "body.json"
    source.write_text('{"b": 2}', encoding="utf-8")
    assert _api_body(f"@{source}") == {"b": 2}
    with pytest.raises(CanvasCliError, match="不是有效 JSON"):
        _api_body("{not json}")
    with pytest.raises(CanvasCliError, match="body 文件不存在"):
        _api_body("@/definitely/missing/body.json")


def test_full_reads_drop_the_summary_whitelist_and_truncation():
    node = {
        "id": "n-1",
        "type": "imageGenNode",
        "position": {"x": 1, "y": 2},
        "parentId": None,
        "data": {"prompt": "p" * 900, "imageUrl": "/static/a.png", "count": 2},
    }

    compact = _node_summary(node)
    full = _node_summary(node, full=True)

    assert "imageUrl" not in compact["data"]
    assert compact["data"]["prompt"].endswith("…")
    assert full["data"]["imageUrl"] == "/static/a.png"
    assert full["data"]["count"] == 2
    assert full["data"]["prompt"] == "p" * 900


def test_canvas_context_full_flag_is_opt_in():
    api = FakeApi()
    compact = _context(
        api, _parsed("--project", "p", "--canvas", "c", "canvas", "context")
    )
    full = _context(
        api, _parsed("--project", "p", "--canvas", "c", "canvas", "context", "--full")
    )

    assert compact["nodes"][0]["data"] == {}
    assert full["node_count"] == compact["node_count"]


def test_node_camera_command_matches_the_gateway_contract():
    assert _node_command(
        _parsed(
            "--project",
            "p",
            "--canvas",
            "c",
            "node",
            "camera",
            "--node",
            "n-1",
            "--camera",
            '{"camera_body_id": "arri_alexa_35", "lens_id": "cooke_s4i",'
            ' "focal_length_mm": 35, "aperture": "f/2.8"}',
        )
    ) == {
        "type": "update_node_camera",
        "node_id": "n-1",
        "camera": {
            "camera_body_id": "arri_alexa_35",
            "lens_id": "cooke_s4i",
            "focal_length_mm": 35,
            "aperture": "f/2.8",
        },
    }
    assert "update_node_camera" in WRITE_COMMANDS

    # The gateway takes the movement template id as a plain string; JSON here
    # was rejected with "视频运镜模板不在当前画布目录中".
    assert _node_command(
        _parsed(
            "--project",
            "p",
            "--canvas",
            "c",
            "node",
            "camera",
            "--node",
            "n-2",
            "--camera-movement",
            "follow_tracking",
        )
    ) == {
        "type": "update_node_camera",
        "node_id": "n-2",
        "camera_movement": "follow_tracking",
    }
    with pytest.raises(CanvasCliError, match="--camera-movement 不能为空"):
        _node_command(
            _parsed(
                "--project",
                "p",
                "--canvas",
                "c",
                "node",
                "camera",
                "--node",
                "n-2",
                "--camera-movement",
                "   ",
            )
        )
    assert _node_command(
        _parsed(
            "--project",
            "p",
            "--canvas",
            "c",
            "node",
            "camera",
            "--node",
            "n-1",
            "--clear-camera",
        )
    ) == {
        "type": "update_node_camera",
        "node_id": "n-1",
        "clear_camera": True,
    }

    with pytest.raises(CanvasCliError, match="需要 --camera"):
        _node_command(
            _parsed(
                "--project", "p", "--canvas", "c", "node", "camera", "--node", "n-1"
            )
        )
    with pytest.raises(CanvasCliError, match="必须是 JSON 对象"):
        _node_command(
            _parsed(
                "--project",
                "p",
                "--canvas",
                "c",
                "node",
                "camera",
                "--node",
                "n-1",
                "--camera",
                "[1]",
            )
        )


@pytest.mark.parametrize(
    ("argv", "path", "expected"),
    [
        (
            ("gen", "image", "--prompt", "雨夜巷战", "--aspect-ratio", "1:1"),
            "/freezone/gen",
            {"prompt": "雨夜巷战", "aspect_ratio": "1:1"},
        ),
        (
            ("gen", "video", "--prompt", "推进", "--duration", "8"),
            "/freezone/video/gen",
            {"prompt": "推进", "duration_seconds": 8},
        ),
        (
            ("gen", "audio", "--text", "台词"),
            "/freezone/audio/speech",
            {"text": "台词"},
        ),
    ],
)
def test_generate_dry_run_targets_the_real_endpoints(argv, path, expected):
    api = FakeApi()
    args = _parsed("--project", "p-1", "--canvas", "c-1", *argv, "--dry-run")
    result = _generate(api, args)

    assert result["dry_run"] is True
    assert result["path"] == f"/projects/p-1{path}"
    assert result["body"]["canvas_id"] == "c-1"
    for key, value in expected.items():
        assert result["body"][key] == value
    assert api.posts == []


def test_generate_requires_the_mandatory_field():
    api = FakeApi()

    with pytest.raises(CanvasCliError, match="需要 --prompt"):
        _generate(api, _parsed("--project", "p", "gen", "image", "--dry-run"))
    with pytest.raises(CanvasCliError, match="需要 --text"):
        _generate(api, _parsed("--project", "p", "gen", "audio", "--dry-run"))


class PollingApi:
    def __init__(self, sequence) -> None:
        self.sequence = list(sequence)
        self.calls = 0

    def get(self, _path: str, **_kwargs):
        item = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        return {"ok": True, "data": item}


def test_task_wait_stops_at_terminal_status():
    api = PollingApi([{"status": "running"}, {"status": "completed", "progress": 1.0}])
    args = _parsed(
        "--project",
        "p",
        "task",
        "wait",
        "--task-type",
        "freezone_gen",
        "--episode",
        "0",
        "--interval",
        "0.2",
    )
    result = _wait_task(api, args)

    assert result["terminal"] is True
    assert result["status"] == "completed"
    assert result["polls"] == 2
    assert result["task"]["progress"] == 1.0


def test_task_wait_returns_immediately_when_the_task_does_not_exist():
    api = PollingApi([None])
    args = _parsed(
        "--project",
        "p",
        "task",
        "wait",
        "--task-type",
        "freezone_gen",
        "--episode",
        "0",
        "--timeout",
        "60",
    )
    result = _wait_task(api, args)

    assert result["found"] is False
    assert result["terminal"] is False
    assert result["polls"] == 1
    assert result["waited_seconds"] < 5


def test_api_delete_requires_confirmation_before_any_request():
    api = FakeApi()
    args = _parsed(
        "--project",
        "p-1",
        "--canvas",
        "c-1",
        "api",
        "delete",
        "/projects/{project}/freezone/canvases/{canvas_id}",
    )
    result = _handle(api, args)

    assert result["confirmation_required"] is True
    assert result["path"] == "/projects/p-1/freezone/canvases/c-1"
    assert api.posts == []


def test_canvas_delete_requires_confirmation_before_any_request():
    api = FakeApi()
    args = _parsed("--project", "p-1", "--canvas", "c-1", "canvas", "delete")
    result = _handle(api, args)

    assert result["confirmation_required"] is True
    assert result["canvas_id"] == "c-1"


def test_upload_builds_multipart_without_extra_dependencies(tmp_path):
    source = tmp_path / "shot.png"
    source.write_bytes(b"PNGDATA")
    captured: dict = {}

    class RecordingApi(CanvasApi):
        def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured.update(kwargs)
            return {"ok": True}

    RecordingApi().upload("/projects/p/freezone/upload", file_path=str(source))

    assert captured["method"] == "POST"
    assert captured["path"] == "/projects/p/freezone/upload"
    assert "multipart/form-data; boundary=" in captured["content_type"]
    body = captured["raw_body"]
    assert b'name="file"; filename="shot.png"' in body
    assert b"PNGDATA" in body


def test_upload_rejects_a_missing_file():
    with pytest.raises(CanvasCliError, match="上传文件不存在"):
        CanvasApi().upload("/projects/p/freezone/upload", file_path="/missing/shot.png")


def test_canvas_use_verifies_then_binds_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _handle(FakeApi(), _parsed("--project", "p-1", "canvas", "use", "c-1"))

    binding = json.loads(
        (tmp_path / ".village-canvas.json").read_text(encoding="utf-8")
    )
    assert binding == {"project": "p-1", "canvas": "c-1"}
    assert result["verified"] is True
    assert result["revision"] == 4


def test_canvas_use_reuses_the_bound_project_for_a_new_canvas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VILLAGE_CANVAS_PROJECT_ID", raising=False)
    (tmp_path / ".village-canvas.json").write_text(
        json.dumps({"project": "p-9"}), encoding="utf-8"
    )
    _handle(FakeApi(), _parsed("canvas", "use", "c-7"))

    binding = json.loads(
        (tmp_path / ".village-canvas.json").read_text(encoding="utf-8")
    )
    assert binding == {"project": "p-9", "canvas": "c-7"}


def test_canvas_use_without_args_prints_the_current_binding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".village-canvas.json").write_text(
        json.dumps({"project": "p-9", "canvas": "c-7"}), encoding="utf-8"
    )
    result = _handle(FakeApi(), _parsed("canvas", "use"))

    assert result["project"] == "p-9"
    assert result["canvas"] == "c-7"
    assert result["context_file"].endswith(".village-canvas.json")


def test_canvas_use_switching_project_drops_the_stale_canvas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".village-canvas.json").write_text(
        json.dumps({"project": "p-1", "canvas": "c-1"}), encoding="utf-8"
    )
    result = _handle(FakeApi(), _parsed("--project", "p-2", "canvas", "use"))

    binding = json.loads(
        (tmp_path / ".village-canvas.json").read_text(encoding="utf-8")
    )
    assert binding == {"project": "p-2"}
    assert result["verified"] is True


def test_canvas_use_requires_a_project_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VILLAGE_CANVAS_PROJECT_ID", raising=False)
    with pytest.raises(CanvasCliError):
        _handle(FakeApi(), _parsed("canvas", "use", "c-1"))


def test_canvas_commands_resolve_ids_from_the_binding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VILLAGE_CANVAS_PROJECT_ID", raising=False)
    monkeypatch.delenv("VILLAGE_CANVAS_CANVAS_ID", raising=False)
    (tmp_path / ".village-canvas.json").write_text(
        json.dumps({"project": "p-1", "canvas": "c-1"}), encoding="utf-8"
    )
    result = _handle(FakeApi(), _parsed("canvas", "delete"))

    assert result["confirmation_required"] is True
    assert result["canvas_id"] == "c-1"


def test_canvas_unuse_clears_the_whole_binding_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = tmp_path / ".village-canvas.json"
    context.write_text(
        json.dumps({"project": "p-1", "canvas": "c-1"}), encoding="utf-8"
    )
    result = _handle(FakeApi(), _parsed("canvas", "unuse"))

    assert context.exists() is False
    assert result["cleared"] == ["canvas", "project"]


def test_canvas_unuse_can_keep_the_project_and_drop_the_canvas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = tmp_path / ".village-canvas.json"
    context.write_text(
        json.dumps({"project": "p-1", "canvas": "c-1"}), encoding="utf-8"
    )
    result = _handle(FakeApi(), _parsed("canvas", "unuse", "--canvas-only"))

    assert json.loads(context.read_text(encoding="utf-8")) == {"project": "p-1"}
    assert result["cleared"] == ["canvas"]
