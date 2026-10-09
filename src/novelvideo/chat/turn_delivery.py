"""Evidence-backed completion state for one Village Agent turn.

The turn intent contract describes what the user asked for.  This module
tracks whether the tools that actually ran produced enough evidence to claim
that delivery finished.  It never trusts model prose.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from novelvideo.chat.turn_intent import tool_has_side_effect


TURN_DELIVERY_SCHEMA = "village_turn_delivery.v1"
TURN_DELIVERY_RECEIPT_SCHEMA = "village_turn_delivery_receipt.v1"

DELIVERY_VERIFIED = "verified"
DELIVERY_PENDING = "pending"
DELIVERY_BLOCKED = "blocked"
DELIVERY_UNVERIFIED = "unverified"
DELIVERY_NOT_APPLICABLE = "not_applicable"

_PENDING_STATUSES = frozenset(
    {
        "accepted",
        "queued",
        "pending",
        "running",
        "processing",
        "in_progress",
        "submitted",
    }
)
_SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success", "verified"})
_FAILURE_STATUSES = frozenset(
    {"failed", "error", "cancelled", "canceled", "blocked", "denied"}
)
_RUN_KEYS = ("run_id", "workflow_run_id", "execution_id", "executionId")
_URL_KEYS = (
    "url",
    "video_url",
    "audio_url",
    "image_url",
    "asset_url",
    "media_url",
    "output_url",
    "download_url",
    "result_url",
)
_COMPLETION_CLAIM_RE = re.compile(
    r"已完成|已经完成|已完成交付|已经交付|已生成|已经生成|已制作|已经制作|"
    r"已成功|发布就绪|可以发布|可发布|已完成成片|"
    r"\bcompleted successfully\b|\bfinished successfully\b|"
    r"\bhas been generated\b|\bis ready to publish\b",
    re.IGNORECASE,
)
# 「工作已经存下来了，以后能从断点续上」比「已完成」更坏：它让用户以为东西已经存住，
# 于是只等重试，实际上什么都没发生。2026-09-30 真机里 Agent 被只读回合合同拦下后，
# 正文写的就是「已保留现有内容和恢复点，后续会从失败步骤继续」，而同一消息的机器记录里
# workflow_status=failed、delivery status=verified_failure。事实一直在，只是被这段话盖住了。
_RESUMABILITY_CLAIM_RE = re.compile(
    r"已保留|已保存|已存下|已存档|恢复点|断点|续跑|从失败步骤继续|"
    r"不会重复已?完成|已排队|已入库|随时继续|"
    r"\bresumable\b|\bcheckpoint (?:saved|created)\b|"
    r"\bwill continue from\b|\bpicked up where\b",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;\n])")


def _text(value: object, *, limit: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text.startswith("{"):
        return {}
    try:
        import json

        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _walk_mappings(value: object, *, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 5:
        return []
    payload = _json_mapping(value)
    if not payload:
        return []
    found = [payload]
    for key in (
        "result",
        "data",
        "response",
        "workflow_run",
        "workflowRun",
        "receipt",
        "workflow_receipt",
        "canvas_receipt",
    ):
        child = payload.get(key)
        if isinstance(child, (Mapping, str)):
            found.extend(_walk_mappings(child, depth=depth + 1))
    return found


def _run_id(payload: Mapping[str, Any]) -> str:
    for key in _RUN_KEYS:
        value = _text(payload.get(key), limit=240)
        if value:
            return value
    nested = payload.get("workflow_run")
    if isinstance(nested, Mapping):
        return _run_id(nested)
    return ""


def _status(payload: Mapping[str, Any]) -> str:
    return _text(
        payload.get("status") or payload.get("workflow_status") or payload.get("state"),
        limit=80,
    ).casefold()


def _error_code(payload: Mapping[str, Any]) -> str:
    return _text(
        payload.get("error_code") or payload.get("errorCode") or payload.get("error"),
        limit=240,
    )


def _canvas_receipt(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("server_applied") is not True:
        return None
    if payload.get("readback_verified", True) is not True:
        return None
    revision = payload.get("revision")
    applied_ops = payload.get("applied_ops")
    command_id = _text(payload.get("command_id"), limit=240)
    if isinstance(revision, bool) or not isinstance(revision, int):
        return None
    if isinstance(applied_ops, bool) or not isinstance(applied_ops, int):
        return None
    if revision <= 0 or applied_ops <= 0 or not command_id:
        return None
    return {
        "kind": "canvas_write",
        "source_ref": command_id,
        "revision": revision,
        "applied_ops": applied_ops,
        "server_applied": True,
        "readback_verified": True,
        **(
            {"structure_status": _text(payload.get("structure_status"), limit=120)}
            if _text(payload.get("structure_status"), limit=120)
            else {}
        ),
    }


def _canvas_readback_failure(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("server_applied") is not True:
        return None
    if payload.get("readback_verified", True) is not False:
        return None
    command_id = _text(payload.get("command_id"), limit=240)
    return {
        "kind": "readback_failure",
        "source_ref": command_id or "canvas_readback",
        "revision": payload.get("revision"),
        "applied_ops": payload.get("applied_ops"),
        **(
            {"structure_status": _text(payload.get("structure_status"), limit=120)}
            if _text(payload.get("structure_status"), limit=120)
            else {}
        ),
        "error_code": "canvas_readback_verification_failed",
    }


def _http_url(value: object) -> str:
    text = _text(value, limit=2_000)
    return text if text.startswith(("http://", "https://")) else ""


def _artifact_urls(value: object, *, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    if isinstance(value, Mapping):
        urls: list[str] = []
        for key, child in value.items():
            if str(key) in _URL_KEYS:
                url = _http_url(child)
                if url:
                    urls.append(url)
            urls.extend(_artifact_urls(child, depth=depth + 1))
        return list(dict.fromkeys(urls))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        urls = []
        for child in value:
            urls.extend(_artifact_urls(child, depth=depth + 1))
        return list(dict.fromkeys(urls))
    return []


def _first_text(value: object, keys: tuple[str, ...], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, Mapping):
        for key in keys:
            candidate = _text(value.get(key), limit=240)
            if candidate:
                return candidate
        for child in value.values():
            candidate = _first_text(child, keys, depth=depth + 1)
            if candidate:
                return candidate
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            candidate = _first_text(child, keys, depth=depth + 1)
            if candidate:
                return candidate
    return ""


def _first_sha256(value: object, *, depth: int = 0) -> str:
    text = _first_text(
        value,
        (
            "artifact_sha256",
            "final_file_sha256",
            "output_sha256",
            "content_sha256",
            "sha256",
        ),
        depth=depth,
    ).casefold()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        return ""
    return text


def _first_true(value: object, keys: tuple[str, ...], *, depth: int = 0) -> bool:
    if depth > 5:
        return False

    def marked_true(candidate: object) -> bool:
        if candidate is True:
            return True
        if isinstance(candidate, str):
            return candidate.strip().casefold() in {
                "passed",
                "ready",
                "success",
                "succeeded",
                "true",
                "verified",
            }
        if isinstance(candidate, Mapping):
            return _text(candidate.get("status"), limit=80).casefold() in {
                "passed",
                "ready",
                "success",
                "succeeded",
                "true",
                "verified",
            }
        return False

    if isinstance(value, Mapping):
        for key in keys:
            if marked_true(value.get(key)):
                return True
        for child in value.values():
            if _first_true(child, keys, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_first_true(child, keys, depth=depth + 1) for child in value)
    return False


def _release_status(payload: Mapping[str, Any]) -> str:
    readiness = payload.get("release_readiness")
    if isinstance(readiness, Mapping):
        return _text(readiness.get("status"), limit=80).casefold()
    return ""


def _is_readback_observation(tool_name: str, arguments: Mapping[str, Any]) -> bool:
    if tool_name == "village_canvas_get_workflow_run":
        return True
    if tool_name != "village_canvas_capability":
        return False
    return (
        _text(arguments.get("action"), limit=80).casefold() == "invoke"
        and _text(arguments.get("capability_id"), limit=200) == "workflow.run.get"
    )


@dataclass(frozen=True, slots=True)
class DeliveryObservation:
    tool_name: str
    failed: bool
    payload: dict[str, Any]


@dataclass(slots=True)
class TurnDeliveryRuntime:
    """Collect side-effect evidence and project one honest completion receipt."""

    contract: dict[str, Any] | None = None
    observations: list[DeliveryObservation] = field(default_factory=list)

    def bind_contract(self, contract: Mapping[str, Any] | None) -> None:
        self.contract = dict(contract) if isinstance(contract, Mapping) else None

    def record(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
        result: object,
        *,
        failed: bool | None = None,
    ) -> None:
        name = _text(tool_name, limit=240)
        if not name:
            return
        if not tool_has_side_effect(name, arguments) and not _is_readback_observation(
            name, arguments
        ):
            return
        payload = _json_mapping(result)
        if not payload:
            payload = {"result": result}
        if failed is None:
            failed = payload.get("ok") is False or bool(_error_code(payload))
        self.observations.append(
            DeliveryObservation(
                tool_name=name,
                failed=bool(failed),
                payload=payload,
            )
        )

    def _contract_delivery(self) -> tuple[str, str | None]:
        delivery = _mapping(_mapping(self.contract).get("delivery"))
        mode = _text(delivery.get("mode"), limit=80)
        media_type = delivery.get("media_type")
        return mode, media_type if isinstance(media_type, str) else None

    def _latest_runs(self) -> dict[str, DeliveryObservation]:
        latest: dict[str, DeliveryObservation] = {}
        anonymous: list[DeliveryObservation] = []
        for observation in self.observations:
            receipts = []
            for payload in _walk_mappings(observation.payload):
                run_id = _run_id(payload)
                if run_id:
                    receipts.append((run_id, payload))
            if not receipts:
                anonymous.append(observation)
                continue
            for run_id, payload in receipts:
                previous = latest.get(run_id)
                merged_payload = dict(previous.payload) if previous is not None else {}
                merged_payload.update(observation.payload)
                merged_payload.update(payload)
                latest[run_id] = DeliveryObservation(
                    tool_name=observation.tool_name,
                    failed=observation.failed,
                    payload=merged_payload,
                )
        return {
            **{f"anonymous:{index}": item for index, item in enumerate(anonymous)},
            **latest,
        }

    def receipt(self, *, final_text: str = "") -> dict[str, Any]:
        mode, media_type = self._contract_delivery()
        if not mode:
            return self._receipt(
                status=DELIVERY_NOT_APPLICABLE,
                reason_code="turn_intent_not_frozen",
                delivery_mode="",
                media_type=None,
            )
        if mode == "response":
            return self._receipt(
                status=(
                    DELIVERY_VERIFIED
                    if str(final_text or "").strip()
                    else DELIVERY_UNVERIFIED
                ),
                reason_code=(
                    "response_text_present"
                    if str(final_text or "").strip()
                    else "response_text_missing"
                ),
                delivery_mode=mode,
                media_type=media_type,
            )
        if not self.observations:
            return self._receipt(
                status=DELIVERY_UNVERIFIED,
                reason_code="side_effect_evidence_missing",
                delivery_mode=mode,
                media_type=media_type,
            )

        verified: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for key, observation in self._latest_runs().items():
            payloads = _walk_mappings(observation.payload)
            readback_failure = next(
                (
                    failure
                    for candidate in payloads
                    if (failure := _canvas_readback_failure(candidate)) is not None
                ),
                None,
            )
            if readback_failure is not None and mode == "state_change":
                blocked.append({**readback_failure, "tool": observation.tool_name})
                continue
            canvas = next(
                (
                    receipt
                    for candidate in payloads
                    if (receipt := _canvas_receipt(candidate)) is not None
                ),
                None,
            )
            if canvas is not None and mode == "state_change" and not observation.failed:
                verified.append({**canvas, "tool": observation.tool_name})
                continue

            run_id = next(
                (
                    candidate
                    for candidate in (_run_id(item) for item in payloads)
                    if candidate
                ),
                "",
            )
            status = next(
                (
                    candidate
                    for candidate in (_status(item) for item in payloads)
                    if candidate
                ),
                "",
            )
            release = next(
                (
                    candidate
                    for candidate in (_release_status(item) for item in payloads)
                    if candidate
                ),
                "",
            )
            urls = _artifact_urls(observation.payload)
            error_code = next(
                (
                    candidate
                    for candidate in (_error_code(item) for item in payloads)
                    if candidate
                ),
                "",
            )
            item = {
                "kind": "tool_receipt",
                "tool": observation.tool_name,
                "source_ref": run_id or key,
                "status": status or ("failed" if observation.failed else "unknown"),
                **({"error_code": error_code} if error_code else {}),
                **({"url": urls[0]} if urls else {}),
                **(
                    {
                        "provider_task_id": provider_task_id,
                    }
                    if (
                        provider_task_id := _first_text(
                            observation.payload,
                            (
                                "provider_task_id",
                                "providerTaskId",
                                "provider_job_id",
                                "providerJobId",
                            ),
                        )
                    )
                    else {}
                ),
                **(
                    {
                        "artifact_sha256": artifact_sha256,
                    }
                    if (artifact_sha256 := _first_sha256(observation.payload))
                    else {}
                ),
                **(
                    {
                        "artifact_id": artifact_id,
                    }
                    if (
                        artifact_id := _first_text(
                            observation.payload,
                            ("artifact_id", "asset_id"),
                        )
                    )
                    else {}
                ),
                **(
                    {"artifact_readback": True}
                    if _first_true(
                        observation.payload,
                        ("artifact_readback", "readback_verified", "file_readback"),
                    )
                    else {}
                ),
            }
            if observation.failed or status in _FAILURE_STATUSES:
                blocked.append(item)
            elif release in {"blocked", "unverified"}:
                blocked.append({**item, "reason_code": f"release_{release}"})
            elif mode == "async_artifact" and status in _SUCCESS_STATUSES and urls:
                verified.append(item)
            elif status in _PENDING_STATUSES or (
                mode == "async_artifact" and status in _SUCCESS_STATUSES
            ):
                pending.append(item)

        if verified:
            return self._receipt(
                status=DELIVERY_VERIFIED,
                reason_code="terminal_tool_evidence_present",
                delivery_mode=mode,
                media_type=media_type,
                verified=verified,
            )
        if blocked:
            return self._receipt(
                status=DELIVERY_BLOCKED,
                reason_code="tool_delivery_failed_or_blocked",
                delivery_mode=mode,
                media_type=media_type,
                blocked=blocked,
            )
        if pending:
            return self._receipt(
                status=DELIVERY_PENDING,
                reason_code="terminal_delivery_evidence_pending",
                delivery_mode=mode,
                media_type=media_type,
                pending=pending,
            )
        return self._receipt(
            status=DELIVERY_UNVERIFIED,
            reason_code="terminal_tool_evidence_missing",
            delivery_mode=mode,
            media_type=media_type,
        )

    def _receipt(
        self,
        *,
        status: str,
        reason_code: str,
        delivery_mode: str,
        media_type: str | None,
        verified: Sequence[Mapping[str, Any]] = (),
        pending: Sequence[Mapping[str, Any]] = (),
        blocked: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return {
            "schema": TURN_DELIVERY_RECEIPT_SCHEMA,
            "status": status,
            "reason_code": reason_code,
            "delivery_mode": delivery_mode,
            "media_type": media_type,
            "allow_finish": status in {DELIVERY_VERIFIED, DELIVERY_NOT_APPLICABLE},
            "verified_evidence": [dict(item) for item in verified],
            "pending_evidence": [dict(item) for item in pending],
            "blocked_evidence": [dict(item) for item in blocked],
            "observation_count": len(self.observations),
        }


def strip_unsubstantiated_resumability(text: object) -> str:
    """Delete sentences promising a saved state that no evidence supports.

    比「已完成」更危险的一类谎话：「已保留 / 已保存 / 恢复点 / 从失败步骤继续」。
    用户读到它会以为活儿存住了，于是只等重试，而实际上什么都没发生。
    2026-09-30 真机复现：Agent 被只读回合合同拦下后正文就是这么写的，同一消息的机器
    记录里却是 workflow_status=failed。事实一直在，只是被这几句话盖住了。
    这类句子直接删掉，不加注释——留着就等于留着误导。
    """

    answer = str(text or "")
    if not _RESUMABILITY_CLAIM_RE.search(answer):
        return answer
    kept = [
        sentence
        for sentence in _SENTENCE_BOUNDARY.split(answer)
        if sentence.strip() and not _RESUMABILITY_CLAIM_RE.search(sentence)
    ]
    return "".join(kept).strip()


def guard_completion_claim(text: object, receipt: Mapping[str, Any]) -> str:
    """Correct a positive completion claim when terminal evidence is absent."""

    answer = str(text or "").strip()
    status = _text(receipt.get("status"), limit=80)
    # 先看证据再看措辞：交付已核验时那些话可能是真的，不能动它。
    if not answer or status == DELIVERY_VERIFIED:
        return answer
    answer = strip_unsubstantiated_resumability(answer).strip()
    if not answer:
        return answer
    if not _COMPLETION_CLAIM_RE.search(answer):
        return answer
    reason = _text(receipt.get("reason_code"), limit=160) or "terminal_evidence_missing"
    # 更正放在开头而不是结尾：那次谎话的问题正是它占着第一句最显眼的位置，
    # 压在末尾等于没改。
    return (
        f"交付状态更正：当前没有通过终态证据核验，不能把本轮标记为已完成。"
        f"状态={status}，原因={reason}。\n\n{answer}"
    )


__all__ = [
    "DELIVERY_BLOCKED",
    "DELIVERY_NOT_APPLICABLE",
    "DELIVERY_PENDING",
    "DELIVERY_UNVERIFIED",
    "DELIVERY_VERIFIED",
    "TURN_DELIVERY_RECEIPT_SCHEMA",
    "TURN_DELIVERY_SCHEMA",
    "TurnDeliveryRuntime",
    "guard_completion_claim",
]
