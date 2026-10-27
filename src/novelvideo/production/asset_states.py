"""Versioned asset states (``C01@wet``) as first-class, image-backed facts.

Dream's paid tests found that "the coat was torn in the previous shot and clean
in the next" is one of the first things an audience notices.  A state is
therefore an independent image plus a locked descriptor, not an adjective in a
prompt.  This module owns validation, reference syntax, regression checks and
prompt-coverage checks; it does not generate images or own the asset store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ASSET_STATE_SCHEMA = "village.asset-states.v1"
STATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_NORMALIZE_RE = re.compile(r"\s+")


class AssetStateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AssetState:
    state_id: str
    path: str
    sha256: str = ""
    locked: bool = False
    descriptor: str = ""

    def reference(self, asset_id: str) -> str:
        return f"{asset_id}@{self.state_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "path": self.path,
            "sha256": self.sha256,
            "locked": self.locked,
            "descriptor": self.descriptor,
        }


def _text(value: object, *, limit: int = 1200) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normalize(value: object) -> str:
    return _NORMALIZE_RE.sub("", str(value or "")).casefold()


def normalize_asset_states(
    value: object,
    *,
    asset_id: str = "",
) -> tuple[AssetState, ...]:
    """Parse and validate a ``states[]`` list.

    Raises :class:`AssetStateError` with a stable code so callers can turn the
    failure into a blocking contract issue instead of silently dropping data.
    """

    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise AssetStateError(
            "asset_state.list_invalid", "states must be a list"
        )
    states: list[AssetState] = []
    seen: set[str] = set()
    for position, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise AssetStateError(
                "asset_state.entry_invalid", f"states[{position}] must be an object"
            )
        state_id = _text(raw.get("state_id") or raw.get("stateId") or raw.get("id"), limit=32)
        if not state_id or not STATE_ID_RE.fullmatch(state_id):
            raise AssetStateError(
                "asset_state.id_invalid",
                f"states[{position}] needs a short lowercase id (got {state_id!r})",
            )
        if state_id in seen:
            raise AssetStateError(
                "asset_state.id_duplicate", f"duplicate state id: {state_id}"
            )
        seen.add(state_id)
        path = _text(raw.get("path") or raw.get("image_path") or raw.get("imagePath"), limit=4000)
        if not path:
            raise AssetStateError(
                "asset_state.path_missing",
                f"{asset_id or 'asset'}@{state_id} has no independent image path",
            )
        sha256 = _text(raw.get("sha256"), limit=64).lower()
        if sha256 and not _SHA256_RE.fullmatch(sha256):
            raise AssetStateError(
                "asset_state.sha256_invalid",
                f"{asset_id or 'asset'}@{state_id} sha256 is not a 64-character digest",
            )
        locked = raw.get("locked") is True
        descriptor = _text(raw.get("descriptor"), limit=1200)
        states.append(
            AssetState(
                state_id=state_id,
                path=path,
                sha256=sha256,
                locked=locked,
                descriptor=descriptor,
            )
        )
    return tuple(states)


def validate_asset_state_ledger(
    states: Iterable[AssetState],
    *,
    asset_id: str = "",
) -> list[dict[str, Any]]:
    """Return deterministic issues for states that cannot be used as evidence."""

    issues: list[dict[str, Any]] = []
    for state in states:
        if not state.locked:
            issues.append(
                {
                    "code": "asset_state.not_locked",
                    "asset_id": asset_id,
                    "state_id": state.state_id,
                    "message": f"{asset_id}@{state.state_id} 还没有锁定；未锁定状态不得进入提示词",
                    "fix": "确认独立状态图后写 locked=true。",
                }
            )
        if not state.descriptor:
            issues.append(
                {
                    "code": "asset_state.descriptor_missing",
                    "asset_id": asset_id,
                    "state_id": state.state_id,
                    "message": f"{asset_id}@{state.state_id} 缺少逐字描述；模型无法把状态写进提示词",
                    "fix": "补一段锁定后逐字复制的可见状态描述。",
                }
            )
        if not state.sha256:
            issues.append(
                {
                    "code": "asset_state.sha256_missing",
                    "asset_id": asset_id,
                    "state_id": state.state_id,
                    "message": f"{asset_id}@{state.state_id} 缺少图片哈希；换图无法让下游失效",
                    "fix": "登记状态图 SHA-256。",
                }
            )
    return issues


def transition_issues(
    previous: Iterable[AssetState],
    current: Iterable[AssetState],
    *,
    released: Mapping[str, str] | None = None,
    asset_id: str = "",
) -> list[dict[str, Any]]:
    """Enforce "states only accumulate" between adjacent shots.

    A state that appeared earlier must stay present unless the current shot
    explicitly releases it with a non-empty reason.  Releasing a state that was
    never present is also reported: it usually means the two shots disagree
    about the asset's history.
    """

    before = {state.state_id: state for state in previous}
    after = {state.state_id: state for state in current}
    release_map = {
        str(key).strip(): _text(reason, limit=600)
        for key, reason in (released or {}).items()
        if str(key).strip()
    }
    issues: list[dict[str, Any]] = []
    for state_id in sorted(set(before) - set(after)):
        if state_id not in release_map:
            issues.append(
                {
                    "code": "asset_state.regressed",
                    "asset_id": asset_id,
                    "state_id": state_id,
                    "message": f"{asset_id}@{state_id} 在上一镜已出现，本镜消失且没有 state_release",
                    "fix": "保留该状态，或写清 state_release 的可见理由（恢复、换装、时间跳跃）。",
                }
            )
        elif not release_map[state_id]:
            issues.append(
                {
                    "code": "asset_state.release_without_reason",
                    "asset_id": asset_id,
                    "state_id": state_id,
                    "message": f"{asset_id}@{state_id} 的 state_release 没有理由",
                    "fix": "写清观众能在画面里看到的恢复原因，不接受「剧情需要」。",
                }
            )
    for state_id in sorted(set(release_map) - set(before)):
        issues.append(
            {
                "code": "asset_state.release_unknown",
                "asset_id": asset_id,
                "state_id": state_id,
                "message": f"{asset_id}@{state_id} 被释放，但上一镜并没有登记这个状态",
                "fix": "核对前后镜状态账本，删掉悬空释放或补登上一镜。",
            }
        )
    return issues


def state_prompt_coverage(
    states: Iterable[AssetState],
    prompt: object,
    *,
    asset_id: str = "",
) -> list[dict[str, Any]]:
    """Report locked state descriptors that never reached the shot prompt.

    Only proves the words are present; it does not prove the model rendered the
    state.  Empty descriptors are already reported by the ledger validator.
    """

    normalized_prompt = _normalize(prompt)
    issues: list[dict[str, Any]] = []
    if not normalized_prompt:
        return issues
    for state in states:
        if not state.locked or not state.descriptor:
            continue
        if _normalize(state.descriptor) in normalized_prompt:
            continue
        issues.append(
            {
                "code": "asset_state.prompt_missing",
                "asset_id": asset_id,
                "state_id": state.state_id,
                "message": f"{asset_id}@{state.state_id} 的锁定描述没有出现在本镜提示词里",
                "fix": "把 descriptor 逐字复制进提示词；改写等于换状态。",
            }
        )
    return issues


def state_reference(asset_id: str, state_id: str) -> str:
    if not asset_id or not STATE_ID_RE.fullmatch(state_id or ""):
        raise AssetStateError("asset_state.reference_invalid", "invalid asset/state id")
    return f"{asset_id}@{state_id}"


__all__ = [
    "ASSET_STATE_SCHEMA",
    "AssetState",
    "AssetStateError",
    "normalize_asset_states",
    "state_prompt_coverage",
    "state_reference",
    "transition_issues",
    "validate_asset_state_ledger",
]
