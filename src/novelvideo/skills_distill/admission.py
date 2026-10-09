"""Deterministic admission gate for imported and installed Agent skills."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any, Iterable

SKILL_ADMISSION_SCHEMA = "skill_admission.v1"
SKILL_PROVENANCE_SCHEMA = "skill.provenance.v1"
SCANNER_SOURCE = {
    "project": "scientific-agent-skills",
    "commit": "cc37669ed0f354619b1ae586e958609a87680718",
    "integration": "schema_overlap_provenance_package_gate",
}

_LOCAL_LINK_RE = re.compile(
    r"\]\((?!https?://|mailto:|#)([^)]+)\)|`((?:scripts|references|assets)/[^`]+)`",
    re.IGNORECASE,
)
_UNSAFE_LINK_RE = re.compile(r"(?:javascript|data|file):", re.IGNORECASE)


def _text(value: object, limit: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalized(value: object) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _digest(value: object) -> str:
    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def _issue(
    code: str,
    message: str,
    *,
    severity: str,
    field: str = "",
    related_skill_id: str = "",
) -> dict[str, str]:
    result = {"code": code, "message": message, "severity": severity}
    if field:
        result["field"] = field
    if related_skill_id:
        result["related_skill_id"] = related_skill_id
    return result


def _local_references(body: str) -> list[str]:
    references: list[str] = []
    for match in _LOCAL_LINK_RE.finditer(body):
        raw = _text(match.group(1) or match.group(2), 500).split("#", 1)[0]
        raw = raw.replace("\\", "/").lstrip("./")
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            continue
        normalized = str(path)
        if normalized not in references:
            references.append(normalized)
    return references[:64]


def scan_skill(
    item: dict[str, Any],
    *,
    existing_items: Iterable[dict[str, Any]] = (),
    phase: str = "import",
) -> dict[str, Any]:
    """Return a stable report; only error issues block the requested phase."""

    clean_phase = "install" if phase == "install" else "import"
    issues: list[dict[str, str]] = []
    name = _text(item.get("name"), 160)
    description = _text(item.get("description"), 600)
    body = str(item.get("body") or "").strip()
    skill_id = _text(item.get("id"), 180)
    skill_key = _text(item.get("skill_key"), 100)
    contract = item.get("contract") if isinstance(item.get("contract"), dict) else {}
    provenance = (
        item.get("source_manifest")
        if isinstance(item.get("source_manifest"), dict)
        else {}
    )
    package = (
        item.get("package_manifest")
        if isinstance(item.get("package_manifest"), dict)
        else {}
    )

    for field, value in (
        ("id", skill_id),
        ("skill_key", skill_key),
        ("name", name),
        ("description", description),
        ("body", body),
    ):
        if not value:
            issues.append(
                _issue(
                    "skill_required_field_missing",
                    f"技能缺少 {field}",
                    severity="error",
                    field=field,
                )
            )
    if body and len(body) < 40:
        issues.append(
        _issue(
                "skill_body_too_short",
                "技能正文过短，无法形成可执行工作流",
                severity="error",
                field="body",
            )
        )

    maturity = _text(contract.get("maturity"), 40)
    readiness = contract.get("readiness_score")
    if contract.get("schema_version") != "canvas_skill_contract.v1":
        issues.append(
            _issue(
                "skill_contract_schema_invalid",
                "缺少 canvas_skill_contract.v1 执行合同",
                severity="error",
                field="contract",
            )
        )
    if maturity == "reference_only" or not isinstance(readiness, int) or readiness < 55:
        issues.append(
            _issue(
                "skill_execution_contract_incomplete",
                "技能缺少足够的输入、步骤、输出或验收条件",
                severity="error",
                field="contract",
            )
        )

    trusted_builtin = bool(item.get("builtin")) and item.get("source") in {
        "libtv_reference",
        "tapnow_reference",
    }
    if not trusted_builtin:
        if provenance.get("schema") != SKILL_PROVENANCE_SCHEMA:
            issues.append(
                _issue(
                    "skill_provenance_missing",
                    "技能缺少来源与内容哈希合同",
                    severity="error",
                    field="source_manifest",
                )
            )
        expected_hash = _digest(body.strip())
        if provenance.get("content_sha256") != expected_hash:
            issues.append(
                _issue(
                    "skill_content_hash_mismatch",
                    "技能正文与来源哈希不一致",
                    severity="error",
                    field="source_manifest.content_sha256",
                )
            )
        source_kind = _text(provenance.get("source_kind"), 80)
        source_ref = _text(provenance.get("source_ref"), 500)
        declared_license = _text(provenance.get("license"), 120)
        source_commit = _text(provenance.get("source_commit"), 120)
        if not source_ref:
            issues.append(
                _issue(
                    "skill_source_reference_missing",
                    "技能缺少来源文件或仓库标识",
                    severity="error",
                    field="source_manifest.source_ref",
                )
            )
        if source_kind == "external_source" and not declared_license:
            issues.append(
                _issue(
                    "skill_license_missing",
                    "外部技能没有声明许可证",
                    severity="error" if clean_phase == "install" else "warning",
                    field="source_manifest.license",
                )
            )
        if source_kind == "external_source" and not source_commit:
            issues.append(
                _issue(
                    "skill_source_revision_missing",
                    "外部技能没有声明 commit 或版本",
                    severity="error" if clean_phase == "install" else "warning",
                    field="source_manifest.source_commit",
                )
            )

    if _UNSAFE_LINK_RE.search(body):
        issues.append(
            _issue(
                "skill_unsafe_link_scheme",
                "技能正文包含不受支持的本地或可执行链接协议",
                severity="error",
                field="body",
            )
        )
    package_files = {
        str(file.get("path") or "").replace("\\", "/").lstrip("./")
        for file in package.get("files") or []
        if isinstance(file, dict) and file.get("path")
    }
    missing_references = sorted(
        reference for reference in _local_references(body) if reference not in package_files
    )
    for reference in missing_references[:16]:
        issues.append(
            _issue(
                "skill_local_reference_missing",
                f"技能引用的本地文件未随包导入：{reference}",
                severity="error" if clean_phase == "install" else "warning",
                field="body",
            )
        )

    own_hash = _digest(body)
    normalized_name = _normalized(name)
    normalized_description = _normalized(description)
    overlap: list[dict[str, Any]] = []
    for other in existing_items:
        if not isinstance(other, dict):
            continue
        other_id = _text(other.get("id"), 180)
        if other_id == skill_id:
            other_body_hash = _digest(other.get("body"))
            if body and own_hash == other_body_hash:
                issues.append(
                    _issue(
                        "skill_id_duplicate",
                        "技能 ID 与现有技能重复",
                        severity="error",
                        field="id",
                        related_skill_id=other_id,
                    )
                )
            else:
                issues.append(
                    _issue(
                        "skill_id_collision",
                        "技能 ID 已存在但正文不同，禁止静默覆盖",
                        severity="error",
                        field="id",
                        related_skill_id=other_id,
                    )
                )
            continue
        other_name = _normalized(other.get("name"))
        other_body_hash = _digest(other.get("body"))
        if normalized_name and normalized_name == other_name:
            issues.append(
                _issue(
                    "skill_name_duplicate",
                    "技能名称与现有技能重复",
                    severity="error",
                    field="name",
                    related_skill_id=other_id,
                )
            )
            continue
        if body and own_hash == other_body_hash:
            issues.append(
                _issue(
                    "skill_content_duplicate",
                    "技能正文与现有技能完全重复",
                    severity="error",
                    field="body",
                    related_skill_id=other_id,
                )
            )
            continue
        other_description = _normalized(other.get("description"))
        if normalized_description and other_description:
            ratio = SequenceMatcher(None, normalized_description, other_description).ratio()
            if ratio >= 0.88:
                overlap.append({"skill_id": other_id, "description_similarity": round(ratio, 3)})
                issues.append(
                    _issue(
                        "skill_description_overlap",
                        f"技能描述与 {other_id} 高度重叠，需要确认职责边界",
                        severity="warning",
                        field="description",
                        related_skill_id=other_id,
                    )
                )

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    status = "blocked" if error_count else "review_required" if warning_count else "admitted"
    # Import may retain a review-required item in the catalog so the user can
    # fix its provenance, but enabling it is a stricter operation: warnings
    # must be cleared before an executable Skill enters the Village Agent runtime.
    can_install = error_count == 0 and (clean_phase != "install" or warning_count == 0)
    return {
        "schema": SKILL_ADMISSION_SCHEMA,
        "phase": clean_phase,
        "status": status,
        "can_install": can_install,
        "scanner_source": dict(SCANNER_SOURCE),
        "content_sha256": own_hash,
        "checks": {
            "required_fields": True,
            "execution_contract": bool(contract),
            "provenance": bool(provenance) or trusted_builtin,
            "duplicates": True,
            "description_overlap": True,
            "local_references": not missing_references,
            "package_integrity": bool(package.get("archive_sha256")) or not package_files,
        },
        "issues": issues,
        "overlap": overlap[:8],
        "summary": {"errors": error_count, "warnings": warning_count},
    }


__all__ = [
    "SKILL_ADMISSION_SCHEMA",
    "SKILL_PROVENANCE_SCHEMA",
    "scan_skill",
]
