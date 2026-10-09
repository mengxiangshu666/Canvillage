"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821

_MEDIA_EXTENSIONS = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".wav": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
}
_URL_RE = re.compile(r"(https?://[^\s)>\"]+|/static/[^\s)>\"]+)")
_REL_PATH_RE = re.compile(
    r"(?P<path>(?:assets|videos|audio|images|frames|sketches|grids|uploads|scripts)/[^\s)>\"]+\.(?:png|jpg|jpeg|webp|gif|mp4|mov|webm|wav|mp3|m4a))"
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_USER_TURN_LABEL_RE = re.compile(r"(?im)^\s*(?:user|human|用户|我)\s*[:：]\s*")
_ASSISTANT_TURN_LABEL_RE = re.compile(r"(?i)^\s*(?:assistant|ai|助手|助理|模型)\s*[:：]\s*")
_UI_SPEC_BLOCK_RE = re.compile(r"<ui-spec\b[^>]*>(.*?)</ui-spec>", re.IGNORECASE | re.DOTALL)
_UI_SPEC_FENCE_RE = re.compile(
    r"```(?:json-render|ui-spec|json)?\s*(<ui-spec\b[\s\S]*?</ui-spec>)\s*```",
    re.IGNORECASE,
)
_LOCAL_FILESYSTEM_PATH_RE = re.compile(
    r"(?<![\w./-])(?:~|/Users/[^\s`'\"<>)]+)(?:/[^\s`'\"<>)]+)+"
)
_CHAT_RUN_LOCK_KEY = "active_chat_run"
_CHAT_RUN_LOCK_TTL_SECONDS = 10 * 60
_CHAT_RUN_LOCK_MAX_SECONDS = 60 * 60
_CHAT_RUN_LOCK_HEARTBEAT_SECONDS = 30.0
_CHAT_RUN_LOCK_BIRTH_GRACE_SECONDS = 5.0
_REINGEST_CONFIRMATION_BLOCK_RE = re.compile(
    r"\[VILLAGE_CANVAS_REINGEST_CONFIRMATION\](.*?)\[/VILLAGE_CANVAS_REINGEST_CONFIRMATION\]",
    re.DOTALL,
)
_CHAT_ATTACHMENTS_BLOCK_RE = re.compile(
    r"\[CHAT_ATTACHMENTS\].*?\[/CHAT_ATTACHMENTS\]",
    re.DOTALL,
)
_HIDDEN_TOOL_MARKERS = (
    "skill_view",
    "skills_list",
    "skill view",
    "skills list",
    "loading skill",
    "→ skill view",
    "→ skills list",
)
_JSON_RENDER_CHAT_INSTRUCTIONS = """[RENDERING_CONTRACT]
这是硬性输出合同，优先级高于普通叙述习惯。违反时必须自我修正后再回复。

触发条件：
- 只有在回复需要展示图片、肖像、身份图、草图、首帧、视频、音频等可视/可播放媒体时，才需要调用对应的 村长无限画布 展示工具。
- 角色列表、剧集规划、项目进度、任务状态、脚本/beat 摘要、表格、长篇正文、普通结构化说明默认使用 markdown；如果没有图片/视频/音频媒体，不要使用媒体展示工具。

禁止事项：
- 不要向用户解释内部渲染格式、渲染机制、工具调用过程或工具名；只给业务结果和必要的下一步提示。
- 不要为纯文本、进度、脚本、表格、角色/剧集清单调用媒体展示工具；这些内容使用 markdown。
- 用户要求查看图片、肖像、身份图、草图、首帧、视频、音频时，不要用文字列表、文件名列表、Beat 名称列表或 URL 列表替代媒体展示；必须调用对应展示工具。若没有工具返回的可展示媒体，只说明当前暂无可展示媒体。
- 一旦本轮调用了媒体展示工具，最终自然语言回复只能是简短说明，绝对禁止输出 markdown 图片语法（例如 ![标题](url)）、纯文本媒体 URL、任何 http/https 链接、/static 路径、HTML <img>/<video>/<audio> 标签或聊天附件 media_json。
- 不要猜测、拼接或改写静态资源路径，尤其禁止自行编造 /static/projects/{project_id}/...、/static/admin/{slug}/...、localhost URL 或下载地址。

资源 URL 规则：
- 展示工具会读取 API 返回的可访问 URL 字段（portrait_url、image_url、sketch_url、frame_url、video_url、audio_url、url）并准备可展示媒体。
- 如果工具/API 只返回本地文件路径或你不确定 URL 是否可访问，必须先调用相应 村长无限画布 展示工具；不能自己按经验拼 /static 路径。
- 如果没有正式结果 URL、URL 为空、或资源尚未生成，只说明当前状态，不要伪造媒体展示。
- 如果工具/API 返回多个候选字段，优先使用明确的 *_url 字段；不要使用 *_path 作为 src，除非 API 明确说明该 path 已是浏览器可访问 URL。

展示工具选择：
- 角色肖像/身份图：调用 village_canvas_get_character_media。
- 当前草图：调用 village_canvas_get_sketches，只展示正式 sketch_url。草图候选池：调用 village_canvas_get_sketch_candidates，只展示 grids/epNNN/sketch/beat_XX_t* 候选。首帧：调用 village_canvas_get_first_frames，只展示首帧。
- 场景图：调用 village_canvas_get_scene_images。
- 视频预览、beat 视频、最终成片：调用 village_canvas_get_episode_media(media_type="video") 或对应最终视频读取工具。
- 配音/TTS/音乐：调用 village_canvas_get_episode_media(media_type="audio") 或对应音频读取工具。
- 指定人物肖像：调用 village_canvas_get_character_media(media_kind="portrait", name="角色名或名称片段")；name 只匹配角色名/别名，不要混入身份图。
- 指定身份图：调用 village_canvas_get_character_media(media_kind="identity", name="角色名或身份名片段")；不要混入角色肖像。name 匹配角色名/别名/身份名/身份 ID；只有用户明确按描述内容查找时才用 query="..."。
- 指定当前草图：调用 village_canvas_get_sketches(episode=N, beat=M)；该工具只展示正式 sketch_url/current sketch，不展示 grids/epNNN/sketch/beat_XX_t* 草图池候选。不要用草图池或首帧替代当前草图。指定草图候选/图池/备选草图：调用 village_canvas_get_sketch_candidates(episode=N, beat=M)。指定首帧：调用 village_canvas_get_first_frames(episode=N, beat=M)。多个正式草图用 beat_indices=[...]；分页用 offset + limit。
- 指定场景图：调用 village_canvas_get_scene_images(name="场景名或名称片段")；名称按包含关系模糊匹配；多个关键词用 names=[...]；按第几个场景用 index=N 或 scene_indices=[...]；按类型筛选用 scene_type="..."；分页用 offset + limit。
- 指定视频：调用 village_canvas_get_episode_media(episode=N, media_type="video", beat=M)；按内容片段查视频用 query="..."，匹配 beat 标题、画面描述、解说/对白、说话人、角色、场景；多个 beat 用 beat_indices=[...]；分页用 offset + limit。
- 指定音频/配音/TTS：调用 village_canvas_get_episode_media(episode=N, media_type="audio", beat=M)；按内容片段查音频用 query="..."，匹配 beat 标题、画面描述、解说/对白、说话人、角色、场景；多个 beat 用 beat_indices=[...]；分页用 offset + limit。

发送前自检：
1. 本回复是否展示图片/视频/音频媒体？如果是，是否调用了对应展示工具？
2. 是否避免暴露内部渲染格式、渲染机制、工具调用过程或工具名？
3. 如果不展示图片/视频/音频，是否使用 markdown？
4. 如果任一答案是否，先修正再回复。
[/RENDERING_CONTRACT]"""

_VILLAGE_RENDER_CHAT_INSTRUCTIONS = """[RENDERING_CONTRACT]
- 普通文本、项目状态、脚本、表格使用 markdown，不调用媒体展示工具。
- 需要展示图片、视频或音频时，调用对应 村长无限画布 展示工具；只使用工具返回的正式 URL。
- 不输出媒体 URL、/static 路径、HTML/markdown 媒体标签，不猜测或拼接资源地址。
- 媒体工具已被调用时，最终回复只保留简短业务说明；没有正式媒体时只报告当前状态。
[/RENDERING_CONTRACT]"""

# Canvas reads return URLs and `has_image: true`, never the pixels. Without this
# the agent answers about a picture it has not looked at -- which is how
# "这明显是一张老女人的图片，你识别不了吗" happened. Kept terse: the compact
# contract has a hard prompt-size budget (see the compact render-contract test).
_VILLAGE_VISION_CONTRACT = """[VISION_CONTRACT]
画布读取只给 URL 和 has_image，不含画面内容——没调用视觉能力前，你不知道图上是什么。
用户提到图片、参考图、首帧或人物长相，或要求"照着这个做"时，先用节点的 image_url 调
village_canvas_capability invoke vision.analyze 真看一次，再回答或生成。不要凭节点名、
提示词或文件名猜画面，也不要声称看过没看过的图。
[/VISION_CONTRACT]"""


def _serial_continuity_evidence_gate(
    prompt: object,
    *,
    project: str,
    canvas_id: str | None,
    indexed_tools: bool,
) -> str:
    """Require real read receipts before a serialized-continuity answer."""

    if not indexed_tools or not project or not is_serial_continuity_task(prompt):
        return ""
    # A narrowly scoped canvas edit should use the direct receipt-backed lane;
    # the ten-source continuity gate is reserved for cross-episode reasoning.
    # This keeps node/prompt fixes fast without weakening their revision gate.
    if knowledge_task_stage(prompt) == "simple_canvas":
        return ""
    project_json = json.dumps(str(project), ensure_ascii=False)
    canvas_json = json.dumps(str(canvas_id or "default"), ensure_ascii=False)
    return f"""[SERIAL_CONTINUITY_EVIDENCE_GATE]
This is a read-only serialized-continuity task. Injected memory and the current
canvas are leads, not proof that durable project assets or history are absent.
Before the final answer, use `village_canvas_capability` and retain real receipts:

1. Search and invoke in small evidence groups. Keep each capability-index search
   narrow and set `limit<=4`; invoke the returned read capabilities before
   searching the next group. Do not request one broad index page for the whole
   task. Cover shared context/expert plan/allowlist/checkpoint, then canon and
   durable media, then knowledge/reference/preview. Do not call the legacy `village_canvas_read_compact`;
   it only describes the current graph and
   cannot prove project assets, generation history, or Story Lab continuity.
2. `invoke` these exact read capabilities returned by the index:
   `context.shared_snapshot`, `context.expert_plan`, `context.tool_allowlist`,
   `story.canon`, `media.character`, `media.generation_history`,
   `knowledge.search`, `memory.preview`, and
   `context.execution_checkpoint`.
3. Use project_id={project_json} and canvas_id={canvas_json}. For
   `media.character`, query every character named by the user and include
   identities. For `media.generation_history`, read completed persistent runs,
   not only current canvas nodes. For `knowledge.search`, query the work title,
   target episode, canon, continuity experience, identities, and previous
   episode; then `invoke` `knowledge.load_reference` for the returned
   `memory://` canon and continuity-experience references.
4. Build `memory.preview.references` only from stable IDs returned by
   `media.character` and `media.generation_history`; every item must contain a
   unique `id` and explicit `role`. Its receipt must report the mapping rule,
   warnings, and review status. Do not call `memory.preview` until those IDs
   are available. A receipt with missing reference metadata, non-empty
   warnings, or `requires_review=true` is not a validated preview; retry once
   with the complete references array and report any remaining warning.
5. Treat the query used to build the plan as an immutable checkpoint key. Copy
   that exact query string byte-for-byte into `context.expert_plan`,
   `context.tool_allowlist`, and `context.execution_checkpoint`; do not
   paraphrase it at the checkpoint step. Pass the real plan/allowlist revisions
   returned by the matching plan and allowlist calls, with `confirm=false` and
   a read capability from that allowlist. If the checkpoint still returns
   `stale_context`, repeat the plan and allowlist calls once with the exact same
   query before reporting the final status. Report the actual final status; do
   not describe an imagined future checkpoint as completed.

Do not create or modify nodes, assets, memories, tasks, or paid media. Do not
infer "asset missing" merely because it is not mounted on the current canvas.
If any required read fails, retry it with the missing required arguments when
the failure is retryable, then report the exact failed receipt and any warning
that remains. Do not claim a mapping or validation succeeded when its receipt
has `requires_review=true`. In the final answer, report stable asset/task IDs
and labels only; never paste `/static/` paths, media URLs, HTML, or guessed
filesystem paths. Future execution tools must be exact capability IDs or
documented ActionRouter routes from the current receipts; do not invent model,
adapter, or grid tool names. Do not finalize while a required read receipt is
still merely planned.
[/SERIAL_CONTINUITY_EVIDENCE_GATE]"""


_SERIAL_CONTINUITY_REQUIRED_CAPABILITIES = frozenset(
    {
        "context.shared_snapshot",
        "context.expert_plan",
        "context.tool_allowlist",
        "story.canon",
        "media.character",
        "media.generation_history",
        "knowledge.search",
        "knowledge.load_reference",
        "memory.preview",
        "context.execution_checkpoint",
    }
)


def _serial_continuity_retry_reason(
    assistant_text: object,
    completed_tools: object,
    capability_ids: object,
    tool_trace: object,
) -> str:
    """Return a bounded retry reason when a continuity answer lacks evidence."""

    completed = {
        str(item).strip()
        for item in (
            completed_tools if isinstance(completed_tools, (list, tuple, set)) else ()
        )
        if str(item).strip()
    }
    capabilities = {
        str(item).strip()
        for item in (
            capability_ids if isinstance(capability_ids, (list, tuple, set)) else ()
        )
        if str(item).strip()
    }
    text = str(assistant_text or "")
    trace = str(tool_trace or "")
    if "village_canvas_read_compact" in completed:
        return "legacy_compact_observation_used"
    missing = sorted(_SERIAL_CONTINUITY_REQUIRED_CAPABILITIES - capabilities)
    if missing:
        return "missing_capability_receipts:" + ",".join(missing)
    if '"requires_review":true' in trace or "缺少 reference metadata" in trace:
        return "memory_preview_not_validated"
    if "/static/" in text:
        return "media_path_leaked"
    if "nanobanana_grid" in text or "nanobanana" in text:
        return "unverified_future_tool_named"
    return ""

_VILLAGE_CANVAS_CONTEXT_PREFIX = "[VILLAGE_CANVAS_USER_CONTEXT]"
_USER_MESSAGE_MARKER = "[USER_MESSAGE]"
_INFRASTRUCTURE_ERROR_FRAME_PATTERNS = (
    re.compile(
        r"context length exceeded\b.*cannot compress further\.?",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"api call failed after\s+\d+\s+retr(?:y|ies)\b.*",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"village agent session compression exhausted\s*[.!。]?",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:http\s+5\d\d\s*[:：-]?\s*)?service temporarily unavailable\s*[.!。]?",
        re.IGNORECASE | re.DOTALL,
    ),
)


def _media_path_from_static_url(url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path if parsed.scheme in {"http", "https"} else url.split("?", 1)[0]
    if not path.startswith("/static/"):
        return None
    rel = path[len("/static/") :]
    parts = rel.split("/", 2)
    if len(parts) == 3:
        return unquote(parts[2])
    return unquote(rel)


def _canonical_project_static_media_url(
    project_id: str,
    project_dir: Path,
    url_or_path: str,
) -> tuple[str, str] | None:
    media_path = _media_path_from_static_url(url_or_path)
    if media_path is None:
        media_path = url_or_path.strip().split("?", 1)[0].lstrip("./")
    if not media_path:
        return None
    local_path = project_dir / media_path
    return project_static_url(project_id, media_path, local_path=local_path), media_path


def _media_project_dir(
    username: str,
    project: str,
    project_dir: str | Path | None = None,
) -> Path:
    return Path(project_dir) if project_dir is not None else _project_dir(username, project)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _output_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_OUTPUT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / "output"


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / "state"


def _json_render_error_log_path() -> Path:
    configured = os.environ.get("JR_ERROR_LOG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _repo_root() / "jr_error.log"


# One canonical-JSON violation appends up to 12 000 characters of *model output*
# to this file, and nothing bounded it: `village_canvas_rotate_log.py` only acts
# above 32 MiB and is wired to the launcher's stdout log, not to this one.  So a
# model that keeps emitting malformed ui-spec grew a file in the source root
# without limit.  This is diagnostic evidence, not an audit log — keep a tail.
_JSON_RENDER_ERROR_LOG_MAX_BYTES = 512 * 1024


def _user_preferences_path(username: str) -> Path:
    return _state_root() / username / "preferences.md"


def load_user_preferences(username: str) -> str:
    """Load/create the user-level long-term preference file.

    This is the Lovart-style long-term memory layer: project chat history stays
    project-scoped, while stable taste/workflow preferences live per user.
    """

    path = _user_preferences_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# User Preferences\n\n"
            "Record stable cross-project preferences here, such as visual taste, "
            "brand/style defaults, pacing habits, and recurring workflow choices.\n",
            encoding="utf-8",
        )
    return path.read_text(encoding="utf-8").strip()


def _unwrap_replayed_user_prompt(prompt: str) -> str:
    """Remove previously persisted transport wrappers before re-wrapping.

    Browser retry/resume paths can replay the wire payload returned by the
    backend instead of the original human text.  Without this guard every
    retry nests another user-context/rendering block inside ``[USER_MESSAGE]``;
    one failed turn was observed with three complete copies in a single ACP
    message.  The Agent harness can compact old turns, but it preserves the
    current user turn, so nested current-turn wrappers are not compressible.
    """

    current = normalize_prompt_text(str(prompt or "").strip())
    for _ in range(8):
        if current.startswith("[CONTEXT: current_project="):
            _first_line, _separator, remainder = current.partition("\n")
            if not _separator:
                break
            current = remainder.lstrip()
            continue
        if not current.startswith(_VILLAGE_CANVAS_CONTEXT_PREFIX):
            break
        marker_index = current.find(_USER_MESSAGE_MARKER)
        if marker_index < 0:
            break
        current = current[marker_index + len(_USER_MESSAGE_MARKER) :].lstrip("\r\n")
    return current


def _human_user_text(prompt: str) -> str:
    """Return only the human request for project chat persistence.

    Canvas V1/V2 envelopes are transport-only. They remain intact in the
    prompt sent to the Agent, but the chat database must contain the human text so
    history, replay and UI search do not grow with static wire contracts.
    """

    current = _unwrap_replayed_user_prompt(prompt)
    v2 = re.search(
        r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
        r"\[/CANVAS_AGENT_REQUEST_V2\]",
        current,
        re.IGNORECASE | re.DOTALL,
    )
    if v2:
        try:
            payload = json.loads(v2.group(1))
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            request = payload.get("request")
            if isinstance(request, str) and request.strip():
                return request.strip()
    v1 = re.search(
        r"\[CANVAS_AGENT_REQUEST_V1\]\s*USER_REQUEST\s*[:：]\s*"
        r"\r?\n(.*?)\r?\n\s*ACTIVE_SKILLS\s*[:：]",
        current,
        re.IGNORECASE | re.DOTALL,
    )
    if v1 and v1.group(1).strip():
        return v1.group(1).strip()
    return current


def _canvas_agent_request_payload(prompt: str) -> dict[str, Any]:
    """Decode the browser's bounded V2 request envelope when one is present."""

    current = _unwrap_replayed_user_prompt(prompt)
    match = re.search(
        r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
        r"\[/CANVAS_AGENT_REQUEST_V2\]",
        current,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _canvas_context_from_prompt(
    prompt: str,
    project: str,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    current = _unwrap_replayed_user_prompt(prompt)
    match = re.search(
        r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
        r"\[/CANVAS_AGENT_REQUEST_V2\]",
        current,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        try:
            payload = json.loads(match.group(1))
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("canvas"), dict):
            canvas = payload["canvas"]
            revision = canvas.get("revision")
            return {
                "project_id": str(canvas.get("project_id") or project or "").strip() or None,
                "canvas_id": str(canvas.get("canvas_id") or "default").strip() or "default",
                "revision": revision
                if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0
                else None,
            }
    return {
        "project_id": str(project or "").strip() or None,
        "canvas_id": str(canvas_id or "default").strip() or "default",
        "revision": None,
    }


def recovery_prompt(original_prompt: str, packet: dict[str, Any]) -> str:
    """Attach a compact machine-readable checkpoint without duplicating history."""
    packet = normalize_payload_schema(packet)
    return (
        f"{original_prompt.rstrip()}\n\n"
        f"{CANONICAL_RECOVERY_OPEN_MARKER}"
        f"{json.dumps(packet, ensure_ascii=False, separators=(',', ':'), default=str)}"
        f"{CANONICAL_RECOVERY_CLOSE_MARKER}\n"
        "Continue this same turn from the last durable checkpoint. Do not repeat "
        "completed tools or recreate canvas commands whose command_id already exists."
    )


def _is_infrastructure_error_message(content: str) -> bool:
    """Return whether *all* of ``content`` is one transport/runtime error frame.

    These strings are also useful prose when the user asks why a request
    failed.  Matching arbitrary substrings used to truncate legitimate
    explanations such as ``Service temporarily unavailable means ...``.
    """

    text = str(content or "").strip()
    if not text:
        return False
    return any(pattern.fullmatch(text) for pattern in _INFRASTRUCTURE_ERROR_FRAME_PATTERNS)


def _strip_infrastructure_error_tail(content: object) -> str:
    """Remove only complete standalone error frames at the end of a reply."""

    text = str(content or "")
    if _is_infrastructure_error_message(text):
        return ""
    lines = text.splitlines(keepends=True)
    while lines:
        last = lines[-1]
        if not last.strip():
            lines.pop()
            continue
        if not _is_infrastructure_error_message(last):
            break
        lines.pop()
    return "".join(lines).rstrip()


def _village_empty_completion_message(
    infrastructure_error: str,
    *,
    had_effect: bool,
) -> str:
    detail = str(infrastructure_error or "").strip().casefold()
    if "system memory overloaded" in detail or "system_memory_overloaded" in detail:
        reason = "当前小树模型上游负载过高（HTTP 503）"
    elif "http 503" in detail or "service temporarily unavailable" in detail:
        reason = "当前小树模型上游暂时没有可用容量（HTTP 503）"
    elif "http 500" in detail:
        reason = "当前小树模型上游返回内部错误（HTTP 500）"
    elif "timed out" in detail or "timeout" in detail:
        reason = "当前小树模型上游响应超时"
    elif "context length exceeded" in detail or "compression exhausted" in detail:
        reason = "当前会话上下文整理失败"
    else:
        reason = "当前小树模型没有返回有效内容"
    if had_effect:
        return f"{reason}；已经落盘的工具回执已保留，请让小树继续核验，避免重复执行。"
    return f"{reason}；本轮尚未开始画布执行，可直接重试或切换其他已配置的 Agent 模型。"


def _prompt_with_user_context(
    username: str,
    project: str,
    prompt: str,
    *,
    compact_contract: bool = False,
) -> str:
    scope = f"project:{project}" if project else "home"
    user_prompt = _unwrap_replayed_user_prompt(prompt)
    preferences = load_user_preferences(username)
    if compact_contract and len(preferences) > 1_200:
        preferences = preferences[:600].rstrip() + "\n…\n" + preferences[-600:].lstrip()
    rendering_contract = (
        _VILLAGE_RENDER_CHAT_INSTRUCTIONS
        if compact_contract
        else _JSON_RENDER_CHAT_INSTRUCTIONS
    )
    return (
        f"{_VILLAGE_CANVAS_CONTEXT_PREFIX}\n"
        f"username: {username}\n"
        f"scope: {scope}\n"
        "Project-scoped facts must stay in the project scope. "
        "Only stable user preferences should be reused across projects.\n\n"
        "[USER_PREFERENCES]\n"
        f"{preferences}\n\n"
        f"{rendering_contract}\n\n"
        f"{_VILLAGE_VISION_CONTRACT}\n\n"
        "[USER_MESSAGE]\n"
        f"{user_prompt}"
    )


def _chat_backend() -> str:
    preferred = (
        os.environ.get("VILLAGE_CANVAS_CHAT_BACKEND")
        or "village"
    ).strip().lower() or "village"
    if preferred == "claude":
        if is_claude_backend_available():
            return "claude"
        raise RuntimeError(
            "VILLAGE_CANVAS_CHAT_BACKEND=claude requested but Claude is unavailable. "
            "Install claude-agent-sdk and ensure CLAUDE_CLI_PATH points to a valid claude binary."
        )
    return "village"


def _claude_cli_path() -> Path:
    configured = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    resolved = shutil.which("claude")
    if resolved:
        return Path(resolved)
    return Path.home() / ".local" / "bin" / "claude"






def _claude_model() -> str | None:
    model = os.environ.get("CLAUDE_MODEL", "").strip()
    return model or None


def _claude_sdk_available() -> bool:
    return importlib.util.find_spec("claude_agent_sdk") is not None


def is_claude_backend_available() -> bool:
    return _claude_cli_path().exists() and _claude_sdk_available()




def is_chat_backend_available() -> bool:
    try:
        backend = _chat_backend()
    except RuntimeError:
        return False
    if backend == "claude":
        return is_claude_backend_available()
    return bool(list_village_agent_models())


def get_chat_backend_name() -> str:
    return _chat_backend()


def _repo_skill_roots() -> list[Path]:
    root = _repo_root()
    return [
        root / "agent_skills",
        root / ".claude" / "skills",
    ]


def _skill_sources() -> list[tuple[str, Path]]:
    sources: dict[str, Path] = {}
    for repo_skills_root in _repo_skill_roots():
        if not repo_skills_root.exists():
            continue
        for child in sorted(repo_skills_root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                # Keep the first matching skill name so .claude/skills remains the default
                # source when both locations expose the same skill.
                sources.setdefault(child.name, child)

    configured = (
        os.environ.get("VILLAGE_CANVAS_CLAUDE_SKILL_PATH")
        or ""
    ).strip()
    if configured:
        sources["village-canvas"] = Path(configured).expanduser()

    return [(name, path) for name, path in sorted(sources.items()) if path.exists()]


def _sync_project_skills(skills_dir: Path) -> None:
    for skill_name, src in _skill_sources():
        dst = skills_dir / skill_name
        if not dst.exists():
            shutil.copytree(src, dst)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _project_dir(username: str, project: str) -> Path:
    base_dir = _output_root() / username / project
    for path in (
        base_dir,
        base_dir / "graph",
        base_dir / "assets",
        base_dir / "assets" / "characters",
        base_dir / "scripts",
        base_dir / "images",
        base_dir / "audio",
        base_dir / "videos",
        base_dir / "uploads",
    ):
        path.mkdir(parents=True, exist_ok=True)
    return base_dir


def _project_state_dir(username: str, project: str) -> Path:
    base_dir = _state_root() / username / project
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _user_state_dir(username: str) -> Path:
    base_dir = _state_root() / username
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _user_agent_workspace(username: str) -> Path:
    workspace = _user_state_dir(username) / ".chat_agents"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _user_chat_agent_locks_dir(username: str) -> Path:
    base_dir = _user_state_dir(username) / "chat_agent_locks"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _legacy_chat_db_path(
    username: str,
    project: str,
    project_dir: str | Path | None = None,
) -> Path:
    base_dir = Path(project_dir) if project_dir is not None else _project_dir(username, project)
    return base_dir / ".chat" / "chat.db"


def _migrate_legacy_chat_db(
    username: str,
    project: str,
    new_db_path: Path,
    project_dir: str | Path | None = None,
    *,
    create_parent: bool = True,
) -> None:
    legacy_db_path = _legacy_chat_db_path(username, project, project_dir)
    if new_db_path.exists() or not legacy_db_path.exists():
        return
    if not create_parent and not new_db_path.parent.exists():
        return

    if create_parent:
        new_db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{legacy_db_path}{suffix}")
        if not src.exists():
            continue
        dst = Path(f"{new_db_path}{suffix}")
        if dst.exists():
            continue
        shutil.move(str(src), str(dst))

    legacy_dir = legacy_db_path.parent
    try:
        if legacy_dir.exists() and not any(legacy_dir.iterdir()):
            legacy_dir.rmdir()
    except OSError:
        pass


def _chat_db_path(
    username: str,
    project: str,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
) -> Path:
    if project_state_dir is not None:
        db_path = Path(project_state_dir) / "chat.db"
        _migrate_legacy_chat_db(
            username,
            project,
            db_path,
            project_dir,
            create_parent=True,
        )
        return db_path
    db_path = _project_state_dir(username, project) / "chat.db"
    _migrate_legacy_chat_db(username, project, db_path, project_dir, create_parent=True)
    return db_path


def _conversation_storage_id(
    conversation_id: str,
    canvas_id: str | None = None,
) -> str:
    """Keep project chat rows isolated by canvas without changing public IDs."""
    normalized_conversation = normalize_conversation_id(conversation_id)
    normalized_canvas = str(canvas_id or "").strip() or "default"
    if normalized_canvas == "default":
        return normalized_conversation
    canvas_digest = hashlib.sha256(normalized_canvas.encode("utf-8")).hexdigest()[:20]
    return f"canvas_{canvas_digest}__{normalized_conversation}"


def _conversation_public_id(
    storage_id: str,
    canvas_id: str | None = None,
) -> str:
    normalized_canvas = str(canvas_id or "").strip() or "default"
    if normalized_canvas == "default":
        return str(storage_id)
    canvas_digest = hashlib.sha256(normalized_canvas.encode("utf-8")).hexdigest()[:20]
    prefix = f"canvas_{canvas_digest}__"
    return str(storage_id)[len(prefix):] if str(storage_id).startswith(prefix) else str(storage_id)


def _chat_input_history_path(username: str, project: str) -> Path:
    return _project_state_dir(username, project) / "chat_input_history.json"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    ensure_chat_schema(conn)
    conn.commit()
    return conn


def load_chat_input_history(username: str, project: str) -> list[str]:
    if not username or not project:
        return []
    path = _chat_input_history_path(username, project)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    history: list[str] = []
    for item in payload:
        text = str(item or "").strip()
        if text:
            history.append(text)
    return history


def save_chat_input_history(
    username: str, project: str, history: list[str], *, limit: int = 200
) -> None:
    if not username or not project:
        return
    cleaned: list[str] = []
    for item in history:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    if limit > 0:
        cleaned = cleaned[-limit:]
    path = _chat_input_history_path(username, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM chat_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO chat_settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (key, value, _now_iso()),
    )
    conn.commit()


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


def _windows_pid_is_alive(pid: int) -> bool:
    """Probe a Windows PID without using ``os.kill(pid, 0)``.

    CPython maps signal 0 to an invalid Windows operation on some builds, which
    raises WinError 87 even for the current live process. ``OpenProcess`` plus
    ``GetExitCodeProcess`` is the platform-native liveness check. Access denied
    still proves that the process exists; every other probe failure is treated
    as dead and never escapes into WebSocket setup.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
    except Exception:  # noqa: BLE001 - liveness probes must never escape
        return False
    if not handle:
        try:
            return int(kernel32.GetLastError()) == _ERROR_ACCESS_DENIED
        except Exception:  # noqa: BLE001 - best-effort platform probe
            return False
    try:
        exit_code = ctypes.c_ulong()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
        except Exception:  # noqa: BLE001 - best-effort platform probe
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - closing a probe handle is best-effort
            pass


def _pid_is_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            return True
        return False
    except Exception:  # noqa: BLE001 - never break chat setup on a probe
        return False
    return True


def _parse_chat_run_lock(
    value: str | None,
) -> tuple[str | None, int | None, datetime | None, datetime | None]:
    if not value:
        return None, None, None, None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value, None, None, None
    if not isinstance(payload, dict):
        return None, None, None, None
    lock_id = payload.get("lock_id")
    owner_pid = payload.get("owner_pid")
    started_at = payload.get("started_at")
    updated_at = payload.get("updated_at") or started_at
    return (
        str(lock_id).strip() or None if lock_id is not None else None,
        int(owner_pid) if isinstance(owner_pid, int) else None,
        _parse_iso_datetime(str(started_at)) if started_at is not None else None,
        _parse_iso_datetime(str(updated_at)) if updated_at is not None else None,
    )


def _chat_run_lock_is_stale(
    started_at: datetime | None,
    updated_at: datetime | None = None,
) -> bool:
    now = datetime.now(timezone.utc)
    if started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if (now - started_at).total_seconds() > _CHAT_RUN_LOCK_MAX_SECONDS:
            return True
    heartbeat_at = updated_at or started_at
    if heartbeat_at is None:
        return False
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    return (now - heartbeat_at).total_seconds() > _CHAT_RUN_LOCK_TTL_SECONDS


def _chat_run_lock_key(project: str) -> str:
    project_key = project.strip() or "home"
    return f"{_CHAT_RUN_LOCK_KEY}:{project_key}"


def _chat_run_lock_path(username: str, project: str) -> Path:
    lock_key = _chat_run_lock_key(project)
    digest = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()
    return _user_chat_agent_locks_dir(username) / f"{digest}.lock"


def _read_chat_run_lock_file(
    path: Path,
) -> tuple[str | None, int | None, datetime | None, datetime | None]:
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None, None, None
    except OSError:
        return None, None, None, None
    return _parse_chat_run_lock(value)


def _remove_chat_run_lock_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _atomic_write_chat_run_lock_file(path: Path, payload: str) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _chat_run_lock_payload(lock_id: str, *, started_at: str | None = None) -> str:
    now = _now_iso()
    return json.dumps(
        {
            "lock_id": lock_id,
            "owner_pid": os.getpid(),
            "started_at": started_at or now,
            "updated_at": now,
        },
        ensure_ascii=False,
    )


def _chat_run_lock_file_is_new(path: Path) -> bool:
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return (datetime.now(timezone.utc).timestamp() - mtime) < _CHAT_RUN_LOCK_BIRTH_GRACE_SECONDS


def _acquire_chat_run_lock(username: str, project: str) -> str:
    lock_path = _chat_run_lock_path(username, project)
    lock_id = uuid.uuid4().hex
    lock_payload = _chat_run_lock_payload(lock_id)
    payload_bytes = lock_payload.encode("utf-8")
    for _attempt in range(3):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing_lock_id, owner_pid, started_at, updated_at = _read_chat_run_lock_file(
                lock_path
            )
            if not existing_lock_id and _chat_run_lock_file_is_new(lock_path):
                raise RuntimeError("当前用户已有 AI 对话正在处理中，请稍后再试。")
            if (
                existing_lock_id
                and _pid_is_alive(owner_pid)
                and not _chat_run_lock_is_stale(started_at, updated_at)
            ):
                raise RuntimeError("当前用户已有 AI 对话正在处理中，请稍后再试。")
            _remove_chat_run_lock_file(lock_path)
            continue
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload_bytes)
            return lock_id
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            _remove_chat_run_lock_file(lock_path)
            raise
    raise RuntimeError("当前用户已有 AI 对话正在处理中，请稍后再试。")


def _release_chat_run_lock(username: str, project: str, lock_id: str) -> None:
    lock_path = _chat_run_lock_path(username, project)
    current_lock_id, _owner_pid, _started_at, _updated_at = _read_chat_run_lock_file(lock_path)
    if current_lock_id == lock_id:
        _remove_chat_run_lock_file(lock_path)


def _heartbeat_chat_run_lock(username: str, project: str, lock_id: str) -> bool:
    lock_path = _chat_run_lock_path(username, project)
    current_lock_id, _owner_pid, started_at, _updated_at = _read_chat_run_lock_file(lock_path)
    if current_lock_id != lock_id:
        return False
    payload = _chat_run_lock_payload(
        lock_id,
        started_at=started_at.isoformat() if started_at else None,
    )
    try:
        _atomic_write_chat_run_lock_file(lock_path, payload)
    except OSError:
        return False
    return True


def chat_run_lock_is_active(username: str, project: str = "") -> bool:
    lock_path = _chat_run_lock_path(username, project)
    existing_lock_id, owner_pid, started_at, updated_at = _read_chat_run_lock_file(lock_path)
    if (
        existing_lock_id
        and _pid_is_alive(owner_pid)
        and not _chat_run_lock_is_stale(started_at, updated_at)
    ):
        return True
    _remove_chat_run_lock_file(lock_path)
    return False


def force_release_chat_run_lock(username: str, project: str) -> None:
    _remove_chat_run_lock_file(_chat_run_lock_path(username, project))


async def _chat_run_lock_heartbeat_loop(username: str, project: str, lock_id: str) -> None:
    while True:
        await asyncio.sleep(_CHAT_RUN_LOCK_HEARTBEAT_SECONDS)
        if not _heartbeat_chat_run_lock(username, project, lock_id):
            return


def _append_message(
    conn: sqlite3.Connection,
    role: str,
    content: str,
    media: list[dict[str, Any]] | None = None,
    *,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    media = media or []
    metadata = sanitize_chat_metadata(metadata)
    public_conversation_id = normalize_conversation_id(conversation_id)
    conversation_id = _conversation_storage_id(public_conversation_id, canvas_id)
    created_at_iso = _now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO chat_conversations(id, title, created_at, updated_at)
        VALUES (?, '新对话', ?, ?)
        """,
        (conversation_id, created_at_iso, created_at_iso),
    )
    if role == "user":
        title = " ".join(str(content or "").split()).strip()[:36]
        if title:
            conn.execute(
                """
                UPDATE chat_conversations
                   SET title=CASE WHEN title='新对话' THEN ? ELSE title END,
                       updated_at=?
                 WHERE id=?
                """,
                (title, created_at_iso, conversation_id),
            )
    else:
        conn.execute(
            "UPDATE chat_conversations SET updated_at=? WHERE id=?",
            (created_at_iso, conversation_id),
        )
    normalized_turn_id = str(turn_id or "").strip() or None
    if role == "assistant" and normalized_turn_id:
        row = conn.execute(
            """
            INSERT INTO chat_messages(
                role, content, media_json, conversation_id, turn_id,
                metadata_json, created_at
            )
            VALUES ('assistant', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, turn_id)
              WHERE role='assistant' AND turn_id IS NOT NULL AND turn_id <> ''
            DO UPDATE SET
                content=excluded.content,
                media_json=excluded.media_json,
                metadata_json=excluded.metadata_json
            RETURNING id, created_at
            """,
            (
                content,
                json.dumps(media, ensure_ascii=False),
                conversation_id,
                normalized_turn_id,
                json.dumps(metadata, ensure_ascii=False),
                created_at_iso,
            ),
        ).fetchone()
        message_id = int(row["id"])
        persisted_created_at = str(row["created_at"])
    else:
        cursor = conn.execute(
            """
            INSERT INTO chat_messages(
                role, content, media_json, conversation_id, turn_id,
                metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                role,
                content,
                json.dumps(media, ensure_ascii=False),
                conversation_id,
                normalized_turn_id,
                json.dumps(metadata, ensure_ascii=False),
                created_at_iso,
            ),
        )
        message_id = int(cursor.lastrowid)
        persisted_created_at = created_at_iso
    conn.commit()
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "media": media,
        "conversation_id": public_conversation_id,
        **({"turn_id": str(turn_id).strip()} if str(turn_id or "").strip() else {}),
        **({"metadata": metadata} if metadata else {}),
        "created_at": persisted_created_at,
    }


def _split_trace_contents(content: str) -> list[str]:
    raw_lines = str(content or "").rstrip().splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in raw_lines:
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks if block]


def _is_hidden_chat_tool_event(name: object, text: object) -> bool:
    """Internal Agent bookkeeping tools should not become user-visible cards."""
    haystack = f"{name or ''}\n{text or ''}".lower()
    return any(marker in haystack for marker in _HIDDEN_TOOL_MARKERS)


def _completion_text_or_existing(event_text: object, existing: str) -> str:
    """ACP may finish with metadata like ``stop=end_turn`` after text deltas."""
    final_text = _strip_infrastructure_error_tail(event_text).strip()
    if not final_text or final_text.startswith("stop="):
        return existing
    if existing.strip() and _is_completion_notice(final_text):
        if final_text in existing:
            return existing
        return f"{existing.rstrip()}\n\n{final_text}"
    return final_text


def _is_completion_notice(text: str) -> bool:
    return text in {
        "当前任务已开始处理。请稍后让我查看当前任务进度，或在任务完成后再继续下一步。",
        "刚才这一步没有成功启动任务。请先根据返回的错误补齐前置条件；如果是配音缺少声线，可以到「声音资产」上传或录制缺失声线后再继续。",
    }


def _merge_stream_text(existing: str, incoming: object) -> str:
    """Support providers that emit either cumulative text or delta chunks."""
    chunk = str(incoming or "")
    if not chunk:
        return existing
    if chunk.startswith(existing):
        return chunk
    if existing.endswith(chunk):
        return existing
    return existing + chunk


async def _emit_chat_event_best_effort(on_event, event: dict[str, Any]) -> bool:
    """Emit to the connected client without making persistence depend on it."""
    try:
        await on_event(event)
        return True
    except Exception:
        return False


