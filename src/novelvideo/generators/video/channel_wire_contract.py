"""出线合同挂在**渠道条目**上，随删随走、随填随有。

背景（T-226 用户口径，2026-10-01）：「只要我们删掉，它就是默认没有了；如果我们把它
接过来，就是有，随删随走，随填随有。」—— 渠道配置是唯一真相，源码里那份声明只是第二
份副本，渠道删掉以后它会变成幽灵。

所以出线合同分三级，顺序固定：

1. ``channel`` —— 挂在渠道条目上、跟渠道一起存、一起删的合同（**权威**）；
2. ``seed``    —— 随源码分发的 8 个数据包（**未验证的种子**，只描述「这家上游大概长这样」，
                  不是「这台机器上配的渠道就是这个样」）；
3. ``default`` —— OpenAI 兼容的通用兜底档案。

层级之间的裁决是硬顺序，不靠名字猜：只要渠道带了自己的合同，种子**不参与**。

本模块只做「怎么存、怎么读、怎么比」；真正在跑的档案对象仍在 ``upstream_profiles``
里，导入放在函数内部以避免循环。
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contract_packages import (
    ContractPackageError,
    normalize_profile_fields,
)

__all__ = [
    "WIRE_SOURCE_CHANNEL",
    "WIRE_SOURCE_SEED",
    "WIRE_SOURCE_DEFAULT",
    "WIRE_EVIDENCE_PROBE",
    "WIRE_EVIDENCE_SUBMIT",
    "WIRE_EVIDENCE_OPERATOR",
    "WireContractError",
    "ResolvedWireContract",
    "contract_field_differences",
    "describe_wire_source",
    "normalize_wire_contract_record",
    "record_from_profile",
    "utc_now_iso",
]

WIRE_SOURCE_CHANNEL = "channel"
WIRE_SOURCE_SEED = "seed"
WIRE_SOURCE_DEFAULT = "default"

#: 渠道合同里的「证据级别」：目录探测 / 真实提交过 / 人工指定。
WIRE_EVIDENCE_PROBE = "probe"
WIRE_EVIDENCE_SUBMIT = "submit"
WIRE_EVIDENCE_OPERATOR = "operator"

#: 渠道合同靠这个字段声明自己的来源；别的来源不允许被存进渠道条目。
_STORED_SOURCES = frozenset({WIRE_SOURCE_CHANNEL})

_RECORD_KEYS = frozenset(
    {"profileId", "source", "evidence", "evidenceAt", "notes", "fields"}
)

_EVIDENCE_VALUES = frozenset(
    {WIRE_EVIDENCE_PROBE, WIRE_EVIDENCE_SUBMIT, WIRE_EVIDENCE_OPERATOR}
)

#: 只有「真实提交过」或「人工指定」才算验证过；目录探测不算。
_VERIFIED_EVIDENCE = frozenset({WIRE_EVIDENCE_SUBMIT, WIRE_EVIDENCE_OPERATOR})

#: 渠道合同不参与名字命中，所以这两个字段必须为空。
_MATCH_RULE_FIELDS = frozenset({"match_prefixes", "match_exact"})

_SOURCE_LABELS = {
    WIRE_SOURCE_CHANNEL: "渠道自带合同（随渠道增删）",
    WIRE_SOURCE_SEED: "源码种子（未验证，按名字猜的）",
    WIRE_SOURCE_DEFAULT: "通用兜底（OpenAI 兼容默认档案）",
}

_EVIDENCE_LABELS = {
    WIRE_EVIDENCE_PROBE: "目录探测",
    WIRE_EVIDENCE_SUBMIT: "真实提交验证",
    WIRE_EVIDENCE_OPERATOR: "人工指定",
}


class WireContractError(ValueError):
    """渠道合同不合法。错误信息带字段名，不静默跳过。"""


def utc_now_iso() -> str:
    """证据时间戳统一用带时区的 UTC，便于跨机器比对。"""

    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ResolvedWireContract:
    """一份生效中的出线合同，附带它是从哪一级来的。"""

    profile: Any
    profile_id: str
    source: str
    evidence: str = ""
    evidence_at: str | None = None
    notes: tuple[str, ...] = ()
    seed_profile_id: str | None = None
    seed_source: str | None = None
    conflicts_with_seed: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """只有「挂在渠道上」且「真实提交验证过或人工指定」才算验证过。

        目录探测（GET /models）只证明这个模型在目录里，不证明报文能提交成功，
        所以它不算验证 —— 只能真，不能骗人。
        """

        return (
            self.source == WIRE_SOURCE_CHANNEL
            and self.evidence in _VERIFIED_EVIDENCE
        )

    @property
    def source_label(self) -> str:
        label = describe_wire_source(self.source)
        if self.source == WIRE_SOURCE_CHANNEL and self.evidence:
            detail = _EVIDENCE_LABELS.get(self.evidence, self.evidence)
            return f"{label} · {detail}"
        return label

    def to_status(self) -> dict[str, Any]:
        """给节点面板/模型中心看的精简状态。"""

        return {
            "wireContractSource": self.source,
            "wireContractSourceLabel": self.source_label,
            "wireContractProfileId": self.profile_id,
            "wireContractEvidence": self.evidence,
            "wireContractVerified": self.verified,
            "wireContractEvidenceAt": self.evidence_at,
            "wireContractSeedProfileId": self.seed_profile_id,
            "wireContractConflictsWithSeed": list(self.conflicts_with_seed),
        }


def describe_wire_source(source: str) -> str:
    return _SOURCE_LABELS.get(str(source or "").strip(), f"未知来源：{source}")


def _profile_field_names() -> tuple[str, ...]:
    from .upstream_profiles import VideoUpstreamProfile

    return tuple(
        item.name
        for item in dataclass_fields(VideoUpstreamProfile)
        if item.name not in _MATCH_RULE_FIELDS
    )


def record_from_profile(
    profile: Any,
    *,
    evidence: str = WIRE_EVIDENCE_OPERATOR,
    evidence_at: str | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """把一个档案完整写成可持久化的渠道合同（字段全部显式落盘）。

    全量写出的理由：渠道合同不该依赖源码里那份种子的默认值。种子将来改一个默认，
    已经配好的渠道报文**一个字都不该变**。
    """

    field_names = _profile_field_names()
    unknown = [name for name in field_names if not hasattr(profile, name)]
    if unknown:
        raise WireContractError(f"档案缺少字段 {unknown}，无法写成渠道合同")
    serialized: dict[str, Any] = {}
    for name in field_names:
        value = getattr(profile, name)
        if isinstance(value, tuple):
            serialized[name] = list(value)
        elif isinstance(value, Mapping):
            serialized[name] = dict(value)
        else:
            serialized[name] = value
    payload = {
        "profileId": str(getattr(profile, "profile_id")),
        "source": WIRE_SOURCE_CHANNEL,
        "evidence": str(evidence or WIRE_EVIDENCE_OPERATOR).strip().lower(),
        "evidenceAt": str(evidence_at or utc_now_iso()),
        "notes": [str(note) for note in notes if str(note).strip()],
        "fields": serialized,
    }
    return normalize_wire_contract_record(payload)


def normalize_wire_contract_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """校验并规范化一条渠道合同，返回可原样写进设置的字典。

    任何拼错、类型错、来源错都在这里报错；调用方不许「读不动就跳过」。
    """

    if not isinstance(raw, Mapping):
        raise WireContractError("渠道合同必须是 JSON 对象")
    unknown = sorted(str(key) for key in raw if str(key) not in _RECORD_KEYS)
    if unknown:
        raise WireContractError(f"渠道合同出现未知字段 {unknown}；拼错字段名会被静默忽略，所以这里直接拒绝")

    profile_id = str(raw.get("profileId") or "").strip()
    if not profile_id:
        raise WireContractError("渠道合同必须有 profileId")

    source = str(raw.get("source") or "").strip().lower()
    if source not in _STORED_SOURCES:
        raise WireContractError(
            f"渠道合同的 source 只能是 {sorted(_STORED_SOURCES)}，当前 {source!r}；"
            "种子与兜底不是「存下来的合同」"
        )

    evidence = str(raw.get("evidence") or WIRE_EVIDENCE_OPERATOR).strip().lower()
    if evidence not in _EVIDENCE_VALUES:
        raise WireContractError(
            f"渠道合同的 evidence 只能是 {sorted(_EVIDENCE_VALUES)}，当前 {evidence!r}"
        )

    evidence_at = raw.get("evidenceAt")
    evidence_at = "" if evidence_at in (None, "") else str(evidence_at).strip()

    notes_raw = raw.get("notes") or ()
    if not isinstance(notes_raw, Sequence) or isinstance(notes_raw, (str, bytes)):
        raise WireContractError("渠道合同的 notes 必须是字符串数组")
    notes = [str(item).strip() for item in notes_raw if str(item).strip()]

    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, Mapping):
        raise WireContractError("渠道合同的 fields 必须是 JSON 对象")

    try:
        normalized_fields = normalize_profile_fields(
            {"profile_id": profile_id, **fields_raw},
            where=f"渠道合同 {profile_id!r}",
            require_match_rules=False,
        )
    except ContractPackageError as exc:
        raise WireContractError(str(exc)) from exc

    return {
        "profileId": profile_id,
        "source": source,
        "evidence": evidence,
        "evidenceAt": evidence_at or None,
        "notes": notes,
        "fields": dict(normalized_fields),
    }


def contract_field_differences(left: Any, right: Any) -> tuple[str, ...]:
    """逐字段比较两份档案，返回取值不同的字段名（按字段名排序）。

    用于「渠道合同」与「同名种子」不一致时**明显报错**：差异不许被静默吞掉，
    要么写清理由，要么把渠道合同调对。
    """

    if left is None or right is None:
        return ()
    differences: list[str] = []
    for name in _profile_field_names():
        if name == "profile_id":
            continue
        left_value = getattr(left, name, None)
        right_value = getattr(right, name, None)
        if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
            left_value = dict(left_value)
            right_value = dict(right_value)
        if isinstance(left_value, tuple):
            left_value = list(left_value)
        if isinstance(right_value, tuple):
            right_value = list(right_value)
        if left_value != right_value:
            differences.append(name)
    return tuple(sorted(differences))
