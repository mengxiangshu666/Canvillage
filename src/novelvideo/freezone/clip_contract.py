"""Freezone clip contract + official Seedance-style prompt compiler.

Canvas can hold production structure before any video upstream is wired.
A clip contract is the unit of one generation: who, where, action, one camera
move, endpoint, reference roles, and constraints.

This module is pure and offline — no model calls.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ReferenceRole = Literal[
    "identity",
    "scene",
    "motion",
    "audio",
    "first_frame",
    "last_frame",
    "prop",
    "style",
]


class ClipReference(BaseModel):
    """One attached reference with an explicit director role."""

    label: str = Field(description="Stable label such as 图片1 / 视频1 / 音频1")
    role: ReferenceRole = "identity"
    note: str = ""
    asset_id: str = ""
    character_name: str = ""

    @field_validator("label", "note", "asset_id", "character_name", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> str:
        return str(value or "").strip()


class ClipSegment(BaseModel):
    """One timed segment inside a single clip (time layer)."""

    start_sec: float = 0
    end_sec: float = 5
    who: str = ""
    where: str = ""
    action: str = ""
    camera: str = ""
    audio: str = ""

    @field_validator("who", "where", "action", "camera", "audio", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> str:
        return str(value or "").strip()


class ClipContract(BaseModel):
    """Structured contract for one Freezone generation node / beat."""

    schema_version: str = "clip_contract.v1"
    clip_id: str = ""
    title: str = ""
    duration_sec: float = 15
    mode: Literal[
        "text_to_video",
        "first_frame",
        "first_last_frame",
        "multimodal_reference",
        "image",
        "standalone",
    ] = "standalone"
    # Space layer
    subject: str = ""
    scene: str = ""
    lighting: str = ""
    style: str = ""
    # Time layer
    segments: list[ClipSegment] = Field(default_factory=list)
    # One primary camera for the whole clip unless segments override
    camera: str = ""
    felt_intent: str = ""
    endpoint: str = ""
    references: list[ClipReference] = Field(default_factory=list)
    dialogue: str = ""
    constraints: list[str] = Field(default_factory=list)
    # Sequence helpers (no video model required)
    parent_clip_id: str = ""
    extension_depth: int = 0
    strategy: Literal["standalone", "extend", "splice"] = "standalone"
    observed_end_state: str = ""
    planned_end_state: str = ""

    @field_validator(
        "clip_id",
        "title",
        "subject",
        "scene",
        "lighting",
        "style",
        "camera",
        "felt_intent",
        "endpoint",
        "dialogue",
        "parent_clip_id",
        "observed_end_state",
        "planned_end_state",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


DEFAULT_FACE_CONSTRAINTS = (
    "面部稳定不变形",
    "五官清晰",
    "人体结构正常",
    "动作自然流畅",
    "不僵硬",
    "画面无卡顿",
    "无闪烁",
)

# Official Seedance order: subject → action → scene → light → camera → style → quality → constraints
MAX_RECOMMENDED_REFERENCES = 5


def default_segments_for_duration(duration_sec: float) -> list[ClipSegment]:
    """Split a clip into 2–3 time buckets for timeline prompting."""
    duration = max(1.0, float(duration_sec or 15))
    if duration <= 6:
        return [ClipSegment(start_sec=0, end_sec=duration)]
    if duration <= 12:
        mid = round(duration / 2, 1)
        return [
            ClipSegment(start_sec=0, end_sec=mid),
            ClipSegment(start_sec=mid, end_sec=duration),
        ]
    a = round(duration / 3, 1)
    b = round(duration * 2 / 3, 1)
    return [
        ClipSegment(start_sec=0, end_sec=a),
        ClipSegment(start_sec=a, end_sec=b),
        ClipSegment(start_sec=b, end_sec=duration),
    ]


def ensure_segments(contract: ClipContract) -> list[ClipSegment]:
    if contract.segments:
        return list(contract.segments)
    return default_segments_for_duration(contract.duration_sec)


def validate_clip_contract(contract: ClipContract) -> list[str]:
    """Return non-fatal warnings for canvas QC / Agent."""
    warnings: list[str] = []
    if not contract.subject and not any(seg.who for seg in contract.segments):
        warnings.append("缺少主体：先写谁在画面里")
    if len(contract.references) > MAX_RECOMMENDED_REFERENCES:
        warnings.append(
            f"参考素材 {len(contract.references)} 个偏多，官方建议角色1-2+场景1+运镜1+音频1（约4-5）"
        )
    roles = [ref.role for ref in contract.references]
    if roles.count("identity") > 2:
        warnings.append("角色锚定参考超过 2 张，可能稀释优先级")
    cameras = [seg.camera for seg in ensure_segments(contract) if seg.camera]
    if contract.camera:
        cameras.append(contract.camera)
    # Rough multi-move detection
    multi_tokens = ("推", "拉", "摇", "移", "环绕", "升降", "dolly", "pan", "tilt", "track")
    for cam in cameras:
        hits = [tok for tok in multi_tokens if tok in cam]
        if len(hits) >= 2:
            warnings.append(f"运镜可能叠加过多（{cam}）；一镜尽量只指定一种运镜")
            break
    if any(ref.role == "identity" for ref in contract.references) and not any(
        ref.character_name or "角色" in ref.note for ref in contract.references
    ):
        # soft hint only
        pass
    names = [ref.character_name for ref in contract.references if ref.character_name]
    if len(names) >= 2 and not contract.subject:
        warnings.append("多人场景请写清站位与图N↔人名对应，避免人数抽卡")
    return warnings


def compile_clip_prompt(
    contract: ClipContract,
    *,
    include_at_syntax: bool = False,
    include_default_constraints: bool = True,
) -> str:
    """Compile a natural-language prompt from a clip contract.

    ``include_at_syntax``:
      - True  → ``@图片1 作为角色参考`` (official Jimeng style)
      - False → ``图片1 作为角色参考`` (Village Infinite Canvas adapter-safe default)
    """
    lines: list[str] = []

    # 1) Reference role assignment (space layer authority)
    for ref in contract.references:
        role_cn = {
            "identity": "角色外观锚定",
            "scene": "场景定调",
            "motion": "运镜与动作节奏参考",
            "audio": "节奏/氛围音频",
            "first_frame": "首帧约束",
            "last_frame": "尾帧约束",
            "prop": "道具参考",
            "style": "风格参考",
        }.get(ref.role, ref.role)
        label = ref.label
        if include_at_syntax and not label.startswith("@"):
            label = f"@{label}"
        piece = f"{label} 作为{role_cn}"
        if ref.character_name:
            piece += f"（{ref.character_name}）"
        if ref.note:
            piece += f"：{ref.note}"
        lines.append(piece + "。")

    # 2) Global subject / scene / light (eight-element order head)
    head_bits: list[str] = []
    if contract.subject:
        head_bits.append(contract.subject)
    if contract.scene:
        head_bits.append(f"场景：{contract.scene}")
    if contract.lighting:
        head_bits.append(f"光影：{contract.lighting}")
    if head_bits:
        lines.append("；".join(head_bits) + "。")

    if contract.observed_end_state and contract.strategy == "extend":
        lines.append(f"承接上一镜实际结尾状态：{contract.observed_end_state}。")
    elif contract.strategy == "extend" and contract.parent_clip_id:
        lines.append("将上一镜内容向后平滑延长，保持角色外观、背景与光线一致。")

    # 3) Timeline segments
    segments = ensure_segments(contract)
    for seg in segments:
        start = int(seg.start_sec) if seg.start_sec == int(seg.start_sec) else seg.start_sec
        end = int(seg.end_sec) if seg.end_sec == int(seg.end_sec) else seg.end_sec
        who = seg.who or contract.subject
        where = seg.where or contract.scene
        action = seg.action or "继续当前状态"
        camera = seg.camera or contract.camera or "固定镜头"
        chunk = f"{start}–{end} 秒：{who}"
        if where:
            chunk += f"在{where}"
        chunk += f"{action}，镜头{camera}"
        if seg.audio:
            chunk += f"，声音：{seg.audio}"
        lines.append(chunk + "。")

    if contract.dialogue:
        lines.append(f"台词/对白：{contract.dialogue}。")
    if contract.endpoint or contract.planned_end_state:
        lines.append(f"本镜终点：{contract.endpoint or contract.planned_end_state}。")
    if contract.style:
        lines.append(f"视觉风格：{contract.style}。")

    # 4) Constraints
    constraints = list(contract.constraints)
    if include_default_constraints:
        for item in DEFAULT_FACE_CONSTRAINTS:
            if item not in constraints:
                constraints.append(item)
    if constraints:
        lines.append("约束：" + "、".join(constraints) + "。")

    return "".join(line if line.endswith(("。", "！", "？", "\n")) else line + "\n" for line in lines).strip()


def clip_contract_from_node_data(data: dict[str, Any] | None) -> ClipContract | None:
    """Read a contract from Freezone node data if present."""
    if not isinstance(data, dict):
        return None
    raw = data.get("clipContract") or data.get("clip_contract")
    if not raw:
        return None
    if isinstance(raw, ClipContract):
        return raw
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return ClipContract.model_validate(raw)
    return None


def attach_clip_contract_to_node_data(
    data: dict[str, Any] | None,
    contract: ClipContract,
    *,
    compile_prompt: bool = True,
) -> dict[str, Any]:
    """Write contract (+ optional compiled prompt) into node data."""
    out = dict(data or {})
    out["clipContract"] = contract.model_dump()
    out["clipContractWarnings"] = validate_clip_contract(contract)
    if compile_prompt:
        out["compiledPrompt"] = compile_clip_prompt(contract)
        if not str(out.get("prompt") or "").strip():
            out["prompt"] = out["compiledPrompt"]
    return out
