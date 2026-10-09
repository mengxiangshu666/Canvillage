"""Evidence-backed final delivery QC contract.

The task system may finish a compose job successfully, but that only proves an
execution reached a terminal state.  This module evaluates the actual finished
artifact facts and keeps missing evidence visible as ``not_run``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


DELIVERY_QC_SCHEMA = "delivery_qc_contract.v1"
DELIVERY_QC_AUDIT_SCHEMA = "delivery_qc_audit.v1"
RELEASE_READINESS_SCHEMA = "release_readiness_contract.v1"

CHECK_PASSED = "passed"
CHECK_FAILED = "failed"
CHECK_NOT_RUN = "not_run"
RELEASE_READY = "ready"
RELEASE_BLOCKED = "blocked"
RELEASE_UNVERIFIED = "unverified"
RELEASE_NOT_APPLICABLE = "not_applicable"

_DEFAULT_REQUIRED_CHECKS = (
    "container_allowed",
    "video_stream_present",
    "audio_stream_present",
    "dimensions",
    "frame_rate",
    "duration",
    "black_frames",
    "freeze_frames",
    "av_sync",
    "loudness",
    "true_peak",
    "subtitle_stream",
    "color_space",
    "bitrate",
    "file_readback",
    "sha256",
)

DELIVERY_QC_RELEASE_CHECKS = (
    "container_allowed",
    "video_stream_present",
    "audio_stream_present",
    "dimensions",
    "frame_rate",
    "duration",
    "black_frames",
    "freeze_frames",
    "av_sync",
    "audio_activity",
    "loudness",
    "true_peak",
    "subtitle_stream",
    "color_space",
    "bitrate",
    "file_readback",
    "sha256",
)


def _text(value: object, *, limit: int = 2000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        if isinstance(value.get("passed"), bool):
            return bool(value["passed"])
        status = _text(value.get("status"), limit=40).casefold()
        if status in {"passed", "ready", "ok", "success", "succeeded", "completed"}:
            return True
        if status in {"failed", "error", "invalid", "missing", "cancelled", "canceled"}:
            return False
    return None


def _status(value: bool | None) -> str:
    if value is True:
        return CHECK_PASSED
    if value is False:
        return CHECK_FAILED
    return CHECK_NOT_RUN


def _hash_valid(value: object) -> bool | None:
    if value in (None, ""):
        return None
    digest = _text(value, limit=64).lower()
    return bool(re.fullmatch(r"[0-9a-f]{64}", digest))


def _evaluate_container(value: object, allowed: tuple[str, ...]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    container = _text(
        source.get("container") or source.get("format") or source.get("extension"),
        limit=40,
    ).lower().lstrip(".")
    if not container:
        return None
    return container in {item.lower().lstrip(".") for item in allowed}


def _evaluate_stream(value: object, key: str) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    if key in source:
        nested = _bool_or_none(source.get(key))
        if nested is not None:
            return nested
    if isinstance(source.get("streams"), (list, tuple)):
        codec_type = "video" if key == "video_stream_present" else "audio"
        return any(
            isinstance(stream, Mapping)
            and _text(stream.get("codec_type") or stream.get("type"), limit=40) == codec_type
            for stream in source["streams"]
        )
    return None


def _evaluate_dimensions(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    width = _number(source.get("width"))
    height = _number(source.get("height"))
    if width is None or height is None:
        return None
    target_width = _number(target.get("width"))
    target_height = _number(target.get("height"))
    if target_width is None or target_height is None:
        # No declared target resolution means there is nothing to compare the
        # measured frame against. Reporting not_run keeps missing evidence
        # visible instead of rubber-stamping any positive width/height.
        return None
    return abs(width - target_width) <= 2 and abs(height - target_height) <= 2


def _evaluate_frame_rate(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    actual = _number(source.get("fps") or source.get("frame_rate") or source.get("frameRate"))
    expected = _number(target.get("fps") or target.get("frame_rate"))
    if actual is None or expected is None:
        return None
    tolerance = _number(target.get("fps_tolerance")) or 0.05
    return abs(actual - expected) <= tolerance


def _evaluate_duration(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    actual = _number(source.get("duration_seconds") or source.get("duration"))
    expected = _number(target.get("duration_seconds") or target.get("duration"))
    if actual is None or expected is None:
        return None
    tolerance = _number(target.get("duration_tolerance")) or 1.0
    return abs(actual - expected) <= tolerance


def _evaluate_ratio(value: object, *, maximum: float) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    ratio = _number(
        source.get("ratio")
        if source.get("ratio") is not None
        else source.get("max_ratio")
        if source.get("max_ratio") is not None
        else source.get("value")
    )
    if ratio is None:
        return None
    return ratio <= maximum


def _evaluate_av_sync(value: object, *, maximum_ms: float) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    offset = _number(
        source.get("max_offset_ms")
        or source.get("offset_ms")
        or source.get("worstErrorSeconds")
    )
    if offset is None:
        return None
    if _text(source.get("unit"), limit=20).casefold() == "seconds":
        offset *= 1000
    elif "worstErrorSeconds" in source:
        offset *= 1000
    return abs(offset) <= maximum_ms


def _evaluate_loudness(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    measured = _number(
        source.get("integrated_lufs")
        or source.get("input_i")
        or source.get("measured")
        or source.get("lufs")
    )
    expected = _number(target.get("integrated_lufs") or target.get("lufs"))
    if measured is None or expected is None:
        return None
    tolerance = _number(target.get("loudness_tolerance")) or 1.0
    return abs(measured - expected) <= tolerance


def _evaluate_true_peak(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    measured = _number(source.get("true_peak_db") or source.get("input_tp") or source.get("tp"))
    ceiling = _number(target.get("true_peak_db") or target.get("tp")) or -2.0
    if measured is None:
        return None
    return measured <= ceiling + 0.05


def _evaluate_bitrate(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    actual = _number(source.get("kbps"))
    if actual is None:
        actual = _number(source.get("bit_rate_kbps"))
    minimum = _number(target.get("min_kbps"))
    if actual is None:
        return None
    if minimum is None:
        return actual > 0
    return actual >= minimum


def _evaluate_color_space(value: object, target: Mapping[str, Any]) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    actual = _text(source.get("color_space") or source.get("colorSpace"), limit=80).casefold()
    expected = _text(target.get("color_space"), limit=80).casefold()
    if not actual or not expected:
        return None
    return actual == expected


def _evaluate_subtitle(value: object) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    if "present" in source:
        present = bool(source.get("present"))
        if present:
            return True
        return False if bool(source.get("required", True)) else True
    return None


def _evaluate_audio_activity(value: object) -> bool | None:
    direct = _bool_or_none(value)
    if direct is not None:
        return direct
    source = _mapping(value)
    if source.get("has_audio") is False:
        return False
    all_silent = source.get("all_silent")
    if all_silent is True:
        return False
    if all_silent is False and source.get("has_audio") is True:
        return True
    return None


def _check_value(
    name: str,
    *,
    observations: Mapping[str, Any],
    target: Mapping[str, Any],
    allowed_containers: tuple[str, ...],
) -> bool | None:
    raw = observations.get(name)
    if name == "container_allowed":
        return _evaluate_container(raw, allowed_containers)
    if name in {"video_stream_present", "audio_stream_present"}:
        return _evaluate_stream(raw, name)
    if name == "dimensions":
        return _evaluate_dimensions(raw, target)
    if name == "frame_rate":
        return _evaluate_frame_rate(raw, target)
    if name == "duration":
        return _evaluate_duration(raw, target)
    if name == "black_frames":
        return _evaluate_ratio(raw, maximum=_number(target.get("max_black_frame_ratio")) or 0.02)
    if name == "freeze_frames":
        return _evaluate_ratio(raw, maximum=_number(target.get("max_freeze_frame_ratio")) or 0.02)
    if name == "av_sync":
        return _evaluate_av_sync(
            raw,
            maximum_ms=_number(target.get("max_av_sync_ms")) or 100.0,
        )
    if name == "audio_activity":
        return _evaluate_audio_activity(raw)
    if name == "loudness":
        return _evaluate_loudness(raw, target)
    if name == "true_peak":
        return _evaluate_true_peak(raw, target)
    if name == "subtitle_stream":
        return _evaluate_subtitle(raw)
    if name == "color_space":
        return _evaluate_color_space(raw, target)
    if name == "bitrate":
        return _evaluate_bitrate(raw, target)
    if name == "file_readback":
        return _bool_or_none(raw)
    if name == "sha256":
        if isinstance(raw, Mapping):
            raw = raw.get("sha256") or raw.get("value")
        return _hash_valid(raw)
    return _bool_or_none(raw)


def build_delivery_qc_contract(
    *,
    observations: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    required_checks: object = None,
) -> dict[str, Any]:
    """Compile a delivery report from actual artifact observations."""

    facts = _mapping(observations)
    delivery_target = _mapping(target)
    requested = (
        [_text(item, limit=80) for item in required_checks if _text(item, limit=80)]
        if isinstance(required_checks, (list, tuple))
        else list(_DEFAULT_REQUIRED_CHECKS)
    )
    requested = list(dict.fromkeys(requested))
    allowed_containers = tuple(
        _text(item, limit=40).lower().lstrip(".")
        for item in (
            delivery_target.get("allowed_containers")
            or ["mp4", "mov", "mkv"]
        )
        if _text(item, limit=40)
    )
    checks: dict[str, dict[str, Any]] = {}
    for name in requested:
        result = _check_value(
            name,
            observations=facts,
            target=delivery_target,
            allowed_containers=allowed_containers,
        )
        checks[name] = {
            "name": name,
            "status": _status(result),
            "passed": result,
            "evidence": deepcopy(facts.get(name)),
        }
    failed = [name for name, check in checks.items() if check["status"] == CHECK_FAILED]
    not_run = [name for name, check in checks.items() if check["status"] == CHECK_NOT_RUN]
    gate_value: bool | None
    if failed:
        gate_value = False
    elif not_run:
        gate_value = None
    else:
        gate_value = True
    return {
        "schema": DELIVERY_QC_SCHEMA,
        "target": delivery_target,
        "checks": checks,
        "failed_checks": failed,
        "not_run_checks": not_run,
        "passed": gate_value,
        "gate_observations": {"final_delivery_qc_passed": gate_value},
    }


def validate_delivery_qc_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("delivery_qc_contract must be an object")
    contract = deepcopy(dict(value))
    if contract.get("schema") != DELIVERY_QC_SCHEMA:
        raise ValueError("delivery_qc_contract schema is unsupported")
    checks = contract.get("checks")
    if not isinstance(checks, Mapping):
        raise ValueError("delivery_qc_contract checks must be an object")
    for name, check in checks.items():
        if not isinstance(check, Mapping):
            raise ValueError(f"delivery_qc_contract check {name} must be an object")
        if check.get("status") not in {CHECK_PASSED, CHECK_FAILED, CHECK_NOT_RUN}:
            raise ValueError(f"delivery_qc_contract check {name} status is invalid")
    if contract.get("passed") not in (True, False, None):
        raise ValueError("delivery_qc_contract passed must be true, false, or null")
    return contract


def audit_delivery_qc_contract(value: object) -> dict[str, Any]:
    try:
        contract = validate_delivery_qc_contract(value)
    except ValueError as exc:
        return {
            "schema": DELIVERY_QC_AUDIT_SCHEMA,
            "passed": False,
            "issues": [{"code": "delivery.contract_invalid", "message": str(exc)}],
            "gate_observations": {"final_delivery_qc_passed": False},
        }
    issues = [
        {
            "code": f"delivery.{check['name']}.{check['status']}",
            "message": (
                f"成片 QC {check['name']} 未通过"
                if check["status"] == CHECK_FAILED
                else f"成片 QC {check['name']} 尚无证据"
            ),
        }
        for check in contract["checks"].values()
        if check["status"] != CHECK_PASSED
    ]
    return {
        "schema": DELIVERY_QC_AUDIT_SCHEMA,
        "passed": contract.get("passed") is True,
        "issues": issues,
        "gate_observations": {
            "final_delivery_qc_passed": contract.get("gate_observations", {}).get(
                "final_delivery_qc_passed",
                contract.get("passed"),
            )
        },
    }


def _release_readiness(
    *,
    status: str,
    reason_code: str,
    failed_checks: object = (),
    not_run_checks: object = (),
    missing_checks: object = (),
    check_count: int = 0,
    required_check_count: int = 0,
) -> dict[str, Any]:
    failed = [
        _text(item, limit=80)
        for item in failed_checks
        if _text(item, limit=80)
    ][:64]
    not_run = [
        _text(item, limit=80)
        for item in not_run_checks
        if _text(item, limit=80)
    ][:64]
    missing = [
        _text(item, limit=80)
        for item in missing_checks
        if _text(item, limit=80)
    ][:64]
    return {
        "schema": RELEASE_READINESS_SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "can_publish": status == RELEASE_READY,
        "failed_checks": failed,
        "not_run_checks": not_run,
        "missing_checks": missing,
        "check_count": max(int(check_count), 0),
        "required_check_count": max(int(required_check_count), 0),
    }


def project_release_readiness(
    value: object,
    *,
    required_checks: object = DELIVERY_QC_RELEASE_CHECKS,
) -> dict[str, Any]:
    """Project a release decision from a valid, complete delivery QC receipt.

    The workflow's terminal status and the final file's existence are execution
    facts, not publication facts.  This projection is deliberately fail-closed:
    absent, malformed, incomplete, failed, or internally inconsistent QC never
    becomes ``ready``.
    """

    required = [
        _text(item, limit=80)
        for item in (
            required_checks
            if isinstance(required_checks, (list, tuple))
            else DELIVERY_QC_RELEASE_CHECKS
        )
        if _text(item, limit=80)
    ]
    required = list(dict.fromkeys(required))
    required_count = len(required)
    if not isinstance(value, Mapping):
        return _release_readiness(
            status=RELEASE_UNVERIFIED,
            reason_code="delivery_qc_missing",
            missing_checks=required,
            required_check_count=required_count,
        )
    try:
        contract = validate_delivery_qc_contract(value)
    except ValueError:
        return _release_readiness(
            status=RELEASE_BLOCKED,
            reason_code="delivery_qc_invalid",
            required_check_count=required_count,
        )

    checks = contract.get("checks")
    assert isinstance(checks, Mapping)
    failed = [
        name
        for name, check in checks.items()
        if isinstance(check, Mapping) and check.get("status") == CHECK_FAILED
    ]
    not_run = [
        name
        for name, check in checks.items()
        if isinstance(check, Mapping) and check.get("status") == CHECK_NOT_RUN
    ]
    missing = [name for name in required if name not in checks]
    expected_passed: bool | None
    if failed:
        expected_passed = False
    elif not_run:
        expected_passed = None
    else:
        expected_passed = True
    if contract.get("passed") != expected_passed:
        return _release_readiness(
            status=RELEASE_BLOCKED,
            reason_code="delivery_qc_inconsistent",
            failed_checks=failed,
            not_run_checks=not_run,
            missing_checks=missing,
            check_count=len(checks),
            required_check_count=required_count,
        )
    if failed:
        status = RELEASE_BLOCKED
        reason_code = "delivery_qc_failed"
    elif missing or not_run:
        status = RELEASE_UNVERIFIED
        reason_code = "delivery_qc_incomplete"
    else:
        status = RELEASE_READY
        reason_code = "delivery_qc_passed"
    return _release_readiness(
        status=status,
        reason_code=reason_code,
        failed_checks=failed,
        not_run_checks=not_run,
        missing_checks=missing,
        check_count=len(checks),
        required_check_count=required_count,
    )


__all__ = [
    "CHECK_FAILED",
    "CHECK_NOT_RUN",
    "CHECK_PASSED",
    "DELIVERY_QC_AUDIT_SCHEMA",
    "DELIVERY_QC_RELEASE_CHECKS",
    "DELIVERY_QC_SCHEMA",
    "RELEASE_BLOCKED",
    "RELEASE_NOT_APPLICABLE",
    "RELEASE_READINESS_SCHEMA",
    "RELEASE_READY",
    "RELEASE_UNVERIFIED",
    "audit_delivery_qc_contract",
    "build_delivery_qc_contract",
    "project_release_readiness",
    "validate_delivery_qc_contract",
]
