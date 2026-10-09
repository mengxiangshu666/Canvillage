"""Shared cinematic craft contract for shots, prompts, and quality gates.

This module owns the missing production layer between director intent and a
shot prompt: lighting, screen direction, color ratios, edit rhythm, sound
design, and series continuity metadata.  It is deliberately pure data and
does not duplicate AssetPassport or the existing shot/continuity contracts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


CINEMATIC_CONTRACT_SCHEMA = "cinematic_contract.v1"
CINEMATIC_AUDIT_SCHEMA = "cinematic_audit.v1"
CINEMATIC_REVISION_PREFIX = "cinematic-contract.v1:"

CINEMATIC_QUALITY_GATES: tuple[str, ...] = (
    "lighting_continuity_consistent",
    "screen_direction_consistent",
    "color_look_consistent",
    "edit_rhythm_ready",
    "sound_design_ready",
    "cross_episode_continuity_valid",
    "final_delivery_qc_passed",
)


def _text(value: object, *, limit: int = 600) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _list(value: object, *, limit: int = 30, item_limit: int = 300) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _pick(source: Mapping[str, Any], *keys: str, limit: int = 600) -> str:
    for key in keys:
        text = _text(source.get(key), limit=limit)
        if text:
            return text
    return ""


def _pick_number(source: Mapping[str, Any], *keys: str) -> float | int | None:
    for key in keys:
        value = source.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return int(number) if number.is_integer() else number
    return None


def _source_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    for key in ("cinematic", "cinematic_contract", "cinematicContract"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return deepcopy(dict(nested))
    return {}


def _normalize_lighting(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": _text(value)}
    source = _mapping(value)
    result: dict[str, Any] = {}
    description = _pick(source, "description", "summary", "look")
    if description:
        result["description"] = description
    source_direction = _pick(
        source,
        "source_direction",
        "sourceDirection",
        "key_light_direction",
        "keyLightDirection",
        "direction",
    )
    if source_direction:
        result["source_direction"] = source_direction
    color_temperature = _pick_number(
        source,
        "color_temperature_k",
        "colorTemperatureK",
        "color_temperature",
        "colorTemperature",
    )
    if color_temperature is not None:
        result["color_temperature_k"] = color_temperature
    key_fill_ratio = _pick(
        source,
        "key_fill_ratio",
        "keyFillRatio",
        "contrast_ratio",
        "contrastRatio",
    )
    if key_fill_ratio:
        result["key_fill_ratio"] = key_fill_ratio
    motivated_source = _pick(
        source,
        "motivated_source",
        "motivatedSource",
        "source",
    )
    if motivated_source:
        result["motivated_source"] = motivated_source
    change_policy = _pick(
        source,
        "change_policy",
        "changePolicy",
        "continuity_policy",
        "continuityPolicy",
    )
    if change_policy:
        result["change_policy"] = change_policy
    return result


def _normalize_color_look(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": _text(value)}
    source = _mapping(value)
    result: dict[str, Any] = {}
    description = _pick(source, "description", "summary", "look")
    if description:
        result["description"] = description
    look_id = _pick(source, "look_id", "lookId", "name")
    if look_id:
        result["look_id"] = look_id
    dominant = _pick(source, "dominant", "dominant_color", "dominantColor")
    secondary = _pick(source, "secondary", "secondary_color", "secondaryColor")
    accent = _pick(source, "accent", "accent_color", "accentColor")
    if dominant:
        result["dominant"] = dominant
    if secondary:
        result["secondary"] = secondary
    if accent:
        result["accent"] = accent
    palette = _list(source.get("palette") or source.get("colors"), limit=12)
    if palette:
        result["palette"] = palette
    raw_ratios = _mapping(source.get("ratios") or source.get("ratio"))
    ratios = {
        key: _pick_number(raw_ratios, key)
        for key in ("dominant", "secondary", "accent")
    }
    ratios = {key: value for key, value in ratios.items() if value is not None}
    if ratios:
        result["ratios"] = ratios
    return result


def _normalize_screen_direction(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": _text(value)}
    source = _mapping(value)
    result: dict[str, Any] = {}
    mapping = (
        ("axis_id", ("axis_id", "axisId", "axis")),
        ("line_side", ("line_side", "lineSide", "side")),
        ("subject_facing", ("subject_facing", "subjectFacing", "facing")),
        ("eyeline", ("eyeline", "gaze", "look_direction", "lookDirection")),
        ("movement_direction", ("movement_direction", "movementDirection", "movement")),
        ("crossing_policy", ("crossing_policy", "crossingPolicy", "axis_change_policy")),
    )
    for target, keys in mapping:
        text = _pick(source, *keys)
        if text:
            result[target] = text
    return result


def _normalize_edit(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": _text(value)}
    source = _mapping(value)
    result: dict[str, Any] = {}
    cut_on = _pick(source, "cut_on", "cutOn", "cut_point", "cutPoint")
    if cut_on:
        result["cut_on"] = cut_on
    match_cut = _pick(source, "match_cut", "matchCut")
    if match_cut:
        result["match_cut"] = match_cut
    rhythm = _pick(source, "rhythm", "transition")
    if rhythm:
        result["rhythm"] = rhythm
    beat_seconds = _pick_number(source, "beat_seconds", "beatSeconds", "beat")
    if beat_seconds is not None:
        result["beat_seconds"] = beat_seconds
    return result


def _normalize_sound(value: object, *, shot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": _text(value)}
    source = _mapping(value)
    raw_shot = shot if isinstance(shot, Mapping) else {}
    result: dict[str, Any] = {}
    ambience = _list(value if isinstance(value, str) else source.get("ambience"))
    if not ambience:
        ambience = _list(source.get("ambient") or source.get("environment"))
    if ambience:
        result["ambience"] = ambience
    diegetic = _list(source.get("diegetic_sources") or source.get("diegeticSources"))
    if diegetic:
        result["diegetic_sources"] = diegetic
    sfx = _list(
        source.get("sfx_cues")
        or source.get("sfxCues")
        or source.get("sound_cues")
        or raw_shot.get("sound_cues")
        or raw_shot.get("soundCues")
    )
    if sfx:
        result["sfx_cues"] = sfx
    dialogue = _pick(source, "dialogue", "dialogue_cue", "dialogueCue")
    if dialogue:
        result["dialogue"] = dialogue
    description = _pick(source, "description", "summary")
    if description:
        result["description"] = description
    return result


def _normalize_continuity(value: object, *, project_dna: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = _mapping(value)
    dna = project_dna if isinstance(project_dna, Mapping) else {}
    result: dict[str, Any] = {}
    for target, keys in (
        ("series_id", ("series_id", "seriesId", "show_id", "showId")),
        ("episode_index", ("episode_index", "episodeIndex", "episode")),
        ("visual_bible_revision", ("visual_bible_revision", "visualBibleRevision", "bible_revision")),
    ):
        text = _pick(source, *keys)
        if text:
            result[target] = text
    asset_revisions = _mapping(
        source.get("asset_revisions") or source.get("assetRevisions")
    )
    if asset_revisions:
        result["asset_revisions"] = asset_revisions
    locked_fields = _list(
        source.get("locked_fields")
        or source.get("lockedFields")
        or source.get("locked_rules")
    )
    if locked_fields:
        result["locked_fields"] = locked_fields
    if dna and result:
        project_id = _pick(dna, "project_id", "projectId")
        dna_revision = _pick(dna, "dna_revision", "dnaRevision")
        if project_id:
            result.setdefault("project_id", project_id)
        if dna_revision:
            result["project_dna_revision"] = dna_revision
    return result


def _merge_section(
    global_value: Mapping[str, Any],
    shot_value: Mapping[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(dict(global_value))
    for key, value in shot_value.items():
        if value not in (None, "", [], {}):
            merged[key] = deepcopy(value)
    return merged


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_cinematic_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{CINEMATIC_REVISION_PREFIX}{digest}"


def build_cinematic_contract(
    *,
    shot: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    creation_stage: object = "",
) -> dict[str, Any]:
    """Merge project-level cinematic rules with one shot's explicit overrides."""

    vision = director_vision if isinstance(director_vision, Mapping) else {}
    raw_shot = shot if isinstance(shot, Mapping) else {}
    global_contract = _source_contract(vision)
    shot_contract = _source_contract(raw_shot)
    if not shot_contract:
        shot_contract = {
            "lighting": raw_shot.get("lighting_contract") or raw_shot.get("lightingContract"),
            "color_look": raw_shot.get("color_look") or raw_shot.get("colorLook"),
            "screen_direction": raw_shot.get("screen_direction") or raw_shot.get("screenDirection"),
            "edit": raw_shot.get("edit_contract") or raw_shot.get("editContract"),
            "sound": (
                raw_shot.get("sound_contract")
                or raw_shot.get("soundContract")
                or raw_shot.get("sound")
            ),
            "continuity": raw_shot.get("continuity_contract") or raw_shot.get("continuity"),
        }
        shot_contract.update(
            {
                key: raw_shot[key]
                for key in (
                    "lighting",
                    "color_look",
                    "colorLook",
                    "screen_direction",
                    "screenDirection",
                    "edit",
                    "editing",
                )
                if raw_shot.get(key) not in (None, "", [], {})
            }
        )

    if not global_contract:
        style_anchor = _mapping(vision.get("style_anchor"))
        global_contract = {
            "lighting": {"description": _pick(style_anchor, "lighting")},
            "color_look": {"description": _pick(style_anchor, "color_palette")},
        }

    lighting = _merge_section(
        _normalize_lighting(global_contract.get("lighting")),
        _normalize_lighting(shot_contract.get("lighting")),
    )
    color_look = _merge_section(
        _normalize_color_look(global_contract.get("color_look") or global_contract.get("color")),
        _normalize_color_look(shot_contract.get("color_look") or shot_contract.get("color")),
    )
    screen_direction = _merge_section(
        _normalize_screen_direction(
            global_contract.get("screen_direction") or global_contract.get("direction")
        ),
        _normalize_screen_direction(
            shot_contract.get("screen_direction") or shot_contract.get("direction")
        ),
    )
    edit = _merge_section(
        _normalize_edit(global_contract.get("edit") or global_contract.get("editing")),
        _normalize_edit(shot_contract.get("edit") or shot_contract.get("editing")),
    )
    sound = _merge_section(
        _normalize_sound(global_contract.get("sound"), shot=raw_shot),
        _normalize_sound(shot_contract.get("sound"), shot=raw_shot),
    )
    continuity = _merge_section(
        _normalize_continuity(
            global_contract.get("continuity") or global_contract.get("series"),
            project_dna=project_dna,
        ),
        _normalize_continuity(shot_contract.get("continuity"), project_dna=project_dna),
    )
    positive_constraints = _list(
        shot_contract.get("positive_constraints")
        or shot_contract.get("positiveConstraints")
        or global_contract.get("positive_constraints")
        or global_contract.get("positiveConstraints"),
        limit=20,
    )
    result: dict[str, Any] = {"schema": CINEMATIC_CONTRACT_SCHEMA}
    for key, value in (
        ("lighting", lighting),
        ("color_look", color_look),
        ("screen_direction", screen_direction),
        ("edit", edit),
        ("sound", sound),
        ("continuity", continuity),
    ):
        if value:
            result[key] = value
    if positive_constraints:
        result["positive_constraints"] = positive_constraints
    stage = _text(creation_stage, limit=80)
    if stage:
        result["creation_stage"] = stage
    if len(result) <= 1:
        return {}
    result["contract_revision"] = compute_cinematic_revision(result)
    return result


def cinematic_prompt_lines(value: object) -> list[str]:
    """Render only explicitly present cinematic facts as compact prompt lines."""

    contract = _mapping(value)
    if contract.get("schema") not in (None, "", CINEMATIC_CONTRACT_SCHEMA):
        return []
    lines: list[str] = []

    lighting = _mapping(contract.get("lighting"))
    light_parts: list[str] = []
    for label, key in (
        ("描述", "description"),
        ("主光方向", "source_direction"),
        ("色温", "color_temperature_k"),
        ("光比", "key_fill_ratio"),
        ("光源依据", "motivated_source"),
        ("变化规则", "change_policy"),
    ):
        value = _text(lighting.get(key))
        if value:
            suffix = "K" if key == "color_temperature_k" else ""
            light_parts.append(f"{label}：{value}{suffix}")
    if light_parts:
        lines.append("光线合同：世界光源与本镜观察关系；" + "；".join(light_parts))
        if _text(lighting.get("source_direction")):
            lines.append("光源以固定地标为基准，屏幕左右随本镜观察方向改变；保留已声明的来光事实，不臆造灯位。")

    direction = _mapping(contract.get("screen_direction"))
    direction_parts = [
        f"{label}：{_text(direction.get(key))}"
        for label, key in (
            ("轴线", "axis_id"),
            ("机位侧", "line_side"),
            ("人物朝向", "subject_facing"),
            ("视线", "eyeline"),
            ("运动方向", "movement_direction"),
            ("越轴规则", "crossing_policy"),
        )
        if _text(direction.get(key))
    ]
    if direction_parts:
        lines.append("屏幕方向：需要保持 " + "；".join(direction_parts))

    color = _mapping(contract.get("color_look"))
    color_parts = [
        f"{label}：{_text(color.get(key))}"
        for label, key in (
            ("Look", "look_id"),
            ("主色", "dominant"),
            ("辅色", "secondary"),
            ("点缀色", "accent"),
        )
        if _text(color.get(key))
    ]
    ratios = _mapping(color.get("ratios"))
    if ratios:
        color_parts.append(
            "配额："
            + " / ".join(
                f"{key} {float(value):.0%}"
                for key, value in ratios.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )
        )
    palette = _list(color.get("palette"), limit=8)
    if palette:
        color_parts.append("色板：" + "、".join(palette))
    if color_parts:
        lines.append("色彩合同：" + "；".join(color_parts))

    edit = _mapping(contract.get("edit"))
    edit_parts = [
        f"{label}：{_text(edit.get(key))}"
        for label, key in (
            ("切点", "cut_on"),
            ("匹配剪辑", "match_cut"),
            ("节奏", "rhythm"),
        )
        if _text(edit.get(key))
    ]
    beat_seconds = edit.get("beat_seconds")
    if isinstance(beat_seconds, (int, float)) and not isinstance(beat_seconds, bool):
        edit_parts.append(f"节拍：{float(beat_seconds):g}s")
    if edit_parts:
        lines.append("剪辑合同：" + "；".join(edit_parts))

    sound = _mapping(contract.get("sound"))
    sound_parts: list[str] = []
    for label, key in (
        ("环境声", "ambience"),
        ("画面内声源", "diegetic_sources"),
        ("音效落点", "sfx_cues"),
    ):
        values = _list(sound.get(key), limit=8)
        if values:
            sound_parts.append(f"{label}：{'、'.join(values)}")
    dialogue = _text(sound.get("dialogue"))
    if dialogue:
        sound_parts.append(f"对白：{dialogue}")
    description = _text(sound.get("description"))
    if description:
        sound_parts.append(f"声音设计：{description}")
    if sound_parts:
        lines.append("声音合同：" + "；".join(sound_parts))

    continuity = _mapping(contract.get("continuity"))
    continuity_parts = [
        f"{label}：{_text(continuity.get(key))}"
        for label, key in (
            ("系列", "series_id"),
            ("集数", "episode_index"),
            ("剧集圣经", "visual_bible_revision"),
        )
        if _text(continuity.get(key))
    ]
    locked_fields = _list(continuity.get("locked_fields"), limit=10)
    if locked_fields:
        continuity_parts.append("跨集锁定：" + "、".join(locked_fields))
    if continuity_parts:
        lines.append("跨集连续性：" + "；".join(continuity_parts))

    positive_constraints = _list(contract.get("positive_constraints"), limit=12)
    if positive_constraints:
        lines.append("正向约束：" + "、".join(positive_constraints))
    return lines


def _shot_has_subject(shot: Mapping[str, Any]) -> bool:
    return bool(
        _text(shot.get("subject"))
        or _text(shot.get("title"))
        or _text(shot.get("primary_action"))
        or _text(shot.get("action"))
    )


def _issue(code: str, message: str, *, shot_id: str = "", fix: str = "") -> dict[str, str]:
    return {
        "code": code,
        "message": message,
        **({"shot_id": shot_id} if shot_id else {}),
        **({"fix": fix} if fix else {}),
    }


def _ratios_ready(value: object) -> bool:
    ratios = _mapping(value)
    if not ratios:
        return True
    numbers = [
        float(number)
        for number in ratios.values()
        if isinstance(number, (int, float)) and not isinstance(number, bool)
    ]
    return bool(numbers) and abs(sum(numbers) - 1.0) <= 0.03


def _delivery_qc_observation(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        if isinstance(value.get("passed"), bool):
            return bool(value["passed"])
        status = _text(value.get("status"), limit=40).casefold()
        # 只有真正的 QC 结果词才算证据（delivery_qc_contract 的 passed/failed/
        # not_run）。`completed`/`success` 这类只是「任务跑完了」，不是「质检通过
        # 了」——此前被误当通过，一份 {status:"completed"} 的空壳回执即可让
        # final_delivery_qc_passed 变 true（T-217）。
        if status in {"passed", "ready"}:
            return True
        if status in {"failed", "error", "cancelled", "canceled"}:
            return False
    return None


def _shot_scene(shot: Mapping[str, Any]) -> str:
    bindings = shot.get("reference_bindings")
    if not isinstance(bindings, Mapping):
        bindings = shot.get("referenceBindings")
    values = bindings.get("scene") if isinstance(bindings, Mapping) else None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values:
        return ""
    if any(not isinstance(value, str) or not value.strip() for value in values):
        return ""
    scenes = {value.strip() for value in values}
    return scenes.pop() if len(scenes) == 1 else ""


def _permits_motivated_change(value: object) -> bool:
    # shortcut: Permission syntax proves a declaration only; verify its visible cause in real media before delivery.
    policy = _text(value).casefold()
    match = re.match(
        r"^(?:允许(?:变化|越轴)?\s*[:：]|(?:allow|allowed)(?:\s*[:：]|\s+(?:when|if|because)\s+))\s*(\S.*)$",
        policy,
    )
    if not match:
        return False
    reason = match.group(1).strip(" :：;；,.。，")
    return any(char.isalnum() for char in reason) and not reason.startswith(("不允许", "禁止", "不得", "disallow", "not allow", "do not allow"))


def _axis_side(value: object) -> str:
    side = _text(value).casefold()
    if side in {"left", "left side", "left-side", "左", "左侧", "轴线左侧"}:
        return "left"
    if side in {"right", "right side", "right-side", "右", "右侧", "轴线右侧"}:
        return "right"
    return ""


def audit_cinematic_contracts(
    shots: object,
    *,
    director_vision: Mapping[str, Any] | None = None,
    project_dna: Mapping[str, Any] | None = None,
    delivery_qc: object = None,
) -> dict[str, Any]:
    """Return evidence-backed observations for the new cinematic quality gates."""

    normalized = [dict(shot) for shot in (shots if isinstance(shots, (list, tuple)) else []) if isinstance(shot, Mapping)]
    contracts = [
        build_cinematic_contract(
            shot=shot,
            director_vision=director_vision,
            project_dna=project_dna,
        )
        for shot in normalized
    ]
    shot_ids = [
        _text(shot.get("shot_id") or shot.get("shotId"), limit=120) or f"S{index:02d}"
        for index, shot in enumerate(normalized, 1)
    ]
    subject_indices = [
        index for index, shot in enumerate(normalized) if _shot_has_subject(shot)
    ]
    scopes = [_shot_scene(shot) for shot in normalized]
    # Incomplete bindings retain the historical batch comparison; cuts alone do not prove a new scene.
    if any(not scopes[index] for index in subject_indices):
        scopes = [""] * len(normalized)
    issues: list[dict[str, str]] = []
    observations: dict[str, bool | None] = {
        gate: None for gate in CINEMATIC_QUALITY_GATES
    }

    lighting = [_mapping(contract.get("lighting")) for contract in contracts]
    lighting_applicable = any(
        any(key in item for key in ("source_direction", "color_temperature_k", "key_fill_ratio"))
        for item in lighting
    )
    if lighting_applicable:
        lighting_states: dict[str, dict[str, str]] = {}
        for index in subject_indices:
            item = lighting[index]
            direction = _text(item.get("source_direction"))
            temperature = _text(item.get("color_temperature_k"))
            if not direction:
                issues.append(_issue(
                    "lighting.source_direction_missing",
                    "镜头缺少主光方向",
                    shot_id=shot_ids[index],
                    fix="补 source_direction，以固定地标说明光源，例如东墙窗向桌面照明。",
                ))
            if not temperature:
                issues.append(_issue(
                    "lighting.color_temperature_missing",
                    "镜头缺少色温",
                    shot_id=shot_ids[index],
                    fix="补 color_temperature_k，例如 3200K。",
                ))
            previous = lighting_states.setdefault(scopes[index], {})
            allowed = _permits_motivated_change(item.get("change_policy"))
            for key, value, label, code in (
                ("source_direction", direction, "主光方向", "source_direction"),
                ("color_temperature_k", temperature, "色温", "color_temperature"),
            ):
                before = previous.get(key)
                if before and value and before != value and not allowed:
                    issues.append(_issue(
                        f"lighting.{code}_inconsistent",
                        f"{'同场' if scopes[index] else '场景归属未明确的跨镜'}{label}发生变化：{before} / {value}",
                        shot_id=shot_ids[index],
                        fix="按固定场景核对光源与本镜观察关系；有意变化须在本镜 change_policy 写明允许及可见原因。",
                    ))
                if value:
                    previous[key] = value
        observations["lighting_continuity_consistent"] = not any(
            issue["code"].startswith("lighting.") for issue in issues
        )

    direction_values = [_mapping(contract.get("screen_direction")) for contract in contracts]
    direction_applicable = any(
        any(key in item for key in ("axis_id", "line_side", "subject_facing", "eyeline", "movement_direction"))
        for item in direction_values
    )
    if direction_applicable:
        axis_states: dict[str, tuple[str, str]] = {}
        for index in subject_indices:
            item = direction_values[index]
            axis_id = _text(item.get("axis_id"))
            line_side = _text(item.get("line_side"))
            if not axis_id and not line_side:
                issues.append(_issue(
                    "screen_direction.axis_missing",
                    "镜头缺少轴线或机位侧声明",
                    shot_id=shot_ids[index],
                    fix="补 axis_id / line_side，并保持机位不越轴。",
                ))
            before_axis, before_side = axis_states.get(scopes[index], ("", ""))
            side = _axis_side(line_side)
            allowed = _permits_motivated_change(item.get("crossing_policy"))
            if before_axis and axis_id and not allowed:
                if before_axis != axis_id:
                    issues.append(_issue(
                        "screen_direction.axis_changed",
                        f"{'同场' if scopes[index] else '场景归属未明确的跨镜'}轴线发生变化：{before_axis} / {axis_id}",
                        shot_id=shot_ids[index],
                        fix="核对本场轴线；有意改变须在本镜 crossing_policy 写明允许、原因与过渡。",
                    ))
                elif before_side and side and before_side != side:
                    issues.append(_issue(
                        "screen_direction.line_side_changed",
                        f"同一轴线的机位换侧：{before_side} / {side}",
                        shot_id=shot_ids[index],
                        fix="保留机位侧；有意越轴须在本镜 crossing_policy 写明允许及可见过渡。",
                    ))
            if axis_id:
                axis_states[scopes[index]] = (axis_id, side or (before_side if axis_id == before_axis else ""))
        observations["screen_direction_consistent"] = not any(
            issue["code"].startswith("screen_direction.") for issue in issues
        )

    colors = [_mapping(contract.get("color_look")) for contract in contracts]
    color_applicable = any(
        any(key in item for key in ("look_id", "dominant", "secondary", "accent", "palette", "ratios"))
        for item in colors
    )
    if color_applicable:
        look_ids: set[str] = set()
        for index in subject_indices:
            item = colors[index]
            look_id = _text(item.get("look_id"))
            if look_id:
                look_ids.add(look_id)
            if not _ratios_ready(item.get("ratios")):
                issues.append(_issue(
                    "color.ratios_invalid",
                    "主色 / 辅色 / 点缀色配额之和不是 100%",
                    shot_id=shot_ids[index],
                    fix="使用 60:30:10 对应 ratios={dominant:0.6, secondary:0.3, accent:0.1}。",
                ))
        if len(look_ids) > 1:
            issues.append(_issue(
                "color.look_changed",
                f"跨镜 Look 不一致：{' / '.join(sorted(look_ids))}",
                fix="沿用同一 look_id；换色必须作为显式剧情变化处理。",
            ))
        observations["color_look_consistent"] = not any(
            issue["code"].startswith("color.") for issue in issues
        )

    edits = [_mapping(contract.get("edit")) for contract in contracts]
    edit_applicable = any(
        any(key in item for key in ("cut_on", "match_cut", "rhythm", "beat_seconds"))
        for item in edits
    )
    if edit_applicable:
        for index in subject_indices:
            beat = edits[index].get("beat_seconds")
            if isinstance(beat, (int, float)) and not isinstance(beat, bool) and not (0.3 <= float(beat) <= 0.8):
                issues.append(_issue(
                    "edit.beat_out_of_range",
                    f"动作节拍 {float(beat):g}s 不在 0.3–0.8s",
                    shot_id=shot_ids[index],
                    fix="把动作拆成 0.3–0.8 秒的可见节拍。",
                ))
            if not _text(edits[index].get("cut_on")) and not _text(edits[index].get("rhythm")):
                issues.append(_issue(
                    "edit.cut_point_missing",
                    "镜头缺少切点或节奏声明",
                    shot_id=shot_ids[index],
                    fix="写清切在动作、视线或匹配形状的哪一拍。",
                ))
        observations["edit_rhythm_ready"] = not any(
            issue["code"].startswith("edit.") for issue in issues
        )

    sounds = [_mapping(contract.get("sound")) for contract in contracts]
    sound_applicable = any(
        bool(item) or bool(_list(shot.get("sound_cues") or shot.get("soundCues")))
        for item, shot in zip(sounds, normalized)
    )
    if sound_applicable:
        for index in subject_indices:
            item = sounds[index]
            has_cue = any(
                _list(item.get(key))
                for key in ("ambience", "diegetic_sources", "sfx_cues")
            ) or bool(_text(item.get("dialogue")) or _text(item.get("description")))
            if not has_cue:
                issues.append(_issue(
                    "sound.cue_missing",
                    "镜头没有环境声、画内声源或音效落点",
                    shot_id=shot_ids[index],
                    fix="至少声明一个画内声音事实，并绑定到可见动作或物体。",
                ))
        observations["sound_design_ready"] = not any(
            issue["code"].startswith("sound.") for issue in issues
        )

    continuity = [_mapping(contract.get("continuity")) for contract in contracts]
    continuity_applicable = any(
        any(item.get(key) not in (None, "", [], {}) for key in ("series_id", "episode_index", "visual_bible_revision", "asset_revisions"))
        for item in continuity
    )
    if continuity_applicable:
        for index in subject_indices:
            item = continuity[index]
            if not _text(item.get("series_id")):
                issues.append(_issue(
                    "continuity.series_id_missing",
                    "跨集合同缺少系列 ID",
                    shot_id=shot_ids[index],
                    fix="引用稳定的系列 ID，而不是按集重建。",
                ))
            if item.get("episode_index") in (None, ""):
                issues.append(_issue(
                    "continuity.episode_index_missing",
                    "跨集合同缺少集数",
                    shot_id=shot_ids[index],
                    fix="写入当前集数，供跨集资版本对账。",
                ))
            if not _mapping(item.get("asset_revisions")) and not _list(item.get("locked_fields")):
                issues.append(_issue(
                    "continuity.lock_reference_missing",
                    "跨集合同没有资产 revision 或锁定字段引用",
                    shot_id=shot_ids[index],
                    fix="引用 AssetPassport revision 或明确的跨集锁定字段，不复制资产正文。",
                ))
        observations["cross_episode_continuity_valid"] = not any(
            issue["code"].startswith("continuity.") for issue in issues
        )

    observations["final_delivery_qc_passed"] = _delivery_qc_observation(delivery_qc)
    applicable_gates = [
        gate for gate, value in observations.items() if value is not None
    ]
    failed_gates = [gate for gate, value in observations.items() if value is False]
    return {
        "schema": CINEMATIC_AUDIT_SCHEMA,
        "passed": not failed_gates,
        "applicable_gates": applicable_gates,
        "failed_gates": failed_gates,
        "gate_observations": observations,
        "issues": issues,
        "contracts": contracts,
        "shot_ids": shot_ids,
    }


__all__ = [
    "CINEMATIC_AUDIT_SCHEMA",
    "CINEMATIC_CONTRACT_SCHEMA",
    "CINEMATIC_QUALITY_GATES",
    "CINEMATIC_REVISION_PREFIX",
    "audit_cinematic_contracts",
    "build_cinematic_contract",
    "cinematic_prompt_lines",
    "compute_cinematic_revision",
]
