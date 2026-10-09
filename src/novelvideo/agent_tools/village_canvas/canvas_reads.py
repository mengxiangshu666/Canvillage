from __future__ import annotations

import asyncio
import base64
import logging
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .canvas_reads_vision import (  # noqa: F401
    _read_vision_source,
    _vision_url_is_internal,
)
from .contracts import _canvas_payload_from_response
from .core import (
    SCRIPT_UPLOAD_EXTENSIONS,
    _agent_context_value,
    _audio_ui_spec,
    _canvas_id_from_args,
    _default_project_id,
    _freezone_tool_error,
    _image_ui_spec,
    _limit_items,
    _matches_any_scene_name,
    _matches_any_text,
    _normalize_api_path,
    _requested_beats,
    _requested_names,
    _requested_queries,
    _requested_scene_indices,
    _requested_scene_names,
    _require_paid_media_authorization,
    _video_ui_spec,
    _with_tool_trace,
)
from .runtime import runtime_handler, runtime_proxy

logger = logging.getLogger(__name__)

_project_from_args = runtime_proxy("_project_from_args")
_request = runtime_proxy("_request")
tool_error = runtime_proxy("tool_error")
tool_result = runtime_proxy("tool_result")


def _handle_get(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            _request("GET", str(args.get("path") or ""), query=args.get("query"))
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_post(args: dict[str, Any], **_: Any) -> str:
    try:
        path = _normalize_api_path(str(args.get("path") or ""))
        paid_markers = (
            "/freezone/image/",
            "/freezone/video/",
            "/freezone/audio/",
            "/generate-async",
            "/sketches/generate",
            "/audio/generate",
            "/beats/regenerate",
            "/videos/compose",
            "/production/control/runs",
        )
        if any(marker in path for marker in paid_markers) or re.search(
            r"/episodes/\d+/beats/\d+/video$", path
        ):
            raise ValueError(
                "paid media endpoints require the matching dedicated "
                "village_canvas/freezone generation tool"
            )
        return tool_result(
            _request("POST", path, query=args.get("query"), body=args.get("body"))
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_patch(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            _request(
                "PATCH",
                str(args.get("path") or ""),
                query=args.get("query"),
                body=args.get("body"),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_delete(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(
            _request(
                "DELETE",
                str(args.get("path") or ""),
                query=args.get("query"),
                body=args.get("body"),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_pipeline_status(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        query = {"episode": args.get("episode")}
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/pipeline/status", query=query)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _state_sql_project(args: dict[str, Any]) -> str:
    """Keep installation-scoped reads working when no project is in context."""

    try:
        return _project_from_args(args)
    except ValueError:
        return ""


def _handle_state_sql_schema(args: dict[str, Any], **_: Any) -> str:
    """Read real table and column names before writing a query."""

    try:
        database = str(args.get("database") or "").strip()
        if not database:
            raise ValueError("database is required")
        query = {
            "database": database,
            "project": _state_sql_project(args),
        }
        # Only send `table` when the model named one: an empty value would ask
        # the same question twice and make the outbound request harder to read.
        table = str(args.get("table") or "").strip()
        if table:
            query["table"] = table
        return tool_result(
            _request(
                "GET",
                "/api/v1/state/schema",
                query=query,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_state_sql_query(args: dict[str, Any], **_: Any) -> str:
    """Run one bounded read-only SELECT/WITH over the local project state."""

    try:
        database = str(args.get("database") or "").strip()
        if not database:
            raise ValueError("database is required")
        sql = str(args.get("sql") or "").strip()
        if not sql:
            raise ValueError("sql is required")
        body: dict[str, Any] = {
            "database": database,
            "sql": sql,
            "project_id": _state_sql_project(args),
        }
        if args.get("max_rows") is not None:
            body["max_rows"] = int(args["max_rows"])
        return tool_result(_request("POST", "/api/v1/state/query", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_knowledge_search(args: dict[str, Any], **_: Any) -> str:
    """Search durable memory, local knowledge notes, and Obsidian references."""
    try:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        raw_sources = args.get("sources")
        if isinstance(raw_sources, list):
            sources = ",".join(
                str(item).strip() for item in raw_sources if str(item).strip()
            )
        else:
            sources = str(raw_sources or "memory,knowledge,obsidian,cognee").strip()
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/knowledge/search",
                query={
                    "query": query,
                    "project": args.get("project_id") or args.get("project"),
                    "sources": sources,
                    "limit": args.get("limit"),
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_knowledge_load_reference(args: dict[str, Any], **_: Any) -> str:
    """Load one explicitly addressed note, memory, or Cognee chunk."""
    try:
        uri = str(args.get("uri") or "").strip()
        if not uri:
            raise ValueError("uri is required")
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/knowledge/reference",
                query={
                    "uri": uri,
                    "project": args.get("project_id") or args.get("project"),
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_memory_preview(args: dict[str, Any], **_: Any) -> str:
    """Preview deterministic memory hooks without persistence or generation."""
    try:
        project = _project_from_args(args)
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        body: dict[str, Any] = {"text": text, "project_id": project}
        for key in ("task_stage", "node_type", "duration_sec", "references"):
            if args.get(key) is not None:
                body[key] = args[key]
        response = _request("POST", "/api/v1/chat/memories/preview", body=body)
        if isinstance(response, dict):
            response = {"ok": True, **response}
        return tool_result(response)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_shared_context_snapshot(args: dict[str, Any], **_: Any) -> str:
    """Read one bounded shared Agent blackboard from authoritative sources."""
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        raw_sources = args.get("sources")
        if isinstance(raw_sources, list):
            sources = ",".join(
                str(item).strip() for item in raw_sources if str(item).strip()
            )
        else:
            sources = str(raw_sources or "memory,knowledge,obsidian,cognee").strip()
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/context/blackboard",
                query={
                    "project": project,
                    "canvas_id": canvas_id,
                    "query": args.get("query"),
                    "sources": sources,
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_shared_context_expert_plan(args: dict[str, Any], **_: Any) -> str:
    """Read the bounded expert plan and dynamically selected Agent fleet."""
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        raw_sources = args.get("sources")
        if isinstance(raw_sources, list):
            sources = ",".join(
                str(item).strip() for item in raw_sources if str(item).strip()
            )
        else:
            sources = str(raw_sources or "memory,knowledge,obsidian,cognee").strip()
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/context/expert-plan",
                query={
                    "project": project,
                    "canvas_id": canvas_id,
                    "query": query,
                    "sources": sources,
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_shared_context_tool_allowlist(args: dict[str, Any], **_: Any) -> str:
    """Compile the bounded runtime capability allowlist for one shadow plan."""
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        raw_sources = args.get("sources")
        if isinstance(raw_sources, list):
            sources = ",".join(
                str(item).strip() for item in raw_sources if str(item).strip()
            )
        else:
            sources = str(raw_sources or "memory,knowledge,obsidian,cognee").strip()
        raw_candidates = args.get("candidates")
        if isinstance(raw_candidates, list):
            candidates = ",".join(
                str(item).strip() for item in raw_candidates if str(item).strip()
            )
        else:
            candidates = str(raw_candidates or "").strip()
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/context/tool-allowlist",
                query={
                    "project": project,
                    "canvas_id": canvas_id,
                    "query": query,
                    "sources": sources,
                    "mode": args.get("mode") or "observe",
                    "candidates": candidates,
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_shared_context_execution_checkpoint(args: dict[str, Any], **_: Any) -> str:
    """Validate current plan/allowlist revisions before a capability call."""
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        query = str(args.get("query") or "").strip()
        capability_id = str(args.get("capability_id") or "").strip()
        if not query or not capability_id:
            raise ValueError("query and capability_id are required")
        raw_sources = args.get("sources")
        if isinstance(raw_sources, list):
            sources = ",".join(
                str(item).strip() for item in raw_sources if str(item).strip()
            )
        else:
            sources = str(raw_sources or "memory,knowledge,obsidian,cognee").strip()
        raw_candidates = args.get("candidates")
        if isinstance(raw_candidates, list):
            candidates = ",".join(
                str(item).strip() for item in raw_candidates if str(item).strip()
            )
        else:
            candidates = str(raw_candidates or "").strip()
        return tool_result(
            _request(
                "GET",
                "/api/v1/chat/context/execution-checkpoint",
                query={
                    "project": project,
                    "canvas_id": canvas_id,
                    "query": query,
                    "capability_id": capability_id,
                    "plan_revision": args.get("plan_revision"),
                    "allowlist_revision": args.get("allowlist_revision"),
                    "confirm": bool(args.get("confirm")),
                    "sources": sources,
                    "mode": args.get("mode") or "observe",
                    "candidates": candidates,
                },
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_tasks(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        try:
            run_limit = max(1, min(int(args.get("run_limit") or 200), 1000))
        except (TypeError, ValueError):
            raise ValueError(
                "run_limit must be an integer between 1 and 1000"
            ) from None
        query = {
            "include_runs": bool(args.get("include_runs", True)),
            "run_limit": run_limit,
        }
        response = _request("GET", f"/api/v1/projects/{project}/tasks", query=query)
        if not isinstance(response, dict):
            return tool_result(response)
        requested_type = str(args.get("task_type") or "").strip()
        requested_status = str(args.get("status") or "").strip().casefold()
        requested_episode = args.get("episode")

        def matches(item: object) -> bool:
            if not isinstance(item, dict):
                return False
            if requested_type and str(item.get("task_type") or "") != requested_type:
                return False
            if (
                requested_status
                and str(item.get("status") or "").casefold() != requested_status
            ):
                return False
            return requested_episode is None or int(item.get("episode") or 0) == int(
                requested_episode
            )

        filtered = dict(response)
        for key in ("data", "runs"):
            values = response.get(key)
            if isinstance(values, list):
                filtered[key] = [item for item in values if matches(item)]
        return tool_result(filtered)
    except Exception as exc:
        return tool_error(str(exc))


def _generation_output_url(result: object) -> str:
    if not isinstance(result, dict):
        return ""
    for key in (
        "output_url",
        "image_url",
        "video_url",
        "audio_url",
        "artifact_url",
        "url",
    ):
        value = str(result.get(key) or "").strip()
        if value.startswith(("/static/", "http://", "https://")):
            return value
    for key in ("result", "generation_history_record"):
        nested = _generation_output_url(result.get(key))
        if nested:
            return nested
    return ""


def _handle_generation_history(args: dict[str, Any], **_: Any) -> str:
    """Read a bounded projection of persistent media task history."""
    try:
        project = _project_from_args(args)
        try:
            run_limit = max(1, min(int(args.get("run_limit") or 500), 1000))
            limit = max(1, min(int(args.get("limit") or 100), 200))
        except (TypeError, ValueError):
            raise ValueError("run_limit and limit must be bounded integers") from None
        response = _request(
            "GET",
            f"/api/v1/projects/{project}/tasks",
            query={"include_runs": True, "run_limit": run_limit},
        )
        runs = response.get("runs") if isinstance(response, dict) else []
        requested_status = str(args.get("status") or "completed").strip().casefold()
        requested_type = str(args.get("task_type") or "").strip()
        requested_job = str(args.get("job_id") or "").strip()
        requested_canvas = str(args.get("canvas_id") or "").strip()
        requested_episode = args.get("episode")
        items: list[dict[str, Any]] = []
        for run in runs if isinstance(runs, list) else []:
            if not isinstance(run, dict):
                continue
            result = run.get("result") if isinstance(run.get("result"), dict) else {}
            metadata = (
                run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
            )
            task_metadata = (
                result.get("task_metadata")
                if isinstance(result.get("task_metadata"), dict)
                else {}
            )
            history_record = (
                result.get("generation_history_record")
                if isinstance(result.get("generation_history_record"), dict)
                else {}
            )
            status = str(run.get("status") or "").strip().casefold()
            task_type = str(run.get("task_type") or "").strip()
            job_id = str(result.get("job_id") or run.get("scope") or "").strip()
            canvas_id = str(
                metadata.get("canvas_id")
                or task_metadata.get("canvas_id")
                or history_record.get("canvas_id")
                or ""
            ).strip()
            if requested_status and status != requested_status:
                continue
            if requested_type and task_type != requested_type:
                continue
            if requested_job and job_id != requested_job:
                continue
            if requested_canvas and canvas_id != requested_canvas:
                continue
            if requested_episode is not None and int(run.get("episode") or 0) != int(
                requested_episode
            ):
                continue
            output_url = _generation_output_url(result)
            if status == "completed" and not output_url:
                continue
            item = {
                "task_id": str(run.get("task_id") or "")[:256],
                "task_type": task_type[:160],
                "job_id": job_id[:256],
                "scope": str(run.get("scope") or "")[:256],
                "status": status[:80],
                "output_url": output_url[:2_000],
                "canvas_id": canvas_id[:200],
                "episode": int(run.get("episode") or 0),
                "created_at": str(run.get("created_at") or "")[:80],
                "completed_at": str(run.get("completed_at") or "")[:80],
                "error": " ".join(str(run.get("error") or "").split())[:500],
            }
            model = str(
                result.get("model") or history_record.get("model") or ""
            ).strip()
            if model:
                item["model"] = model[:256]
            items.append(item)
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "count": min(len(items), limit),
                "truncated": len(items) > limit,
                "items": items[:limit],
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_task(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        task_type = str(args.get("task_type") or "").strip()
        episode = int(args.get("episode") or 0)
        if not task_type:
            raise ValueError("task_type is required")
        query = {
            "beat_num": args.get("beat_num") or args.get("beat"),
            "scope": args.get("scope"),
        }
        return tool_result(
            _request(
                "GET",
                f"/api/v1/projects/{project}/tasks/{task_type}/{episode}",
                query=query,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_episode_script(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 1)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/script")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_story_lab_get(args: dict[str, Any], **_: Any) -> str:
    """Read the current project's durable Story Lab state."""
    try:
        project = _project_from_args(args)
        return tool_result(_request("GET", f"/api/v1/projects/{project}/story-lab"))
    except Exception as exc:
        return tool_error(str(exc))


def _story_lab_config_from_args(args: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "title",
        "logline",
        "work_type",
        "genre",
        "theme",
        "target_units",
        "target_length",
        "point_of_view",
        "audience",
        "style_mode",
        "style_id",
        "style_name",
        "style_prompt",
    )
    return {name: args[name] for name in fields if args.get(name) is not None}


def _handle_story_lab_save(args: dict[str, Any], **_: Any) -> str:
    """Save one Story Lab creative brief without starting generation."""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request(
                "PUT",
                f"/api/v1/projects/{project}/story-lab/config",
                body=_story_lab_config_from_args(args),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_story_lab_generate(args: dict[str, Any], **_: Any) -> str:
    """Optionally save a brief, then start exactly one Story Lab stage."""
    try:
        project = _project_from_args(args)
        config = _story_lab_config_from_args(args)
        saved: dict[str, Any] | None = None
        if config:
            saved = _request(
                "PUT",
                f"/api/v1/projects/{project}/story-lab/config",
                body=config,
            )
        stage = str(args.get("stage") or "").strip().lower()
        if stage not in {"bible", "outline", "draft", "audit"}:
            raise ValueError("stage must be bible, outline, draft, or audit")
        generated = _request(
            "POST",
            f"/api/v1/projects/{project}/story-lab/generate",
            body={
                "stage": stage,
                "instructions": str(args.get("instructions") or "").strip(),
            },
        )
        if saved is not None:
            generated = dict(generated)
            generated["config_saved"] = bool(saved.get("ok"))
        return tool_result(generated)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_story_lab_publish(args: dict[str, Any], **_: Any) -> str:
    """Export the approved Story Lab draft and enqueue the canonical ingest task."""
    try:
        project = _project_from_args(args)
        body = {
            "filename": str(args.get("filename") or "").strip() or None,
            "rebuild": bool(args.get("rebuild", True)),
        }
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/story-lab/publish",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_ingest_uploads(args: dict[str, Any], **_: Any) -> str:
    """List project files already uploaded to the local ingest script directory."""
    try:
        project = _project_from_args(args)
        current_project = _default_project_id()
        if current_project and project != current_project:
            raise ValueError(
                "can only list uploads for the current Hermes project scope"
            )

        project_dir_raw = _agent_context_value("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR")
        if not project_dir_raw:
            return tool_result(
                {
                    "ok": True,
                    "data": {
                        "project_id": project,
                        "count": 0,
                        "files": [],
                        "upload_dir_available": False,
                        "message": "project upload directory is not available in this Hermes session",
                    },
                }
            )

        project_dir = Path(project_dir_raw).expanduser().resolve()
        upload_dir = (project_dir / "uploads").resolve()
        if not upload_dir.is_relative_to(project_dir):
            raise ValueError("invalid upload directory")
        if not upload_dir.exists():
            return tool_result(
                {
                    "ok": True,
                    "data": {
                        "project_id": project,
                        "count": 0,
                        "files": [],
                        "upload_dir_available": True,
                    },
                }
            )

        files = []
        for path in upload_dir.iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            suffix = path.suffix.lower()
            if suffix not in SCRIPT_UPLOAD_EXTENSIONS:
                continue
            stat = path.stat()
            files.append(
                {
                    "filename": path.name,
                    "size": stat.st_size,
                    "modified_at": int(stat.st_mtime),
                    "extension": suffix,
                }
            )
        files.sort(
            key=lambda item: (item["modified_at"], item["filename"]), reverse=True
        )
        return tool_result(
            {
                "ok": True,
                "data": {
                    "project_id": project,
                    "count": len(files),
                    "files": files,
                    "upload_dir_available": True,
                },
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_build_characters(args: dict[str, Any], **_: Any) -> str:
    """Trigger character extraction from the project's knowledge graph.

    Wraps POST /projects/{project}/characters/build (the async ``build_characters``
    task at episode 0) so the agent never has to guess the path. Requires ingest
    to be complete. Poll progress with
    ``village_canvas_get_task(task_type="build_characters", episode=0)`` and read
    results with ``village_canvas_get(path="/projects/{project}/characters")``.
    """
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/characters/build")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_episodes(args: dict[str, Any], **_: Any) -> str:
    """Plan/generate episodes (分集规划) from the ingested story + characters.

    Wraps POST /projects/{project}/episodes/plan (the async ``build_episodes``
    task at episode 0) so the agent never has to guess the path. Requires ingest
    + character extraction to be complete. Poll with
    ``village_canvas_get_task(task_type="build_episodes", episode=0)`` and read results
    with ``village_canvas_get(path="/projects/{project}/episodes")``.
    """
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {}
        if args.get("target_episodes") is not None:
            body["target_episodes"] = int(args["target_episodes"])
        if args.get("planning_mode"):
            body["planning_mode"] = str(args["planning_mode"])
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/episodes/plan", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_script(args: dict[str, Any], **_: Any) -> str:
    """Generate the screenplay for one episode (脚本生成, script_writer task).

    Wraps POST /projects/{project}/episodes/{episode}/script/generate. Requires
    the episode's character identities to be planned first; if not, the API
    returns {"ok": false, "code": "identity_plan_required"} — plan identities
    before retrying. Poll with village_canvas_get_task(task_type="script_writer",
    episode=N); read with village_canvas_get_episode_script(episode=N).
    """
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 0)
        if episode <= 0:
            raise ValueError("episode is required and must be a positive integer")
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/script/generate",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_rewrite_content(args: dict[str, Any], **_: Any) -> str:
    """Queue ContentRewriter without duplicating its model or storage logic."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        body: dict[str, Any] = {
            "target_beats": int(args.get("target_beats") or 18),
            "beat_chars_min": int(args.get("beat_chars_min") or 14),
            "beat_chars_max": int(args.get("beat_chars_max") or 20),
            "narration_style": str(args.get("narration_style") or "first_person"),
            "apply": bool(args.get("apply")),
            "force_retry": bool(args.get("force_retry")),
        }
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/rewrite/generate-async",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_optimize_prompt(args: dict[str, Any], **_: Any) -> str:
    """Queue the existing model-aware prompt optimizer for one canvas node."""
    try:
        project = _project_from_args(args)
        text = str(args.get("text") or "").strip()
        node_type = str(args.get("node_type") or "").strip()
        target_model_id = str(args.get("target_model_id") or "").strip()
        if not text:
            raise ValueError("text is required")
        if node_type not in {"image", "video"}:
            raise ValueError("node_type must be image or video")
        if not target_model_id:
            raise ValueError("target_model_id is required")
        body: dict[str, Any] = {
            "text": text,
            "node_type": node_type,
            "target_model_id": target_model_id,
        }
        for key in (
            "target_api_model",
            "target_model_label",
            "params",
            "references",
            "guidance",
            "director_vision",
            "project_dna",
            "canvas_id",
            "node_id",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        # The style is a prompt parameter, so keep the existing API envelope
        # and make the capability discoverable without adding another route.
        if args.get("director_style") is not None:
            params = body.get("params")
            if not isinstance(params, dict):
                params = {}
            body["params"] = {**params, "director_style": args["director_style"]}
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/prompt/optimize",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _require_episode(args: dict[str, Any]) -> int:
    episode = int(args.get("episode") or 0)
    if episode <= 0:
        raise ValueError("episode is required and must be a positive integer")
    return episode


def _require_name(args: dict[str, Any]) -> str:
    name = str(args.get("name") or args.get("character") or "").strip()
    if not name:
        raise ValueError("name (character name) is required")
    return name


def _handle_update_character_face_prompt(args: dict[str, Any], **_: Any) -> str:
    """Update a character's face_prompt before portrait generation."""
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        face_prompt = str(args.get("face_prompt") or "").strip()
        if not face_prompt:
            raise ValueError("face_prompt is required")
        return tool_result(
            _request(
                "PATCH",
                f"/api/v1/projects/{project}/characters/{quote(name, safe='')}",
                body={"face_prompt": face_prompt},
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _episode_post(
    args: dict[str, Any], suffix: str, *, body: Any = None
) -> dict[str, Any]:
    project = _project_from_args(args)
    episode = _require_episode(args)
    return _request(
        "POST", f"/api/v1/projects/{project}/episodes/{episode}/{suffix}", body=body
    )


def _handle_plan_identities(args: dict[str, Any], **_: Any) -> str:
    """Plan character identities for one episode (身份规划, identity_planner task).

    POST /projects/{project}/episodes/{episode}/identities/plan-async. Prerequisite
    for village_canvas_generate_script. Poll task_type="identity_planner", episode=N.
    """
    try:
        return tool_result(_episode_post(args, "identities/plan-async"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_scenes(args: dict[str, Any], **_: Any) -> str:
    """Plan an episode scene menu before sketch generation."""
    try:
        return tool_result(_episode_post(args, "scenes/plan"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_props(args: dict[str, Any], **_: Any) -> str:
    """Plan an episode prop menu before sketch generation."""
    try:
        return tool_result(_episode_post(args, "props/plan"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_scene_master(args: dict[str, Any], **_: Any) -> str:
    """Generate one scene's canonical master reference image."""
    try:
        project = _project_from_args(args)
        name = str(args.get("name") or args.get("scene_name") or "").strip()
        if not name:
            raise ValueError("name (scene name) is required")
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="generate_scene_master",
            title="生成场景正向参考图",
            description=f"场景 {name} 将启动真实图片生成任务。",
        )
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/scenes/{quote(name, safe='')}/master/generate-async",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_scene_reverse(args: dict[str, Any], **_: Any) -> str:
    """Generate one scene's reverse master reference image."""
    try:
        project = _project_from_args(args)
        name = str(args.get("name") or args.get("scene_name") or "").strip()
        if not name:
            raise ValueError("name (scene name) is required")
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="generate_scene_reverse",
            title="生成场景反向参考图",
            description=f"场景 {name} 将启动真实图片生成任务。",
        )
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/scenes/{quote(name, safe='')}/reverse/generate-async",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_sketches(args: dict[str, Any], **_: Any) -> str:
    """Generate beat sketches for one episode (草图生成, sketch_generation task).

    POST /projects/{project}/episodes/{episode}/sketches/assign-colors, then
    POST /projects/{project}/episodes/{episode}/sketches/generate with the
    canonical request body. Runs after the script exists. Poll
    task_type="sketch_generation", episode=N.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="generate_sketches",
            title="生成分镜草图",
            description=f"第 {episode} 集将启动真实草图生成任务。",
        )
        body = {
            "model": "nanobanana",
            "grid_index": -1,
            "sketch_scene_grouping": True,
            "aspect_ratio": "2:3",
        }
        if isinstance(args.get("body"), dict):
            body.update(
                {key: value for key, value in args["body"].items() if value is not None}
            )
        for key in (
            "style",
            "model",
            "grid_index",
            "sketch_scene_grouping",
            "aspect_ratio",
            "image_generation_selection",
        ):
            if key in args and args[key] is not None:
                body[key] = args[key]

        if args.get("auto_assign_colors", True):
            colors = _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/sketches/assign-colors",
            )
            if not colors.get("ok"):
                return tool_result(
                    {
                        "ok": False,
                        "stage": "assign-colors",
                        "error": colors.get("error") or "assign-colors failed",
                        "data": colors,
                    }
                )

        result = _request(
            "POST",
            f"/api/v1/projects/{project}/episodes/{episode}/sketches/generate",
            body=body,
        )
        if isinstance(result, dict):
            result.setdefault("request_body", body)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_detect_sketch_identities(args: dict[str, Any], **_: Any) -> str:
    """Run episode-wide sketch AI detection for identities and props."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        result = _request(
            "POST",
            f"/api/v1/projects/{project}/episodes/{episode}/sketches/detect-identities",
        )
        if isinstance(result, dict) and not result.get("ok"):
            error_text = str(result.get("error") or "").casefold()
            if "timed out" in error_text or "timeout" in error_text:
                result.setdefault("retryable", False)
                result.setdefault(
                    "agent_instruction",
                    "Stop retrying this tool in the same turn. Report that AI detection timed "
                    "out and ask the user to retry later or run it from the frontend.",
                )
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_optimize_video_global(args: dict[str, Any], **_: Any) -> str:
    """Run global video optimization for one episode (全局视频优化, global_optimize_video).

    POST /projects/{project}/episodes/{episode}/optimize/video-global.
    Poll task_type="global_optimize_video", episode=N.
    """
    try:
        body: dict[str, Any] = {}
        for key in ("language", "visual_style"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _episode_post(args, "optimize/video-global", body=body or None)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_review_episode_plan(args: dict[str, Any], **_: Any) -> str:
    """Queue the real episode-plan reviewer through the task backend."""
    try:
        project = _project_from_args(args)
        body = {"force_retry": bool(args.get("force_retry"))}
        return tool_result(
            _request(
                "POST", f"/api/v1/projects/{project}/episodes/plan/review", body=body
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_fix_episode_plan(args: dict[str, Any], **_: Any) -> str:
    """Queue the minimal episode-plan fixer; persistence requires apply=true."""
    try:
        project = _project_from_args(args)
        body = {
            "apply": bool(args.get("apply")),
            "force_retry": bool(args.get("force_retry")),
        }
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/episodes/plan/fix", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_review_characters(args: dict[str, Any], **_: Any) -> str:
    """Queue the real character reviewer through the task backend."""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/characters/review",
                body={"force_retry": bool(args.get("force_retry"))},
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_fix_characters(args: dict[str, Any], **_: Any) -> str:
    """Queue the character fixer; persistence requires apply=true."""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "apply": bool(args.get("apply")),
            "force_retry": bool(args.get("force_retry")),
        }
        if isinstance(args.get("report"), dict):
            body["report"] = args["report"]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/characters/fix",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_build_beat_prompt(args: dict[str, Any], **_: Any) -> str:
    """Queue the existing beat prompt task for the selected builder mode."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beat = int(args.get("beat") or args.get("beat_num") or 0)
        if beat <= 0:
            raise ValueError("beat is required and must be a positive integer")
        body = {"language": str(args.get("language") or "en")}
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/video-prompt/generate",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_audio(args: dict[str, Any], **_: Any) -> str:
    """Generate episode audio via the current IndexTTS2 audio pipeline."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        _require_paid_media_authorization(
            args,
            project=project,
            kind="audio",
            action="generate_audio",
            title="生成剧集音频",
            description=f"第 {episode} 集将启动真实音频生成任务。",
        )
        body: dict[str, Any] = {}
        for key in ("provider", "voice", "model", "rate", "mode", "beat_numbers"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/audio/generate",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _resolve_episode_beats(project: str, episode: int) -> list[int]:
    """Fetch the episode's beat numbers via GET /episodes/{ep}/beats."""
    resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
    items: Any = None
    if isinstance(resp, dict):
        for key in ("data", "beats", "items"):
            value = resp.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and isinstance(resp.get("data"), dict):
            items = resp["data"].get("beats")
    return [
        int(b["beat_number"])
        for b in (items or [])
        if isinstance(b, dict) and b.get("beat_number") is not None
    ]


def _handle_get_sketches(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready sketch URLs for an episode (to SHOW the user).

    Wraps GET /projects/{project}/episodes/{episode}/beats and returns, per beat,
    the servable ``sketch_url``. Use ``village_canvas_get_first_frames`` for first frames.
    Do NOT read
    the local ``sketch_path`` from a task result, and do NOT use vision_analyze —
    that only lets the agent look at the image, it does NOT show it to the user.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        media_kind = "sketch"
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")
        sketches = []
        media_items = []
        requested_beats = _requested_beats(args)
        for b in items or []:
            if not isinstance(b, dict):
                continue
            beat_number = b.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            sketch_url = b.get("sketch_url") or ""
            video_url = b.get("video_url") or ""
            sketches.append(
                {
                    "beat_number": beat_number,
                    "sketch_url": sketch_url,
                    "sketch_source": "sketch" if sketch_url else "",
                    "video_url": video_url,
                    "characters": b.get("character_names") or b.get("characters"),
                }
            )
            if sketch_url:
                media_items.append(
                    {
                        "src": sketch_url,
                        "title": f"Beat {beat_number} 草图",
                        "description": "草图",
                        "aspectRatio": "3/4",
                    }
                )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "media_kind": media_kind,
                "count": len(sketches),
                "sketches": sketches,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media)
                if limited_media
                else None,
            }
        )
    except Exception as exc:
        return tool_result({"ok": False, "error": str(exc)})


def _handle_get_first_frames(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready first-frame URLs for an episode (to SHOW the user).

    Wraps GET /projects/{project}/episodes/{episode}/beats and returns, per beat,
    the servable ``frame_url``. Use ``village_canvas_get_sketches`` for sketches.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")
        frames = []
        media_items = []
        requested_beats = _requested_beats(args)
        for b in items or []:
            if not isinstance(b, dict):
                continue
            beat_number = b.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            frame_url = b.get("frame_url") or ""
            frames.append(
                {
                    "beat_number": beat_number,
                    "frame_url": frame_url,
                    "video_url": b.get("video_url") or "",
                    "characters": b.get("character_names") or b.get("characters"),
                }
            )
            if frame_url:
                media_items.append(
                    {
                        "src": frame_url,
                        "title": f"Beat {beat_number} 首帧",
                        "description": "首帧",
                        "aspectRatio": "3/4",
                    }
                )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "media_kind": "frame",
                "count": len(frames),
                "frames": frames,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media)
                if limited_media
                else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_sketch_candidates(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready sketch pool candidates for one beat."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beat = int(
            args.get("beat") or args.get("beat_num") or args.get("beat_number") or 0
        )
        if beat <= 0:
            raise ValueError("beat is required")
        resp = _request(
            "GET",
            f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/sketch-candidates",
        )
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            data = {}
        candidates = (
            data.get("candidates") if isinstance(data.get("candidates"), list) else []
        )
        media_items = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            src = str(candidate.get("url") or "").strip()
            if not src:
                continue
            stale = bool(candidate.get("stale"))
            media_items.append(
                {
                    "src": src,
                    "title": f"Beat {beat} 草图候选",
                    "description": "过期候选" if stale else "草图候选",
                    "aspectRatio": "3/4",
                }
            )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": bool(resp.get("ok", True)) if isinstance(resp, dict) else True,
                "episode": episode,
                "beat": beat,
                "media_kind": "sketch_candidate",
                "current_sketch_url": data.get("current_sketch_url", ""),
                "candidate_count": int(data.get("candidate_count") or len(candidates)),
                "candidates": candidates,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media)
                if limited_media
                else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_scene_images(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready scene image URLs for a project (to SHOW the user).

    Wraps GET /projects/{project}/scenes and returns only servable scene asset
    URLs. Do NOT use local ``*_path`` fields and do NOT synthesize URLs.
    """
    try:
        project = _project_from_args(args)
        include_reverse = bool(args.get("include_reverse", True))
        include_pano = bool(args.get("include_pano", False))
        include_custom = bool(args.get("include_custom", False))
        resp = _request("GET", f"/api/v1/projects/{project}/scenes")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "scenes", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("scenes")

        scenes = []
        media_items = []
        image_count = 0
        requested_names = _requested_scene_names(args)
        requested_indices = _requested_scene_indices(args)
        requested_type = str(args.get("scene_type") or "").strip()
        for scene_index, scene in enumerate(items or [], start=1):
            if not isinstance(scene, dict):
                continue
            scene_name = str(scene.get("name") or "").strip()
            scene_type = str(scene.get("scene_type") or "").strip()
            if requested_indices is not None and scene_index not in requested_indices:
                continue
            if not _matches_any_scene_name(scene_name, requested_names):
                continue
            if requested_type and scene_type != requested_type:
                continue
            images = []
            for kind, field, enabled in (
                ("master", "master_url", True),
                ("reverse_master", "reverse_master_url", include_reverse),
                ("pano", "pano_url", include_pano),
                ("custom_scene", "custom_scene_url", include_custom),
            ):
                url = str(scene.get(field) or "").strip()
                if enabled and url:
                    images.append({"kind": kind, "url": url})
                    media_items.append(
                        {
                            "src": url,
                            "title": f"{scene_name or '场景'} · {kind}",
                            "description": scene.get("description")
                            or scene.get("environment_prompt")
                            or "",
                            "aspectRatio": "16/9" if kind == "pano" else "3/4",
                        }
                    )
            image_count += len(images)
            scenes.append(
                {
                    "index": scene_index,
                    "name": scene_name,
                    "scene_type": scene_type,
                    "description": scene.get("description") or "",
                    "environment_prompt": scene.get("environment_prompt") or "",
                    "master_url": scene.get("master_url") or "",
                    "reverse_master_url": scene.get("reverse_master_url") or "",
                    "pano_url": scene.get("pano_url") or "",
                    "custom_scene_url": scene.get("custom_scene_url") or "",
                    "images": images,
                }
            )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "count": len(scenes),
                "image_count": image_count,
                "scenes": scenes,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media)
                if limited_media
                else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_character_media(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready character portrait/identity image URLs."""
    try:
        project = _project_from_args(args)
        media_kind = (
            str(args.get("media_kind") or args.get("kind") or "all").strip().lower()
        )
        if media_kind not in {"all", "portrait", "identity"}:
            media_kind = "all"
        include_identities = (
            bool(args.get("include_identities", True)) and media_kind != "portrait"
        )
        resp = _request("GET", f"/api/v1/projects/{project}/characters")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "characters", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("characters")

        characters = []
        media_items = []
        requested_names = _requested_names(args)
        requested_queries = _requested_queries(args)
        for character in items or []:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            role = str(
                character.get("role") or character.get("description") or ""
            ).strip()
            character_name_match = _matches_any_text(
                [name, character.get("aliases")],
                requested_names,
            )
            character_query_match = _matches_any_text(
                [
                    name,
                    role,
                    character.get("description"),
                    character.get("appearance"),
                    character.get("profile"),
                    character.get("aliases"),
                ],
                requested_queries,
            )
            character_match = character_name_match and character_query_match
            portrait_url = str(character.get("portrait_url") or "").strip()
            if portrait_url and character_match:
                if media_kind in {"all", "portrait"}:
                    media_items.append(
                        {
                            "src": portrait_url,
                            "title": name or "角色肖像",
                            "description": role,
                            "aspectRatio": "3/4",
                        }
                    )
            identity_items = []
            identities = (
                character.get("identities") or character.get("identity_images") or []
            )
            if include_identities:
                try:
                    identities_resp = _request(
                        "GET",
                        f"/api/v1/projects/{project}/characters/{quote(name, safe='')}/identities",
                    )
                    if isinstance(identities_resp, dict):
                        for key in ("data", "identities", "items"):
                            value = identities_resp.get(key)
                            if isinstance(value, list):
                                identities = value
                                break
                        if isinstance(identities_resp.get("data"), dict):
                            value = identities_resp["data"].get("identities")
                            if isinstance(value, list):
                                identities = value
                except Exception:
                    logger.warning(
                        "character identity images read failed project=%s character=%s; "
                        "continuing without identities",
                        project,
                        name,
                        exc_info=True,
                    )
            if include_identities and isinstance(identities, list):
                for identity in identities:
                    if not isinstance(identity, dict):
                        continue
                    image_url = str(
                        identity.get("image_url")
                        or identity.get("portrait_image_url")
                        or identity.get("costume_image_url")
                        or ""
                    ).strip()
                    if not image_url:
                        continue
                    title = str(
                        identity.get("identity_name")
                        or identity.get("name")
                        or identity.get("identity_id")
                        or name
                        or "身份图"
                    )
                    identity_name_match = _matches_any_text(
                        [
                            name,
                            character.get("aliases"),
                            title,
                            identity.get("identity_name"),
                            identity.get("name"),
                            identity.get("identity_id"),
                        ],
                        requested_names,
                    )
                    identity_query_match = _matches_any_text(
                        [
                            title,
                            identity.get("identity_name"),
                            identity.get("name"),
                            identity.get("identity_id"),
                            identity.get("description"),
                            identity.get("appearance_details"),
                            identity.get("prompt"),
                            identity.get("role"),
                            name,
                            role,
                        ],
                        requested_queries,
                    )
                    identity_match = identity_name_match and identity_query_match
                    if not identity_match:
                        continue
                    identity_items.append({"title": title, "image_url": image_url})
                    media_items.append(
                        {
                            "src": image_url,
                            "title": f"{name} · {title}" if name else title,
                            "description": role,
                            "aspectRatio": "3/4",
                        }
                    )
            if (
                (requested_names is not None or requested_queries is not None)
                and not character_match
                and not identity_items
            ):
                continue
            characters.append(
                {
                    "name": name,
                    "role": role,
                    "portrait_url": portrait_url,
                    "identities": identity_items,
                }
            )

        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "count": len(characters),
                "media_count": len(media_items),
                "characters": characters,
                "ui_spec": _image_ui_spec("character_showcase", limited_media)
                if limited_media
                else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_episode_media(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready episode video/audio URLs."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        media_type = str(args.get("media_type") or "video").strip().lower()
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")

        video_items = []
        audio_items = []
        beats = []
        requested_beats = _requested_beats(args)
        requested_queries = _requested_queries(args)
        for beat in items or []:
            if not isinstance(beat, dict):
                continue
            beat_number = beat.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            if not _matches_any_text(
                [
                    beat.get("title"),
                    beat.get("summary"),
                    beat.get("description"),
                    beat.get("visual_description"),
                    beat.get("image_prompt"),
                    beat.get("video_prompt"),
                    beat.get("narration"),
                    beat.get("voiceover"),
                    beat.get("dialogue"),
                    beat.get("audio_text"),
                    beat.get("speaker"),
                    beat.get("character_names"),
                    beat.get("characters"),
                    beat.get("scene_name"),
                    beat.get("location"),
                ],
                requested_queries,
            ):
                continue
            video_url = str(beat.get("video_url") or "").strip()
            audio_url = str(beat.get("audio_url") or "").strip()
            frame_url = str(
                beat.get("frame_url") or beat.get("sketch_url") or ""
            ).strip()
            beats.append(
                {
                    "beat_number": beat_number,
                    "video_url": video_url,
                    "audio_url": audio_url,
                }
            )
            if video_url:
                video_items.append(
                    {
                        "src": video_url,
                        "poster": frame_url,
                        "title": f"Beat {beat_number} 视频",
                    }
                )
            if audio_url:
                audio_items.append(
                    {"src": audio_url, "title": f"Beat {beat_number} 音频"}
                )

        if media_type == "audio":
            limited = _limit_items(audio_items, args, 20)
            ui_spec = _audio_ui_spec(limited) if limited else None
        else:
            limited = _limit_items(video_items, args, 6)
            ui_spec = _video_ui_spec(limited) if limited else None
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "episode": episode,
                "media_type": media_type,
                "video_count": len(video_items),
                "audio_count": len(audio_items),
                "beats": beats,
                "ui_spec": ui_spec,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _resolve_canvas_video_node(
    args: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any], str]:
    """Read one concrete video node and return its current source URL.

    Node actions must bind to the persisted node, not to a model-invented URL.
    This keeps Agent task submissions aligned with the canvas revision it just
    inspected and makes missing/empty outputs fail before any task is created.
    """
    project = _project_from_args(args)
    canvas_id = _canvas_id_from_args(args)
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("node_id is required")
    response = _request(
        "GET",
        f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}",
    )
    doc = _canvas_payload_from_response(response) or {}
    nodes = doc.get("nodes") if isinstance(doc.get("nodes"), list) else []
    node = next(
        (
            item
            for item in nodes
            if isinstance(item, dict) and str(item.get("id") or "").strip() == node_id
        ),
        None,
    )
    if not isinstance(node, dict):
        raise ValueError(f"canvas node not found: {node_id}")
    node_type = str(node.get("type") or "").strip()
    if node_type != "videoNode":
        raise ValueError(f"node {node_id} is not a video node")
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    source_url = str(data.get("videoUrl") or data.get("resultVideoUrl") or "").strip()
    if not source_url:
        raise ValueError(f"video node {node_id} has no completed video output")
    return project, canvas_id, node_id, data, source_url


def _video_task_response(
    response: dict[str, Any],
    *,
    capability_id: str,
    task_type: str,
    project: str,
    canvas_id: str,
    node_id: str,
) -> str:
    """Attach stable source/task facts without rewriting the API response."""
    payload = dict(response) if isinstance(response, dict) else {"data": response}
    payload["capability_execution"] = {
        "capability_id": capability_id,
        "task_type": task_type,
        "project_id": project,
        "canvas_id": canvas_id,
        "source_node_id": node_id,
        "result_readback": f"freezone/jobs/{task_type}/<job_id>/result",
    }
    return tool_result(payload)


def _handle_video_node_story_analysis(args: dict[str, Any], **_: Any) -> str:
    """Submit real video-story analysis for a persisted video node."""
    try:
        project, canvas_id, node_id, data, source_url = _resolve_canvas_video_node(args)
        body: dict[str, Any] = {
            "video_url": source_url,
            "canvas_id": canvas_id,
            "node_id": node_id,
        }
        duration_sec = args.get("duration_sec")
        if duration_sec is None:
            duration_ms = data.get("durationMs")
            if isinstance(duration_ms, (int, float)) and duration_ms > 0:
                duration_sec = duration_ms / 1000
        if duration_sec is not None:
            body["duration_sec"] = max(0.1, min(float(duration_sec), 24 * 60 * 60))
        if args.get("max_frames") is not None:
            body["max_frames"] = max(3, min(int(args["max_frames"]), 50))
        if args.get("scene_threshold") is not None:
            body["scene_threshold"] = max(0.0, min(float(args["scene_threshold"]), 1.0))
        if args.get("model"):
            body["model"] = str(args["model"]).strip()
        return _video_task_response(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/analyze-video-story",
                body=body,
            ),
            capability_id="canvas.node.video.story_analysis",
            task_type="freezone_video_story",
            project=project,
            canvas_id=canvas_id,
            node_id=node_id,
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_node_upscale(args: dict[str, Any], **_: Any) -> str:
    """Submit the existing video-upscale task contract for one node."""
    try:
        project, canvas_id, node_id, _data, source_url = _resolve_canvas_video_node(
            args
        )
        resolution = str(args.get("resolution") or "1080p").strip().lower()
        if resolution not in {"1080p", "2k", "4k"}:
            raise ValueError("resolution must be 1080p, 2k, or 4k")
        denoise = str(args.get("denoise_strength") or "1x").strip().lower()
        if denoise not in {"none", "1x", "2x"}:
            raise ValueError("denoise_strength must be none, 1x, or 2x")
        body = {
            "source_url": source_url,
            "resolution": resolution,
            "frame_interpolation": "none",
            "denoise_strength": denoise,
        }
        return _video_task_response(
            _request(
                "POST", f"/api/v1/projects/{project}/freezone/video/upscale", body=body
            ),
            capability_id="canvas.node.video.upscale",
            task_type="freezone_video_upscale",
            project=project,
            canvas_id=canvas_id,
            node_id=node_id,
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_node_audio_separate(args: dict[str, Any], **_: Any) -> str:
    """Submit audio/video separation and preserve optional beat targeting."""
    try:
        project, canvas_id, node_id, _data, source_url = _resolve_canvas_video_node(
            args
        )
        body: dict[str, Any] = {"source_url": source_url}
        if args.get("target_episode") is not None:
            body["target_episode"] = max(1, int(args["target_episode"]))
        if args.get("target_beat") is not None:
            body["target_beat"] = max(1, int(args["target_beat"]))
        return _video_task_response(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/video/audio-separate",
                body=body,
            ),
            capability_id="canvas.node.video.audio_separate",
            task_type="freezone_audio_separate",
            project=project,
            canvas_id=canvas_id,
            node_id=node_id,
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_render_first_frames(args: dict[str, Any], **_: Any) -> str:
    """Generate first frames for an episode (首帧生成, selected_regen task).

    Wraps POST /projects/{project}/episodes/{episode}/beats/regenerate with
    ``{"beat_indices": [...]}``. If ``beat_indices`` is omitted, ALL beats of the
    episode are resolved automatically (GET /episodes/{ep}/beats). Requires sketches
    to exist first. Poll village_canvas_get_task(task_type="selected_regen", episode=N).
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beats = args.get("beat_indices") or args.get("beats")
        if not isinstance(beats, list) or not beats:
            beats = _resolve_episode_beats(project, episode)
            if not beats:
                raise ValueError(
                    "could not resolve beats for this episode; generate sketches first "
                    "or pass beat_indices explicitly"
                )
        body: dict[str, Any] = {"beat_indices": [int(b) for b in beats]}
        if args.get("style"):
            body["style"] = str(args["style"])
        if args.get("model"):
            body["model"] = str(args["model"])
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/beats/regenerate",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_compose_episode(args: dict[str, Any], **_: Any) -> str:
    """Compose/export the final video for one episode (合成导出, compose_episode task).

    POST /projects/{project}/episodes/{episode}/videos/compose.
    Poll task_type="compose_episode", episode=N.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="render_first_frames",
            title="生成视频首帧",
            description=f"第 {episode} 集将启动真实首帧生成任务。",
        )
        _require_paid_media_authorization(
            args,
            project=project,
            kind="video",
            action="compose_episode",
            title="合成最终视频",
            description=f"第 {episode} 集将启动真实视频合成任务。",
        )
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/videos/compose",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_final_video(args: dict[str, Any], **_: Any) -> str:
    """Get and display the composed final episode video when it exists."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        result = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/final")
        data = result.get("data") if isinstance(result, dict) else None
        video_url = ""
        if isinstance(data, dict) and data.get("exists"):
            video_url = str(data.get("video_url") or "").strip()
        if video_url and isinstance(result, dict):
            result["ui_spec"] = _video_ui_spec(
                [
                    {
                        "src": video_url,
                        "title": f"第 {episode} 集成片",
                        "description": "最终合成视频",
                    }
                ]
            )
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_portrait(args: dict[str, Any], **_: Any) -> str:
    """Generate one character's portrait (肖像生成, character_portrait task).

    POST /projects/{project}/characters/{name}/portrait-async. Poll
    task_type="character_portrait" (per character). Read via
    village_canvas_get('/projects/{project}/characters').
    """
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="generate_portrait",
            title="生成角色肖像",
            description=f"角色 {name} 将启动真实图片生成任务。",
        )
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/characters/{quote(name, safe='')}/portrait-async",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_identity_image(args: dict[str, Any], **_: Any) -> str:
    """Generate a character identity image (身份图生成, identity_image task).

    POST /projects/{project}/characters/{name}/identities/{identity_id}/generate-async.
    Needs both the character name and the identity_id (from the character's identity
    list). Poll task_type="identity_image".
    """
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        identity_id = str(args.get("identity_id") or "").strip()
        if not identity_id:
            raise ValueError("identity_id is required")
        _require_paid_media_authorization(
            args,
            project=project,
            kind="image",
            action="generate_identity_image",
            title="生成角色身份图",
            description=f"角色 {name} 的身份 {identity_id} 将启动真实图片生成任务。",
        )
        path = (
            f"/api/v1/projects/{project}/characters/{quote(name, safe='')}"
            f"/identities/{quote(identity_id, safe='')}/generate-async"
        )
        return tool_result(_request("POST", path))
    except Exception as exc:
        return tool_error(str(exc))


def _canvas_reference_manifest(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    selected_node_id: str | None = None,
) -> dict[str, Any]:
    # Keep the Hermes plugin and the HTTP/chat blackboard on one projection.
    from novelvideo.freezone.reference_manifest import build_canvas_reference_manifest

    return build_canvas_reference_manifest(nodes, edges, selected_node_id)


_SNAPSHOT_INTERNAL_METADATA_KEYS = frozenset(
    {
        "village_canvas_agent_command_ids",
        "village_canvas_command_receipts_v2",
    }
)


def _snapshot_metadata(value: object) -> object:
    """Keep canvas facts, but never echo the command ledger as a read result."""
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if str(key) not in _SNAPSHOT_INTERNAL_METADATA_KEYS
    }


def _page_number(
    value: object, *, minimum: int, maximum: int | None, field: str
) -> int:
    """Coerce a model-supplied paging number into the declared range.

    A paging knob is not a permission boundary: a value that clearly means a
    number is coerced and clamped instead of failing the whole read and costing
    the turn. Only a value that does not mean a number at all stays a hard error.
    """

    if isinstance(value, bool):
        number: object = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        number = int(value.strip())
    else:
        number = None
    if number is None:
        if maximum is None:
            raise ValueError(f"{field} must be a non-negative integer")
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    if maximum is None:
        return max(int(number), minimum)
    return min(max(int(number), minimum), maximum)


def _bounded_page_cursor(value: object, *, default: int) -> tuple[int, int | None]:
    """Clamp a page cursor into range; the original is echoed when it moved."""

    if value is None:
        return default, None
    cursor = _page_number(value, minimum=0, maximum=None, field="node_cursor")
    return cursor, (None if cursor == value else value)


def _bounded_page_limit(
    value: object,
    *,
    default: int,
    maximum: int,
) -> tuple[int, int | None]:
    """Clamp a page size into range; the original is echoed when it moved."""

    if value is None:
        return default, None
    limit = _page_number(value, minimum=1, maximum=maximum, field="node_limit")
    return limit, (None if limit == value else value)


def _handle_get_canvas_snapshot(args: dict[str, Any], **_: Any) -> str:
    """Read a bounded page of the persisted canvas document."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        raw = _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}",
        )
        payload = raw if isinstance(raw, dict) else {"data": raw}
        doc = _canvas_payload_from_response(payload) or {}
        nodes = doc.get("nodes") if isinstance(doc.get("nodes"), list) else []
        edges = doc.get("edges") if isinstance(doc.get("edges"), list) else []
        node_count = len(nodes)
        edge_count = len(edges)

        node_cursor, requested_node_cursor = _bounded_page_cursor(
            args.get("node_cursor"), default=0
        )
        node_limit, requested_node_limit = _bounded_page_limit(
            args.get("node_limit"), default=20, maximum=50
        )
        include_edges = args.get("include_edges", True)
        if not isinstance(include_edges, bool):
            raise ValueError("include_edges must be a boolean")

        requested_ids = args.get("node_ids")
        selection_mode = "cursor"
        if requested_ids is not None:
            if not isinstance(requested_ids, list) or len(requested_ids) > 50:
                raise ValueError("node_ids must be an array with at most 50 ids")
            clean_ids = [str(value or "").strip() for value in requested_ids]
            if any(not value or len(value) > 512 for value in clean_ids):
                raise ValueError(
                    "node_ids must contain non-empty ids up to 512 characters"
                )
            node_by_id = {
                str(node.get("id") or ""): node
                for node in nodes
                if isinstance(node, dict) and str(node.get("id") or "")
            }
            page_nodes = [
                node_by_id[node_id] for node_id in clean_ids if node_id in node_by_id
            ]
            selection_mode = "node_ids"
            next_cursor = None
            effective_cursor = 0
        else:
            page_nodes = nodes[node_cursor : node_cursor + node_limit]
            page_end = node_cursor + len(page_nodes)
            next_cursor = page_end if page_end < node_count else None
            effective_cursor = node_cursor

        page_node_ids = {
            str(node.get("id") or "")
            for node in page_nodes
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        page_edges = (
            [
                edge
                for edge in edges
                if isinstance(edge, dict)
                and (
                    str(edge.get("source") or "") in page_node_ids
                    or str(edge.get("target") or "") in page_node_ids
                )
            ]
            if include_edges
            else []
        )
        page_meta = {
            "selection_mode": selection_mode,
            "node_cursor": effective_cursor,
            "node_limit": node_limit,
            "returned_nodes": len(page_nodes),
            "total_nodes": node_count,
            "truncated": len(page_nodes) < node_count,
            "omitted_nodes": max(0, node_count - len(page_nodes)),
            "next_cursor": next_cursor,
            "include_edges": include_edges,
            "returned_edges": len(page_edges),
            "total_edges": edge_count,
        }
        if requested_node_limit is not None:
            page_meta["requested_node_limit"] = requested_node_limit
            page_meta["node_limit_adjusted"] = True
        if requested_node_cursor is not None:
            page_meta["requested_node_cursor"] = requested_node_cursor
            page_meta["node_cursor_adjusted"] = True
        paged_doc: dict[str, Any] = {}
        for field_name in (
            "schema_version",
            "canvas_id",
            "project_id",
            "revision",
            "viewport",
            "updated_at",
        ):
            if field_name in doc:
                paged_doc[field_name] = doc[field_name]
        paged_doc["page"] = page_meta
        paged_doc["nodes"] = page_nodes
        paged_doc["edges"] = page_edges
        for field_name, value in doc.items():
            if field_name not in paged_doc and field_name not in {"nodes", "edges"}:
                paged_doc[field_name] = (
                    _snapshot_metadata(value) if field_name == "metadata" else value
                )
        selected_node_id = next(
            (
                str(node.get("id") or "")
                for node in nodes
                if isinstance(node, dict) and node.get("selected") is True
            ),
            None,
        )
        paged_doc["reference_manifest"] = _canvas_reference_manifest(
            nodes,
            edges,
            selected_node_id,
        )

        enriched = dict(payload)
        if isinstance(payload.get("data"), dict):
            enriched["data"] = paged_doc
        else:
            enriched = paged_doc
        # Keep both response shapes consumable by Agent callers. The compact
        # reader already exposes this at top level; snapshot callers should not
        # need to know whether the HTTP adapter wrapped the document in data.
        enriched["reference_manifest"] = paged_doc["reference_manifest"]
        enriched.update(
            {
                "error_code": None,
                "handoff_level": "L1" if node_count is not None else "L0",
                "snapshot_meta": {
                    "project_id": project,
                    "canvas_id": canvas_id,
                    "revision": doc.get("revision") if isinstance(doc, dict) else None,
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "page": page_meta,
                },
            }
        )
        return tool_result(
            _with_tool_trace(
                enriched,
                tool="freezone_get_canvas_snapshot",
                t0=t0,
                canvas_id=canvas_id,
                revision=enriched["snapshot_meta"].get("revision"),
                node_count=node_count,
                edge_count=edge_count,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="freezone_get_canvas_snapshot", t0=t0)


def _compact_canvas_node(node: dict[str, Any], prompt_chars: int) -> dict[str, Any]:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    position = node.get("position") if isinstance(node.get("position"), dict) else {}

    def coordinate(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return round(number, 2) if math.isfinite(number) else 0.0

    prompt = str(
        data.get("prompt") or data.get("content") or data.get("text") or ""
    ).strip()
    status = str(
        data.get("generationStatus")
        or data.get("generation_status")
        or ("running" if data.get("isGenerating") else "")
    ).strip()
    image_url = str(data.get("imageUrl") or "").strip()
    video_url = str(data.get("videoUrl") or data.get("resultVideoUrl") or "").strip()
    preview_url = str(data.get("previewImageUrl") or "").strip()
    node_type = str(node.get("type") or "").strip()
    preview_media_type = (
        "video"
        if preview_url and node_type in {"videoNode", "videoComposeNode"}
        else "image"
        if preview_url
        else ""
    )
    compact: dict[str, Any] = {
        "id": str(node.get("id") or ""),
        "type": node_type,
        "label": str(
            data.get("displayName")
            or data.get("label")
            or data.get("title")
            or node.get("type")
            or node.get("id")
            or ""
        )[:160],
        "position": {
            "x": coordinate(position.get("x")),
            "y": coordinate(position.get("y")),
        },
        "selected": bool(node.get("selected")),
        "has_prompt": bool(prompt),
        "has_image": bool(image_url),
        "has_video": bool(video_url),
        "has_preview": bool(preview_url),
        "has_audio": bool(data.get("audioUrl") or data.get("resultAudioUrl")),
    }
    if image_url:
        compact["image_url"] = image_url[:500]
    if video_url:
        compact["video_url"] = video_url[:500]
    if preview_url:
        compact["preview_url"] = preview_url[:500]
        compact["preview_media_type"] = preview_media_type or "image"
    model = str(data.get("model") or "").strip()
    if model:
        compact["model"] = model[:200]
    camera_selection = data.get("cameraSelection")
    if isinstance(camera_selection, dict) and camera_selection:
        compact["camera_selection"] = dict(camera_selection)
    if data.get("cameraMovement") not in (None, ""):
        compact["camera_movement"] = str(data.get("cameraMovement"))[:200]
    if status:
        compact["status"] = status[:80]
    if data.get("generationError"):
        compact["error"] = str(data.get("generationError"))[:200]
    if prompt_chars > 0 and prompt:
        compact["prompt_excerpt"] = prompt[:prompt_chars]
        compact["prompt_truncated"] = len(prompt) > prompt_chars
    return compact


def _handle_read_canvas_compact(args: dict[str, Any], **_: Any) -> str:
    """Read a compact graph projection whose header always precedes node data."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        node_cursor, requested_node_cursor = _bounded_page_cursor(
            args.get("node_cursor"), default=0
        )
        node_limit, requested_node_limit = _bounded_page_limit(
            args.get("node_limit"), default=40, maximum=200
        )
        prompt_chars = args.get("prompt_chars", 0)
        if (
            not isinstance(prompt_chars, int)
            or isinstance(prompt_chars, bool)
            or not 0 <= prompt_chars <= 500
        ):
            raise ValueError("prompt_chars must be an integer between 0 and 500")

        raw = _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}",
        )
        doc = (
            _canvas_payload_from_response(
                raw if isinstance(raw, dict) else {"data": raw}
            )
            or {}
        )
        nodes = [node for node in (doc.get("nodes") or []) if isinstance(node, dict)]
        edges = [edge for edge in (doc.get("edges") or []) if isinstance(edge, dict)]
        page_nodes = nodes[node_cursor : node_cursor + node_limit]
        page_ids = {str(node.get("id") or "") for node in page_nodes}
        page_edges = [
            {
                "id": str(edge.get("id") or ""),
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
            }
            for edge in edges
            if str(edge.get("source") or "") in page_ids
            or str(edge.get("target") or "") in page_ids
        ]
        next_cursor = node_cursor + len(page_nodes)
        if next_cursor >= len(nodes):
            next_cursor = None
        selected_node_id = next(
            (
                str(node.get("id") or "")
                for node in nodes
                if node.get("selected") is True
            ),
            None,
        )
        result = {
            "schema": "village_canvas_compact.v1",
            "project_id": project,
            "canvas_id": canvas_id,
            "revision": doc.get("revision"),
            "viewport": doc.get("viewport"),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "selected_node_id": selected_node_id,
            "page": {
                **{
                    "node_cursor": node_cursor,
                    "node_limit": node_limit,
                    "returned_nodes": len(page_nodes),
                    "next_cursor": next_cursor,
                    "truncated": next_cursor is not None,
                },
                **(
                    {
                        "requested_node_limit": requested_node_limit,
                        "node_limit_adjusted": True,
                    }
                    if requested_node_limit is not None
                    else {}
                ),
                **(
                    {
                        "requested_node_cursor": requested_node_cursor,
                        "node_cursor_adjusted": True,
                    }
                    if requested_node_cursor is not None
                    else {}
                ),
            },
            "nodes": [_compact_canvas_node(node, prompt_chars) for node in page_nodes],
            "edges": page_edges,
            "reference_manifest": _canvas_reference_manifest(
                nodes, edges, selected_node_id
            ),
            "snapshot_required": False,
        }
        return tool_result(
            _with_tool_trace(
                result,
                tool="village_canvas_read_compact",
                t0=t0,
                canvas_id=canvas_id,
                revision=doc.get("revision"),
                node_count=len(nodes),
                edge_count=len(edges),
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="village_canvas_read_compact", t0=t0)


def _handle_get_canvas_viewport(args: dict[str, Any], **_: Any) -> str:
    """Read the small persisted viewport summary; live browser context remains authoritative."""
    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        raw = _request(
            "GET",
            f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}/viewport",
        )
        payload = raw if isinstance(raw, dict) else {"data": raw}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        enriched = dict(payload)
        enriched.update(
            {
                "error_code": None,
                "handoff_level": "L1",
                "viewport_meta": {
                    "project_id": project,
                    "canvas_id": canvas_id,
                    "revision": data.get("revision")
                    if isinstance(data, dict)
                    else None,
                    "source": "persisted_canvas",
                },
            }
        )
        return tool_result(
            _with_tool_trace(
                enriched,
                tool="freezone_get_canvas_viewport",
                t0=t0,
                canvas_id=canvas_id,
                revision=enriched["viewport_meta"].get("revision"),
            )
        )
    except Exception as exc:
        return _freezone_tool_error(exc, tool="freezone_get_canvas_viewport", t0=t0)


def _handle_get_script_media_readiness(args: dict[str, Any], **_: Any) -> str:
    """Read current paid-media readiness without writing or starting a task."""

    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        node_id = str(args.get("node_id") or "").strip()
        action = str(args.get("action") or "").strip()
        if not node_id:
            raise ValueError("node_id is required")
        if action not in {"storyboard-images", "shot-videos"}:
            raise ValueError("action must be storyboard-images or shot-videos")
        query = {"node_id": node_id, "action": action}
        step_id = str(args.get("step_id") or "").strip()
        if step_id:
            query["step_id"] = step_id[:160]
        return tool_result(
            _with_tool_trace(
                _request(
                    "GET",
                    (
                        f"/api/v1/projects/{project}/freezone/canvases/"
                        f"{quote(canvas_id, safe='')}/script-media/readiness"
                    ),
                    query=query,
                ),
                tool="village_canvas_get_script_media_readiness",
                t0=t0,
                canvas_id=canvas_id,
                node_id=node_id,
                action=action,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(
            exc,
            tool="village_canvas_get_script_media_readiness",
            t0=t0,
        )


def _handle_repair_workflow_canvas_asset_binding(
    args: dict[str, Any],
    **_: Any,
) -> str:
    """Run the exact T-093 duplicate-binding repair for a failed Run."""

    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        step_id = str(args.get("step_id") or "").strip()
        command_id = str(args.get("command_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        if not step_id:
            raise ValueError("step_id is required")
        if not command_id:
            raise ValueError("command_id is required")
        canvas_id = str(args.get("canvas_id") or "").strip()
        if not canvas_id:
            current = _request(
                "GET",
                (f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}"),
            )
            current_data = current.get("data") if isinstance(current, dict) else None
            canvas_id = (
                str(current_data.get("canvas_id") or "").strip()
                if isinstance(current_data, dict)
                else ""
            )
        if not canvas_id:
            raise ValueError("canvas_id is required")
        body: dict[str, Any] = {
            "canvas_id": canvas_id,
            "step_id": step_id,
            "command_id": command_id,
        }
        source_turn_id = str(args.get("source_turn_id") or "").strip()
        if source_turn_id:
            body["source_turn_id"] = source_turn_id[:240]
        expected_run_revision = args.get("expected_run_revision")
        if (
            isinstance(expected_run_revision, int)
            and not isinstance(expected_run_revision, bool)
            and expected_run_revision >= 0
        ):
            body["expected_run_revision"] = expected_run_revision
        result = _request(
            "POST",
            (
                f"/api/v1/projects/{project}/workflow-runs/"
                f"{quote(run_id, safe='')}/canvas-asset-binding-repair"
            ),
            body=body,
        )
        return tool_result(
            _with_tool_trace(
                result,
                tool="workflow.asset_binding.repair",
                t0=t0,
                run_id=run_id,
                step_id=step_id,
                canvas_id=canvas_id,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(
            exc,
            tool="workflow.asset_binding.repair",
            t0=t0,
        )


def _handle_revalidate_workflow_canvas_asset_binding(
    args: dict[str, Any],
    **_: Any,
) -> str:
    """Prove a repaired binding is ready and hand off to media authorization."""

    t0 = time.perf_counter()
    try:
        project = _project_from_args(args)
        run_id = str(args.get("run_id") or "").strip()
        step_id = str(args.get("step_id") or "").strip()
        command_id = str(args.get("command_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        if not step_id:
            raise ValueError("step_id is required")
        if not command_id:
            raise ValueError("command_id is required")
        canvas_id = str(args.get("canvas_id") or "").strip()
        if not canvas_id:
            current = _request(
                "GET",
                (f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}"),
            )
            current_data = current.get("data") if isinstance(current, dict) else None
            canvas_id = (
                str(current_data.get("canvas_id") or "").strip()
                if isinstance(current_data, dict)
                else ""
            )
        if not canvas_id:
            raise ValueError("canvas_id is required")
        body: dict[str, Any] = {
            "canvas_id": canvas_id,
            "step_id": step_id,
            "command_id": command_id,
        }
        source_turn_id = str(args.get("source_turn_id") or "").strip()
        if source_turn_id:
            body["source_turn_id"] = source_turn_id[:240]
        expected_run_revision = args.get("expected_run_revision")
        if (
            isinstance(expected_run_revision, int)
            and not isinstance(expected_run_revision, bool)
            and expected_run_revision >= 0
        ):
            body["expected_run_revision"] = expected_run_revision
        result = _request(
            "POST",
            (
                f"/api/v1/projects/{project}/workflow-runs/"
                f"{quote(run_id, safe='')}/canvas-asset-binding-revalidate"
            ),
            body=body,
        )
        return tool_result(
            _with_tool_trace(
                result,
                tool="workflow.asset_binding.revalidate",
                t0=t0,
                run_id=run_id,
                step_id=step_id,
                canvas_id=canvas_id,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(
            exc,
            tool="workflow.asset_binding.revalidate",
            t0=t0,
        )


def _handle_tavily_search(args: dict[str, Any], **_: Any) -> str:
    """Search through the parent API's optional research capability."""
    t0 = time.perf_counter()
    try:
        project = str(args.get("project_id") or _default_project_id()).strip()
        canvas_id = _canvas_id_from_args(args)
        research_body: dict[str, Any] = {
            "project_id": project,
            "canvas_id": canvas_id,
            "query": str(args.get("query") or "").strip(),
            "max_results": int(args.get("max_results") or 5),
            "topic": str(args.get("topic") or "general"),
            "search_depth": str(args.get("search_depth") or "basic"),
            "include_answer": bool(args.get("include_answer", False)),
        }
        if args.get("research_mode"):
            research_body["research_mode"] = args["research_mode"]
        if args.get("include_raw_content") is True:
            research_body["include_raw_content"] = True
        if args.get("include_domains"):
            research_body["include_domains"] = args["include_domains"]
        if args.get("exclude_domains"):
            research_body["exclude_domains"] = args["exclude_domains"]
        if args.get("time_range"):
            research_body["time_range"] = args["time_range"]
        if args.get("counter_search"):
            research_body["counter_search"] = args["counter_search"]
        if args.get("min_sources") is not None:
            research_body["min_sources"] = args["min_sources"]
        if args.get("independent_domains_required") is not None:
            research_body["independent_domains_required"] = args[
                "independent_domains_required"
            ]
        if args.get("counter_queries"):
            research_body["counter_queries"] = list(args["counter_queries"])[:3]
        response = _request(
            "POST",
            "/api/v1/chat/research",
            body=research_body,
        )
        response_data = response.get("data") if isinstance(response, dict) else None
        result = (
            {**response_data, "status_code": response.get("status_code")}
            if isinstance(response_data, dict)
            else dict(response)
            if isinstance(response, dict)
            else {"ok": False, "error": "invalid research response"}
        )
        if isinstance(response, dict) and response.get("ok") is False:
            result["ok"] = False
            result["error_code"] = response.get("error_code") or "research_error"
            result["error"] = response.get("error") or "联网研究未返回结果。"
        return tool_result(
            _with_tool_trace(
                result,
                tool="village_canvas_tavily_search",
                t0=t0,
                ok=result.get("ok") is True,
                result_count=result.get("result_count", 0),
                cached=bool(result.get("cached")),
                learned=bool(result.get("learned")),
            )
        )
    except (ImportError, RuntimeError, ValueError) as exc:
        return _freezone_tool_error(exc, tool="village_canvas_tavily_search", t0=t0)


async def _handle_vision_analyze(args: dict[str, Any], **_: Any) -> str:
    """Analyze project imagery through Village Infinite Canvas's verified vision gateway."""
    t0 = time.perf_counter()
    try:
        source = str(args.get("image_url") or "").strip()
        question = str(args.get("question") or "请完整描述并分析这张图片。").strip()
        data, media_type = await asyncio.to_thread(
            runtime_handler("_read_vision_source"), source
        )
        response = await asyncio.to_thread(
            _request,
            "POST",
            "/api/v1/chat/vision",
            body={
                "project_id": str(
                    args.get("project_id") or _default_project_id()
                ).strip(),
                "canvas_id": _canvas_id_from_args(args),
                "question": question,
                "image_base64": base64.b64encode(data).decode("ascii"),
                "media_type": media_type,
                "model": str(args.get("model") or "").strip() or None,
            },
        )
        response_data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(response_data, dict) or response.get("ok") is False:
            message = (
                str(response.get("error") or "视觉分析未返回结果。")
                if isinstance(response, dict)
                else "视觉分析未返回结果。"
            )
            raise RuntimeError(message)
        model = str(response_data.get("model") or "")
        analysis = str(response_data.get("analysis") or "").strip()
        if not analysis:
            raise RuntimeError("视觉模型返回空内容")
        return tool_result(
            _with_tool_trace(
                {
                    "success": True,
                    "analysis": analysis,
                    "model": model,
                    "source": source,
                },
                tool="vision_analyze",
                t0=t0,
                model=model,
                bytes=len(data),
            )
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
        return _freezone_tool_error(exc, tool="vision_analyze", t0=t0)
