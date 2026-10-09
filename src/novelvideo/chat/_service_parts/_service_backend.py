"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821

PAGE_AGENT_SCOPES = [
    "projects:read",
    "projects:write",
    "tasks:submit",
    "tasks:poll",
    "media:read",
    "assets:read",
]
PAGE_AGENT_SESSION_TTL_SECONDS = 24 * 3600


async def _create_page_agent_session_token(
    username: str,
    project: str,
    *,
    agent_kind: str,
) -> str:
    token = await get_auth_session_port().create_agent_session(
        username=username,
        scopes=PAGE_AGENT_SCOPES,
        ttl_seconds=PAGE_AGENT_SESSION_TTL_SECONDS,
        agent_kind=agent_kind,
        worker_id=f"page-agent:{agent_kind}:{username}",
        current_scope_kind="project" if project else "home",
        current_project_id=project or None,
        metadata={"source": "chat_service"},
    )
    return token.value


def _project_skill_settings_payload(
    username: str,
    project: str,
    agent_token: str = "",
) -> dict[str, Any]:
    env = {
        "VILLAGE_CANVAS_USERNAME": username,
        "VILLAGE_CANVAS_AGENT_SCOPE": "user",
        "VILLAGE_CANVAS_API_URL": _load_api_url(),
        "VILLAGE_CANVAS_AGENT_TOKEN": agent_token,
    }
    if project:
        env["VILLAGE_CANVAS_PROJECT_ID"] = project
    return {"env": env}


def _write_user_skill_settings(username: str, project: str, agent_token: str = "") -> None:
    workspace = _user_agent_workspace(username)
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    payload = _project_skill_settings_payload(username, project, agent_token)
    (claude_dir / "settings.local.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def ensure_user_claude_workspace(username: str, project: str, agent_token: str = "") -> None:
    workspace = _user_agent_workspace(username)
    claude_dir = workspace / ".claude"
    skills_dir = claude_dir / "skills"
    claude_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    _write_user_skill_settings(username, project, agent_token)
    _sync_project_skills(skills_dir)




def _build_claude_env(username: str, project: str, agent_token: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["VILLAGE_CANVAS_USERNAME"] = username
    env["VILLAGE_CANVAS_AGENT_SCOPE"] = "user"
    if project:
        env["VILLAGE_CANVAS_PROJECT_ID"] = project
    env["VILLAGE_CANVAS_API_URL"] = _load_api_url()
    env["VILLAGE_CANVAS_AGENT_TOKEN"] = agent_token
    return env




def _extract_media(
    content: str,
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
) -> list[dict[str, str]]:
    media_project_dir = _media_project_dir(username, project, project_dir)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    markdown_images = _collect_markdown_image_refs(content)

    def add_item(raw_url: str, path: str | None = None) -> None:
        candidate = raw_url.strip(".,;)]}")
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.path.startswith("/static/"):
            candidate = parsed.path
        if candidate.startswith("/static/"):
            canonical = _canonical_project_static_media_url(project, media_project_dir, candidate)
            if canonical is None:
                return
            candidate, path = canonical
        ext = Path(urlparse(candidate).path).suffix.lower()
        kind = _MEDIA_EXTENSIONS.get(ext)
        if not kind:
            return
        if kind == "image" and (
            candidate in markdown_images
            or (path and path in markdown_images)
            or (path and path.lstrip("./") in markdown_images)
        ):
            return
        effective_path = path or ""
        if not effective_path:
            effective_path = _media_path_from_static_url(candidate) or ""
        key = f"{kind}:{effective_path or candidate}"
        if key in seen:
            return
        seen.add(key)
        items.append(
            {
                "kind": kind,
                "url": candidate,
                "path": effective_path,
                "label": Path(effective_path or candidate).name,
            }
        )

    for match in _URL_RE.finditer(content):
        url = match.group(1)
        if url.startswith("/static/"):
            add_item(url)
        else:
            add_item(url)

    for match in _REL_PATH_RE.finditer(content):
        rel_path = match.group("path")
        full_path = media_project_dir / rel_path
        if full_path.exists():
            static_url = project_static_url(project, rel_path, local_path=full_path)
            add_item(static_url, rel_path)

    return items


def _collect_markdown_image_refs(content: str) -> set[str]:
    refs: set[str] = set()

    for match in _MARKDOWN_IMAGE_RE.finditer(content):
        raw = (match.group(1) or "").strip().strip("<>").strip(".,;)]}")
        if not raw:
            continue
        refs.add(raw)
        parsed = urlparse(raw)
        path = parsed.path if parsed.scheme in {"http", "https"} else raw.split("?", 1)[0]
        if path:
            refs.add(path)
        static_path = _media_path_from_static_url(raw)
        if static_path:
            refs.add(static_path)
            refs.add(static_path.lstrip("./"))
        elif parsed.scheme in {"http", "https"} and parsed.path.startswith("/static/"):
            refs.add(parsed.path)
        elif raw.startswith("/static/"):
            refs.add(raw.split("?", 1)[0])
        else:
            refs.add(path.lstrip("./") if path else raw.lstrip("./"))

    return refs


def _normalize_media_items(
    media: list[dict[str, Any]],
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    media_project_dir = _media_project_dir(username, project, project_dir)

    for item in media:
        if not isinstance(item, dict):
            continue

        candidate = str(item.get("url", "") or "").strip()
        path = str(item.get("path", "") or "").strip()
        if not candidate and not path:
            continue

        if not candidate and path:
            canonical = _canonical_project_static_media_url(project, media_project_dir, path)
            if canonical is None:
                continue
            candidate, path = canonical

        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.path.startswith("/static/"):
            candidate = parsed.path
        if candidate.startswith("/static/"):
            canonical = _canonical_project_static_media_url(project, media_project_dir, candidate)
            if canonical is None:
                continue
            candidate, path = canonical

        ext = Path(urlparse(candidate).path).suffix.lower()
        kind = _MEDIA_EXTENSIONS.get(ext)
        if not kind:
            continue

        if not path:
            path = _media_path_from_static_url(candidate) or ""

        key = f"{kind}:{path or candidate}"
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "kind": kind,
                "url": candidate,
                "path": path,
                "label": str(item.get("label", "") or Path(path or candidate).name),
            }
        )

    return normalized


def _merge_media_items(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for group in groups:
        for item in group:
            kind = str(item.get("kind", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            path = str(item.get("path", "") or "").strip()
            if not kind or not url:
                continue
            key = f"{kind}:{path or url}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "kind": kind,
                    "url": url,
                    "path": path,
                    "label": str(item.get("label", "") or Path(path or url).name),
                }
            )

    return merged


def _filter_markdown_duplicate_images(
    content: str, media: list[dict[str, str]]
) -> list[dict[str, str]]:
    markdown_images = _collect_markdown_image_refs(content)
    if not markdown_images:
        return media

    filtered: list[dict[str, str]] = []
    for item in media:
        kind = str(item.get("kind", "") or "").strip()
        if kind != "image":
            filtered.append(item)
            continue

        url = str(item.get("url", "") or "").strip()
        path = str(item.get("path", "") or "").strip()
        if (
            url in markdown_images
            or (path and path in markdown_images)
            or (path and path.lstrip("./") in markdown_images)
        ):
            continue
        filtered.append(item)

    return filtered


def _build_claude_thread(
    username: str,
    project: str,
    agent_token: str,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
):
    ensure_user_claude_workspace(username, project, agent_token)
    workspace = _user_agent_workspace(username)
    client = ClaudeSdkClient(
        cli_path=_claude_cli_path(),
        cwd=workspace,
        env=_build_claude_env(username, project, agent_token),
        model=_claude_model(),
    )
    session_id = _get_claude_session_id(username, project, conversation_id, canvas_id)
    return client.thread_resume(session_id) if session_id else client.thread_start()










async def interrupt_chat_turn(
    username: str,
    project: str,
    thread_id: str,
    turn_id: str,
    *,
    agent_engine: AgentEngine | None = None,
) -> bool:
    thread_id = str(thread_id or "").strip()
    turn_id = str(turn_id or "").strip()
    backend = normalize_agent_engine(agent_engine) if agent_engine else _chat_backend()
    if backend == "village":
        return False
    if backend == "claude":
        if not thread_id:
            return False
        try:
            return await interrupt_live_claude_client(thread_id)
        except Exception as exc:
            if "closed stdout" in str(exc):
                return True
            raise
    return False


async def stream_assistant_reply(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    image_parts: list[dict[str, str]] | None = None,
    agent_engine: AgentEngine | None = None,
    model: str | None = None,
    agent_model_config: DirectVillageAgentModelConfig | None = None,
    turn_id: str | None = None,
    checkpoint_turn_id: str | None = None,
    canvas_id: str | None = None,
    research_enabled: bool = False,
) -> dict[str, Any]:
    run_lock_id = _acquire_chat_run_lock(username, project)
    heartbeat_task = asyncio.create_task(
        _chat_run_lock_heartbeat_loop(username, project, run_lock_id)
    )
    if project:
        _schedule_growth_task(
            _drain_growth_distillation_events(username, project=project, limit=2)
        )
    try:
        deterministic = _frontend_context_reply(prompt)
        if deterministic is not None:
            return await _stream_deterministic_assistant_reply(
                username,
                project,
                deterministic,
                on_event,
                project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
                turn_id=turn_id,
            )
        model_prompt = _script_creation_model_reply_prompt(prompt) or prompt
        director_preflight = await _director_clarification_preflight(
            username=username,
            project=project,
            prompt=model_prompt,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id,
            adaptive=True,
        )
        director_status = str(director_preflight.get("status") or "")
        if director_status in {"ask", "ready", "cancelled"}:
            clarification = dict(director_preflight.get("clarification") or {})
            await _emit_chat_event_best_effort(
                on_event,
                {
                    "type": (
                        "director_clarification"
                        if director_status == "ask"
                        else "director_clarification_completed"
                    ),
                    "clarification": clarification,
                    "director_clarification_answers": dict(
                        director_preflight.get("answers") or {}
                    ),
                    "director_request": str(
                        director_preflight.get("director_request") or ""
                    ),
                    "director_brief_id": str(
                        director_preflight.get("director_brief_id") or turn_id or ""
                    ),
                    "director_run_mode": str(
                        director_preflight.get("director_run_mode") or "draft"
                    ),
                },
            )
        if director_status == "ask":
            content = _director_clarification_text(
                director_preflight.get("clarification")
            )
            return await _stream_deterministic_assistant_reply(
                username,
                project,
                content,
                on_event,
                project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
                turn_id=turn_id,
                metadata={
                    "backend": "director-preflight",
                    "delivery_verification": {
                        "status": "awaiting_clarification",
                        "clarification_required": True,
                        "clarification_question_id": str(
                            director_preflight.get("clarification", {}).get(
                                "question_id"
                            )
                            or ""
                        ),
                    },
                    "director_clarification": dict(
                        director_preflight.get("clarification") or {}
                    ),
                },
            )
        director_context = _director_clarification_context(director_preflight)
        if director_context:
            model_prompt = "\n\n".join((model_prompt, director_context))
        backend = normalize_agent_engine(agent_engine) if agent_engine else _chat_backend()
        if backend == "village":
            return await _stream_assistant_reply_village(
                username,
                project,
                model_prompt,
                on_event,
                project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                image_parts=image_parts,
                model=model,
                agent_model_config=agent_model_config,
                turn_id=turn_id,
                checkpoint_turn_id=checkpoint_turn_id,
                canvas_id=canvas_id,
                research_enabled=research_enabled,
            )
        if backend != "claude":
            raise RuntimeError(f"Unsupported chat backend: {backend}")
        return await _stream_assistant_reply_claude(
            username,
            project,
            model_prompt,
            on_event,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id,
        )
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        _release_chat_run_lock(username, project, run_lock_id)


def _frontend_context_reply(prompt: str) -> str | None:
    confirmation = _REINGEST_CONFIRMATION_BLOCK_RE.search(normalize_prompt_text(prompt))
    if confirmation:
        body = confirmation.group(1)
        if re.search(r"(?m)^\s*stage:\s*confirm_clear\s*$", body):
            return (
                "覆盖会清空/重建当前项目已有角色、分集、脚本、草图、音频、视频等"
                "流水线结果。是否继续？\n\n请回复 `确定` 或 `继续` 后才会开始覆盖。"
            )
        return (
            "当前项目已有摄入内容，继续会覆盖现有项目。是否要覆盖当前项目？\n\n"
            "请回复 `覆盖` 进入下一步确认。"
        )

    return None


def _script_creation_model_reply_prompt(prompt: str) -> str | None:
    """Allow story creation requests to reach the Agent and Story Lab tools.

    The old deterministic reply forced every no-attachment screenplay request
    back to ingest.  Story Lab is now the first-class upstream authoring path,
    so these turns must keep their normal project context and tool registry.
    """
    return None


async def _stream_deterministic_assistant_reply(
    username: str,
    project: str,
    content: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = _redact_local_filesystem_paths(content)
    message = add_assistant_message(
        username,
        project,
        content,
        [],
        project_dir=project_dir,
        project_state_dir=project_state_dir,
        conversation_id=conversation_id,
        canvas_id=canvas_id,
        turn_id=turn_id,
        metadata={"backend": "deterministic", **dict(metadata or {})},
    )
    await _emit_chat_event_best_effort(on_event, {"type": "assistant_delta", "text": content})
    await _emit_chat_event_best_effort(on_event, {"type": "done", "message": message})
    return message


async def prewarm_chat_backend(
    username: str,
    *,
    project: str | None = None,
    canvas_id: str | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
) -> None:
    """In-process Village turns have no worker cold start."""
    return None


