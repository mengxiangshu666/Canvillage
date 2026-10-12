"""Use the canvas script generator without duplicating its creative engine."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from novelvideo.services.canvas_commands import validate_canvas_script_rows
from novelvideo.services.production_contracts import inject_filmcraft_rules

from .contracts import _canvas_payload_from_response, _response_payload
from .runtime import runtime_proxy

_request = runtime_proxy("_request")
_project_from_args = runtime_proxy("_project_from_args")
_canvas_id_from_args = runtime_proxy("_canvas_id_from_args")
_handle_dispatch_canvas_action = runtime_proxy("_handle_dispatch_canvas_action")
tool_result = runtime_proxy("tool_result")
tool_error = runtime_proxy("tool_error")


def _script_node_snapshot(args: dict[str, Any]) -> tuple[str, str, dict, dict]:
    project = _project_from_args(args)
    canvas_id = _canvas_id_from_args(args)
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("node_id is required")
    response = _request(
        "GET", f"/api/v1/projects/{project}/freezone/canvases/{quote(canvas_id, safe='')}"
    )
    doc = _canvas_payload_from_response(response) or {}
    node = next(
        (node for node in doc.get("nodes", [])
         if isinstance(node, dict) and node.get("id") == node_id),
        None,
    )
    if not node or node.get("type") != "scriptNode":
        raise ValueError("请指定当前画布中真实存在的脚本节点")
    revision = doc.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("画布缺少有效版本，不能安全提交或改稿")
    return project, canvas_id, doc, node.get("data") or {}


def _script_report(result: dict) -> dict:
    return validate_canvas_script_rows(
        result.get("rows") or [], director_plan=result.get("director_plan")
    ).as_dict()


def _handle_script_node_inspect(args: dict[str, Any], **_: Any) -> str:
    try:
        project, canvas_id, doc, data = _script_node_snapshot(args)
        result = data.get("scriptResult") or {}
        plan = result.get("director_plan") or {}
        visual = plan.get("visual_bible") or {}
        return tool_result({
            "ok": True,
            "project_id": project,
            "canvas_id": canvas_id,
            "node_id": args["node_id"],
            "revision": doc["revision"],
            "prompt": data.get("prompt") or "",
            "model": data.get("model") or "",
            "active_task": {
                "is_generating": data.get("isGenerating") is True,
                "job_id": data.get("generationTaskJobId"),
                "task_key": data.get("generationTaskKey"),
            },
            "script_result": result,
            "contract_report": _script_report(result),
            "filmcraft_guidance": inject_filmcraft_rules(
                node_type="script",
                creation_stage="script",
                params={"shot_count": len(result.get("rows") or [])},
                director_vision={
                    "style_anchor": visual,
                    "rhythm": plan.get("rhythm_curve") or "",
                    "cinematic": {"lighting": visual.get("lighting"),
                                  "color_look": visual.get("color_progression"),
                                  "sound": plan.get("sound_plan")},
                },
                source_text="\n".join(filter(None, (
                    str(args.get("focus") or ""), str(data.get("prompt") or ""),
                    str(plan.get("sound_plan") or ""),
                ))),
            ),
            "evidence_scope": "script_design_only; not_generated_media_review",
        })
    except Exception as exc:
        return tool_error(str(exc))


def _script_generation_inputs(doc: dict, node_id: str) -> dict[str, Any]:
    """Match the script node's connected text/video/image input semantics."""
    by_id = {node["id"]: node for node in doc.get("nodes", []) if isinstance(node, dict)}
    upstream_ids = dict.fromkeys(
        edge.get("source") for edge in doc.get("edges", [])
        if isinstance(edge, dict) and edge.get("target") == node_id
    )
    texts: list[str] = []
    refs: list[dict] = []
    inputs: dict[str, Any] = {}
    for source_id in upstream_ids:
        source = by_id.get(source_id) or {}
        data = source.get("data") or {}
        kind = source.get("type")
        if kind == "textAnnotationNode" and data.get("content"):
            texts.append(str(data["content"]))
        elif kind == "scriptNode" and data.get("scriptResult"):
            texts.append(json.dumps(data["scriptResult"], ensure_ascii=False))
        elif kind == "videoNode" and data.get("videoUrl"):
            if inputs.get("video_url"):
                raise ValueError("连接了多个视频参考，请先明确保留哪一个")
            inputs["video_url"] = data["videoUrl"]
            duration = data.get("durationMs")
            if isinstance(duration, (int, float)) and duration > 0:
                inputs["duration_sec"] = duration / 1000
        elif kind in {
            "uploadNode", "exportImageNode", "imageGenNode", "imageNode",
            "storyboardGenNode",
        }:
            # Unfinished derived keyframes must not silently reuse the opening image.
            url = data.get("imageUrl")
            if not url and not data.get("scriptShotKeyframeRowKey"):
                url = data.get("previewImageUrl") or data.get("referenceImageUrl")
            if url:
                refs.append({"image_url": url, "name": data.get("displayName") or ""})
    if texts:
        inputs["source_text"] = "\n\n".join(texts)
    if refs:
        inputs["character_refs"] = refs
    return inputs


def _handle_script_node_submit(args: dict[str, Any], **_: Any) -> str:
    try:
        project, canvas_id, doc, data = _script_node_snapshot(args)
        if data.get("isGenerating") is True:
            raise ValueError("该脚本节点正在生成，请先查询当前任务，不要重复提交")
        mode = str(args.get("mode") or "")
        if mode not in {"generate", "rewrite_shot", "rewrite_sequence", "repair"}:
            raise ValueError("mode 必须是 generate/rewrite_shot/rewrite_sequence/repair")
        result = data.get("scriptResult") or {}
        node_id = str(args["node_id"])
        body: dict[str, Any] = {
            "canvas_id": canvas_id,
            "node_id": node_id,
            "expected_canvas_revision": doc["revision"],
            "model": args.get("model") or data.get("model") or "",
            "video_model": (data.get("videoGenConfig") or {}).get("model", ""),
            "source_text": str(data.get("prompt") or ""),
            "prompt": str(args.get("prompt") or "").strip(),
        }
        if mode == "generate":
            inputs = _script_generation_inputs(doc, node_id)
            body.update(inputs)
            if (body.get("video_url") or body.get("character_refs")) and "source_text" not in inputs:
                body["source_text"] = ""
            original_prompt = str(data.get("prompt") or "").strip()
            if inputs and original_prompt and original_prompt != body["prompt"]:
                body["prompt"] = "\n\n".join(filter(None, (original_prompt, body["prompt"])))
            else:
                body["prompt"] = body["prompt"] or original_prompt
            if result.get("director_plan"):
                body["prompt"] += "\n参考当前导演规划，按本次要求修改；assumptions 不代表用户已确认：\n" + json.dumps(
                    result["director_plan"], ensure_ascii=False
                )
        else:
            rows = result.get("rows") or []
            if not rows:
                raise ValueError("当前脚本还没有镜头表，请先生成")
            body.update({
                "current_rows": rows,
                "director_plan": result.get("director_plan"),
                "title": result.get("title") or data.get("scriptTitle") or "",
            })
            if mode == "repair":
                issues = [issue for issue in _script_report(result)["issues"]
                          if not issue.get("fixed")]
                if not issues:
                    raise ValueError("当前检查没有未修复问题，无需提交修复任务")
                body.update(repair_mode="script-contract", repair_issues=issues)
            else:
                if not body["prompt"]:
                    raise ValueError("请给出这次修改的具体要求")
                if mode == "rewrite_shot":
                    shot_id = str(args.get("shot_id") or "")
                    if not shot_id or not any(row.get("shot_id") == shot_id for row in rows):
                        raise ValueError("shot_id 必须来自当前脚本表，不能猜镜头编号")
                    body["rewrite_shot_id"] = shot_id
                else:
                    body["rewrite_sequence_id"] = str(args.get("sequence_id") or "")
                    if not body["rewrite_sequence_id"]:
                        raise ValueError("sequence_id is required")
        response = _request(
            "POST", f"/api/v1/projects/{project}/freezone/text/story-script", body=body
        )
        return tool_result({
            **response,
            "canvas_source": {
                "canvas_id": canvas_id, "node_id": node_id, "revision": doc["revision"],
            },
            "next": "canvas.node.script.result; 完成后 canvas.node.script.apply",
            "canvas_applied": False,
            "retry_policy": "只查询已有 job_id；提交结果不明时先查任务，禁止自动重提",
        })
    except Exception as exc:
        return tool_error(str(exc))


def _script_job_result(args: dict[str, Any]) -> dict:
    project = _project_from_args(args)
    job_id = str(args.get("job_id") or "").strip()
    if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
        raise ValueError("有效的 job_id is required")
    return _request(
        "GET",
        f"/api/v1/projects/{project}/freezone/jobs/freezone_story_script/"
        f"{quote(job_id, safe='')}/result",
    )


def _handle_script_node_result(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_script_job_result(args))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_script_node_apply(args: dict[str, Any], **_: Any) -> Any:
    try:
        response = _script_job_result(args)
        if response.get("ok") is not True:
            return tool_result(response)
        result = _response_payload(response)
        source = result.get("canvas_source") or {}
        project, canvas_id, doc, data = _script_node_snapshot(args)
        if source.get("canvas_id") != canvas_id or source.get("node_id") != args["node_id"]:
            raise ValueError("任务没有匹配的脚本来源，不能写回此节点")
        if not isinstance(result.get("rows"), list) or not result["rows"]:
            raise ValueError("任务没有可用的完整脚本表，不能写回")
        if data.get("scriptResult") == result:
            return tool_result({"ok": True, "already_applied": True, "revision": doc["revision"]})
        revision = source.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or doc["revision"] != revision:
            raise ValueError("提交后画布已变化；请先核对新旧稿，不能覆盖当前内容")
        node_id = str(args["node_id"])
        return _handle_dispatch_canvas_action({
            "project_id": project,
            "canvas_id": canvas_id,
            "request": str(args.get("request") or "将已完成的脚本改稿应用到原节点"),
            "goal": "仅将已完成的脚本任务写回原脚本节点",
            "success_criteria": ["原节点脚本已保存，其他节点和用户字段保持不变"],
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": [node_id],
                "contains_paid_media": False,
                "requires_recovery": False,
                "requires_delivery": False,
            },
            "dynamic_execution": True,
            "command_id": f"script-result:{args['job_id']}",
            "expected_canvas_revision": revision,
            "commands": [{
                "type": "update_node_data",
                "node_id": node_id,
                "node_data": {
                    "scriptResult": result,
                    "scriptTitle": result.get("title") or "",
                    "scriptContractReport": result.get("contract_report"),
                    "scriptDirectorPlanNeedsSync": False,
                    "generationError": None,
                    "generationErrorDetails": None,
                    "generationErrorRequestId": None,
                    "generationErrorStage": None,
                    "generationErrorSuggestedAction": None,
                    "generationErrorCode": None,
                    "generationErrorRetryable": None,
                    "generationRecoveryJobId": None,
                    "generationRecoveryTaskType": None,
                    "generationJobId": None,
                    "generationProviderId": None,
                    "generationClientSessionId": None,
                },
            }],
        })
    except Exception as exc:
        return tool_error(str(exc))
