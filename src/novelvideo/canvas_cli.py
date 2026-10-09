"""Portable CLI and JSON-RPC facade for Village Infinite Canvas.

The CLI is deliberately a thin client: the REST API remains the only business
authority, while receipts and revisions prove every mutating operation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


DEFAULT_API_BASE = "http://127.0.0.1:8784/api/v1"
DEFAULT_TIMEOUT = 30
WRITE_COMMANDS = {
    "create_canvas_node",
    "create_image_prompt_node",
    "create_video_prompt_node",
    "annotate",
    "create_shot_sequence",
    "insert_starter_workflow",
    "update_node_prompt",
    "update_node_label",
    "update_node_data",
    "update_node_camera",
    "move_node",
    "connect_nodes",
    "remove_edge",
    "duplicate_node",
    "delete_node",
    "focus_node",
    "select_node",
}


class CanvasCliError(RuntimeError):
    """Stable, user-facing CLI error."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _compact(v)
            for k, v in value.items()
            if k not in {"apiKey", "api_key", "token", "access_token"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value]
    return value


def _id(prefix: str) -> str:
    return f"{prefix}:{uuid4().hex}"


def _safe_error(value: str) -> str:
    return re.sub(
        r'("(?:apiKey|api_key|token|access_token)"\s*:\s*)"[^"]*"',
        r'\1"<redacted>"',
        value,
        flags=re.IGNORECASE,
    )


def _truncate(value: Any, limit: int = 800) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}…"
    return value


def _node_summary(node: Any, *, full: bool = False) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {"value": node}
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if full:
        return {
            "id": node.get("id"),
            "type": node.get("type"),
            "position": node.get("position"),
            "parentId": node.get("parentId"),
            "data": data,
        }
    allowed = {
        "displayName",
        "label",
        "prompt",
        "content",
        "model",
        "aspectRatio",
        "requestAspectRatio",
        "quality",
        "durationSec",
        "generateAudio",
        "genMode",
        "isGenerating",
        "generationError",
        "errorMessage",
    }
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "position": node.get("position"),
        "parentId": node.get("parentId"),
        "data": {key: _truncate(data[key]) for key in allowed if key in data},
    }


def _task_summary(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {"value": task}
    keys = (
        "id",
        "task_id",
        "task_key",
        "job_id",
        "task_type",
        "episode",
        "beat_num",
        "status",
        "progress",
        "error",
        "error_code",
        "updated_at",
    )
    return {key: _truncate(task[key]) for key in keys if key in task}


def _run_summary(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {"value": run}
    keys = (
        "id",
        "workflow_id",
        "status",
        "revision",
        "current_frontier",
        "runtime_phase",
        "error",
        "error_code",
        "updated_at",
        "last_verified_canvas_revision",
    )
    return {key: _truncate(run[key]) for key in keys if key in run}


class CanvasApi:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        *,
        append_api_prefix: bool = True,
    ):
        self.base_url = (
            base_url
            or os.environ.get("VILLAGE_CANVAS_API_BASE_URL")
            or os.environ.get("VILLAGE_CANVAS_API_URL")
            or DEFAULT_API_BASE
        ).rstrip("/")
        if append_api_prefix and not self.base_url.endswith("/api/v1"):
            self.base_url = f"{self.base_url}/api/v1"
        self.token = token or os.environ.get("VILLAGE_CANVAS_AGENT_TOKEN")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        if query:
            pairs = [
                (key, value)
                for key, value in query.items()
                if value is not None and value != ""
            ]
            if pairs:
                url = f"{url}?{urlencode(pairs, doseq=True)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "village-canvas-cli/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if raw_body is not None:
            data = raw_body
            if content_type:
                headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(
                Request(url, data=data, headers=headers, method=method.upper()),
                timeout=self.timeout,
            ) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CanvasCliError(
                f"HTTP {exc.code}: {_safe_error(detail[:1000])}"
            ) from exc
        except URLError as exc:
            raise CanvasCliError(f"连接村长无限画布失败: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanvasCliError("服务端返回了非 JSON 响应") from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def upload(
        self,
        path: str,
        *,
        file_path: str,
        field: str = "file",
        query: dict[str, Any] | None = None,
    ) -> Any:
        """POST one file as multipart/form-data without extra dependencies."""

        source = Path(file_path)
        if not source.is_file():
            raise CanvasCliError(f"上传文件不存在: {source}")
        boundary = f"----villagecanvas{uuid4().hex}"
        payload = source.read_bytes()
        chunk = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{field}"; '
                    f'filename="{source.name}"\r\n'
                ).encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                payload,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        return self.request(
            "POST",
            path,
            query=query,
            raw_body=chunk,
            content_type=f"multipart/form-data; boundary={boundary}",
        )


def _data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


_CONTEXT_FILENAME = ".village-canvas.json"


def _context_path() -> Path:
    return Path.cwd() / _CONTEXT_FILENAME


def _load_context() -> dict[str, str]:
    """Read the working-directory binding written by ``canvas use``."""

    try:
        raw = json.loads(_context_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise CanvasCliError(
            f"上下文文件 {_CONTEXT_FILENAME} 无法读取，请修复或删除它: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise CanvasCliError(f"上下文文件 {_CONTEXT_FILENAME} 必须是 JSON 对象")
    return {
        str(key): str(value).strip()
        for key, value in raw.items()
        if key in ("project", "canvas") and str(value or "").strip()
    }


def _save_context(project: str | None, canvas: str | None) -> dict[str, str]:
    current = _load_context()
    if project:
        current["project"] = project
    if canvas:
        current["canvas"] = canvas
    payload = {key: current[key] for key in ("project", "canvas") if current.get(key)}
    _context_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def _project(args: argparse.Namespace) -> str:
    value = (
        getattr(args, "project", None)
        or os.environ.get("VILLAGE_CANVAS_PROJECT_ID")
        or _load_context().get("project")
    )
    if not value:
        raise CanvasCliError(
            "需要 --project、VILLAGE_CANVAS_PROJECT_ID，或先执行 canvas use 绑定"
        )
    return value


def _canvas(args: argparse.Namespace) -> str:
    value = (
        getattr(args, "canvas", None)
        or os.environ.get("VILLAGE_CANVAS_CANVAS_ID")
        or _load_context().get("canvas")
    )
    if not value:
        raise CanvasCliError(
            "需要 --canvas、VILLAGE_CANVAS_CANVAS_ID，或先执行 canvas use 绑定"
        )
    return value


def _optional_canvas(args: argparse.Namespace) -> str:
    """Canvas id for requests whose canvas scope is optional."""

    return str(
        getattr(args, "canvas", None)
        or os.environ.get("VILLAGE_CANVAS_CANVAS_ID")
        or _load_context().get("canvas")
        or ""
    ).strip()


def _full(args: argparse.Namespace) -> bool:
    """Whether a read command must return raw payloads without summary trims."""

    return bool(getattr(args, "full", False))


def _api_path(args: argparse.Namespace, value: str) -> str:
    """Normalize one relative API path and expand the OpenAPI placeholders."""

    path = str(value or "").strip()
    if not path:
        raise CanvasCliError(
            "需要相对 API 路径，例如 /projects/{project}/freezone/canvases"
        )
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path):
        raise CanvasCliError("只接受相对 API 路径，不接受绝对 URL")
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path.startswith("/api/v1"):
        path = path[len("/api/v1") :]
    if not path.startswith("/"):
        path = f"/{path}"
    if "{project}" in path:
        path = path.replace("{project}", quote(_project(args), safe=""))
    if "{canvas_id}" in path or "{canvas}" in path:
        path = path.replace("{canvas_id}", quote(_canvas(args), safe=""))
        path = path.replace("{canvas}", quote(_canvas(args), safe=""))
    return path


def _api_query(pairs: Iterable[str] | None) -> dict[str, str]:
    query: dict[str, str] = {}
    for item in pairs or ():
        key, separator, raw = str(item).partition("=")
        key = key.strip()
        if not separator or not key:
            raise CanvasCliError(f"--query 需要 key=value 形式: {item}")
        query[key] = raw
    return query


def _api_body(value: str | None) -> Any:
    if value is None:
        return None
    text = value
    if value.startswith("@"):
        source = Path(value[1:])
        if not source.is_file():
            raise CanvasCliError(f"body 文件不存在: {source}")
        text = source.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanvasCliError(f"--body 不是有效 JSON: {exc}") from exc


def _json_arg(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CanvasCliError(f"{label} 不是有效 JSON: {exc}") from exc


def _print(value: Any, args: argparse.Namespace) -> None:
    value = _compact(value)
    if args.json:
        if isinstance(value, dict) and "ok" in value and "data" in value:
            payload = value
        else:
            payload = {"ok": True, "data": value}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(value, dict) and set(value) >= {"revision", "nodes", "edges"}:
        print(f"画布版本：{value.get('revision', 0)}")
        print(f"节点：{len(value.get('nodes') or [])}")
        print(f"连线：{len(value.get('edges') or [])}")
        return
    print(_json(value))


def _canvas_path(args: argparse.Namespace, suffix: str = "") -> str:
    return f"/projects/{quote(_project(args), safe='')}/freezone/canvases/{quote(_canvas(args), safe='')}{suffix}"


def _run_path(args: argparse.Namespace, run_id: str, suffix: str = "") -> str:
    return f"/projects/{quote(_project(args), safe='')}/workflow-runs/{quote(run_id, safe='')}{suffix}"


def _wait_workflow(
    api: CanvasApi,
    args: argparse.Namespace,
    run_id: str,
    initial: Any,
) -> dict[str, Any]:
    terminal = {"completed", "failed", "cancelled", "paused"}
    current = initial
    deadline = time.monotonic() + max(1, int(args.wait_seconds))
    while True:
        current = _data(api.get(_run_path(args, run_id)))
        status = str(current.get("status") or "") if isinstance(current, dict) else ""
        if status in terminal or time.monotonic() >= deadline:
            return {
                "run": current,
                "run_id": run_id,
                "waited": True,
                "terminal": status in terminal,
            }
        time.sleep(1)


_TASK_TERMINAL_STATUS = {"completed", "failed", "cancelled", "canceled", "error"}


def _task_status(task: Any) -> str:
    if not isinstance(task, dict):
        return ""
    return str(task.get("status") or "").strip().lower()


def _wait_task(api: CanvasApi, args: argparse.Namespace) -> dict[str, Any]:
    """Poll one task endpoint until it reaches a terminal status or times out."""

    project = _project(args)
    path = (
        f"/projects/{quote(project, safe='')}/tasks/"
        f"{quote(args.task_type, safe='')}/{args.episode}"
    )
    query = {"beat_num": args.beat_num, "scope": args.scope}
    deadline = time.monotonic() + max(1, int(args.timeout))
    interval = max(0.2, float(args.interval))
    started = time.monotonic()
    polls = 0
    task: Any = None
    while True:
        task = _data(api.get(path, query=query))
        polls += 1
        status = _task_status(task)
        if task is None or status in _TASK_TERMINAL_STATUS:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    status = _task_status(task)
    return {
        "found": task is not None,
        "status": status,
        "terminal": status in _TASK_TERMINAL_STATUS,
        "polls": polls,
        "waited_seconds": round(time.monotonic() - started, 2),
        "task": _task_summary(task) if isinstance(task, dict) else task,
    }


def _generate(api: CanvasApi, args: argparse.Namespace) -> dict[str, Any]:
    """Run one real media generation request and return its task receipt."""

    project = _project(args)
    kind = args.action.removeprefix("gen-")
    prefix = f"/projects/{quote(project, safe='')}"
    body: dict[str, Any] = {}
    if kind == "image":
        if not args.prompt:
            raise CanvasCliError("gen image 需要 --prompt")
        path = f"{prefix}/freezone/gen"
        body["prompt"] = args.prompt
        for key, value in (
            ("node_id", args.node),
            ("model", args.model),
            ("aspect_ratio", args.aspect_ratio),
            ("image_size", args.size),
            ("quality", args.quality),
            ("style", args.style),
            ("gen_mode", args.mode),
        ):
            if value:
                body[key] = value
        if args.reference:
            body["reference_urls"] = list(args.reference)
    elif kind == "video":
        if not args.prompt:
            raise CanvasCliError("gen video 需要 --prompt")
        path = f"{prefix}/freezone/video/gen"
        body["prompt"] = args.prompt
        for key, value in (
            ("node_id", args.node),
            ("model", args.model),
            ("aspect_ratio", args.aspect_ratio),
            ("resolution", args.quality),
            ("gen_mode", args.mode),
            ("camera_template_id", args.camera_template),
            ("dialogue_text", args.dialogue),
            ("speaker", args.speaker),
        ):
            if value:
                body[key] = value
        if args.duration is not None:
            body["duration_seconds"] = args.duration
        if args.generate_audio is not None:
            body["generate_audio"] = args.generate_audio
        if args.character:
            body["character_ids"] = list(args.character)
        if args.reference:
            body["reference_urls"] = list(args.reference)
    elif kind == "audio":
        if not args.text:
            raise CanvasCliError("gen audio 需要 --text")
        path = f"{prefix}/freezone/audio/speech"
        body["text"] = args.text
        for key, value in (
            ("node_id", args.node),
            ("model", args.model),
            ("voice_ref", args.voice_ref),
            ("emotion_prompt", args.emotion),
        ):
            if value:
                body[key] = value
        if args.target_episode is not None:
            body["target_episode"] = args.target_episode
        if args.target_beat is not None:
            body["target_beat"] = args.target_beat
    else:
        raise CanvasCliError(f"未知生成类型: {kind}")
    canvas = _optional_canvas(args)
    if canvas:
        body["canvas_id"] = canvas
    if args.dry_run:
        return {
            "dry_run": True,
            "verified": False,
            "method": "POST",
            "path": path,
            "body": body,
        }
    return api.post(path, body=body)


def _context(api: CanvasApi, args: argparse.Namespace) -> dict[str, Any]:
    canvas = _data(api.get(_canvas_path(args)))
    if not isinstance(canvas, dict):
        return {"canvas": canvas}
    nodes = canvas.get("nodes") or []
    edges = canvas.get("edges") or []
    project = _project(args)
    workflow_runs = _data(
        api.get(
            f"/projects/{quote(project, safe='')}/workflow-runs",
            query={"canvas_id": _canvas(args), "limit": 20},
        )
    )
    model_config = _data(api.get("/model-gateway/config"))
    raw_tasks = _data(api.get(f"/projects/{quote(project, safe='')}/tasks"))
    raw_runs = (
        workflow_runs
        if isinstance(workflow_runs, list)
        else (workflow_runs.get("items", []) if isinstance(workflow_runs, dict) else [])
    )
    tasks = (
        raw_tasks
        if isinstance(raw_tasks, list)
        else (raw_tasks.get("items", []) if isinstance(raw_tasks, dict) else [])
    )
    return {
        "canvas_id": _canvas(args),
        "project_id": _project(args),
        "revision": canvas.get("revision", 0),
        "nodes": [_node_summary(node, full=_full(args)) for node in nodes],
        "edges": [
            {
                key: edge.get(key)
                for key in ("id", "source", "target", "type")
                if key in edge
            }
            for edge in edges
            if isinstance(edge, dict)
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "active_tasks": [_task_summary(task) for task in tasks],
        "workflow_runs": [_run_summary(run) for run in raw_runs],
        "models": _model_summary(model_config),
    }


def _commands_from_file(path: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise CanvasCliError(f"读取命令文件失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CanvasCliError(f"命令文件不是有效 JSON: {exc}") from exc
    commands = raw.get("commands") if isinstance(raw, dict) else raw
    if (
        not isinstance(commands, list)
        or not commands
        or not all(isinstance(item, dict) for item in commands)
    ):
        raise CanvasCliError("命令文件必须是非空数组，或包含 commands 数组")
    return commands


def _apply(
    api: CanvasApi, args: argparse.Namespace, commands: list[dict[str, Any]]
) -> dict[str, Any]:
    if not commands or len(commands) > 100:
        raise CanvasCliError("画布命令数量必须在 1 到 100 之间")
    unknown = [
        item.get("type") for item in commands if item.get("type") not in WRITE_COMMANDS
    ]
    if unknown:
        raise CanvasCliError(
            f"不支持的画布命令: {', '.join(str(item) for item in unknown)}"
        )
    context = _context(api, args)
    revision = context.get("revision", 0)
    expected = (
        args.expected_revision if args.expected_revision is not None else revision
    )
    envelope = {
        "command_id": args.command_id or _id("cli-command"),
        "commands": commands,
        "source_turn_id": args.source_turn_id or "village-canvas-cli",
        "expected_canvas_revision": expected,
    }
    if args.dry_run:
        return {
            "dry_run": True,
            "verified": False,
            "expected_revision": expected,
            "envelope": envelope,
            "diff": {
                "command_types": [item.get("type") for item in commands],
                "before_node_count": context.get("node_count"),
                "before_edge_count": context.get("edge_count"),
            },
        }
    receipt = _data(api.post(_canvas_path(args, "/commands:apply"), body=envelope))
    if not isinstance(receipt, dict):
        raise CanvasCliError("画布命令未返回可验证回执")
    after = _data(api.get(_canvas_path(args)))
    after_revision = after.get("revision") if isinstance(after, dict) else None
    receipt_revision = receipt.get("canvas_revision", receipt.get("revision"))
    verified = isinstance(after_revision, int) and (
        (isinstance(receipt_revision, int) and after_revision >= receipt_revision)
        or after_revision > revision
    )
    if not verified:
        raise CanvasCliError("画布命令已返回，但读取后的 revision 无法与回执对账")
    result = {
        "verified": True,
        "receipt": receipt,
        "before_revision": revision,
        "after_revision": after_revision,
        "node_count": len(after.get("nodes") or [])
        if isinstance(after, dict)
        else None,
        "edge_count": len(after.get("edges") or [])
        if isinstance(after, dict)
        else None,
        "diff": {
            "revision_delta": after_revision - revision,
            "node_delta": (
                len(after.get("nodes") or []) - int(context.get("node_count") or 0)
                if isinstance(after, dict)
                else None
            ),
            "edge_delta": (
                len(after.get("edges") or []) - int(context.get("edge_count") or 0)
                if isinstance(after, dict)
                else None
            ),
            "command_types": [item.get("type") for item in commands],
        },
    }
    if receipt_revision is not None:
        result["revision"] = receipt_revision
    return result


def _node_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.action.removeprefix("node-")
    if action == "create-text":
        return {
            "type": "create_canvas_node",
            "node_type": "textAnnotationNode",
            "text": args.text,
            "x": args.x,
            "y": args.y,
        }
    if action == "create-image":
        command = {
            "type": "create_image_prompt_node",
            "display_name": args.label,
            "prompt": args.prompt,
            "model": args.model,
            "x": args.x,
            "y": args.y,
        }
        if args.aspect_ratio:
            command["aspect_ratio"] = args.aspect_ratio
        if args.size:
            command["image_size"] = args.size
        if args.count is not None:
            command["count"] = args.count
        return command
    if action == "create-video":
        command = {
            "type": "create_video_prompt_node",
            "display_name": args.label,
            "prompt": args.prompt,
            "model": args.model,
            "x": args.x,
            "y": args.y,
        }
        if args.aspect_ratio:
            command["aspect_ratio"] = args.aspect_ratio
        if args.duration is not None:
            command["duration_sec"] = args.duration
        if args.quality:
            command["video_quality"] = args.quality
        if args.mode:
            command["generation_mode"] = args.mode
        if args.generate_audio is not None:
            command["generate_audio"] = args.generate_audio
        if args.count is not None:
            command["count"] = args.count
        return command
    if action == "update":
        if args.prompt is not None:
            return {
                "type": "update_node_prompt",
                "node_id": args.node,
                "prompt": args.prompt,
            }
        if args.label is not None:
            return {
                "type": "update_node_label",
                "node_id": args.node,
                "display_name": args.label,
            }
        if args.data is not None:
            try:
                data = json.loads(args.data)
            except json.JSONDecodeError as exc:
                raise CanvasCliError(f"--data 不是有效 JSON: {exc}") from exc
            return {"type": "update_node_data", "node_id": args.node, "node_data": data}
        raise CanvasCliError("node update 需要 --prompt、--label 或 --data")
    if action == "camera":
        command: dict[str, Any] = {
            "type": "update_node_camera",
            "node_id": args.node,
        }
        if args.camera is not None:
            camera = _json_arg(args.camera, "--camera")
            if not isinstance(camera, dict):
                raise CanvasCliError("--camera 必须是 JSON 对象")
            command["camera"] = camera
        if args.camera_movement is not None:
            # The gateway takes the template id as a plain string, not as JSON.
            movement = str(args.camera_movement).strip()
            if not movement:
                raise CanvasCliError("--camera-movement 不能为空")
            command["camera_movement"] = movement
        if args.clear_camera:
            command["clear_camera"] = True
        if len(command) == 2:
            raise CanvasCliError(
                "node camera 需要 --camera、--camera-movement 或 --clear-camera"
            )
        return command
    if action == "move":
        return {"type": "move_node", "node_id": args.node, "x": args.x, "y": args.y}
    if action == "connect":
        return {"type": "connect_nodes", "source": args.source, "target": args.target}
    if action == "duplicate":
        return {
            "type": "duplicate_node",
            "node_id": args.node,
            "created_node_id": args.created_node_id or _id("node-copy"),
        }
    if action == "delete":
        return {"type": "delete_node", "node_id": args.node}
    raise CanvasCliError(f"未知节点动作: {args.action}")


def _validate_video_command(
    api: CanvasApi, args: argparse.Namespace, command: dict[str, Any]
) -> None:
    model_id = str(command.get("model") or "").strip()
    if not model_id:
        return
    rows = _model_rows(_data(api.get("/model-gateway/config")), "video")
    match = next(
        (
            row
            for row in rows
            if model_id
            in {
                str(row.get("id") or ""),
                str(row.get("modelId") or row.get("model_id") or ""),
                str(row.get("label") or ""),
                f"direct/{row.get('id')}",
            }
        ),
        None,
    )
    if match is None:
        raise CanvasCliError(f"视频模型未找到能力合同: {model_id}")
    aspect_declared = "aspectRatioOptions" in match or "aspect_ratio_options" in match
    aspect_raw = match.get("aspectRatioOptions", match.get("aspect_ratio_options"))
    aspect_options = aspect_raw if isinstance(aspect_raw, list) else []
    aspect = command.get("aspect_ratio")
    if aspect and aspect_declared and aspect not in aspect_options:
        raise CanvasCliError(
            f"模型 {model_id} 不支持尺寸比例 {aspect}，可用值: {', '.join(aspect_options)}"
        )
    quality_declared = "resolutionOptions" in match or "resolution_options" in match
    quality_raw = match.get("resolutionOptions", match.get("resolution_options"))
    quality_options = quality_raw if isinstance(quality_raw, list) else []
    quality = command.get("video_quality")
    if (
        quality
        and quality_declared
        and quality.casefold() not in {str(item).casefold() for item in quality_options}
    ):
        raise CanvasCliError(
            f"模型 {model_id} 不支持清晰度 {quality}，可用值: {', '.join(quality_options)}"
        )
    duration = command.get("duration_sec")
    minimum = match.get("minDuration", match.get("min_duration"))
    maximum = match.get("maxDuration", match.get("max_duration"))
    if duration is not None and minimum is not None and duration < minimum:
        raise CanvasCliError(f"模型 {model_id} 的最短时长是 {minimum} 秒")
    if duration is not None and maximum is not None and duration > maximum:
        raise CanvasCliError(f"模型 {model_id} 的最长时长是 {maximum} 秒")
    audio = command.get("generate_audio")
    native_audio = str(
        match.get("nativeAudio", match.get("native_audio")) or ""
    ).casefold()
    if audio is True and native_audio == "unsupported":
        raise CanvasCliError(f"模型 {model_id} 不支持声音生成")
    modes_declared = "supportedModes" in match or "supported_modes" in match
    modes_raw = match.get("supportedModes", match.get("supported_modes"))
    modes = modes_raw if isinstance(modes_raw, list) else []
    mode = command.get("generation_mode")
    if mode and modes_declared and mode not in modes:
        raise CanvasCliError(
            f"模型 {model_id} 不支持模式 {mode}，可用值: {', '.join(modes)}"
        )


def _canvas_use(api: CanvasApi, args: argparse.Namespace) -> dict[str, Any]:
    """Bind the working directory to a project/canvas (``.village-canvas.json``)."""

    canvas_id = str(getattr(args, "canvas_id", "") or "").strip()
    project = str(getattr(args, "project", None) or "").strip()
    if not canvas_id and not project:
        current = _load_context()
        return {"context_file": str(_context_path()), **current}
    if canvas_id and not project:
        project = (
            os.environ.get("VILLAGE_CANVAS_PROJECT_ID")
            or _load_context().get("project")
            or ""
        )
    if not project:
        raise CanvasCliError(
            "canvas use 需要 --project，或已通过环境变量/既有绑定确定项目"
        )
    if canvas_id:
        # 先向服务端确认画布真实存在，再把绑定写盘（fail-closed，防错字）。
        receipt = _data(api.get(f"/projects/{quote(project, safe='')}/freezone/canvases/{quote(canvas_id, safe='')}"))
        payload = _save_context(project, canvas_id)
        return {
            "context_file": str(_context_path()),
            "verified": True,
            "revision": (receipt or {}).get("revision")
            if isinstance(receipt, dict)
            else None,
            **payload,
        }
    _data(api.get(f"/projects/{quote(project, safe='')}"))
    current = _load_context()
    if current.get("project") != project:
        # 项目切换后旧画布 id 不再可靠，一并清掉。
        current.pop("canvas", None)
    current["project"] = project
    payload = {key: current[key] for key in ("project", "canvas") if current.get(key)}
    _context_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"context_file": str(_context_path()), "verified": True, **payload}


def _canvas_unuse(args: argparse.Namespace) -> dict[str, Any]:
    project_only = bool(getattr(args, "project_only", False))
    canvas_only = bool(getattr(args, "canvas_only", False))
    current = _load_context()
    cleared: list[str] = []
    if not project_only:
        current.pop("canvas", None)
        cleared.append("canvas")
    if not canvas_only:
        current.pop("project", None)
        cleared.append("project")
    path = _context_path()
    if current:
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    elif path.exists():
        path.unlink()
    return {"context_file": str(path), "cleared": cleared, **current}


def _handle(api: CanvasApi, args: argparse.Namespace) -> Any:
    if args.action == "health":
        root = api.base_url.removesuffix("/api/v1")
        return CanvasApi(root, api.token, api.timeout, append_api_prefix=False).get(
            "/healthz"
        )
    if args.action == "project-list":
        return api.get("/projects")
    if args.action == "project-inspect":
        return api.get(f"/projects/{quote(_project(args), safe='')}")
    if args.action == "canvas-list":
        return api.get(f"/projects/{quote(_project(args), safe='')}/freezone/canvases")
    if args.action == "canvas-inspect":
        return api.get(_canvas_path(args))
    if args.action == "canvas-context":
        return _context(api, args)
    if args.action == "canvas-apply":
        return _apply(api, args, _commands_from_file(args.file))
    if args.action == "canvas-viewport":
        return api.get(_canvas_path(args, "/viewport"))
    if args.action == "canvas-history":
        return api.get(_canvas_path(args, "/history"))
    if args.action == "canvas-restore":
        if not args.yes:
            return {
                "dry_run": True,
                "verified": False,
                "confirmation_required": True,
                "history_id": args.history_id,
                "base_revision": args.base_revision,
            }
        return api.post(
            _canvas_path(args, "/restore"),
            body={
                "history_id": args.history_id,
                **(
                    {"base_revision": args.base_revision}
                    if args.base_revision is not None
                    else {}
                ),
            },
        )
    if args.action == "canvas-use":
        return _canvas_use(api, args)
    if args.action == "canvas-unuse":
        return _canvas_unuse(args)
    if args.action == "canvas-delete":
        if not args.yes:
            return {
                "dry_run": True,
                "verified": False,
                "confirmation_required": True,
                "canvas_id": _canvas(args),
            }
        return api.delete(_canvas_path(args))
    if args.action == "canvas-create":
        project = _project(args)
        body = {
            "scope": args.scope,
            "primary_slot": args.primary_slot,
            "overwrite_existing": bool(args.overwrite_existing),
        }
        for key, value in (
            ("canvas_id", args.canvas_id),
            ("episode", args.episode),
            ("beat", args.beat),
            ("asset_kind", args.asset_kind),
            ("character", args.character),
            ("identity_id", args.identity_id),
            ("asset_id", args.asset_id),
        ):
            if value is not None:
                body[key] = value
        return api.post(
            f"/projects/{quote(project, safe='')}/freezone/canvases:from-preset",
            body=body,
        )
    if args.action == "asset-upload":
        project = _project(args)
        return api.upload(
            f"/projects/{quote(project, safe='')}/freezone/upload",
            file_path=args.file,
            field=args.field,
        )
    if args.action.startswith("api-"):
        method = args.action.removeprefix("api-").upper()
        path = _api_path(args, args.path)
        query = _api_query(args.query)
        if method == "GET":
            return api.get(path, query=query)
        if method == "DELETE":
            if not args.yes:
                return {
                    "dry_run": True,
                    "verified": False,
                    "confirmation_required": True,
                    "method": method,
                    "path": path,
                }
            return api.delete(path, query=query)
        return api.request(
            method,
            path,
            query=query,
            body=_api_body(args.body),
        )
    if args.action.startswith("gen-"):
        return _generate(api, args)
    if args.action == "node-list":
        return _context(api, args).get("nodes", [])
    if args.action == "node-inspect":
        canvas = _data(api.get(_canvas_path(args)))
        for node in canvas.get("nodes", []) if isinstance(canvas, dict) else []:
            if node.get("id") == args.node:
                return node
        raise CanvasCliError(f"节点不存在: {args.node}")
    if args.action.startswith("node-"):
        command = _node_command(args)
        if args.action == "node-create-video":
            _validate_video_command(api, args, command)
        if args.action == "node-delete" and not args.yes:
            return {
                "dry_run": True,
                "verified": False,
                "confirmation_required": True,
                "command": command,
            }
        return _apply(api, args, [command])
    if args.action == "model-list":
        return _model_summary(_data(api.get("/model-gateway/config")))
    if args.action == "model-config":
        return api.get("/model-gateway/config")
    if args.action == "model-capabilities":
        config = _data(api.get("/model-gateway/config"))
        rows = _model_rows(config, args.kind)
        if args.model_id:
            rows = [
                row
                for row in rows
                if args.model_id
                in {
                    row.get("id"),
                    row.get("modelId"),
                    row.get("model_id"),
                    row.get("label"),
                }
            ]
        if not rows:
            raise CanvasCliError("没有找到匹配的模型能力合同")
        return rows
    if args.action == "model-check":
        config = _data(api.get("/model-gateway/config"))
        rows = _model_rows(config, args.kind)
        return [
            {
                "id": row.get("id"),
                "model_id": row.get("modelId", row.get("model_id")),
                "enabled": row.get("enabled"),
                "runtime_ready": row.get("runtimeReady", row.get("runtime_ready")),
                "verification_status": row.get(
                    "verificationStatus", row.get("verification_status")
                ),
                "catalog_verification": row.get(
                    "catalogVerification", row.get("catalog_verification")
                ),
            }
            for row in rows
        ]
    if args.action == "workflow-list":
        return api.get(f"/projects/{quote(_project(args), safe='')}/workflows")
    if args.action == "workflow-runs":
        return api.get(
            f"/projects/{quote(_project(args), safe='')}/workflow-runs",
            query={"canvas_id": _canvas(args), "limit": args.limit},
        )
    if args.action == "workflow-status":
        return api.get(_run_path(args, args.run_id))
    if args.action == "workflow-events":
        return api.get(
            _run_path(args, args.run_id, "/events"),
            query={"after_seq": args.after_seq, "limit": args.limit},
        )
    if args.action == "workflow-start":
        snapshot = _data(api.get(_canvas_path(args)))
        body = {
            "workflow_id": args.workflow_id,
            "canvas_id": _canvas(args),
            "run_mode": args.run_mode,
            "inputs": {"request": args.request},
            "goal": args.request,
            "idempotency_key": args.idempotency_key or _id("cli-workflow"),
            "source_turn_id": args.source_turn_id or "village-canvas-cli",
            "canvas_revision": snapshot.get("revision")
            if isinstance(snapshot, dict)
            else None,
        }
        result = api.post(
            f"/projects/{quote(_project(args), safe='')}/workflow-runs", body=body
        )
        if not args.wait:
            return result
        run = _data(result)
        run_id = run.get("id") if isinstance(run, dict) else None
        if not run_id:
            return {"run": run, "waited": False, "wait_error": "服务端未返回 run id"}
        return _wait_workflow(api, args, run_id, run)
    if args.action == "workflow-control":
        if args.command == "cancel" and not args.yes:
            return {
                "dry_run": True,
                "verified": False,
                "confirmation_required": True,
                "command": args.command,
            }
        current = _data(api.get(_run_path(args, args.run_id)))
        expected_revision = args.expected_revision
        if expected_revision is None and isinstance(current, dict):
            expected_revision = current.get("revision")
        body = {
            "command": args.command,
            "step_id": args.step_id or "",
            "direction": args.direction or "",
            "retry_scope": args.retry_scope,
            "item_ids": args.item_ids,
            "idempotency_key": args.idempotency_key or _id("cli-workflow-command"),
            "expected_revision": expected_revision,
        }
        return api.post(_run_path(args, args.run_id, "/command"), body=body)
    if args.action == "task-list":
        return api.get(
            f"/projects/{quote(_project(args), safe='')}/tasks",
            query={"include_runs": args.include_runs},
        )
    if args.action == "task-inspect":
        path = f"/projects/{quote(_project(args), safe='')}/tasks/{quote(args.task_type, safe='')}/{args.episode}"
        return api.get(path, query={"beat_num": args.beat_num, "scope": args.scope})
    if args.action == "task-stop":
        if not args.yes:
            return {"dry_run": True, "verified": False, "confirmation_required": True}
        path = f"/projects/{quote(_project(args), safe='')}/tasks/{quote(args.task_type, safe='')}/{args.episode}"
        return api.delete(path, query={"beat_num": args.beat_num, "scope": args.scope})
    if args.action == "task-wait":
        return _wait_task(api, args)
    if args.action == "task-result":
        project = _project(args)
        return api.get(
            f"/projects/{quote(project, safe='')}/freezone/jobs/"
            f"{quote(args.task_type, safe='')}/{quote(args.job_id, safe='')}/result"
        )
    raise CanvasCliError(f"未知命令: {args.action}")


def _model_rows(config: Any, kind: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        return []
    if kind:
        if kind == "video":
            value = (
                config.get("directVideoModels")
                or config.get("direct_video_models")
                or []
            )
        else:
            value = (
                config.get("directModels") or config.get("direct_models") or {}
            ).get(kind, [])
        return [item for item in value if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    direct = config.get("directModels") or config.get("direct_models") or {}
    for family, items in direct.items():
        rows.extend(
            {"kind": family, **item} for item in items if isinstance(item, dict)
        )
    rows.extend(
        {"kind": "video", **item}
        for item in config.get("directVideoModels") or []
        if isinstance(item, dict)
    )
    return rows


def _model_summary(config: Any) -> dict[str, Any]:
    rows = _model_rows(config)
    return {
        "models": [
            {
                "kind": row.get("kind"),
                "id": row.get("id"),
                "label": row.get("label"),
                "model_id": row.get("modelId", row.get("model_id")),
                "enabled": row.get("enabled"),
                "default": row.get("isDefault", row.get("is_default")),
                "protocol": row.get("protocol"),
                "runtime_ready": row.get("runtimeReady", row.get("runtime_ready")),
                "verification_status": row.get(
                    "verificationStatus", row.get("verification_status")
                ),
                "supported_modes": row.get(
                    "supportedModes", row.get("supported_modes")
                ),
                "parameter_schema": row.get(
                    "parameterSchema", row.get("parameter_schema")
                ),
                "aspect_ratio_options": row.get(
                    "aspectRatioOptions", row.get("aspect_ratio_options")
                ),
                "resolution_options": row.get(
                    "resolutionOptions", row.get("resolution_options")
                ),
                "native_audio": row.get("nativeAudio", row.get("native_audio")),
            }
            for row in rows
        ],
        "count": len(rows),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="village-canvas", description="村长无限画布便携 CLI"
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--token", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--project", default=None)
    parser.add_argument("--canvas", default=None)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, action: str, **kwargs: Any) -> argparse.ArgumentParser:
        item = sub.add_parser(name, **kwargs)
        item.set_defaults(action=action)
        return item

    command("health", "health")
    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    p = project_sub.add_parser("list")
    p.set_defaults(action="project-list")
    p = project_sub.add_parser("inspect")
    p.set_defaults(action="project-inspect")
    canvas = sub.add_parser("canvas")
    canvas_sub = canvas.add_subparsers(dest="canvas_command", required=True)
    for name, action in (
        ("list", "canvas-list"),
        ("inspect", "canvas-inspect"),
    ):
        p = canvas_sub.add_parser(name)
        p.set_defaults(action=action)
    p = canvas_sub.add_parser("context")
    p.set_defaults(action="canvas-context")
    p.add_argument(
        "--full",
        action="store_true",
        help="返回未裁剪的节点数据（含 imageUrl 等产出字段），不做摘要瘦身",
    )
    p = canvas_sub.add_parser("viewport")
    p.set_defaults(action="canvas-viewport")
    p = canvas_sub.add_parser("history")
    p.set_defaults(action="canvas-history")
    p = canvas_sub.add_parser("restore")
    p.set_defaults(action="canvas-restore")
    p.add_argument("--history-id", required=True)
    p.add_argument("--base-revision", type=int)
    p.add_argument("--yes", action="store_true")
    p = canvas_sub.add_parser("unuse")
    p.set_defaults(action="canvas-unuse")
    p.add_argument("--project-only", action="store_true", help="只清除项目绑定")
    p.add_argument("--canvas-only", action="store_true", help="只清除画布绑定")
    p = canvas_sub.add_parser("use")
    p.set_defaults(action="canvas-use")
    p.add_argument(
        "canvas_id",
        nargs="?",
        default="",
        help="要绑定的画布 id；省略时只绑定 --project 指定的项目",
    )
    p = canvas_sub.add_parser("delete")
    p.set_defaults(action="canvas-delete")
    p.add_argument("--yes", action="store_true")
    p = canvas_sub.add_parser("create")
    p.set_defaults(action="canvas-create")
    p.add_argument("--canvas-id")
    p.add_argument("--scope", default="beat")
    p.add_argument("--episode", type=int)
    p.add_argument("--beat", type=int)
    p.add_argument("--primary-slot", default="render")
    p.add_argument("--asset-kind")
    p.add_argument("--character")
    p.add_argument("--identity-id")
    p.add_argument("--asset-id")
    p.add_argument("--overwrite-existing", action="store_true")
    p = canvas_sub.add_parser("apply")
    p.set_defaults(action="canvas-apply")
    p.add_argument("--file", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--command-id")
    p.add_argument("--source-turn-id")
    p.add_argument("--expected-revision", type=int)
    node = sub.add_parser("node")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    p = node_sub.add_parser("list")
    p.set_defaults(action="node-list")
    p.add_argument(
        "--full",
        action="store_true",
        help="返回未裁剪的节点数据（含 imageUrl 等产出字段）",
    )
    p = node_sub.add_parser("inspect")
    p.set_defaults(action="node-inspect")
    p.add_argument("--node", required=True)
    for name, action in (
        ("create-text", "node-create-text"),
        ("create-image", "node-create-image"),
        ("create-video", "node-create-video"),
    ):
        p = node_sub.add_parser(name)
        p.set_defaults(action=action)
        p.add_argument("--text", default="")
        p.add_argument("--prompt", default="")
        p.add_argument("--label", default=name)
        p.add_argument("--model", default="")
        p.add_argument("--x", type=float, default=0)
        p.add_argument("--y", type=float, default=0)
        p.add_argument("--aspect-ratio")
        p.add_argument("--size")
        p.add_argument("--count", type=int)
        p.add_argument("--duration", type=int)
        p.add_argument("--quality")
        p.add_argument("--mode")
        p.add_argument(
            "--generate-audio", action=argparse.BooleanOptionalAction, default=None
        )
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--command-id")
        p.add_argument("--source-turn-id")
        p.add_argument("--expected-revision", type=int)
    p = node_sub.add_parser("update")
    p.set_defaults(action="node-update")
    p.add_argument("--node", required=True)
    p.add_argument("--prompt")
    p.add_argument("--label")
    p.add_argument("--data")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--command-id")
    p.add_argument("--source-turn-id")
    p.add_argument("--expected-revision", type=int)
    p = node_sub.add_parser("camera")
    p.set_defaults(action="node-camera")
    p.add_argument("--node", required=True)
    p.add_argument(
        "--camera",
        help=(
            "图片节点机身/镜头参数 JSON 对象，字段取 camera_body_id / lens_id / "
            "focal_length_mm / aperture；可选值见 "
            "api get /projects/{project}/freezone/image/camera-options"
        ),
    )
    p.add_argument(
        "--camera-movement",
        help=(
            "视频节点运镜模板 id（纯字符串，不是 JSON），例如 follow_tracking；"
            "可选值见 api get /projects/{project}/freezone/video/camera-templates"
        ),
    )
    p.add_argument(
        "--clear-camera",
        action="store_true",
        help="清除已保存的摄像机参数",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--command-id")
    p.add_argument("--source-turn-id")
    p.add_argument("--expected-revision", type=int)
    p = node_sub.add_parser("move")
    p.set_defaults(action="node-move")
    p.add_argument("--node", required=True)
    p.add_argument("--x", type=float, required=True)
    p.add_argument("--y", type=float, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--command-id")
    p.add_argument("--source-turn-id")
    p.add_argument("--expected-revision", type=int)
    p = node_sub.add_parser("connect")
    p.set_defaults(action="node-connect")
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--command-id")
    p.add_argument("--source-turn-id")
    p.add_argument("--expected-revision", type=int)
    for name, action in (("duplicate", "node-duplicate"), ("delete", "node-delete")):
        p = node_sub.add_parser(name)
        p.set_defaults(action=action)
        p.add_argument("--node", required=True)
        if name == "duplicate":
            p.add_argument("--created-node-id")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--command-id")
        p.add_argument("--source-turn-id")
        p.add_argument("--expected-revision", type=int)
    model = sub.add_parser("model")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    for name, action in (("list", "model-list"), ("config", "model-config")):
        p = model_sub.add_parser(name)
        p.set_defaults(action=action)
    p = model_sub.add_parser("capabilities")
    p.set_defaults(action="model-capabilities")
    p.add_argument(
        "--kind",
        choices=(
            "agent",
            "text",
            "vision",
            "image",
            "embedding",
            "audio",
            "video",
        ),
    )
    p.add_argument("--model-id")
    p = model_sub.add_parser("check")
    p.set_defaults(action="model-check")
    p.add_argument(
        "--kind",
        choices=(
            "agent",
            "text",
            "vision",
            "image",
            "embedding",
            "audio",
            "video",
        ),
    )
    workflow = sub.add_parser("workflow")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    p = workflow_sub.add_parser("list")
    p.set_defaults(action="workflow-list")
    p = workflow_sub.add_parser("runs")
    p.set_defaults(action="workflow-runs")
    p.add_argument("--limit", type=int, default=20)
    p = workflow_sub.add_parser("start")
    p.set_defaults(action="workflow-start")
    p.add_argument("--workflow-id", default="custom-canvas-workflow")
    p.add_argument("--request", required=True)
    p.add_argument("--run-mode", choices=("draft", "auto"), default="draft")
    p.add_argument("--idempotency-key")
    p.add_argument("--source-turn-id")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--wait-seconds", type=int, default=30)
    p = workflow_sub.add_parser("status")
    p.set_defaults(action="workflow-status")
    p.add_argument("--run-id", required=True)
    p = workflow_sub.add_parser("events")
    p.set_defaults(action="workflow-events")
    p.add_argument("--run-id", required=True)
    p.add_argument("--after-seq", type=int, default=0)
    p.add_argument("--limit", type=int, default=200)
    p = workflow_sub.add_parser("control")
    p.set_defaults(action="workflow-control")
    p.add_argument("--run-id", required=True)
    p.add_argument(
        "--command",
        choices=("pause", "resume", "cancel", "retry", "steer"),
        required=True,
    )
    p.add_argument("--step-id")
    p.add_argument("--direction")
    p.add_argument(
        "--retry-scope",
        choices=("whole_step", "failed_items_only"),
        default="whole_step",
    )
    p.add_argument("--item-ids", nargs="*", default=[])
    p.add_argument("--idempotency-key")
    p.add_argument("--expected-revision", type=int)
    p.add_argument("--yes", action="store_true")
    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    p = task_sub.add_parser("list")
    p.set_defaults(action="task-list")
    p.add_argument("--include-runs", action="store_true")
    p = task_sub.add_parser("inspect")
    p.set_defaults(action="task-inspect")
    p.add_argument("--task-type", required=True)
    p.add_argument("--episode", type=int, required=True)
    p.add_argument("--beat-num", type=int)
    p.add_argument("--scope")
    p = task_sub.add_parser("stop")
    p.set_defaults(action="task-stop")
    p.add_argument("--task-type", required=True)
    p.add_argument("--episode", type=int, required=True)
    p.add_argument("--beat-num", type=int)
    p.add_argument("--scope")
    p.add_argument("--yes", action="store_true")
    p = task_sub.add_parser("wait")
    p.set_defaults(action="task-wait")
    p.add_argument("--task-type", required=True)
    p.add_argument("--episode", type=int, required=True)
    p.add_argument("--beat-num", type=int)
    p.add_argument("--scope")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--interval", type=float, default=2.0)
    p = task_sub.add_parser("result")
    p.set_defaults(action="task-result")
    p.add_argument("--task-type", required=True)
    p.add_argument("--job-id", required=True)
    gen = sub.add_parser("gen")
    gen_sub = gen.add_subparsers(dest="gen_command", required=True)
    for name, action in (
        ("image", "gen-image"),
        ("video", "gen-video"),
        ("audio", "gen-audio"),
    ):
        p = gen_sub.add_parser(name)
        p.set_defaults(action=action)
        p.add_argument("--prompt", default="")
        p.add_argument("--text", default="")
        p.add_argument("--node")
        p.add_argument("--model")
        p.add_argument("--aspect-ratio")
        p.add_argument("--size")
        p.add_argument("--quality")
        p.add_argument("--style")
        p.add_argument("--mode")
        p.add_argument("--duration", type=int)
        p.add_argument("--reference", action="append")
        p.add_argument("--character", action="append")
        p.add_argument("--camera-template")
        p.add_argument("--dialogue")
        p.add_argument("--speaker")
        p.add_argument("--voice-ref")
        p.add_argument("--emotion")
        p.add_argument("--target-episode", type=int)
        p.add_argument("--target-beat", type=int)
        p.add_argument(
            "--generate-audio", action=argparse.BooleanOptionalAction, default=None
        )
        p.add_argument("--dry-run", action="store_true")
    api_parser = sub.add_parser("api")
    api_sub = api_parser.add_subparsers(dest="api_command", required=True)
    for name, action in (
        ("get", "api-GET"),
        ("post", "api-POST"),
        ("patch", "api-PATCH"),
        ("put", "api-PUT"),
        ("delete", "api-DELETE"),
    ):
        p = api_sub.add_parser(name)
        p.set_defaults(action=action)
        p.set_defaults(method=action.removeprefix("api-"))
        p.add_argument("path", help="相对 API 路径，支持 {project}/{canvas_id} 占位符")
        p.add_argument("--query", action="append", help="重复传入 key=value")
        if name in {"post", "patch", "put"}:
            p.add_argument("--body", help="JSON 字面量或 @文件路径")
        if name == "delete":
            p.add_argument("--yes", action="store_true")
    asset = sub.add_parser("asset")
    asset_sub = asset.add_subparsers(dest="asset_command", required=True)
    p = asset_sub.add_parser("upload")
    p.set_defaults(action="asset-upload")
    p.add_argument("--file", required=True)
    p.add_argument("--field", default="file")
    return parser


def _json_rpc(api: CanvasApi) -> None:
    methods = {
        "health",
        "project.list",
        "project.inspect",
        "canvas.list",
        "canvas.inspect",
        "canvas.context",
        "canvas.apply",
        "canvas.viewport",
        "canvas.history",
        "canvas.restore",
        "canvas.delete",
        "canvas.create",
        "canvas.use",
        "canvas.unuse",
        "node.list",
        "node.inspect",
        "node.create-text",
        "node.create-image",
        "node.create-video",
        "node.update",
        "node.camera",
        "node.move",
        "node.connect",
        "node.duplicate",
        "node.delete",
        "api.get",
        "api.post",
        "api.patch",
        "api.delete",
        "gen.image",
        "gen.video",
        "gen.audio",
        "asset.upload",
        "workflow.list",
        "workflow.runs",
        "workflow.start",
        "workflow.status",
        "workflow.events",
        "workflow.control",
        "task.list",
        "task.inspect",
        "task.stop",
        "task.wait",
        "task.result",
        "model.list",
        "model.config",
        "model.capabilities",
        "model.check",
    }
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = str(request.get("method") or "")
            if method == "tools/list":
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "tools": [{"name": item} for item in sorted(methods)]
                            },
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                continue
            if method not in methods:
                raise CanvasCliError(f"未知 JSON-RPC 方法: {method}")
            defaults = {
                "json": True,
                "project": None,
                "canvas": None,
                "base_url": None,
                "token": None,
                "limit": 20,
                "after_seq": 0,
                "include_runs": False,
                "beat_num": None,
                "scope": None,
                "item_ids": [],
                "expected_revision": None,
                "retry_scope": "whole_step",
                "yes": False,
                "dry_run": False,
                "command_id": None,
                "source_turn_id": None,
                "idempotency_key": None,
                "workflow_id": "custom-canvas-workflow",
                "run_mode": "draft",
                "request": "",
                "run_id": "",
                "kind": None,
                "model_id": None,
                "task_type": "",
                "episode": 0,
                "commands": [],
                "node": "",
                "source": "",
                "target": "",
                "text": "",
                "prompt": "",
                "label": "node",
                "model": "",
                "data": None,
                "path": "",
                "body": None,
                "query": None,
                "full": False,
                "file": "",
                "field": "file",
                "history_id": "",
                "base_revision": None,
                "job_id": "",
                "timeout": 300,
                "interval": 2.0,
                "camera": None,
                "camera_movement": None,
                "clear_camera": False,
                "style": None,
                "reference": None,
                "character": None,
                "camera_template": None,
                "dialogue": None,
                "speaker": None,
                "voice_ref": None,
                "emotion": None,
                "target_episode": None,
                "target_beat": None,
                "canvas_id": None,
                "primary_slot": "render",
                "asset_kind": None,
                "identity_id": None,
                "asset_id": None,
                "overwrite_existing": False,
                "beat": None,
                "x": 0,
                "y": 0,
                "aspect_ratio": None,
                "duration": None,
                "direction": None,
                "step_id": None,
                "command": None,
                "wait": False,
                "wait_seconds": 30,
                "size": None,
                "count": None,
                "quality": None,
                "mode": None,
                "generate_audio": None,
                "created_node_id": None,
            }
            defaults.update(request.get("params") or {})
            params = argparse.Namespace(**defaults)
            aliases = {
                "canvas.context": "canvas-context",
                "canvas.inspect": "canvas-inspect",
                "canvas.list": "canvas-list",
                "canvas.apply": "canvas-apply",
                "project.list": "project-list",
                "workflow.list": "workflow-list",
                "workflow.status": "workflow-status",
            }
            params.action = aliases.get(method, method.replace(".", "-"))
            if method == "canvas.apply" and isinstance(params.commands, list):
                result = _apply(api, params, params.commands)
            else:
                result = _handle(api, params)
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"ok": True, "data": _compact(result)},
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except (
            Exception
        ) as exc:  # JSON-RPC must keep the process alive for the next request.
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": locals().get("request_id"),
                        "error": {"code": -32000, "message": str(exc)},
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def main(argv: Iterable[str] | None = None) -> int:
    tokens = list(argv) if argv is not None else sys.argv[1:]
    if tokens and tokens[0] in {"serve", "serve--stdio"}:
        _json_rpc(CanvasApi())
        return 0
    if tokens and tokens[0] == "mcp":
        from novelvideo.chat.village_canvas_mcp import main as mcp_main

        mcp_main()
        return 0
    global_flags = {"--json", "--base-url", "--token", "--project", "--canvas"}
    leading: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in global_flags:
            leading.append(token)
            if token != "--json":
                index += 1
                if index >= len(tokens):
                    raise CanvasCliError(f"{token} 缺少值")
                leading.append(tokens[index])
        else:
            remaining.append(token)
        index += 1
    args = _parser().parse_args(leading + remaining)
    try:
        _print(_handle(CanvasApi(args.base_url, args.token), args), args)
    except CanvasCliError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
