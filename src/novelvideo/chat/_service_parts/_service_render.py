"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821


def _release_readiness_from_workflow_run(run: Mapping[str, Any]) -> dict[str, Any]:
    provided = run.get("release_readiness")
    if isinstance(provided, Mapping):
        status = str(provided.get("status") or "").strip().casefold()
        if status in {"ready", "blocked", "unverified", "not_applicable"}:
            return dict(provided)
    projected = project_workflow_release_readiness(run)
    return dict(projected) if isinstance(projected, Mapping) else {}


def _release_readiness_note(verification: Mapping[str, Any]) -> str:
    readiness = (
        verification.get("release_readiness")
        if isinstance(verification.get("release_readiness"), Mapping)
        else {}
    )
    failed = [
        str(item).strip()
        for item in readiness.get("failed_checks") or []
        if str(item).strip()
    ][:8]
    not_run = [
        str(item).strip()
        for item in readiness.get("not_run_checks") or []
        if str(item).strip()
    ][:8]
    missing = [
        str(item).strip()
        for item in readiness.get("missing_checks") or []
        if str(item).strip()
    ][:8]
    if str(verification.get("status") or "") == "release_blocked":
        detail = f"失败检查：{'、'.join(failed)}。" if failed else "发布门存在未通过检查。"
        return (
            f"成片文件已经生成，但发布门未通过。{detail}"
            "当前结果不能标记为可交付或可发布；修复失败项后必须重新执行终片 QC。"
        )
    pending = [*missing, *not_run]
    detail = f"缺少或未测检查：{'、'.join(pending)}。" if pending else "发布门证据尚未补齐。"
    return (
        f"成片文件已经生成，但发布门尚未通过。{detail}"
        "当前只能确认合成任务已完成，不能标记为可发布；补齐检查后再交付。"
    )


def _text_claims_release_ready(text: object) -> bool:
    value = str(text or "")
    for marker in (
        "发布就绪",
        "可以发布",
        "可发布",
        "可以交付",
        "可交付",
        "已交付",
        "ready to publish",
        "ready for release",
        "release-ready",
    ):
        for match in re.finditer(re.escape(marker), value, flags=re.IGNORECASE):
            prefix = value[max(0, match.start() - 6) : match.start()]
            if any(
                negative in prefix
                for negative in ("不可", "不能", "无法", "尚未", "还没有", "没有", "未")
            ):
                continue
            return True
    return False


def _delivery_truth(
    *,
    write_attempted: bool,
    receipt: object,
    workflow_run: object,
    failed_receipt: object = None,
    clarification: object = None,
    dispatch_category: str | None = None,
    dispatch_error_code: str | None = None,
    dispatch_error: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic delivery state from authoritative runtime facts."""

    canvas_receipt = receipt if isinstance(receipt, dict) else {}
    revision = canvas_receipt.get("revision")
    applied_ops = int(canvas_receipt.get("applied_ops") or 0)
    receipt_verified = bool(
        canvas_receipt.get("server_applied") is True
        and canvas_receipt.get("readback_verified", True) is True
        and isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision > 0
        and (applied_ops > 0 or bool(canvas_receipt.get("created_node_ids")))
    )
    run = workflow_run if isinstance(workflow_run, dict) else {}
    workflow_status = str(run.get("status") or "").strip().casefold()
    workflow_verified = workflow_status in {
        "completed",
        "succeeded",
        "success",
        "verified",
    }
    workflow_failed = workflow_status in {
        "failed",
        "error",
        "verification_failed",
    }
    run_id = str(run.get("run_id") or run.get("id") or "").strip()
    release_readiness = _release_readiness_from_workflow_run(run)
    release_status = str(release_readiness.get("status") or "").strip().casefold()
    release_required = bool(release_readiness.get("required")) or release_status in {
        "ready",
        "blocked",
        "unverified",
    }
    release_blocked = (
        workflow_verified and release_required and release_status == "blocked"
    )
    release_unverified = (
        workflow_verified and release_required and release_status == "unverified"
    )
    release_ready = bool(release_required and release_status == "ready")
    release_blocks_delivery = bool(
        workflow_verified
        and release_required
        and release_status in {"blocked", "unverified"}
    )
    delivery_success = bool(
        (receipt_verified or workflow_verified) and not release_blocks_delivery
    )
    diagnostic_receipt = failed_receipt if isinstance(failed_receipt, dict) else {}
    clarification_value = clarification if isinstance(clarification, dict) else {}
    clarification_required = clarification_value.get("required") is True
    category = str(dispatch_category or "").strip().lower()
    if clarification_required and not receipt_verified and not run_id:
        status = "awaiting_clarification"
    elif category in {
        "blocked",
        "blocked_clarification",
        "blocked_authorization",
    } and not receipt_verified and not run_id:
        status = category
    elif release_blocked:
        status = "release_blocked"
    elif release_unverified:
        status = "release_unverified"
    elif workflow_verified or receipt_verified:
        status = "verified_success"
    elif workflow_failed:
        status = "verified_failure"
    elif category == "workflow_started":
        status = "workflow_started"
    elif category == "receipt_missing" and not run_id:
        status = "receipt_missing"
    elif category == "canvas_failed":
        status = "canvas_failed"
    elif category == "tool_failed":
        status = "verified_failure"
    elif run_id:
        status = "in_progress"
    elif write_attempted:
        status = "incomplete"
    else:
        status = "not_applicable"
    return {
        "status": status,
        "receipt_verified": receipt_verified,
        "workflow_verified": workflow_verified,
        "workflow_failed": workflow_failed,
        "workflow_status": workflow_status,
        "workflow_run_id": run_id,
        "release_readiness": release_readiness,
        "release_required": release_required,
        "release_status": release_status,
        "release_ready": release_ready,
        "delivery_success": delivery_success,
        "server_applied": canvas_receipt.get("server_applied"),
        "structure_status": str(canvas_receipt.get("structure_status") or ""),
        "command_id": str(canvas_receipt.get("command_id") or "").strip(),
        "revision": revision,
        "applied_ops": applied_ops,
        "followup_receipt_failed": _canvas_receipt_is_failed_diagnostic(
            diagnostic_receipt
        ),
        "failed_command_id": str(diagnostic_receipt.get("command_id") or "").strip(),
        "clarification_required": clarification_required,
        "clarification_question_id": str(
            clarification_value.get("question_id") or ""
        ).strip(),
        "dispatch_category": category,
        "dispatch_error_code": str(dispatch_error_code or "").strip()[:200],
        "dispatch_error": redact_secrets(str(dispatch_error or "").strip())[:1200],
    }


_IN_PROGRESS_USER_NOTE = (
    "任务已经进入后台执行，目前仍在进行中。"
    "我会沿着现有任务继续，不会把它误报为已经完成。"
)
_UNVERIFIED_WRITE_NOTE = (
    "这次调整没有真正写入画布，现有内容保持不变。"
    "请重试刚才的操作；如果调整对象还不明确，我会先问清楚再执行。"
)
#: Internal error codes must never reach the user; the Agent's own preserved prose
#: carries the explanation. These are the only codes we translate to plain language.
_DELIVERY_FAILURE_BY_CODE = {
    "canvas_revision_conflict": "画布在你操作期间被别的改动改过，我没有覆盖它，先核对最新内容再重试。",
    "canvas_action_requires_workflow": "这次调整不是直接改画布能完成的，应该走工作流。",
    "canvas_existing_mutation_revision_required": "这次修改需要先绑定画布当前版本，我会重新取一次再改。",
    "canvas_command_apply_failed": "画布没能写入，现有内容保持不变。",
    "FZ_EMIT_UNVERIFIED": "画布写入没有被确认，现有内容保持不变。",
    "FZ_SERVER_APPLY_FAILED": "画布没能写入，现有内容保持不变。",
}
_GENERIC_WRITE_FAILURE = "这次画布写入没有成功，现有内容保持不变。"
_CODE_LIKE_RE = re.compile(r"[a-z]{3,}_[a-z_]+|\bFZ_[A-Z_]+\b|\b[a-z]+\.[a-z_.]+\(|error_code")


def _user_facing_reason(raw: object, *, fallback: str) -> str:
    """Pass through a human block reason, but never an internal code (T-213/JEV)."""

    value = str(raw or "").strip()
    if not value:
        return fallback
    if _CODE_LIKE_RE.search(value):
        return fallback
    if not any("\u4e00" <= char <= "\u9fff" for char in value):
        return fallback
    return value


def _delivery_failure_note(verification: Mapping[str, Any]) -> str:
    """One plain-language failure line; raw codes never surface to the user."""

    code = str(verification.get("dispatch_error_code") or "").strip()
    return _DELIVERY_FAILURE_BY_CODE.get(code, _GENERIC_WRITE_FAILURE)


def _enforce_delivery_truth(
    text: str,
    *,
    verification: dict[str, Any],
) -> str:
    """Keep model prose; intervene only when it contradicts what runtime proved.

    T-213 design (JEV verdict): the previous version replaced the Agent's whole
    reply with a canned line whenever a turn was not a clean success, so every
    in-progress turn read like the same script. Now the model's own words are
    preserved and only two things are enforced — (a) it may not claim a delivery
    the runtime did not verify, and (b) internal error codes never reach the user.
    """

    status = str(verification.get("status") or "not_applicable")
    stripped = str(text or "").strip()
    claims_saved = _text_claims_persisted_delivery(text)

    if status == "verified_success":
        if any(
            marker in text
            for marker in (
                "本轮没有形成可验证的持久交付",
                "没有形成可验证的持久交付",
                "server_applied: `false`",
                "structure_status: `emit_only`",
                _DISPATCH_UNVERIFIED_USER_TEXT,
            )
        ):
            lines = ["画布调整已真正保存。"]
            if verification.get("followup_receipt_failed"):
                lines.append("后续的一次重复同步没有生效，但不影响已保存的结果。")
            return "\n".join(lines)
        return text

    if status in {"release_blocked", "release_unverified"}:
        note = _release_readiness_note(verification)
        if not stripped or _text_claims_release_ready(text):
            return note
        if "发布门" in text and any(
            marker in text for marker in ("未通过", "尚未", "未证实")
        ):
            return text
        return "\n\n".join((stripped, note))

    if status in {"not_applicable", "awaiting_clarification"}:
        return text

    if status in {"blocked", "blocked_clarification", "blocked_authorization"}:
        if any(
            marker in text
            for marker in (
                _DISPATCH_UNVERIFIED_USER_TEXT,
                "这次调整没有真正写入画布",
                "前一项写入没有成功",
            )
        ):
            return _user_facing_reason(
                verification.get("dispatch_error"),
                fallback="本轮请求未进入画布写入，当前内容保持不变。",
            )
        return text

    if status in {"in_progress", "workflow_started"}:
        # Preserve the model's own narration; only make sure it does not claim a
        # finished delivery while the run is still going, and always append the
        # honest in-progress note (its line is what completion receipts rewrite).
        if _IN_PROGRESS_USER_NOTE.split("。")[0] in text:
            return text
        if not stripped or claims_saved:
            return _IN_PROGRESS_USER_NOTE
        return "\n\n".join((stripped, _IN_PROGRESS_USER_NOTE))

    if status == "receipt_missing":
        note = "画布写入请求已经提交，但保存回执暂未返回，正在核对最终状态。核对完成前不会重复写入。"
        if not stripped:
            return note
        if "回执" in text:
            return text
        return "\n\n".join((stripped, note))

    if status == "canvas_failed":
        note = _delivery_failure_note(verification)
        if not stripped or claims_saved:
            return note
        return "\n\n".join((stripped, note))

    if status == "verified_failure":
        # 只有真的存在一个可续跑的 WorkflowRun 时，才能说「已保留恢复点 / 从
        # 失败步骤继续」——否则那句话正是 turn_delivery 的 _RESUMABILITY_CLAIM_RE
        # 判定的「比『已完成』更危险」的谎话（T-217）。
        if str(verification.get("workflow_run_id") or "").strip():
            note = (
                "这次执行没有成功。原 WorkflowRun 仍在，"
                "后续会从失败步骤继续，不会重复已经完成的部分。"
            )
        else:
            note = "这次执行没有成功。当前内容保持不变，可以重试。"
        if not stripped or claims_saved:
            return note
        return "\n\n".join((stripped, note))

    if _DISPATCH_UNVERIFIED_USER_TEXT in text:
        return text
    if stripped and not claims_saved:
        return "\n\n".join((stripped, _UNVERIFIED_WRITE_NOTE))
    return _UNVERIFIED_WRITE_NOTE


def _assistant_prefix_candidates(previous_assistant: object) -> list[str]:
    if isinstance(previous_assistant, (list, tuple)):
        items = [str(item or "").strip() for item in previous_assistant if str(item or "").strip()]
        # A replayed sequence can be removed one historical message at a time.
        # Building every joined suffix is quadratic in both bytes and time and
        # used to happen again for every streaming delta.
        return sorted(set(items), key=len, reverse=True)
    prefix = str(previous_assistant or "").strip()
    return [prefix] if prefix else []


def _semantic_operator(text: str, index: int, char: str) -> bool:
    if char in "<>!=+*/%&^~":
        return True

    previous = index - 1
    while previous >= 0 and text[previous].isspace():
        previous -= 1
    following = index + 1
    while following < len(text) and text[following].isspace():
        following += 1
    left = text[previous] if previous >= 0 else ""
    right = text[following] if following < len(text) else ""
    if char in "-|?:":
        return left.isalnum() and right.isalnum()
    if char == ".":
        return left.isdigit() and right.isdigit()
    return False


def _semantic_chars_with_end_positions(value: object) -> tuple[str, list[int]]:
    text = str(value or "")
    normalized: list[str] = []
    end_positions: list[int] = []
    for index, char in enumerate(text):
        for normalized_char in unicodedata.normalize("NFKC", char).casefold():
            if normalized_char.isalnum() or _semantic_operator(text, index, normalized_char):
                normalized.append(normalized_char)
                end_positions.append(index + 1)
    return "".join(normalized), end_positions


def _trim_replay_boundary(text: str) -> str:
    index = 0
    while index < len(text) and not text[index].isalnum():
        index += 1
    return text[index:]


_PreparedReplayPrefix = tuple[str, str, str]


def _prepare_replay_prefix_candidates(previous_assistant: object) -> tuple[_PreparedReplayPrefix, ...]:
    prepared: list[_PreparedReplayPrefix] = []
    for prefix in _assistant_prefix_candidates(previous_assistant):
        compact = "".join(prefix.split())
        semantic, _ = _semantic_chars_with_end_positions(prefix)
        if prefix and compact and semantic:
            prepared.append((prefix, compact, semantic))
    return tuple(prepared)


def _strip_replayed_assistant_prefix(
    content: str,
    previous_assistant: object,
    *,
    suppress_partial_replay: bool = False,
    prepared_candidates: tuple[_PreparedReplayPrefix, ...] | None = None,
) -> str:
    """Some model providers can replay prior assistant text at turn start."""
    text = str(content or "")
    original_text = text
    candidates = prepared_candidates or _prepare_replay_prefix_candidates(previous_assistant)
    while text and candidates:
        original = text
        normalized_text: str | None = None
        end_positions: list[int] = []
        for prefix, compact_prefix, normalized_prefix in candidates:
            if text.startswith(prefix):
                text = text[len(prefix) :].lstrip()
                break
            matched = 0
            end_index = 0
            for index, char in enumerate(text):
                if char.isspace():
                    continue
                if matched >= len(compact_prefix) or char != compact_prefix[matched]:
                    break
                matched += 1
                end_index = index + 1
                if matched == len(compact_prefix):
                    text = text[end_index:].lstrip()
                    break
            if text != original:
                break
            if normalized_text is None:
                normalized_text, end_positions = _semantic_chars_with_end_positions(text)
            if normalized_prefix and normalized_text.startswith(normalized_prefix):
                end_index = end_positions[len(normalized_prefix) - 1]
                text = _trim_replay_boundary(text[end_index:])
                break
        if text == original:
            break
    if suppress_partial_replay and not text.strip() and str(content or "").strip():
        return ""
    if (
        not suppress_partial_replay
        and not text.strip()
        and original_text.strip()
        and len(original_text) <= 160
    ):
        return original_text
    return text


def _compact_chat_text(content: object) -> str:
    return "".join(str(content or "").split())


def _strip_leading_assistant_label(content: str) -> str:
    return _ASSISTANT_TURN_LABEL_RE.sub("", str(content or ""), count=1).lstrip()


def _looks_like_labeled_transcript_replay(content: str) -> bool:
    text = str(content or "").lstrip()
    if not text:
        return False
    if _USER_TURN_LABEL_RE.match(text):
        return True
    return bool(_USER_TURN_LABEL_RE.search(text) and _ASSISTANT_TURN_LABEL_RE.search(text))


def _strip_replayed_turn_transcript(
    content: str,
    current_prompt: object,
    *,
    suppress_partial_replay: bool = False,
) -> str:
    """Remove a replayed labeled transcript while keeping normal short replies intact."""
    text = str(content or "")
    prompt = str(current_prompt or "").strip()
    if not text or not prompt:
        return text

    compact_prompt = _compact_chat_text(prompt)
    best_end = -1
    for match in _USER_TURN_LABEL_RE.finditer(text):
        start = match.end()
        line_end = text.find("\n", start)
        if line_end < 0:
            line_end = len(text)
        line = text[start:line_end]

        prompt_index = line.rfind(prompt)
        if prompt_index >= 0:
            best_end = max(best_end, start + prompt_index + len(prompt))
            continue

        if len(compact_prompt) >= 4 and compact_prompt in _compact_chat_text(line):
            best_end = max(best_end, line_end)

    if best_end < 0:
        if suppress_partial_replay and _looks_like_labeled_transcript_replay(text):
            return ""
        return text
    remainder = _strip_leading_assistant_label(text[best_end:])
    if suppress_partial_replay and not remainder.strip():
        return ""
    return remainder


def _strip_replayed_chat_response(
    content: str,
    previous_assistant: object,
    current_prompt: object,
    *,
    suppress_partial_replay: bool = False,
    prepared_candidates: tuple[_PreparedReplayPrefix, ...] | None = None,
) -> str:
    text = _strip_replayed_turn_transcript(
        content,
        current_prompt,
        suppress_partial_replay=suppress_partial_replay,
    )
    return _strip_replayed_assistant_prefix(
        text,
        previous_assistant,
        suppress_partial_replay=suppress_partial_replay,
        prepared_candidates=prepared_candidates,
    )


def _json_loads_with_trailing_repair(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty ui-spec")
    first_object = text.find("{")
    first_array = text.find("[")
    starts = [index for index in (first_object, first_array) if index >= 0]
    if not starts:
        raise ValueError("ui-spec does not contain JSON")
    start = min(starts)
    text = text[start:].strip()

    candidates = [text]
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in {"}", "]"} and stack and stack[-1] == char:
            stack.pop()
    if 0 < len(stack) <= 4:
        candidates.append(text + "".join(reversed(stack)))

    last_object = text.rfind("}")
    last_array = text.rfind("]")
    end = max(last_object, last_array)
    if end >= 0:
        candidates.append(text[: end + 1])

    errors: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
    raise ValueError("; ".join(errors) or "invalid ui-spec JSON")


def _canonicalize_ui_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("ui-spec root must be an object")
    spec = dict(value)
    spec_type = spec.get("type")
    root = spec.get("root")
    elements = spec.get("elements")
    if not isinstance(spec_type, str) or not spec_type.strip():
        raise ValueError("ui-spec.type is required")
    if not isinstance(root, str) or not root.strip():
        raise ValueError("ui-spec.root is required")
    if not isinstance(elements, dict) or not elements:
        raise ValueError("ui-spec.elements is required")
    if root not in elements:
        raise ValueError("ui-spec.root must point to an element")

    canonical_elements: dict[str, Any] = {}
    for key, element in elements.items():
        if not isinstance(key, str) or not key:
            raise ValueError("ui-spec element keys must be strings")
        if not isinstance(element, dict):
            raise ValueError(f"ui-spec element {key} must be an object")
        element_type = element.get("type")
        if not isinstance(element_type, str) or not element_type.strip():
            raise ValueError(f"ui-spec element {key}.type is required")
        props = element.get("props")
        children = element.get("children")
        if props is None:
            props = {}
        if children is None:
            children = []
        if not isinstance(props, dict):
            raise ValueError(f"ui-spec element {key}.props must be an object")
        if not isinstance(children, list) or not all(isinstance(child, str) for child in children):
            raise ValueError(f"ui-spec element {key}.children must be a string array")
        normalized_props = dict(props)
        legacy_text = normalized_props.get("children")
        if isinstance(legacy_text, str):
            if element_type in {"Text", "Heading"} and "content" not in normalized_props:
                normalized_props["content"] = legacy_text
                normalized_props.pop("children", None)
            elif element_type == "Badge" and "label" not in normalized_props:
                normalized_props["label"] = legacy_text
                normalized_props.pop("children", None)

        if element_type == "Stack" and "direction" not in normalized_props:
            if normalized_props.get("row") is True:
                normalized_props["direction"] = "row"
            elif normalized_props.get("row") is False:
                normalized_props["direction"] = "column"

        canonical_elements[key] = {
            **element,
            "type": element_type,
            "props": normalized_props,
            "children": children,
        }

    reachable: set[str] = set()
    pending = [root]
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        element = canonical_elements.get(key)
        if element is None:
            raise ValueError(f"ui-spec references missing child {key}")
        reachable.add(key)
        pending.extend(element["children"])

    spec["type"] = spec_type
    spec["root"] = root
    spec["elements"] = canonical_elements
    return spec


def _log_json_render_error(error: ValueError, body: str) -> None:
    original_body = str(body or "")
    raw_body = original_body
    max_chars = 12000
    if len(raw_body) > max_chars:
        raw_body = f"{raw_body[:max_chars]}\n...[truncated {len(original_body) - max_chars} chars]"
    entry = (
        f"\n--- {_now_iso()} ---\n"
        f"error: {error}\n"
        "body:\n"
        f"{raw_body}\n"
    )
    try:
        _append_bounded_log(
            _json_render_error_log_path(),
            entry,
            _JSON_RENDER_ERROR_LOG_MAX_BYTES,
        )
    except OSError:
        return


def _append_bounded_log(path: Path, entry: str, max_bytes: int) -> None:
    """Append *entry* to *path*, then keep only the newest *max_bytes*.

    The retained tail starts at an entry boundary, so the file never opens in the
    middle of a message that someone is reading.  Truncation is by encoded length,
    because ``max_bytes`` is what the disk sees.

    Callers treat every failure as "diagnostics unavailable": the atomic replace
    can lose the race against another process still holding the old file open on
    Windows, and that must not turn a format error into a request failure.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    if path.stat().st_size <= max_bytes:
        return

    encoded = path.read_bytes()
    tail = encoded[-max_bytes:].decode("utf-8", errors="replace")
    boundary = tail.find("\n--- ")
    if boundary >= 0:
        tail = tail[boundary + 1 :]

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(tail, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _normalize_single_ui_spec_block(body: str) -> str:
    nested_start = body.lower().rfind("<ui-spec")
    if nested_start >= 0:
        close_index = body.lower().find("</ui-spec>", nested_start)
        if close_index >= 0:
            nested_block = body[nested_start : close_index + len("</ui-spec>")]
            return _normalize_json_render_reply(nested_block)

    try:
        value = _json_loads_with_trailing_repair(body)
        if isinstance(value, list):
            specs = [_canonicalize_ui_spec(item) for item in value]
            return _wrap_ui_spec_bundle(specs)
        spec = _canonicalize_ui_spec(value)
    except ValueError as exc:
        _log_json_render_error(exc, body)
        return "（json-render 格式校验失败：模型返回的 ui-spec 不是合法 canonical JSON，已阻止展示。请重新生成。）"

    spec_type = spec.get("type") if isinstance(spec.get("type"), str) else "ui_spec"
    json_text = json.dumps(spec, ensure_ascii=False, indent=2)
    return f'<ui-spec type="{spec_type}">\n{json_text}\n</ui-spec>'


def _normalize_json_render_reply(content: str) -> str:
    text = str(content or "")
    text = _wrap_embedded_ui_spec_json(text)
    if "<ui-spec" not in text.lower():
        return text
    text = _UI_SPEC_FENCE_RE.sub(lambda match: match.group(1).strip(), text)
    return _UI_SPEC_BLOCK_RE.sub(
        lambda match: _normalize_single_ui_spec_block(match.group(1)),
        text,
    )


def _wrap_embedded_ui_spec_json(content: str) -> str:
    text = str(content or "")
    if "<ui-spec" in text.lower():
        return text
    if '"elements"' not in text or '"root"' not in text:
        return text

    decoder = json.JSONDecoder()
    index = 0
    parts: list[str] = []
    changed = False
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            parts.append(text[index:])
            break
        parts.append(text[index:start])
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            parts.append(text[start : start + 1])
            index = start + 1
            continue
        if isinstance(value, dict):
            try:
                spec = _canonicalize_ui_spec(value)
            except ValueError:
                spec = None
            if spec is not None:
                parts.append(_ui_spec_block(spec))
                index = start + end
                changed = True
                continue
        parts.append(text[start : start + end])
        index = start + end

    if not changed:
        return text
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def _redact_local_filesystem_paths(content: str) -> str:
    """Hide local developer paths before text is shown or persisted in chat."""
    text = str(content or "")
    if not text:
        return ""
    return _LOCAL_FILESYSTEM_PATH_RE.sub("[本地路径]", text)


def _strip_media_rendering_leaks(content: str) -> str:
    """Remove internal rendering/tool chatter that models sometimes echo."""
    lines: list[str] = []
    for line in str(content or "").splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped:
            lines.append(line)
            continue
        if "<ui-spec" in lower or "ui-spec" in lower or "ui_spec" in lower:
            continue
        if "json-render" in lower or "automatically rendered" in lower or "backend" in lower:
            continue
        if "village_canvas_" in lower or f"{LEGACY_PRODUCT_ID}_" in lower:
            continue
        if "按规范渲染" in stripped or "UI画廊" in stripped:
            continue
        lines.append(line)
    text = _redact_local_filesystem_paths("\n".join(lines).strip())
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_embedded_ui_spec_json_text(content: str) -> str:
    """Remove model-written media JSON from prose before appending tool specs."""
    text = str(content or "")
    pattern = re.compile(
        r'\{\s*"type"\s*:\s*"(?:character_showcase|sketch_gallery|keyframe_video|audio_list|media_bundle)"'
    )
    index = 0
    parts: list[str] = []
    decoder = json.JSONDecoder()
    changed = False

    while True:
        match = pattern.search(text, index)
        if not match:
            parts.append(text[index:])
            break
        start = match.start()
        parts.append(text[index:start])
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            next_paragraph = text.find("\n\n", start)
            index = len(text) if next_paragraph < 0 else next_paragraph
            changed = True
            continue
        if isinstance(value, dict):
            try:
                _canonicalize_ui_spec(value)
                index = start + end
                changed = True
                continue
            except ValueError:
                pass
        parts.append(text[start : start + end])
        index = start + end

    if not changed:
        return text.strip()
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def _extract_tool_ui_specs(value: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def append_spec(node: Any) -> None:
        try:
            specs.append(_canonicalize_ui_spec(node))
        except ValueError as exc:
            _log_json_render_error(exc, json.dumps(node, ensure_ascii=False, default=str))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            ui_spec = node.get("ui_spec")
            if isinstance(ui_spec, dict):
                append_spec(ui_spec)
            elif {"type", "root", "elements"}.issubset(node):
                append_spec(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            text = node.strip()
            if not text or len(text) > 1_000_000:
                return
            if "<ui-spec" in text.casefold():
                _, embedded_specs = _split_ui_specs_from_text(text)
                specs.extend(embedded_specs)
                return
            if "ui_spec" not in text and not {"type", "root", "elements"}.issubset(set(re.findall(r'"([^"]+)"\s*:', text))):
                return
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return
            visit(decoded)

    visit(value)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in specs:
        key = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


class _VillageScopeMismatchError(RuntimeError):
    """Internal sentinel for a poisoned project-scoped Agent session."""


def _has_agent_scope_mismatch(value: Any) -> bool:
    """Return true when a tool payload reports a stale project scope."""
    if isinstance(value, str):
        return "agent session scope mismatch" in value.casefold()
    if isinstance(value, dict):
        return any(_has_agent_scope_mismatch(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_agent_scope_mismatch(item) for item in value)
    return False


def _extract_tool_chat_error(value: Any) -> str | None:
    def normalize_error_text(text: object) -> str:
        raw = redact_secrets(str(text or "")).strip()
        raw = re.sub(r"\s+", " ", raw)
        raw = re.sub(r"provider_response_id[\"']?\s*[:=]\s*[\"']?[^\"'\s,;}]+", "provider_response_id=[redacted]", raw, flags=re.IGNORECASE)
        raw = re.sub(r"response_id[\"']?\s*[:=]\s*[\"']?[^\"'\s,;}]+", "response_id=[redacted]", raw, flags=re.IGNORECASE)
        if len(raw) > 1200:
            raw = raw[:1200].rstrip() + "..."
        return raw

    def business_chat_error_from_text(text: object) -> str | None:
        raw = normalize_error_text(text)
        if not raw:
            return None
        if "Render 模式需要草图" in raw or "未生成可用图片" in raw:
            return (
                "Render 任务没有生成可用图片：当前缺少必要草图前置。"
                "请先在「剧集分镜」生成或确认对应 Beat 的草图后，再重新生成 Render。"
                f"\n\n错误原因：{raw[:1200]}"
            )
        return None

    def generic_chat_error_from_text(text: object) -> str | None:
        raw = normalize_error_text(text)
        if not raw:
            return None
        lowered = raw.casefold()
        if "provider_response_id" in lowered and "content_filter" in lowered:
            return None
        return f"任务执行失败：{raw}"

    def parse_jsonish(text: str) -> Any | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        try:
            return _json_loads_with_trailing_repair(raw)
        except ValueError:
            return None

    def visit(node: Any) -> str | None:
        if isinstance(node, str):
            decoded = parse_jsonish(node)
            if decoded is not None:
                return visit(decoded)
            return None
        if isinstance(node, list):
            for child in node:
                found = visit(child)
                if found:
                    return found
            return None
        if not isinstance(node, dict):
            return None

        # These are expected ActionRouter control states. Director
        # clarification is rendered from its structured receipt, while
        # discuss/plan mode must leave the Agent's normal answer untouched.
        if str(node.get("error_code") or "").strip() in {
            "director_clarification_required",
            "execution_not_authorized",
        }:
            return None

        chat_error = node.get("chat_error")
        if isinstance(chat_error, str) and chat_error.strip():
            return chat_error.strip()

        for key in ("error", "detail", "message"):
            mapped = business_chat_error_from_text(node.get(key))
            if mapped:
                return mapped

        status = str(node.get("status") or "").strip().lower()
        failed_status = status in {"failed", "error", "cancelled", "canceled"}
        ok_false = node.get("ok") is False
        if failed_status or ok_false:
            for key in ("error", "detail", "message"):
                generic = generic_chat_error_from_text(node.get(key))
                if generic:
                    return generic
            # A nested status alone is not evidence that the user-requested
            # task failed.  Tool envelopes frequently use ``failed`` for an
            # optional analysis step (or an abandoned internal poll) while a
            # canvas command, generation proposal, or already-created asset
            # succeeded.  The previous synthetic message was injected into
            # the visible chat reply and repeatedly misreported success as a
            # task failure.  Surface concrete errors above; otherwise let the
            # Agent continue from its real receipts without UI noise.
            return None

        for key in ("result", "message", "content", "data", "output"):
            found = visit(node.get(key))
            if found:
                return found
        for child in node.values():
            found = visit(child)
            if found:
                return found
        return None

    return visit(value)


def _ui_spec_json(spec: dict[str, Any]) -> tuple[str, str]:
    canonical = _canonicalize_ui_spec(spec)
    spec_type = canonical.get("type") if isinstance(canonical.get("type"), str) else "ui_spec"
    return spec_type, json.dumps(canonical, ensure_ascii=False, indent=2)


def _wrap_ui_spec_json(spec_type: str, json_text: str) -> str:
    return (
        f'<ui-spec type="{spec_type}">\n'
        f"{json_text}\n"
        "</ui-spec>"
    )


def _wrap_ui_spec_bundle(specs: list[dict[str, Any]]) -> str:
    canonical_specs = [_canonicalize_ui_spec(spec) for spec in specs]
    if len(canonical_specs) == 1:
        spec_type = canonical_specs[0].get("type")
        return _wrap_ui_spec_json(
            spec_type if isinstance(spec_type, str) and spec_type else "ui_spec",
            json.dumps(canonical_specs[0], ensure_ascii=False, indent=2),
        )
    return _wrap_ui_spec_json(
        "media_bundle",
        json.dumps(canonical_specs, ensure_ascii=False, indent=2),
    )


def _ui_spec_block(spec: dict[str, Any]) -> str:
    spec_type, json_text = _ui_spec_json(spec)
    return _wrap_ui_spec_json(spec_type, json_text)


_MERGEABLE_MEDIA_SPEC_TYPES = {
    "character_showcase",
    "sketch_gallery",
    "keyframe_video",
    "audio_list",
}


def _can_merge_ui_specs(left: dict[str, Any], right: dict[str, Any]) -> bool:
    spec_type = left.get("type")
    if spec_type != right.get("type") or spec_type not in _MERGEABLE_MEDIA_SPEC_TYPES:
        return False
    left_elements = left.get("elements")
    right_elements = right.get("elements")
    left_root_id = left.get("root")
    right_root_id = right.get("root")
    if not (
        isinstance(left_elements, dict)
        and isinstance(right_elements, dict)
        and isinstance(left_root_id, str)
        and isinstance(right_root_id, str)
    ):
        return False
    left_root = left_elements.get(left_root_id)
    right_root = right_elements.get(right_root_id)
    if not isinstance(left_root, dict) or not isinstance(right_root, dict):
        return False
    return left_root.get("type") == right_root.get("type") == "Stack"


def _merge_ui_specs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left = _canonicalize_ui_spec(left)
    right = _canonicalize_ui_spec(right)
    left_elements = dict(left["elements"])
    right_elements = right["elements"]
    left_root_id = left["root"]
    right_root_id = right["root"]
    left_root = dict(left_elements[left_root_id])
    right_root = right_elements[right_root_id]
    left_children = list(left_root.get("children") or [])
    right_children = list(right_root.get("children") or [])

    def unique_key(key: str) -> str:
        if key not in left_elements:
            return key
        index = 2
        while f"{key}_{index}" in left_elements:
            index += 1
        return f"{key}_{index}"

    key_map: dict[str, str] = {}
    for key, element in right_elements.items():
        if key == right_root_id:
            continue
        next_key = unique_key(key)
        key_map[key] = next_key
        left_elements[next_key] = element

    left_root["children"] = [
        *left_children,
        *[key_map.get(child, child) for child in right_children if isinstance(child, str)],
    ]
    left_elements[left_root_id] = left_root
    return {**left, "elements": left_elements}


def _merge_tool_ui_specs_by_type(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    merge_indexes: dict[str, int] = {}
    for spec in specs:
        spec_type = spec.get("type")
        merge_index = merge_indexes.get(spec_type) if isinstance(spec_type, str) else None
        if merge_index is not None and _can_merge_ui_specs(merged[merge_index], spec):
            try:
                merged[merge_index] = _merge_ui_specs(merged[merge_index], spec)
                continue
            except ValueError as exc:
                _log_json_render_error(exc, json.dumps(spec, ensure_ascii=False))
        merged.append(spec)
        if isinstance(spec_type, str) and spec_type in _MERGEABLE_MEDIA_SPEC_TYPES:
            merge_indexes.setdefault(spec_type, len(merged) - 1)
    return merged


def _append_tool_ui_specs(content: str, specs: list[dict[str, Any]]) -> str:
    raw_text = str(content or "").strip()
    if specs and _UI_SPEC_BLOCK_RE.search(raw_text):
        return raw_text
    text = _strip_media_rendering_leaks(raw_text)
    if not specs:
        return text
    text = _strip_embedded_ui_spec_json_text(text)
    specs = _merge_tool_ui_specs_by_type(specs)
    blocks: list[str] = []
    for spec in specs:
        try:
            blocks.append(_ui_spec_block(spec))
        except ValueError as exc:
            _log_json_render_error(exc, json.dumps(spec, ensure_ascii=False))
    if not blocks:
        return text
    prefix = text or "已为你展示相关媒体。"
    return f"{prefix}\n\n" + "\n\n".join(blocks)


def _split_ui_specs_from_text(content: str) -> tuple[str, list[dict[str, Any]]]:
    text = str(content or "")
    if "<ui-spec" not in text.lower():
        return text, []

    text = _UI_SPEC_FENCE_RE.sub(lambda match: match.group(1).strip(), text)
    specs: list[dict[str, Any]] = []

    def replace_block(match: re.Match[str]) -> str:
        body = match.group(1)
        try:
            value = _json_loads_with_trailing_repair(body)
            if isinstance(value, list):
                specs.extend(_canonicalize_ui_spec(item) for item in value)
            else:
                specs.append(_canonicalize_ui_spec(value))
        except ValueError as exc:
            _log_json_render_error(exc, body)
            return "（json-render 格式校验失败：模型返回的 ui-spec 不是合法 canonical JSON，已阻止展示。请重新生成。）"
        return ""

    display_text = _UI_SPEC_BLOCK_RE.sub(replace_block, text)
    display_text = re.sub(r"\n{3,}", "\n\n", display_text).strip()
    return display_text, specs


def _dedupe_tool_ui_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in specs:
        key = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def _prompt_wants_sketch_only(prompt: str) -> bool:
    text = str(prompt or "")
    if "草图" not in text and "sketch" not in text.casefold():
        return False
    frame_terms = ("首帧", "第一帧", "关键帧", "first frame", "first-frame", "keyframe", "frame")
    return not any(term in text.casefold() for term in frame_terms)


def _is_frame_image_element(element: Any) -> bool:
    if not isinstance(element, dict):
        return False
    props = element.get("props")
    if not isinstance(props, dict):
        return False
    fields = [
        props.get("src"),
        props.get("poster"),
        props.get("title"),
        props.get("alt"),
        props.get("description"),
        props.get("overlayTitle"),
        props.get("overlayDescription"),
    ]
    text = "\n".join(str(value or "") for value in fields).casefold()
    return "首帧" in text or "/frames/" in text or "first frame" in text or "first-frame" in text


def _filter_tool_ui_specs_for_prompt(prompt: str, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not specs or not _prompt_wants_sketch_only(prompt):
        return specs

    filtered_specs: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("type") != "sketch_gallery":
            filtered_specs.append(spec)
            continue
        elements = spec.get("elements")
        root_key = spec.get("root")
        if not isinstance(elements, dict) or not isinstance(root_key, str):
            filtered_specs.append(spec)
            continue
        root = elements.get(root_key)
        if not isinstance(root, dict):
            filtered_specs.append(spec)
            continue
        children = root.get("children")
        if not isinstance(children, list):
            filtered_specs.append(spec)
            continue

        kept_children: list[str] = []
        kept_elements: dict[str, Any] = {}
        for key, element in elements.items():
            if key == root_key:
                continue
            if key in children and _is_frame_image_element(element):
                continue
            kept_elements[key] = element
            if key in children:
                kept_children.append(key)

        if not kept_children:
            continue
        new_root = copy.deepcopy(root)
        new_root["children"] = kept_children
        filtered_specs.append(
            {
                **spec,
                "elements": {
                    root_key: new_root,
                    **{key: kept_elements[key] for key in kept_elements},
                },
            }
        )
    return filtered_specs


_DISPLAY_TOOL_NAMES = {
    "village_canvas_get_sketches",
    "village_canvas_get_sketch_candidates",
    "village_canvas_get_first_frames",
    "village_canvas_get_scene_images",
    "village_canvas_get_character_media",
    "village_canvas_get_episode_media",
}


def _limit_display_items(items: list[dict[str, Any]], args: dict[str, Any], default: int) -> list[dict[str, Any]]:
    try:
        limit = int(args.get("limit")) if args.get("limit") is not None else default
    except (TypeError, ValueError):
        limit = default
    try:
        offset = int(args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    limit = max(1, min(limit, default))
    return items[offset : offset + limit]


def _requested_display_beats(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("beat_indices") or args.get("beats")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("beat", "beat_num", "beat_number", "index"):
        if args.get(key) is not None:
            values.append(args[key])
    beats: set[int] = set()
    for value in values:
        try:
            beat = int(value)
        except (TypeError, ValueError):
            continue
        if beat > 0:
            beats.add(beat)
    return beats or None


def _requested_display_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "character"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_display_queries(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("queries") or args.get("keywords")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("query", "search", "keyword", "text", "identity_name"):
        if args.get(key) is not None:
            values.append(args[key])
    queries = {str(value).strip() for value in values if str(value or "").strip()}
    return queries or None


def _requested_display_scene_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names") or args.get("scene_names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "scene_name"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_display_scene_indices(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("scene_indices") or args.get("indices")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    if args.get("index") is not None:
        values.append(args["index"])
    indices: set[int] = set()
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index > 0:
            indices.add(index)
    return indices or None


def _matches_any_display_scene_name(scene_name: str, requested_names: set[str] | None) -> bool:
    if requested_names is None:
        return True
    haystack = str(scene_name or "").casefold()
    return any(needle.casefold() in haystack for needle in requested_names if needle)


def _flatten_display_text_fields(fields: list[Any]) -> list[str]:
    values: list[str] = []
    for field in fields:
        if isinstance(field, dict):
            values.extend(_flatten_display_text_fields(list(field.values())))
        elif isinstance(field, list):
            values.extend(_flatten_display_text_fields(field))
        elif field is not None:
            text = str(field).strip()
            if text:
                values.append(text)
    return values


def _matches_any_display_text(fields: list[Any], queries: set[str] | None) -> bool:
    if queries is None:
        return True
    haystack = "\n".join(_flatten_display_text_fields(fields)).casefold()
    return any(query.casefold() in haystack for query in queries if query)


def _media_ui_spec(spec_type: str, component_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    elements: dict[str, Any] = {
        "root": {
            "type": "Stack",
            "props": {
                "direction": "row",
                "wrap": "wrap",
                "spacing": 16,
                "alignItems": "flex-start",
                "width": "100%",
            },
            "children": [],
        }
    }
    for index, item in enumerate(items, start=1):
        src = str(item.get("src") or item.get("url") or "").strip()
        if not src:
            continue
        key = f"media_{index}"
        title = str(item.get("title") or item.get("label") or f"媒体 {index}").strip()
        description = str(item.get("description") or "").strip()
        props: dict[str, Any] = {"src": src, "alt": title, "title": title}
        if description:
            props["description"] = description
        if component_type == "Image":
            props.update(
                {
                    "fit": item.get("fit") or "cover",
                    "aspectRatio": item.get("aspectRatio") or "3/4",
                    "overlayTitle": title,
                }
            )
            if description:
                props["overlayDescription"] = description
        elif component_type == "Video":
            poster = str(item.get("poster") or item.get("thumbnail") or "").strip()
            if poster:
                props["poster"] = poster
            props["controls"] = True
        elif component_type == "Audio":
            props["controls"] = True

        elements[key] = {"type": component_type, "props": props, "children": []}
        elements["root"]["children"].append(key)
    return {"type": spec_type, "root": "root", "elements": elements}


def _project_static_url_from_path(project_id: str, rel_path: str, local_path: Path | None = None) -> str:
    return project_static_url(project_id, rel_path, local_path=local_path)


def _api_response_items(resp: Any, *keys: str) -> list[Any]:
    if not isinstance(resp, dict):
        return []
    for key in keys:
        value = resp.get(key)
        if isinstance(value, list):
            return value
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _decode_tool_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _extract_display_tool_call(raw: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or raw.get("kind") or raw.get("name") or raw.get("tool_name") or "").strip()
    title_head = title.partition(":")[0].split()
    tool_name = normalize_tool_name(title_head[0].strip()) if title_head else ""
    if tool_name not in _DISPLAY_TOOL_NAMES:
        for key in ("name", "tool", "toolName", "tool_name"):
            candidate = normalize_tool_name(raw.get(key))
            if candidate in _DISPLAY_TOOL_NAMES:
                tool_name = candidate
                break
    if tool_name not in _DISPLAY_TOOL_NAMES:
        function = raw.get("function")
        if isinstance(function, dict):
            candidate = normalize_tool_name(function.get("name"))
            if candidate in _DISPLAY_TOOL_NAMES:
                tool_name = candidate
    if tool_name not in _DISPLAY_TOOL_NAMES:
        return None
    for key in ("arguments", "args", "input", "params"):
        args = _decode_tool_args(raw.get(key))
        if args:
            return tool_name, args
    content = raw.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            nested = item.get("content")
            if isinstance(nested, dict):
                args = _decode_tool_args(nested.get("text"))
                if args:
                    return tool_name, args
    return tool_name, {}


def _display_tool_call_key(tool_name: str, args: dict[str, Any]) -> str:
    try:
        encoded_args = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        encoded_args = repr(args)
    return f"{tool_name}:{encoded_args}"


def _infer_display_tool_call_from_text(
    prompt: str,
    assistant_text: str,
    previous_assistant: list[str],
) -> tuple[str, dict[str, Any]] | None:
    """Recover from display promises where the model forgot to call a display tool."""
    prompt_text = str(prompt or "")
    prompt_lower = prompt_text.casefold()
    recent_context = "\n".join(previous_assistant[-2:] if previous_assistant else [])
    context_text = "\n".join([prompt_text, str(assistant_text or ""), recent_context])
    context_lower = context_text.casefold()
    progress_terms = ("进度", "状态", "任务", "做到哪", "做到哪儿", "当前情况")
    if any(term in prompt_text for term in progress_terms):
        return None
    display_terms = ("展示", "显示", "查看", "看", "全部显示", "show", "display", "view")
    if not any(term in prompt_lower for term in display_terms):
        return None
    prompt_mentions_sketch = "草图" in prompt_text or "sketch" in prompt_lower
    context_mentions_sketch = "草图" in context_text or "sketch" in context_lower
    short_followup = len(prompt_text.strip()) <= 20 and any(
        term in prompt_text for term in ("全部", "继续", "下一页", "更多")
    )
    if not prompt_mentions_sketch and not (short_followup and context_mentions_sketch):
        return None

    episode = 1
    episode_match = re.search(
        r"(?:第\s*(\d+)\s*集|ep(?:isode)?\s*\.?\s*(\d+))",
        context_text,
        re.IGNORECASE,
    )
    if episode_match:
        raw_episode = episode_match.group(1) or episode_match.group(2)
        try:
            episode = max(1, int(raw_episode))
        except (TypeError, ValueError):
            episode = 1
    wants_sketch_candidates = any(term in context_text for term in ("草图候选", "候选草图", "图池", "备选草图"))
    if wants_sketch_candidates:
        beat_match = re.search(
            r"(?:beat|Beat|BEAT)\s*\.?\s*(\d+)|第\s*(\d+)\s*(?:个|张)?\s*beat|Beat\s*(\d+)",
            context_text,
            re.IGNORECASE,
        )
        raw_beat = None
        if beat_match:
            raw_beat = next((group for group in beat_match.groups() if group), None)
        if raw_beat:
            try:
                beat = max(1, int(raw_beat))
            except (TypeError, ValueError):
                beat = 0
            if beat > 0:
                return "village_canvas_get_sketch_candidates", {"episode": episode, "beat": beat}
        return None
    return "village_canvas_get_sketches", {"episode": episode}


def _backend_api_get(path: str, token: str) -> dict[str, Any]:
    # Keep every backend read on the packaged API URL.  The previous fallback
    # silently used the legacy 19080 port, so preflight could read an unrelated
    # service (or HTML) and leave the model guessing current canvas facts.
    base_url = _load_api_url()
    url = f"{base_url.rstrip('/')}{path}"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "village-canvas-chat/1.0.0",
        },
        method="GET",
    )
    with urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": text[:500]}
    return value if isinstance(value, dict) else {"ok": True, "data": value}


