"""Single source of truth for identity-image reference binding.

Identity generation must always have a face anchor.  An identity-specific
portrait wins when it exists; otherwise the character's canonical portrait is
used even for age variants.  Costume images are secondary references and can
never replace the face anchor.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from novelvideo.utils.identity_resolver import compute_char_tag
from novelvideo.utils.path_resolver import (
    compute_identity_costume_path,
    compute_identity_portrait_path,
    compute_portrait_path,
)


_AGE_ALIASES = {
    "child": "child",
    "childhood": "child",
    "kid": "child",
    "children": "child",
    "幼年": "child",
    "幼年时期": "child",
    "孩童": "child",
    "儿童": "child",
    "童年": "child",
    "youth": "youth",
    "young": "youth",
    "adult": "youth",
    "teen": "youth",
    "teenager": "youth",
    "青年": "youth",
    "青年时期": "youth",
    "少年": "youth",
    "少年时期": "youth",
    "学生时期": "youth",
    "middle": "middle",
    "middleage": "middle",
    "middleaged": "middle",
    "中年": "middle",
    "中年时期": "middle",
    "elder": "elder",
    "elderly": "elder",
    "old": "elder",
    "senior": "elder",
    "老年": "elder",
    "老年时期": "elder",
    "老人": "elder",
}


def normalize_age_group(value: object) -> str:
    """Normalize API, historical, Chinese and English age labels."""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    compact = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return _AGE_ALIASES.get(compact, _AGE_ALIASES.get(text, ""))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _existing_path(*candidates: object) -> Path | None:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path.resolve()
    return None


def _project_relative(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


@dataclass(frozen=True)
class IdentityReference:
    role: str
    path: Path
    sha256: str

    @classmethod
    def from_path(cls, role: str, path: Path) -> "IdentityReference":
        return cls(role=role, path=path.resolve(), sha256=file_sha256(path))

    def to_payload(self, project_dir: Path) -> dict[str, str]:
        return {
            "role": self.role,
            "path": _project_relative(project_dir, self.path),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class IdentityGenerationBinding:
    character_name: str
    identity_id: str
    identity_name: str
    character_tag: str
    character_age_group: str
    identity_age_group: str
    identity_prompt: str
    face_anchor: IdentityReference
    costume_reference: IdentityReference | None
    revision: str

    @property
    def references(self) -> tuple[IdentityReference, ...]:
        if self.costume_reference is None:
            return (self.face_anchor,)
        return (self.face_anchor, self.costume_reference)

    def to_payload(self, project_dir: str | Path) -> dict[str, Any]:
        root = Path(project_dir)
        return {
            "schema_version": 1,
            "character_name": self.character_name,
            "identity_id": self.identity_id,
            "identity_name": self.identity_name,
            "character_tag": self.character_tag,
            "character_age_group": self.character_age_group,
            "identity_age_group": self.identity_age_group,
            "identity_prompt_sha256": hashlib.sha256(
                self.identity_prompt.encode("utf-8")
            ).hexdigest(),
            "revision": self.revision,
            "references": [ref.to_payload(root) for ref in self.references],
        }


class IdentityReferenceChangedError(RuntimeError):
    """Raised when queued identity inputs no longer match their snapshot."""


def build_identity_editor_snapshot(character, identity) -> dict[str, Any]:
    """Freeze the identity fields consumed by queued portrait/image generation."""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "character_name": str(getattr(character, "name", "") or "").strip(),
        "identity_id": str(getattr(identity, "identity_id", "") or "").strip(),
        "identity_name": str(getattr(identity, "identity_name", "") or "").strip(),
        "character_tag": compute_char_tag(str(getattr(character, "name", "") or "").strip()),
        "face_prompt": str(getattr(identity, "face_prompt", "") or "").strip(),
        "appearance_details": str(
            getattr(identity, "appearance_details", "") or ""
        ).strip(),
        "body_type": str(getattr(identity, "body_type", "") or "").strip(),
        "age_group": normalize_age_group(getattr(identity, "age_group", "")),
    }
    revision_source = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["revision"] = hashlib.sha256(revision_source.encode("utf-8")).hexdigest()
    payload["face_prompt_sha256"] = hashlib.sha256(
        payload["face_prompt"].encode("utf-8")
    ).hexdigest()
    return payload


def validate_identity_editor_snapshot(
    expected: Mapping[str, Any] | None,
    character,
    identity,
) -> dict[str, Any]:
    """Reject a queued portrait when its editor fields changed after submit."""

    current = build_identity_editor_snapshot(character, identity)
    if not expected:
        return current
    expected_revision = str(expected.get("revision") or "").strip()
    if not expected_revision:
        raise IdentityReferenceChangedError("身份编辑快照缺少 revision")
    if expected_revision != current["revision"]:
        raise IdentityReferenceChangedError(
            "身份面部、年龄或造型资料已变化，请基于最新资料重新提交 Portrait 生成"
        )
    return current


def _binding_revision(
    *,
    character,
    identity,
    character_age_group: str,
    identity_age_group: str,
    identity_prompt: str,
    references: tuple[IdentityReference, ...],
) -> str:
    value = {
        "character_name": str(getattr(character, "name", "") or ""),
        "identity_id": str(getattr(identity, "identity_id", "") or ""),
        "identity_name": str(getattr(identity, "identity_name", "") or ""),
        "appearance_details": str(getattr(identity, "appearance_details", "") or ""),
        "face_prompt": str(getattr(identity, "face_prompt", "") or ""),
        "body_type": str(getattr(identity, "body_type", "") or ""),
        "character_age_group": character_age_group,
        "identity_age_group": identity_age_group,
        "identity_prompt": identity_prompt,
        "references": [
            {"role": ref.role, "path": str(ref.path), "sha256": ref.sha256}
            for ref in references
        ],
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_identity_generation_binding(
    *,
    project_dir: str | Path,
    character,
    identity,
) -> IdentityGenerationBinding:
    """Resolve the exact ordered references and prompt for one identity image."""

    root = Path(project_dir).resolve()
    character_name = str(getattr(character, "name", "") or "").strip()
    identity_name = str(getattr(identity, "identity_name", "") or "").strip()
    identity_id = str(getattr(identity, "identity_id", "") or "").strip()
    if not character_name or not identity_name or not identity_id:
        raise ValueError("角色名、身份 ID 和身份名称均不能为空")

    identity_portrait = _existing_path(
        compute_identity_portrait_path(root, character_name, identity_name),
        getattr(identity, "portrait_image", ""),
    )
    character_portrait = _existing_path(compute_portrait_path(root, character_name))
    if identity_portrait is not None:
        face_anchor = IdentityReference.from_path("identity_portrait", identity_portrait)
    elif character_portrait is not None:
        face_anchor = IdentityReference.from_path("character_portrait", character_portrait)
    else:
        raise FileNotFoundError(f"角色「{character_name}」缺少可用 Portrait，身份图禁止无脸锚生成")

    costume_path = _existing_path(
        compute_identity_costume_path(root, character_name, identity_name),
        getattr(identity, "costume_image", ""),
    )
    costume_reference = (
        IdentityReference.from_path("identity_costume", costume_path)
        if costume_path is not None
        else None
    )

    appearance = str(getattr(identity, "appearance_details", "") or "").strip()
    face_prompt = str(getattr(identity, "face_prompt", "") or "").strip()
    body_type = str(getattr(identity, "body_type", "") or "").strip()
    character_age_group = normalize_age_group(getattr(character, "age_group", "")) or "youth"
    identity_age_group = normalize_age_group(getattr(identity, "age_group", ""))

    prompt_parts: list[str] = []
    if face_prompt:
        prompt_parts.append(f"身份面部与年龄特征：{face_prompt}")
    if appearance:
        prompt_parts.append(f"身份服装、配饰与发型：{appearance}")
    if body_type:
        prompt_parts.append(f"身份体型：{body_type}")
    if costume_reference is not None and appearance:
        prompt_parts.append("服装参考图是视觉主锚；文字描述用于补足其未展示的造型细节。")
    identity_prompt = "\n".join(prompt_parts).strip()
    if not identity_prompt and costume_reference is None:
        raise ValueError("身份缺少造型描述或服装参考图")

    references = (
        (face_anchor, costume_reference)
        if costume_reference is not None
        else (face_anchor,)
    )
    revision = _binding_revision(
        character=character,
        identity=identity,
        character_age_group=character_age_group,
        identity_age_group=identity_age_group,
        identity_prompt=identity_prompt,
        references=references,
    )
    return IdentityGenerationBinding(
        character_name=character_name,
        identity_id=identity_id,
        identity_name=identity_name,
        character_tag=compute_char_tag(character_name),
        character_age_group=character_age_group,
        identity_age_group=identity_age_group,
        identity_prompt=identity_prompt,
        face_anchor=face_anchor,
        costume_reference=costume_reference,
        revision=revision,
    )


def validate_identity_reference_snapshot(
    expected: Mapping[str, Any] | None,
    current: IdentityGenerationBinding,
    project_dir: str | Path,
) -> None:
    """Reject stale or cross-character queued inputs instead of silently drifting."""

    if not expected:
        return
    expected_revision = str(expected.get("revision") or "").strip()
    if not expected_revision:
        raise IdentityReferenceChangedError("身份参考图快照缺少 revision")
    if expected_revision != current.revision:
        expected_refs = expected.get("references")
        current_refs = current.to_payload(project_dir)["references"]
        raise IdentityReferenceChangedError(
            "身份参考图或造型数据已变化，请基于最新角色资料重新提交生成；"
            f"expected={expected_refs!r}, current={current_refs!r}"
        )


def write_identity_generation_evidence(
    *,
    output_path: str | Path,
    project_dir: str | Path,
    binding: IdentityGenerationBinding,
    model: str,
) -> Path:
    """Write an auditable sidecar beside the generated identity image."""

    output = Path(output_path)
    payload = binding.to_payload(project_dir)
    payload.update(
        {
            "model": str(model or ""),
            "output_path": _project_relative(Path(project_dir), output),
            "output_sha256": file_sha256(output),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    sidecar = output.with_suffix(".identity.json")
    temporary = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(sidecar)
    finally:
        temporary.unlink(missing_ok=True)
    return sidecar
