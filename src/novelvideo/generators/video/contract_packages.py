"""视频出线合同的运行时数据包。

中性请求模型仍然留在 Python 代码里；数据包只回答一件事：这份中性请求该怎样
落到某一个上游 API 上 —— 命中规则、字段名、档位对齐、任务状态怎么读。

边界（T-226 JEV 判定，2026-10-01）：不做整套渠道插件层，也不照搬参考项目
BeefTV 的 manifest 格式。只做「合同是数据」这一刀，够用即止。

数据包是随源码分发的普通 JSON，不联网下载、不执行代码；缺字段一律回落到
``VideoUpstreamProfile`` 自己的默认值，所以「没写」和「写了 null」不会混淆。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "AUTHORITY_SEED",
    "ContractPackageError",
    "LoadedContractPackage",
    "MatchShadow",
    "load_contract_packages",
    "find_duplicate_match_tokens",
    "find_match_shadows",
    "normalize_profile_fields",
    "resolve_declared_profile",
]

SCHEMA_VERSION = 1

#: 随源码分发的数据包只有这一种权威级别：**种子**。它描述的是「这家上游大概长这样」，
#: 不是「这台机器上配置的渠道就是这个样」。权威的那份存在渠道条目里，随删随走。
AUTHORITY_SEED = "seed"

_PACKAGE_KEYS = frozenset({"schemaVersion", "packageId", "authority", "notes", "profiles"})
_PROFILE_KEYS = frozenset(
    {
        "profile_id",
        "match_prefixes",
        "match_exact",
        "create_path",
        "query_path",
        "duration_field",
        "duration_choices",
        "ratio_field",
        "first_frame_field",
        "ref_field",
        "audio_field",
        "auto_face_field",
        "extra_static",
        "drop_fields",
        "status_keys",
        "completed_values",
        "failed_values",
        "url_keys",
        "progress_keys",
        "task_id_keys",
    }
)
_STRING_TUPLES = (
    "match_prefixes",
    "match_exact",
    "drop_fields",
    "status_keys",
    "completed_values",
    "failed_values",
    "url_keys",
    "progress_keys",
    "task_id_keys",
)
_OPTIONAL_STRINGS = (
    "create_path",
    "query_path",
    "duration_field",
    "ratio_field",
    "first_frame_field",
    "ref_field",
    "audio_field",
    "auto_face_field",
)


class ContractPackageError(ValueError):
    """数据包不合法。错误信息带上文件与字段，不静默跳过。"""


@dataclass(frozen=True)
class LoadedContractPackage:
    """一个已校验的数据包；profiles 是可直接构造档案的字段字典。"""

    package_id: str
    path: Path
    authority: str
    notes: tuple[str, ...]
    profiles: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class MatchShadow:
    """一条档案的命中词盖住了另一条的。

    ``winner_token`` 比 ``loser_token`` 更具体（更长，或同样是长词但是精确匹配），
    所以任何同时命中两者的模型 id 都会归到 winner。被压住的那条档案必须显式登记，
    否则新加一个前缀就会悄悄抢走已有模型。
    """

    loser_token: str
    loser_profile: str
    winner_token: str
    winner_profile: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.loser_token, self.loser_profile, self.winner_token, self.winner_profile)


def _require_object(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractPackageError(f"{where} 必须是 JSON 对象")
    return value


def _reject_unknown_keys(data: Mapping[str, Any], allowed: frozenset[str], *, where: str) -> None:
    unknown = sorted(str(key) for key in data if str(key) not in allowed)
    if unknown:
        raise ContractPackageError(f"{where} 出现未知字段 {unknown}；拼错字段名会被静默忽略，所以这里直接拒绝")


def _require_string(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractPackageError(f"{where} 必须是非空字符串")
    return value


def _string_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractPackageError(f"{where} 必须是字符串数组")
    items = tuple(_require_string(item, where=f"{where}[]") for item in value)
    if len(set(items)) != len(items):
        raise ContractPackageError(f"{where} 存在重复项")
    return items


def _optional_string(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, where=where)


def _duration_choices(value: Any, *, where: str) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractPackageError(f"{where} 必须是整数数组或 null")
    choices: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ContractPackageError(f"{where} 只能放正整数秒")
        choices.append(int(item))
    if len(set(choices)) != len(choices):
        raise ContractPackageError(f"{where} 存在重复档位")
    return tuple(choices)


def normalize_profile_fields(
    raw: Mapping[str, Any],
    *,
    where: str,
    require_match_rules: bool = True,
) -> Mapping[str, Any]:
    """校验一组档案字段，返回可直接构造 ``VideoUpstreamProfile`` 的字典。

    ``require_match_rules=False`` 是给**渠道合同**用的：挂在渠道条目上的合同不是靠
    名字命中的，所以它可以、而且必须不带命中规则。
    """

    _reject_unknown_keys(raw, _PROFILE_KEYS, where=where)

    spec: dict[str, Any] = {"profile_id": _require_string(raw.get("profile_id"), where=f"{where}.profile_id")}
    for key in _STRING_TUPLES:
        if key in raw:
            spec[key] = _string_tuple(raw[key], where=f"{where}.{key}")
    for key in _OPTIONAL_STRINGS:
        if key in raw:
            spec[key] = _optional_string(raw[key], where=f"{where}.{key}")
    if "duration_choices" in raw:
        spec["duration_choices"] = _duration_choices(raw["duration_choices"], where=f"{where}.duration_choices")
    if "extra_static" in raw:
        extra = _require_object(raw["extra_static"], where=f"{where}.extra_static")
        spec["extra_static"] = {str(key): value for key, value in extra.items()}

    if require_match_rules and not spec.get("match_prefixes") and not spec.get("match_exact"):
        raise ContractPackageError(f"{where} 没有任何命中规则（match_prefixes 或 match_exact），这条档案永远不会生效")
    if not require_match_rules and (spec.get("match_prefixes") or spec.get("match_exact")):
        raise ContractPackageError(
            f"{where} 是渠道合同，不该带命中规则；命中规则只属于随源码分发的种子包"
        )
    return spec


def _normalize_profile(raw: Mapping[str, Any], *, where: str) -> Mapping[str, Any]:
    return normalize_profile_fields(raw, where=where)


def load_contract_packages(directory: str | Path) -> tuple[LoadedContractPackage, ...]:
    """读取目录下所有 ``*.json`` 数据包。按文件名排序，读到的顺序可复现。"""

    root = Path(directory)
    if not root.is_dir():
        return ()

    packages: list[LoadedContractPackage] = []
    seen_ids: dict[str, Path] = {}
    seen_profiles: dict[str, Path] = {}
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractPackageError(f"{path.name} 读不了或不是合法 JSON：{exc}") from exc
        data = _require_object(raw, where=path.name)
        _reject_unknown_keys(data, _PACKAGE_KEYS, where=path.name)

        version = data.get("schemaVersion")
        if version != SCHEMA_VERSION:
            raise ContractPackageError(
                f"{path.name} 的 schemaVersion 是 {version!r}，本代码只认 {SCHEMA_VERSION}"
            )
        package_id = _require_string(data.get("packageId"), where=f"{path.name}.packageId")
        if package_id in seen_ids:
            raise ContractPackageError(f"{path.name} 的 packageId {package_id!r} 与 {seen_ids[package_id].name} 重复")
        seen_ids[package_id] = path

        authority = str(data.get("authority") or "").strip().lower()
        if authority != AUTHORITY_SEED:
            raise ContractPackageError(
                f"{path.name} 必须声明 authority={AUTHORITY_SEED!r}（当前 {authority!r}）。"
                "随源码分发的合同只是未经验证的种子；权威合同挂在渠道条目上。"
            )

        notes_raw = data.get("notes", [])
        notes = _string_tuple(notes_raw, where=f"{path.name}.notes") if notes_raw else ()

        profiles_raw = data.get("profiles")
        if not isinstance(profiles_raw, Sequence) or isinstance(profiles_raw, (str, bytes)) or not profiles_raw:
            raise ContractPackageError(f"{path.name}.profiles 必须是非空数组")

        profiles: list[Mapping[str, Any]] = []
        for index, item in enumerate(profiles_raw):
            where = f"{path.name}.profiles[{index}]"
            spec = _normalize_profile(_require_object(item, where=where), where=where)
            profile_id = str(spec["profile_id"])
            if profile_id in seen_profiles:
                raise ContractPackageError(
                    f"{where} 的 profile_id {profile_id!r} 与 {seen_profiles[profile_id].name} 重复"
                )
            seen_profiles[profile_id] = path
            profiles.append(spec)

        packages.append(
            LoadedContractPackage(
                package_id=package_id,
                path=path,
                authority=authority,
                notes=notes,
                profiles=tuple(profiles),
            )
        )
    return tuple(packages)


def find_duplicate_match_tokens(
    declarations: Iterable[tuple[str, Sequence[str], Sequence[str]]],
) -> dict[str, tuple[str, ...]]:
    """找出被多条档案同时声明的命中词。

    两条档案声明同一个词时，谁生效只取决于排序，改一行顺序就静默改行为 ——
    2026-09-12 的时长截断事故就是这一类。这里让它在导入期直接报出来。
    """

    owners: dict[str, list[str]] = {}
    for profile_id, prefixes, exacts in declarations:
        for token in list(prefixes) + list(exacts):
            normalized = str(token).strip().casefold()
            if not normalized:
                continue
            owners.setdefault(normalized, []).append(str(profile_id))
    return {token: tuple(ids) for token, ids in owners.items() if len(ids) > 1}


def _token_declarations(
    declarations: Iterable[tuple[str, Sequence[str], Sequence[str]]],
) -> list[tuple[str, str, bool]]:
    """展开成 ``(token, profile_id, 是否精确匹配)`` 的三元组。"""

    tokens: list[tuple[str, str, bool]] = []
    for profile_id, prefixes, exacts in declarations:
        for token in exacts:
            normalized = str(token).strip().casefold()
            if normalized:
                tokens.append((normalized, str(profile_id), True))
        for token in prefixes:
            normalized = str(token).strip().casefold()
            if normalized:
                tokens.append((normalized, str(profile_id), False))
    return tokens


def find_match_shadows(
    declarations: Iterable[tuple[str, Sequence[str], Sequence[str]]],
) -> tuple[MatchShadow, ...]:
    """找出「一条档案的命中词盖住另一条」的全部组合。

    判定只用到两个客观事实：命中词之间是否存在前缀关系，以及谁更具体。
    不含档案在列表里的先后 —— 顺序不再参与裁决，所以重排档案不会改变行为。
    """

    tokens = _token_declarations(declarations)
    shadows: list[MatchShadow] = []
    for loser_token, loser_profile, _ in tokens:
        for winner_token, winner_profile, _ in tokens:
            if loser_profile == winner_profile:
                continue
            if len(winner_token) <= len(loser_token):
                continue
            if not winner_token.startswith(loser_token):
                continue
            shadows.append(
                MatchShadow(
                    loser_token=loser_token,
                    loser_profile=loser_profile,
                    winner_token=winner_token,
                    winner_profile=winner_profile,
                )
            )
    return tuple(sorted(set(shadows), key=lambda item: item.key))


def resolve_declared_profile(
    declarations: Sequence[tuple[str, Sequence[str], Sequence[str]]],
    model_key: str,
) -> tuple[str, str, bool] | None:
    """按「越具体越优先」解析命中，返回 ``(profile_id, 命中词, 是否精确)``。

    规则只有一条：命中词更长者胜；长度相同时精确匹配胜出。因为等长且互相为前缀
    的命中词必然字面相同（那种情况已被重复检测拦下），所以这个排序是全序，
    结果与档案顺序无关。
    """

    key = str(model_key or "").strip().casefold()
    if not key:
        return None
    best: tuple[int, int, str, str, bool] | None = None
    for token, profile_id, is_exact in _token_declarations(declarations):
        if is_exact:
            matched = key == token
        else:
            matched = key.startswith(token)
        if not matched:
            continue
        rank = (1 if is_exact else 0, len(token))
        if best is None or rank > best[0:2]:
            best = (rank[0], rank[1], token, profile_id, is_exact)
    if best is None:
        return None
    return (best[3], best[2], best[4])
