"""Local-first Skill Store catalog, imports, and installation lifecycle."""

from __future__ import annotations

import io
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from novelvideo.config import STATE_DIR
from novelvideo.skills_distill.admission import (
    SKILL_PROVENANCE_SCHEMA,
    scan_skill,
)
from novelvideo.skills_distill.install import (
    installed_skill_path,
    kebab,
    uninstall_skill,
)

BUNDLED_CATALOG = Path(__file__).parent / "catalog" / "libtv_skills.json"
TAPNOW_CATALOG = Path(__file__).parent / "catalog" / "tapnow_skills.json"
SKILL_STORE_ROOT = Path(STATE_DIR) / "skill_store"
CUSTOM_SKILLS_DIR = SKILL_STORE_ROOT / "custom"
SKILL_ARCHIVES_DIR = SKILL_STORE_ROOT / "archives"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_FILES = 200
MAX_SKILL_BODY_CHARS = 80_000
SKILL_CONTRACT_SCHEMA_VERSION = "canvas_skill_contract.v1"

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("分镜镜头", ("分镜", "镜头", "运镜", "机位", "拉片")),
    ("角色表演", ("角色", "人物", "表情", "表演", "选角", "casting")),
    ("广告电商", ("广告", "tvc", "品牌", "带货", "产品", "营销", "电商")),
    ("音乐声音", ("音乐", "歌曲", "mv", "声音", "字幕", "音频")),
    ("剧本故事", ("剧本", "写作", "故事", "叙事", "世界观")),
    ("风格美学", ("美学", "风格", "动画", "胶片", "复古", "写实", "艺术")),
    ("成片制作", ("短剧", "成片", "视频", "剪辑", "混剪", "导演")),
)


class SkillStoreError(ValueError):
    """Expected user-facing Skill Store validation error."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
    temp.write_bytes(payload)
    temp.replace(path)


def _plain_text(value: Any, *, limit: int = 1024) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _category_for(*values: str) -> str:
    haystack = " ".join(values).lower()
    for category, words in _CATEGORY_RULES:
        if any(word in haystack for word in words):
            return category
    return "通用创作"


def _trigger_words(*values: str) -> list[str]:
    haystack = " ".join(values).lower()
    tags: list[str] = []
    for category, words in _CATEGORY_RULES:
        if any(word in haystack for word in words):
            tags.append(category)
    return tags[:4]


def _markdown_sections(body: str) -> dict[str, str]:
    """Map common Skill headings into stable contract sections."""
    sections = {"purpose": [], "inputs": [], "workflow": [], "outputs": [], "quality": []}
    current: str | None = None
    for raw_line in body.replace("\r\n", "\n").splitlines():
        heading_match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", raw_line)
        if heading_match:
            heading = re.sub(r"^[\d.、)）\s-]+", "", heading_match.group(1)).lower()
            if any(token in heading for token in ("用途", "purpose", "overview", "目标")):
                current = "purpose"
            elif any(
                token in heading
                for token in ("适用输入", "输入", "前置条件", "前提", "precondition", "input")
            ):
                current = "inputs"
            elif any(token in heading for token in ("操作步骤", "工作流程", "步骤", "workflow", "steps", "执行流程")):
                current = "workflow"
            elif any(
                token in heading
                for token in (
                    "输出要求",
                    "输出",
                    "交付物",
                    "完成判定",
                    "退出条件",
                    "exit condition",
                    "output",
                    "deliverable",
                )
            ):
                current = "outputs"
            elif any(
                token in heading
                for token in (
                    "注意事项",
                    "质量",
                    "验收",
                    "审核清单",
                    "约束",
                    "边界",
                    "boundary",
                    "quality",
                    "constraint",
                    "notes",
                )
            ):
                current = "quality"
            else:
                current = None
            continue
        if current and raw_line.strip():
            sections[current].append(raw_line.strip())
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _contract_steps(section: str, *, limit: int = 8) -> list[str]:
    steps: list[str] = []
    for raw_line in section.splitlines():
        line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、）]\s*)", "", raw_line).strip()
        if line and line not in steps:
            steps.append(_plain_text(line, limit=360))
    if not steps and section.strip():
        steps.append(_plain_text(section, limit=360))
    return steps[:limit]


def _infer_canvas_commands(*values: str) -> list[str]:
    text = " ".join(values).lower()
    commands = ["freezone_get_canvas_snapshot", "freezone_emit_canvas_command"]
    if any(token in text for token in ("分镜", "镜头", "脚本", "故事", "shot", "storyboard")):
        commands.append("create_shot_sequence")
    if any(token in text for token in ("图片", "图像", "海报", "草图", "image", "poster")):
        commands.append("create_image_prompt_node")
    if any(token in text for token in ("视频", "动画", "mv", "video", "animation")):
        commands.append("create_video_prompt_node")
    if any(token in text for token in ("音频", "声音", "配音", "音乐", "audio", "sound")):
        commands.append("create_canvas_node(audioNode)")
    if any(token in text for token in ("提示词", "prompt", "光影", "动作", "风格", "表演")):
        commands.append("update_node_prompt")
    if len(commands) == 2:
        commands.append("create_canvas_node(textAnnotationNode)")
    return list(dict.fromkeys(commands))


def _skill_contract(
    name: str,
    description: str,
    body: str,
    *,
    input_hint: str = "",
    output_hint: str = "",
) -> dict[str, Any]:
    sections = _markdown_sections(body)
    purpose = _plain_text(sections["purpose"] or description, limit=1000)
    inputs = _plain_text(sections["inputs"] or input_hint, limit=1400)
    workflow = _contract_steps(sections["workflow"])
    output_contract = _plain_text(sections["outputs"] or output_hint, limit=1400)
    quality_gate = _contract_steps(sections["quality"], limit=6)
    canvas_commands = _infer_canvas_commands(name, description, body, inputs, output_contract)
    missing: list[str] = []
    if not inputs:
        missing.append("缺少明确输入")
    if len(workflow) < 2:
        missing.append("缺少至少两步执行流程")
    if not output_contract:
        missing.append("缺少明确输出")
    if not quality_gate:
        missing.append("缺少质量验收条件")
    score = 15
    score += 20 if inputs else 0
    score += 25 if len(workflow) >= 2 else 8 if workflow else 0
    score += 20 if output_contract else 0
    score += 10 if quality_gate else 0
    score += 10 if len(canvas_commands) > 2 else 0
    maturity = (
        "production_ready"
        if score >= 80 and not missing
        else "workflow_ready"
        if score >= 55 and workflow and len(canvas_commands) > 2
        else "reference_only"
    )
    return {
        "schema_version": SKILL_CONTRACT_SCHEMA_VERSION,
        "maturity": maturity,
        "readiness_score": score,
        "readiness_issues": missing,
        "purpose": purpose,
        "inputs": inputs,
        "workflow": workflow,
        "output_contract": output_contract,
        "quality_gate": quality_gate,
        "canvas_commands": canvas_commands,
        "completion_rule": "必须实际写入画布或产出可追踪结果，并核对 command_id、revision、applied_ops；只回复文字不算完成。",
    }


def _activation(description: str, body: str, *, extra: list[str] | None = None) -> str:
    sections = [
        description.strip(),
        *(part.strip() for part in (extra or []) if part.strip()),
    ]
    compact_body = body.strip()
    if compact_body:
        sections.append(compact_body[:2400])
    return "\n\n".join(part for part in sections if part)[:3200]


def _cover_image(value: Any) -> str | None:
    """Accept only browser-safe local or HTTPS cover references."""
    candidate = _plain_text(value, limit=512)
    if candidate.startswith("/") or candidate.startswith("https://"):
        return candidate
    return None


def _normalize_bundled(raw: dict[str, Any]) -> dict[str, Any]:
    optimized = raw.get("optimized") if isinstance(raw.get("optimized"), dict) else {}
    name = _plain_text(
        optimized.get("name")
        or raw.get("name")
        or raw.get("skillId")
        or raw.get("skillKey"),
        limit=160,
    )
    description = _plain_text(optimized.get("description") or name, limit=600)
    body = str(optimized.get("markdownContent") or "").strip()[:MAX_SKILL_BODY_CHARS]
    skill_key = (
        kebab(
            str(
                optimized.get("skillIdName")
                or optimized.get("skillKeyName")
                or raw.get("skillId")
                or raw.get("skillKey")
                or name
            )
        )
        or f"skill-{uuid.uuid4().hex[:8]}"
    )
    input_hint = _plain_text(optimized.get("inputType"), limit=600)
    output_hint = _plain_text(optimized.get("outputContent"), limit=600)
    extras = [
        f"适用场景：{_plain_text(optimized.get('useScenario'), limit=300)}"
        if optimized.get("useScenario")
        else "",
        f"输入：{input_hint}" if input_hint else "",
        f"输出：{output_hint}" if output_hint else "",
    ]
    category = _category_for(name, description, *extras)
    tags = list(dict.fromkeys([category, *_trigger_words(name, description, body)]))
    return {
        "id": f"libtv.{skill_key}",
        "skill_key": skill_key,
        "name": name,
        "description": description,
        "category": category,
        "source": "libtv_reference",
        "source_label": "LibTV 参考技能",
        "version": "1.0.0",
        "tags": tags,
        "model_hint": _plain_text(raw.get("model"), limit=120) or None,
        "cover_image": _cover_image(
            optimized.get("cover_image") or raw.get("cover_image")
        ),
        "activation": _activation(description, body, extra=extras),
        "contract": _skill_contract(
            name,
            description,
            body,
            input_hint=input_hint,
            output_hint=output_hint,
        ),
        "body": body or f"## Purpose\n\n{description}\n",
        "builtin": True,
        "removable": False,
    }


def _tapnow_items() -> list[dict[str, Any]]:
    try:
        payload = _read_json(TAPNOW_CATALOG)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        skill_key = kebab(str(raw.get("skill_key") or raw.get("id") or ""))
        name = _plain_text(raw.get("name") or skill_key, limit=160)
        body = str(raw.get("body") or "").strip()[:MAX_SKILL_BODY_CHARS]
        if not skill_key or not name or not body:
            continue
        description = _plain_text(raw.get("description") or name, limit=600)
        category = _plain_text(raw.get("category") or "通用创作", limit=80)
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            tags = [tags]
        clean_tags = list(
            dict.fromkeys(
                [category, *(_plain_text(tag, limit=80) for tag in tags if tag)]
            )
        )[:8]
        items.append(
            {
                "id": f"tapnow.{skill_key}",
                "skill_key": skill_key,
                "name": name,
                "description": description,
                "category": category,
                "source": "tapnow_reference",
                "source_label": "TapNow 机制融合",
                "version": _plain_text(raw.get("version"), limit=40) or "1.0.0",
                "tags": clean_tags,
                "model_hint": _plain_text(raw.get("model_hint"), limit=120) or None,
                "cover_image": _cover_image(raw.get("cover_image")),
                "activation": _activation(description, body),
                "contract": _skill_contract(name, description, body),
                "body": body,
                "builtin": True,
                "removable": False,
            }
        )
    return items


def _bundled_items() -> list[dict[str, Any]]:
    try:
        payload = _read_json(BUNDLED_CATALOG)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items = [_normalize_bundled(item) for item in payload if isinstance(item, dict)]
    items.extend(_tapnow_items())
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = str(item["id"])
        if item_id in unique:
            digest = hashlib.sha256(
                str(item.get("body", "")).encode("utf-8")
            ).hexdigest()[:8]
            item = {
                **item,
                "id": f"{item_id}.{digest}",
                "skill_key": f"{item['skill_key']}-{digest}",
            }
        unique[str(item["id"])] = item
    return list(unique.values())


def _custom_items() -> list[dict[str, Any]]:
    if not CUSTOM_SKILLS_DIR.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(CUSTOM_SKILLS_DIR.glob("*.json")):
        try:
            item = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("id") and item.get("skill_key"):
            items.append(item)
    return items


def _with_install_state(item: dict[str, Any], *, include_body: bool) -> dict[str, Any]:
    result = dict(item)
    if not isinstance(result.get("contract"), dict):
        result["contract"] = _skill_contract(
            str(result.get("name") or result.get("skill_key") or "技能"),
            str(result.get("description") or ""),
            str(result.get("body") or result.get("activation") or ""),
        )
    result["installed"] = installed_skill_path(str(item["skill_key"])).is_file()
    result["enabled"] = result["installed"]
    # Recompute the enablement report from the current package, while keeping
    # import-time provenance and duplicate findings deterministic.
    result["admission"] = scan_skill(result, phase="install")
    if not include_body:
        result.pop("body", None)
    return result


def list_store_items(
    *,
    query: str = "",
    source: str = "",
    category: str = "",
    installed: bool | None = None,
    include_body: bool = False,
) -> list[dict[str, Any]]:
    items = [*_bundled_items(), *_custom_items()]
    needle = query.strip().lower()
    source_filter = source.strip().lower()
    category_filter = category.strip().lower()
    results: list[dict[str, Any]] = []
    for raw in items:
        item = _with_install_state(raw, include_body=include_body)
        if source_filter and str(item.get("source", "")).lower() != source_filter:
            continue
        if category_filter and str(item.get("category", "")).lower() != category_filter:
            continue
        if installed is not None and bool(item["installed"]) is not installed:
            continue
        if needle:
            searchable = " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("description", "")),
                    str(item.get("skill_key", "")),
                    str(item.get("category", "")),
                    " ".join(str(tag) for tag in item.get("tags", [])),
                ]
            ).lower()
            if needle not in searchable:
                continue
        results.append(item)
    return sorted(
        results,
        key=lambda item: (
            not bool(item.get("installed")),
            str(item.get("category", "")),
            str(item.get("name", "")),
        ),
    )


def get_store_item(skill_id: str, *, include_body: bool = True) -> dict[str, Any]:
    normalized_id = skill_id.strip()
    for item in [*_bundled_items(), *_custom_items()]:
        if item.get("id") == normalized_id:
            return _with_install_state(item, include_body=include_body)
    raise KeyError(normalized_id)


def install_store_item(skill_id: str) -> dict[str, Any]:
    item = get_store_item(skill_id, include_body=True)
    existing = [
        other
        for other in [*_bundled_items(), *_custom_items()]
        if other.get("id") != item.get("id")
    ]
    admission = scan_skill(item, existing_items=existing, phase="install")
    if not admission["can_install"]:
        messages = [
            str(issue.get("message") or issue.get("code"))
            for issue in admission.get("issues") or []
            if issue.get("severity") == "error"
        ]
        raise SkillStoreError("技能未通过启用检查：" + "；".join(messages[:4]))
    path = _install_skill_atomically(item)
    result = get_store_item(skill_id, include_body=False)
    result["installed_path"] = str(path)
    result["admission"] = admission
    return result


def uninstall_store_item(skill_id: str) -> dict[str, Any]:
    item = get_store_item(skill_id, include_body=False)
    uninstall_skill(str(item["skill_key"]))
    return get_store_item(skill_id, include_body=False)


def delete_custom_item(skill_id: str) -> bool:
    item = get_store_item(skill_id, include_body=False)
    if item.get("source") != "custom":
        raise SkillStoreError("内置技能不能删除，可以停用")
    uninstall_skill(str(item["skill_key"]))
    path = CUSTOM_SKILLS_DIR / f"{item['id'].removeprefix('custom.')}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def _parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    text = markdown.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {}, text.strip()
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text.strip()
    header = text[4:end]
    body = text[end + 4 :].lstrip("\r\n")
    metadata: dict[str, Any] = {}
    triggers: list[str] = []
    in_triggers = False
    for raw_line in header.splitlines():
        line = raw_line.rstrip()
        if in_triggers and re.match(r"^\s*-\s+", line):
            triggers.append(re.sub(r"^\s*-\s+", "", line).strip().strip("\"'"))
            continue
        in_triggers = False
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key == "triggers":
            in_triggers = True
            if value:
                triggers.extend(
                    part.strip()
                    for part in value.strip("[]").split(",")
                    if part.strip()
                )
        elif key in {
            "name",
            "description",
            "category",
            "version",
            "cover_image",
            "source",
            "source_commit",
            "license",
        }:
            metadata[key] = value
    if triggers:
        metadata["triggers"] = triggers
    return metadata, body.strip()


def _custom_item(
    *,
    name: str,
    description: str,
    body: str,
    skill_key: str = "",
    category: str = "",
    tags: list[str] | None = None,
    version: str = "1.0.0",
    cover_image: str | None = None,
    source_ref: str = "",
    source_commit: str = "",
    license_name: str = "",
) -> dict[str, Any]:
    clean_name = _plain_text(name, limit=160)
    clean_body = body.strip()[:MAX_SKILL_BODY_CHARS]
    if not clean_name:
        raise SkillStoreError("技能名称不能为空")
    if not clean_body:
        raise SkillStoreError(f"技能 {clean_name} 没有可执行正文")
    key = kebab(skill_key or clean_name)
    if not key:
        raise SkillStoreError(f"技能 {clean_name} 缺少有效 ID")
    clean_description = _plain_text(description or clean_name, limit=600)
    clean_category = _plain_text(category, limit=80) or _category_for(
        clean_name, clean_description, clean_body
    )
    clean_tags = list(
        dict.fromkeys(
            [
                clean_category,
                *(
                    _plain_text(tag, limit=80)
                    for tag in (tags or [])
                    if _plain_text(tag, limit=80)
                ),
                *_trigger_words(clean_name, clean_description, clean_body),
            ]
        )
    )[:8]
    declared_source = _plain_text(source_ref, limit=800)
    source_kind = "external_source" if declared_source else "user_authored"
    source_manifest = {
        "schema": SKILL_PROVENANCE_SCHEMA,
        "source_kind": source_kind,
        "source_ref": declared_source,
        "source_commit": _plain_text(source_commit, limit=120),
        "license": _plain_text(license_name, limit=120)
        or ("User-provided" if source_kind == "user_authored" else ""),
        "content_sha256": hashlib.sha256(clean_body.encode("utf-8")).hexdigest(),
    }
    return {
        "id": f"custom.{key}",
        "skill_key": key,
        "name": clean_name,
        "description": clean_description,
        "category": clean_category,
        "source": "custom",
        "source_label": "我的技能",
        "version": _plain_text(version, limit=40) or "1.0.0",
        "tags": clean_tags,
        "model_hint": None,
        "cover_image": _cover_image(cover_image),
        "activation": _activation(clean_description, clean_body),
        "contract": _skill_contract(clean_name, clean_description, clean_body),
        "body": clean_body,
        "builtin": False,
        "removable": True,
        "source_manifest": source_manifest,
    }


def _items_from_markdown(text: str, *, fallback_name: str) -> list[dict[str, Any]]:
    metadata, body = _parse_frontmatter(text)
    return [
        _custom_item(
            name=str(metadata.get("name") or fallback_name),
            description=str(metadata.get("description") or fallback_name),
            body=body,
            category=str(metadata.get("category") or ""),
            tags=[str(value) for value in metadata.get("triggers", [])],
            version=str(metadata.get("version") or "1.0.0"),
            cover_image=str(metadata.get("cover_image") or ""),
            source_ref=str(metadata.get("source") or ""),
            source_commit=str(metadata.get("source_commit") or ""),
            license_name=str(metadata.get("license") or ""),
        )
    ]


def _items_from_json(payload: Any, *, fallback_name: str) -> list[dict[str, Any]]:
    records = payload if isinstance(payload, list) else [payload]
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise SkillStoreError("JSON 技能必须是对象或对象数组")
        optimized = (
            raw.get("optimized") if isinstance(raw.get("optimized"), dict) else {}
        )
        name = (
            optimized.get("name")
            or raw.get("name")
            or raw.get("title")
            or f"{fallback_name}-{index + 1}"
        )
        body = (
            optimized.get("markdownContent")
            or raw.get("markdownContent")
            or raw.get("body")
            or raw.get("content")
            or raw.get("instructions")
        )
        tags = raw.get("triggers") or raw.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        items.append(
            _custom_item(
                name=str(name),
                description=str(
                    optimized.get("description") or raw.get("description") or name
                ),
                body=str(body or ""),
                skill_key=str(
                    optimized.get("skillIdName")
                    or optimized.get("skillKeyName")
                    or raw.get("skillId")
                    or raw.get("skillKey")
                    or raw.get("id")
                    or ""
                ),
                category=str(raw.get("category") or ""),
                tags=[str(tag) for tag in tags],
                version=str(raw.get("version") or "1.0.0"),
                cover_image=str(
                    optimized.get("cover_image") or raw.get("cover_image") or ""
                ),
                source_ref=str(raw.get("source") or raw.get("source_url") or ""),
                source_commit=str(raw.get("source_commit") or raw.get("commit") or ""),
                license_name=str(raw.get("license") or ""),
            )
        )
    return items


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SkillStoreError("技能文件不是支持的文本编码")


def _items_from_zip(data: bytes) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillStoreError("ZIP 文件损坏") from exc
    files = [entry for entry in archive.infolist() if not entry.is_dir()]
    if len(files) > MAX_ARCHIVE_FILES:
        raise SkillStoreError(f"ZIP 最多包含 {MAX_ARCHIVE_FILES} 个文件")
    total = sum(entry.file_size for entry in files)
    if total > MAX_UPLOAD_BYTES:
        raise SkillStoreError("ZIP 解压后超过 5MB")
    archive_sha256 = hashlib.sha256(data).hexdigest()
    for entry in files:
        path = PurePosixPath(entry.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise SkillStoreError("ZIP 包含不安全路径")
        suffix = path.suffix.lower()
        if path.name.lower() != "skill.md" and suffix != ".json":
            continue
        raw = archive.read(entry)
        stem = path.parent.name or path.stem
        if suffix == ".json":
            try:
                parsed = _items_from_json(json.loads(_decode_text(raw)), fallback_name=stem)
            except json.JSONDecodeError as exc:
                raise SkillStoreError(f"{entry.filename} 不是有效 JSON") from exc
        else:
            parsed = _items_from_markdown(_decode_text(raw), fallback_name=stem)
        root_parts = path.parent.parts
        package_files: list[dict[str, Any]] = []
        for package_entry in files:
            package_path = PurePosixPath(package_entry.filename.replace("\\", "/"))
            if root_parts and package_path.parts[: len(root_parts)] != root_parts:
                continue
            relative = PurePosixPath(*package_path.parts[len(root_parts) :])
            if not relative.parts:
                continue
            package_files.append(
                {
                    "path": str(relative),
                    "archive_path": str(package_path),
                    "sha256": hashlib.sha256(archive.read(package_entry)).hexdigest(),
                    "size": int(package_entry.file_size),
                }
            )
        for item in parsed:
            item["package_manifest"] = {
                "schema": "skill.package.v1",
                "archive_sha256": archive_sha256,
                "root": str(path.parent),
                "files": package_files[:MAX_ARCHIVE_FILES],
            }
            items.append(item)
    if not items:
        raise SkillStoreError("ZIP 中未找到 SKILL.md 或技能 JSON")
    return items


def import_skill_file(filename: str, data: bytes) -> list[dict[str, Any]]:
    if not data:
        raise SkillStoreError("上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SkillStoreError("技能文件最大 5MB")
    safe_name = Path(filename or "skill").name
    suffix = Path(safe_name).suffix.lower()
    if suffix in {".md", ".markdown"}:
        items = _items_from_markdown(
            _decode_text(data), fallback_name=Path(safe_name).stem
        )
    elif suffix == ".json":
        try:
            payload = json.loads(_decode_text(data))
        except json.JSONDecodeError as exc:
            raise SkillStoreError("上传内容不是有效 JSON") from exc
        items = _items_from_json(payload, fallback_name=Path(safe_name).stem)
    elif suffix == ".zip":
        items = _items_from_zip(data)
    else:
        raise SkillStoreError("仅支持 SKILL.md、JSON 或 ZIP")
    if len(items) > MAX_ARCHIVE_FILES:
        raise SkillStoreError(f"单次最多导入 {MAX_ARCHIVE_FILES} 个技能")
    upload_sha256 = hashlib.sha256(data).hexdigest()
    existing = [*_bundled_items(), *_custom_items()]
    admitted: list[dict[str, Any]] = []
    for item in items:
        source_manifest = (
            item.get("source_manifest")
            if isinstance(item.get("source_manifest"), dict)
            else {}
        )
        if not source_manifest.get("source_ref"):
            source_manifest["source_ref"] = safe_name
        source_manifest["upload_sha256"] = upload_sha256
        item["source_manifest"] = source_manifest
        admission = scan_skill(
            item,
            existing_items=[*existing, *admitted],
            phase="import",
        )
        if admission["status"] == "blocked":
            messages = [
                str(issue.get("message") or issue.get("code"))
                for issue in admission.get("issues") or []
                if issue.get("severity") == "error"
            ]
            raise SkillStoreError("技能未通过导入检查：" + "；".join(messages[:4]))
        item["admission"] = admission
        admitted.append(item)
    if suffix == ".zip":
        _atomic_write_bytes(SKILL_ARCHIVES_DIR / f"{upload_sha256}.zip", data)
    saved: list[dict[str, Any]] = []
    for item in admitted:
        key = str(item["id"]).removeprefix("custom.")
        _atomic_write_json(CUSTOM_SKILLS_DIR / f"{key}.json", item)
        saved.append(_with_install_state(item, include_body=False))
    return saved


def _install_package_files(item: dict[str, Any], destination: Path) -> None:
    manifest = item.get("package_manifest")
    if not isinstance(manifest, dict) or not manifest.get("archive_sha256"):
        return
    archive_sha256 = str(manifest["archive_sha256"])
    archive_path = SKILL_ARCHIVES_DIR / f"{archive_sha256}.zip"
    if not archive_path.is_file():
        raise SkillStoreError("技能辅助文件包缺失，请重新导入")
    payload = archive_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != archive_sha256:
        raise SkillStoreError("技能辅助文件包哈希不一致")
    destination_root = destination.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for file in manifest.get("files") or []:
            if not isinstance(file, dict):
                continue
            relative = PurePosixPath(str(file.get("path") or "").replace("\\", "/"))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise SkillStoreError("技能辅助文件路径不安全")
            if relative.name.lower() in {"skill.md", "skill.json"}:
                continue
            raw = archive.read(str(file.get("archive_path") or ""))
            if hashlib.sha256(raw).hexdigest() != str(file.get("sha256") or ""):
                raise SkillStoreError(f"技能辅助文件哈希不一致：{relative}")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.resolve().relative_to(destination_root)
            except (OSError, ValueError) as exc:
                raise SkillStoreError("技能辅助文件越出安装目录") from exc
            _atomic_write_bytes(target, raw)


def _write_staged_skill(item: dict[str, Any], destination: Path) -> Path:
    """Write the complete skill package into an isolated directory."""
    destination.mkdir(parents=True, exist_ok=True)
    body = str(item.get("body") or item.get("activation") or item["description"])
    wrapped = body.strip()
    if not wrapped.startswith("---"):
        triggers = [str(tag) for tag in item.get("tags", [])]
        desc = str(item.get("description") or item["name"])
        from novelvideo.skills_distill.install import frontmatter

        wrapped = frontmatter(
            kebab(str(item["skill_key"])),
            desc,
            triggers,
        ) + wrapped + "\n"
    md = destination / "SKILL.md"
    md.write_text(wrapped, encoding="utf-8")
    return md


def _install_skill_atomically(item: dict[str, Any]) -> Path:
    """Prepare all files, then swap the package into place as one operation."""
    canonical = installed_skill_path(str(item["skill_key"]))
    root = canonical.parent.parent
    root.mkdir(parents=True, exist_ok=True)
    safe = kebab(str(item["skill_key"])) or "skill"
    staging_parent = root.parent / f".{safe}.install-{uuid.uuid4().hex[:12]}"
    staged = staging_parent / safe
    backup: Path | None = None
    try:
        _write_staged_skill(item, staged)
        _install_package_files(item, staged)
        destination = canonical.parent
        if destination.exists():
            backup = root.parent / f".{safe}.backup-{uuid.uuid4().hex[:12]}"
            destination.replace(backup)
        try:
            staged.replace(destination)
        except Exception:
            if backup is not None and backup.exists() and not destination.exists():
                backup.replace(destination)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
        return destination / "SKILL.md"
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)
        if backup is not None and backup.exists():
            # Keep the previous package if cleanup/publish failed midway.
            if not canonical.parent.exists():
                backup.replace(canonical.parent)
            else:
                shutil.rmtree(backup, ignore_errors=True)
