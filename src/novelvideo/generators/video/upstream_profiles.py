"""Provider-agnostic video upstream adaption ("万能橡皮泥").

Turn any upstream video API contract into a few declarative lines and let the
compiler do the rest.  New providers that follow the OpenAI-compatible
``/v1/videos`` shape work out of the box via :data:`DEFAULT_PROFILE`; special
providers are declared either as a :class:`VideoUpstreamProfile` entry below or,
preferably for new families, as a data package under ``contracts/`` (see
``contract_packages``) so fixing one upstream is a data edit with no generator
code change.

除导入期读取 ``contracts/*.json`` 之外没有其它 I/O：编译与解析仍是纯函数，可以单独
测试并被任意网关/运行时复用。
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .contract_packages import (
    ContractPackageError,
    find_duplicate_match_tokens,
    find_match_shadows,
    load_contract_packages,
    resolve_declared_profile,
)
from .channel_wire_contract import (
    WIRE_EVIDENCE_PROBE,
    WIRE_EVIDENCE_SUBMIT,
    WIRE_SOURCE_CHANNEL,
    WIRE_SOURCE_DEFAULT,
    WIRE_SOURCE_SEED,
    ResolvedWireContract,
    contract_field_differences,
    normalize_wire_contract_record,
    record_from_profile,
)

__all__ = [
    "VideoUpstreamProfile",
    "DEFAULT_PROFILE",
    "DOLASD_OPENAI_VIDEO_PROFILE",
    "DOLASD_OPENAI_VIDEO_PROFILE_ID",
    "PROFILES",
    "declared_profile_ids",
    "describe_profile_match",
    "channel_contract_from_probe",
    "derived_contract_adds_information",
    "resolve_wire_contract",
    "relative_wire_route",
    "resolve_profile",
    "align_duration",
    "convert_ratio",
    "compile_payload",
    "compiled_duration",
    "compiled_aspect_ratio",
    "parse_task_response",
    "ParsedVideoTask",
    "TaskStatus",
]

_RATIO_RE = re.compile(r"^(\d+):(\d+)$")


class TaskStatus(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedVideoTask:
    """Normalized view of any upstream video task response."""

    task_id: str | None
    status: str
    result_url: str | None
    error: str | None
    progress: int | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class VideoUpstreamProfile:
    """Declarative contract describing one upstream video API family.

    Fields:
      profile_id:         stable identifier used in logs/metrics
      match_prefixes:     model keys starting with any of these prefixes
      match_exact:        exact model keys
      create_path:        task submission endpoint (relative)
      query_path:         task status endpoint; ``{task_id}`` is substituted
      duration_field:     payload key carrying seconds (``None`` = omit duration)
      duration_choices:   allowed durations in seconds (``None`` = any)
      ratio_field:        payload key carrying aspect ratio:
                          ``size`` ("16:9") | ``ratio`` ("16:9") |
                          ``resolution`` ("720p") | ``None`` (omit)
      first_frame_field:  payload key for the single first-frame image
                          (``None`` = not supported)
      ref_field:          payload key for array-style reference inputs
                          (``None`` = not supported)
      audio_field:        payload key for native-audio toggle (``None`` = n/a)
      auto_face_field:    payload key for face-preservation toggle (None = n/a)
      extra_static:       extra constant fields merged into every payload
      drop_fields:        internal fields that must never reach this upstream
      status_keys:        candidate keys holding the task status string
      completed_values:   values treated as "completed"
      failed_values:      values treated as "failed"
      url_keys:           candidate keys holding the result/download URL
      progress_keys:      candidate keys holding progress (0-100)
      task_id_keys:       candidate keys holding the task id
    """

    profile_id: str
    match_prefixes: tuple[str, ...] = ()
    match_exact: tuple[str, ...] = ()
    create_path: str = "/v1/videos"
    query_path: str = "/v1/videos/{task_id}"
    duration_field: str | None = "duration"
    duration_choices: tuple[int, ...] | None = None
    ratio_field: str | None = "size"
    first_frame_field: str | None = "image"
    ref_field: str | None = None
    audio_field: str | None = None
    auto_face_field: str | None = None
    extra_static: Mapping[str, Any] = field(default_factory=dict)
    drop_fields: tuple[str, ...] = ()
    status_keys: tuple[str, ...] = ("status", "state")
    completed_values: tuple[str, ...] = ("completed", "success", "succeeded")
    failed_values: tuple[str, ...] = ("failed", "error", "failure")
    url_keys: tuple[str, ...] = (
        "result_url",
        "download_url",
        "video_url",
        "url",
        "content_url",
    )
    progress_keys: tuple[str, ...] = ("progress", "percent")
    task_id_keys: tuple[str, ...] = ("id", "task_id")

    def matches(self, model_key: str) -> bool:
        key = str(model_key or "").strip().lower()
        if key in self.match_exact:
            return True
        return any(key.startswith(prefix.lower()) for prefix in self.match_prefixes)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

# OpenAI-compatible /v1/videos shape (covers huabu/画布, most OpenAI-video
# gateways, and is the safe default for anything unknown).
DEFAULT_PROFILE = VideoUpstreamProfile(
    profile_id="openai_video_default",
    match_prefixes=(),
    match_exact=(),
    duration_field="duration",
    duration_choices=None,
    ratio_field="size",
    first_frame_field="image",
    ref_field=None,
    audio_field=None,
    auto_face_field=None,
)

#: Dola publishes video through an OpenAI-compatible contract even for model
#: IDs that look like the Huimeng Seedance SKU, so the host decides the shape.
DOLASD_OPENAI_VIDEO_PROFILE_ID = "dolasd_openai_video"

DOLASD_OPENAI_VIDEO_PROFILE = VideoUpstreamProfile(
    profile_id=DOLASD_OPENAI_VIDEO_PROFILE_ID,
    create_path="/v1/videos/generations",
    query_path="/v1/videos/{task_id}",
    duration_field="duration",
    ratio_field="ratio",
    first_frame_field="image",
    ref_field="reference_images",
)

# ---------------------------------------------------------------------------
# Declared data packages (contracts/*.json)
# ---------------------------------------------------------------------------

_CONTRACT_PACKAGE_DIR = Path(__file__).resolve().parent / "contracts"

_DECLARED_PROFILES: dict[str, VideoUpstreamProfile] = {
    str(spec["profile_id"]): VideoUpstreamProfile(**spec)
    for package in load_contract_packages(_CONTRACT_PACKAGE_DIR)
    for spec in package.profiles
}


def declared_profile_ids() -> tuple[str, ...]:
    """数据包声明了哪些档案；缺包时用来给出可读的报错。"""

    return tuple(sorted(_DECLARED_PROFILES))


def _declared_profile(profile_id: str) -> VideoUpstreamProfile:
    try:
        return _DECLARED_PROFILES[profile_id]
    except KeyError as exc:
        raise ContractPackageError(
            f"数据包里没有档案 {profile_id!r}；当前可用：{list(declared_profile_ids())}"
        ) from exc


PROFILES: tuple[VideoUpstreamProfile, ...] = (
    # Prompt-Hubs SD family: ``sd2.0*`` (without the dash used by huabu).
    # These models use the public /v1/videos body with a ``ratio`` field and
    # support the same explicit face-preservation flag as Firefly.
    # 合同已搬到数据包 `contracts/prompt-hubs-sd.json`（T-226 第三刀）。
    _declared_profile("prompt_hubs_sd"),
    # 画布「满血」SD 2.5-M 系列（sd-2.5-M-720P-v1 等）：文档标称可选
    # 5/10/15/30 秒。命中靠「sd-2.5-m 比 sd-2 更具体」取胜，不再依赖排列顺序 ——
    # 2026-09-12 那次 30 秒被吸成 15 秒就是顺序写错一次造成的。
    # 合同已搬到数据包 `contracts/sd2-5-m-full.json`（图片字段名 first_image 亦在该包注释里）。
    _declared_profile("sd2_5_m_full"),
    # huabu / 画布 (983 文档): sd-2.0-fast / sd-2.0-fast-v1, 5/10/15s。
    # 合同已搬到数据包 `contracts/huabu-sd2.json`（T-226 第一刀）；顺序仍是代码的
    # 事实，所以必须留在这个位置：sd2_5_m_full 在它前面，prompt_hubs_flex 在它后面。
    _declared_profile("huabu"),
    # Prompt-Hubs Firefly Seedance2: firefly-seedance2-* (480p/720p fast).
    # Billed per second, so durations are free-form (no alignment).
    # 合同已搬到数据包 `contracts/prompt-hubs-firefly.json`（T-226 第三刀）。
    _declared_profile("prompt_hubs_firefly"),
    # 卡藏 933 Fast: the public model id starts with an upper-case ``S`` but
    # otherwise follows the Firefly multi-reference contract exactly.
    # 合同已搬到数据包 `contracts/kacang-933-fast.json`；它与上一条逐字段相同、
    # 只差命中规则，两者的关系由 tests 里的漂移守卫钉住（不合并成共享基类）。
    _declared_profile("kacang_933_fast"),
    # Prompt-Hubs flex family (veo-3.1*, kling-*, runway-*, sora-*):
    # duration/ratio are normalized by the caller to the exact model contract.
    # 合同已搬到数据包 `contracts/prompt-hubs-flex.json`。它与上面两条同门，但
    # 刻意不带 auto_face / generate_audio / 强制 480p —— 差别由漂移守卫显式钉住。
    _declared_profile("prompt_hubs_flex"),
    # Seedance2 native family: snake_case duration_seconds + aspect_ratio.
    # 合同已搬到数据包 `contracts/seedance2-native.json`（T-226 第二刀）。它是第一条
    # 带路径模板的合同，用来证明数据包不只够改字段名。
    _declared_profile("seedance2_native"),
    # HappyHorse 1.0 (legacy t2v/i2v): seconds field, metadata wrapper.
    # 合同已搬到数据包 `contracts/happyhorse.json`；它的字段名与别家毫无共性，单列一包。
    _declared_profile("happyhorse"),
)


def _assert_unique_match_tokens(profiles: Sequence[VideoUpstreamProfile]) -> None:
    """两条档案抢同一个命中词时直接报错，而不是靠顺序静默裁决。"""

    duplicates = find_duplicate_match_tokens(
        (profile.profile_id, profile.match_prefixes, profile.match_exact) for profile in profiles
    )
    if duplicates:
        detail = "; ".join(f"{token!r} 被 {list(ids)} 同时声明" for token, ids in sorted(duplicates.items()))
        raise ContractPackageError(f"出线档案命中词冲突：{detail}")


def _declarations(
    profiles: Sequence[VideoUpstreamProfile],
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    return tuple(
        (profile.profile_id, profile.match_prefixes, profile.match_exact) for profile in profiles
    )


# 已知且**有意**的命中词覆盖：更长的词赢，被压住的短词只是兜底。
#
# 这几条不是「顺手记一笔」，而是地基的一部分：huabu 的 ``sd2`` / ``sd-2`` 是为
# sd-2.0-fast 写的粗口径前缀，会盖住同族更新的型号。2026-09-12 的真实事故是
# sd-2.5-M*（标称 30 秒）被这条前缀吸走、30 秒被压成 15 秒；当天的修法是「把
# 档案排前面」——那是靠顺序，重排一次就会复发。现在改成按具体度裁决，并由本表
# 显式登记覆盖关系；将来再加一条新前缀盖住别人时会**导入期报错**，逼出决定。
_ACKNOWLEDGED_SHADOWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "sd-2",
        "huabu",
        "sd-2.5-m",
        "sd2_5_m_full",
        "sd-2.5-M* 标称 5/10/15/30 秒，必须胜过 huabu 为 sd-2.0-fast 写死的 5/10/15（2026-09-12 实测事故）",
    ),
    (
        "sd2",
        "huabu",
        "sd2.",
        "prompt_hubs_sd",
        "Prompt-Hubs 的 sd2.0* 走带 ratio/auto_face 的报文，与 huabu 的 sd-2.0-fast 不是同一份合同",
    ),
    (
        "sd2",
        "huabu",
        "sd2fast",
        "prompt_hubs_sd",
        "sd2fast 同属 Prompt-Hubs sd2.0* 口径",
    ),
)

_ACKNOWLEDGED_SHADOW_KEYS = frozenset(item[:4] for item in _ACKNOWLEDGED_SHADOWS)


def _assert_acknowledged_shadows(profiles: Sequence[VideoUpstreamProfile]) -> None:
    """新的前缀覆盖关系必须显式登记；没登记的当场报错。"""

    unacknowledged = [
        shadow
        for shadow in find_match_shadows(_declarations(profiles))
        if shadow.key not in _ACKNOWLEDGED_SHADOW_KEYS
    ]
    if unacknowledged:
        detail = "; ".join(
            f"{shadow.winner_token!r}({shadow.winner_profile}) 盖住了 "
            f"{shadow.loser_token!r}({shadow.loser_profile})"
            for shadow in unacknowledged
        )
        raise ContractPackageError(
            f"出线档案出现未登记的命中覆盖：{detail}。"
            "请把模型 id 写得更具体，或在本模块 _ACKNOWLEDGED_SHADOWS 里登记并说明理由。"
        )

    stale = _ACKNOWLEDGED_SHADOW_KEYS - {
        shadow.key for shadow in find_match_shadows(_declarations(profiles))
    }
    if stale:
        raise ContractPackageError(f"_ACKNOWLEDGED_SHADOWS 有过期条目：{sorted(stale)}")


_assert_unique_match_tokens(PROFILES)
_assert_acknowledged_shadows(PROFILES)


def describe_profile_match(model_key: str) -> dict[str, Any]:
    """解释一个模型 id 为什么命中某条档案：命中词、是否精确、是否走了兜底。"""

    resolved = resolve_declared_profile(_declarations(PROFILES), model_key)
    if resolved is None:
        return {
            "model_key": model_key,
            "profile_id": DEFAULT_PROFILE.profile_id,
            "matched_token": None,
            "exact": False,
            "fallback": True,
        }
    profile_id, token, is_exact = resolved
    return {
        "model_key": model_key,
        "profile_id": profile_id,
        "matched_token": token,
        "exact": is_exact,
        "fallback": False,
    }


def resolve_profile(model_key: str) -> VideoUpstreamProfile:
    """Return the best-matching profile, falling back to DEFAULT_PROFILE.

    裁决只看「命中词有多具体」，与档案在 ``PROFILES`` 里的先后无关；所以重排档案
    不会改变任何模型走哪份合同。未知模型照旧由 OpenAI 兼容默认档案兜底，
    只要对方的网关说 ``/v1/videos`` 就能用。

    .. note::

       这是**按名字猜**的路径，源码里的 8 个包只是未验证种子。渠道条目自带合同
       时走 :func:`resolve_wire_contract`，那份才是权威。
    """

    resolved = resolve_declared_profile(_declarations(PROFILES), model_key)
    if resolved is not None:
        profile_id = resolved[0]
        for profile in PROFILES:
            if profile.profile_id == profile_id:
                return profile
    return DEFAULT_PROFILE


def _seed_profile_for(model_key: str) -> tuple[str, VideoUpstreamProfile] | None:
    """按名字在源码种子里找一条；找不到返回 None，不猜通用默认。"""

    resolved = resolve_declared_profile(_declarations(PROFILES), model_key)
    if resolved is None:
        return None
    profile_id = resolved[0]
    for profile in PROFILES:
        if profile.profile_id == profile_id:
            return profile_id, profile
    return None


def resolve_wire_contract(
    model_key: str,
    stored: Mapping[str, Any] | None = None,
    *,
    base_url: str = "",
    protocol: str = "",
) -> ResolvedWireContract:
    """按固定三级顺序解析出线合同：渠道合同 → 源码种子 → 通用兜底。

    ``stored`` 是渠道条目上存下来的那份合同（``direct_video_models`` 里的
    ``wireContract`` 字段）。只要它存在并且合法，种子**不参与**本次解析 —— 通道删掉
    渠道，这份合同就自然没有了；重新添加渠道，它又随渠道写回来。
    """

    seed = _seed_profile_for(model_key)
    seed_id, seed_profile = seed if seed else (None, None)
    seed_source = WIRE_SOURCE_SEED if seed_id else None

    if stored:
        record = normalize_wire_contract_record(stored)
        channel_profile = VideoUpstreamProfile(**record["fields"])
        return ResolvedWireContract(
            profile=channel_profile,
            profile_id=channel_profile.profile_id,
            source=WIRE_SOURCE_CHANNEL,
            evidence=str(record.get("evidence") or ""),
            evidence_at=record.get("evidenceAt"),
            notes=tuple(record.get("notes") or ()),
            seed_profile_id=seed_id,
            seed_source=seed_source,
            conflicts_with_seed=contract_field_differences(channel_profile, seed_profile),
        )

    hostname = (urlsplit(str(base_url or "").strip()).hostname or "").lower()
    if hostname == "dolasd.xyz" and str(protocol or "").strip().lower() == "openai-video":
        return ResolvedWireContract(
            profile=DOLASD_OPENAI_VIDEO_PROFILE,
            profile_id=DOLASD_OPENAI_VIDEO_PROFILE.profile_id,
            source=WIRE_SOURCE_DEFAULT,
            seed_profile_id=seed_id,
            seed_source=seed_source,
            conflicts_with_seed=contract_field_differences(
                DOLASD_OPENAI_VIDEO_PROFILE, seed_profile
            ),
        )

    if seed_profile is not None:
        return ResolvedWireContract(
            profile=seed_profile,
            profile_id=str(seed_id),
            source=WIRE_SOURCE_SEED,
            seed_profile_id=seed_id,
            seed_source=seed_source,
        )

    return ResolvedWireContract(
        profile=DEFAULT_PROFILE,
        profile_id=DEFAULT_PROFILE.profile_id,
        source=WIRE_SOURCE_DEFAULT,
    )


def relative_wire_route(path: object, base_url: object) -> str:
    """把档案里相对上游根域写的路径，落到这台机器配置的 base URL 上。

    档案按官方文档记的是 ``/v1/videos/generations`` 这种**相对根域**的路径；
    渠道里填的 base URL 往往已经带了版本目录（``https://host/v1``）。两段直接拼
    会得到 ``/v1/v1/...``，所以重合的前缀要去掉一次。base 没带版本目录时原样保留，
    ``/v1/videos`` 这类默认档案因此还是落在 ``/v1/...`` 上。
    """

    value = "/" + str(path or "").strip().strip("/")
    if value == "/":
        return ""
    base_path = urlsplit(str(base_url or "").strip()).path.rstrip("/")
    if base_path and (value == base_path or value.startswith(base_path + "/")):
        value = value[len(base_path) :]
    normalized = "/" + value.strip("/") if value.strip("/") else ""
    return normalized


def channel_contract_from_probe(
    *,
    protocol: object,
    runtime_verified: bool = False,
) -> dict[str, Any] | None:
    """从探测证据推导一份**渠道合同**；推不出来就返回 ``None``，不编。

    只有 OpenAI 兼容（``/v1/videos``）这条路会用档案编译报文，所以只有它能从探测
    推出合同。MiniMax 原生 v2 与 AutoDL ComfyUI 的传输合同已经按协议挂在渠道上
    （``direct_video_protocol_contracts``），探测阶段推不出额外的档案字段。

    ``runtime_verified`` 表示这条渠道有**真实提交过**的记录；只有那种情况才把证据
    级别写成 ``submit``。只读一次 ``GET /models`` 只能算 ``probe`` —— 目录里有这个
    模型不等于报文能提交成功。

    **还要更具体才许写**。目录探测（``GET /models``）看不到任何字段名，这里能给出的
    只是通用默认档案；把它当成「这条渠道自己的合同」写进渠道，会盖掉按名字命中的那份
    更具体的源码种子，等于用机器猜的东西顶掉真数据。所以当推导结果和通用兜底档案一样
    时直接返回 ``None``：不编合同，让三级解析照常走到种子那一层。这条规则是 2026-10-02
    真机 8784 验收抓到的 —— 用户原样保存一次列表，`seedance-2.5` 渠道就被写进了一份
    `openai_video_default`，把它自己的 `seedance2_native` 种子顶掉了。
    """

    from .direct_video_protocol_contracts import (
        DIRECT_VIDEO_PROTOCOL_OPENAI,
        normalize_direct_video_protocol,
    )

    try:
        normalized = normalize_direct_video_protocol(protocol)
    except ValueError:
        return None
    if normalized != DIRECT_VIDEO_PROTOCOL_OPENAI:
        return None
    derived = record_from_profile(
        DEFAULT_PROFILE,
        evidence=WIRE_EVIDENCE_SUBMIT if runtime_verified else WIRE_EVIDENCE_PROBE,
        notes=[
            "由渠道探测自动写入：上游按 OpenAI 兼容 /v1/videos 出线，字段取通用默认档案。",
            "目录探测只证明模型在目录里，不证明报文能提交成功，所以未提交验证时标记为未验证。",
        ],
    )
    if not derived_contract_adds_information(derived):
        return None
    return derived


def derived_contract_adds_information(record: Mapping[str, Any] | None) -> bool:
    """探测推出来的合同，是否比三级解析本来就有的东西更具体。

    和通用兜底档案**逐字段一样**就说明：把它写进渠道只会多一层「机器写的东西」，
    盖掉按名字命中的那份种子，一点新信息都不加。那种情况不算「推导出来了」。
    """

    if not record:
        return False
    fields = record.get("fields") if isinstance(record, Mapping) else None
    if not isinstance(fields, Mapping):
        return False
    baseline = record_from_profile(DEFAULT_PROFILE).get("fields")
    if set(fields) != set(baseline or {}):
        # 字段不全就不是一份完整合同，谈不上「更具体」。
        return False
    return dict(fields) != dict(baseline or {})


def align_duration(
    seconds: float | int | str | None,
    choices: Sequence[int] | None,
) -> int:
    """Round seconds to the closest allowed choice (or ceil to int if free)."""
    try:
        value = float(seconds or 5)
    except (TypeError, ValueError):
        value = 5.0
    if not choices:
        return int(math.ceil(value)) or 1
    best = int(choices[0])
    best_dist = abs(float(best) - value)
    for choice in choices:
        dist = abs(float(choice) - value)
        if dist < best_dist:
            best = int(choice)
            best_dist = dist
    return best


def convert_ratio(aspect_ratio: str | None, field_kind: str | None) -> str | None:
    """Map an internal aspect ratio ("16:9") to the upstream field format."""
    if not field_kind:
        return None
    ratio = str(aspect_ratio or "").strip() or "16:9"
    match = _RATIO_RE.match(ratio)
    if not match:
        # Already a token like 720p/1080p.
        return ratio if field_kind == "resolution" else None
    # ``aspect_ratio`` 与 ``size`` / ``ratio`` 一样是「直接放 "16:9" 这种字面量」的字段。
    # 漏掉它会让数据包里声明了 `ratio_field: "aspect_ratio"` 的家族（seedance2_native）
    # 静默不带画幅 —— 元数据写着 9:16、报文里什么都没有，属于「骗人」。
    if field_kind in ("size", "ratio", "aspect_ratio"):
        return ratio
    if field_kind == "resolution":
        _, height = int(match.group(1)), int(match.group(2))
        if height <= 0:
            return "480p"
        # 16:9 -> 720p, 9:16 -> 720p (short edge), 1:1 -> 720p
        return "480p" if height <= 540 else "720p"
    return None


def compile_payload(
    *,
    model_key: str,
    prompt: str,
    duration_seconds: float | int | str | None = None,
    aspect_ratio: str | None = None,
    first_frame_uri: str | None = None,
    reference_uris: Sequence[str] = (),
    auto_face: bool = False,
    generate_audio: bool = False,
    profile: VideoUpstreamProfile | None = None,
) -> dict[str, Any]:
    """Compile the provider-neutral request into an upstream payload.

    This is the "clay" step: every field the upstream does not declare is
    dropped, every field it declares is mapped to its exact key, and duration
    is aligned to the declared choices.
    """
    profile = profile or resolve_profile(model_key)
    payload: dict[str, Any] = {"model": model_key, "prompt": str(prompt or "")}

    if profile.duration_field is not None and duration_seconds is not None:
        payload[profile.duration_field] = align_duration(
            duration_seconds, profile.duration_choices
        )

    ratio = convert_ratio(aspect_ratio, profile.ratio_field)
    if ratio is not None:
        payload[profile.ratio_field] = ratio  # type: ignore[index]

    if first_frame_uri and profile.first_frame_field is not None:
        payload[profile.first_frame_field] = first_frame_uri
    if profile.ref_field is not None and reference_uris:
        payload[profile.ref_field] = list(reference_uris)
    if profile.auto_face_field is not None:
        # Prompt-Hubs distinguishes an omitted flag from an explicit ``false``;
        # preserve the generator contract in both directions.
        payload[profile.auto_face_field] = bool(auto_face)
    if profile.audio_field is not None and generate_audio:
        payload[profile.audio_field] = True

    for key, value in profile.extra_static.items():
        payload.setdefault(key, value)
    for key in profile.drop_fields:
        payload.pop(key, None)
    return payload


def compiled_duration(payload: Mapping[str, Any], fallback: float | int) -> int:
    """Read a profile-compiled duration without assuming one field spelling."""

    for key in ("duration", "duration_seconds", "seconds"):
        value = payload.get(key)
        try:
            parsed = int(math.ceil(float(value)))
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return max(1, int(math.ceil(float(fallback or 1))))


def compiled_aspect_ratio(payload: Mapping[str, Any], fallback: str = "16:9") -> str:
    """Read a profile-compiled ratio without requiring a provider field."""

    for key in ("ratio", "size", "aspect_ratio"):
        value = str(payload.get(key) or "").strip()
        if _RATIO_RE.fullmatch(value):
            return value
    return str(fallback or "16:9")


def _first_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def parse_task_response(
    raw: Mapping[str, Any],
    profile: VideoUpstreamProfile | None = None,
) -> ParsedVideoTask:
    """Tolerant parsing of any upstream task payload into ParsedVideoTask.

    Handles wrapper shapes ({"data": {...}}, {"task": {...}}) and multiple
    naming conventions for status/url/progress.
    """
    profile = profile or DEFAULT_PROFILE
    data = raw
    if isinstance(data, Mapping):
        for wrapper in ("data", "task", "result"):
            if wrapper in data and isinstance(data[wrapper], Mapping):
                inner = data[wrapper]
                if _first_value(inner, profile.status_keys) is not None:
                    data = inner
                    break

    status_raw = str(_first_value(data, profile.status_keys) or "").strip().lower()
    if status_raw in profile.completed_values:
        status = TaskStatus.COMPLETED
    elif status_raw in profile.failed_values:
        status = TaskStatus.FAILED
    elif status_raw in ("pending", "queued", "waiting"):
        status = TaskStatus.PENDING
    elif status_raw in ("running", "in_progress", "processing"):
        status = TaskStatus.RUNNING
    else:
        status = TaskStatus.UNKNOWN

    result_url = _first_value(data, profile.url_keys)
    if not result_url:
        result_url = _first_value(data, ("result",))
        if isinstance(result_url, Mapping):
            result_url = _first_value(result_url, profile.url_keys)

    task_id = _first_value(data, profile.task_id_keys)
    progress = _first_value(data, profile.progress_keys)
    try:
        progress = int(progress) if progress is not None else None
    except (TypeError, ValueError):
        progress = None

    error = None
    err = data.get("error") if isinstance(data, Mapping) else None
    if isinstance(err, Mapping):
        error = err.get("message") or err.get("code") or str(err)
    elif err:
        error = str(err)

    return ParsedVideoTask(
        task_id=str(task_id) if task_id is not None else None,
        status=status,
        result_url=str(result_url) if result_url else None,
        error=error,
        progress=progress,
        raw=dict(data),
    )
