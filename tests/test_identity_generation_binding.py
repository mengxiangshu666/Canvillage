from pathlib import Path

import pytest

from novelvideo.models import CharacterIdentity, NovelCharacter
from novelvideo.utils.identity_binding import (
    IdentityReferenceChangedError,
    build_identity_editor_snapshot,
    file_sha256,
    resolve_identity_generation_binding,
    validate_identity_editor_snapshot,
    validate_identity_reference_snapshot,
)


def _character(*, age_group: str = "youth") -> NovelCharacter:
    return NovelCharacter(
        name="阿诚",
        age_group=age_group,
        face_prompt="清晰稳定的角色面部特征",
    )


def _identity(
    *,
    age_group: str = "middle",
    appearance_details: str = "中年时期的深色西装与白衬衫",
    face_prompt: str = "保留原人物辨识度，增加中年轮廓与成熟气质",
    body_type: str = "修长匀称",
) -> CharacterIdentity:
    return CharacterIdentity(
        identity_id="阿诚_中年时期",
        character_name="阿诚",
        identity_name="中年时期",
        character_tag="[AC_identity_specific]",
        age_group=age_group,
        appearance_details=appearance_details,
        face_prompt=face_prompt,
        body_type=body_type,
    )


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_age_variant_without_identity_portrait_uses_character_portrait_first(tmp_path: Path) -> None:
    portrait = _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face",
    )
    costume = _write(
        tmp_path
        / "assets"
        / "characters"
        / "阿诚"
        / "identities"
        / "中年时期_costume.png",
        b"identity-costume",
    )

    binding = resolve_identity_generation_binding(
        project_dir=tmp_path,
        character=_character(),
        identity=_identity(),
    )

    assert binding.face_anchor.path == portrait
    assert binding.face_anchor.role == "character_portrait"
    assert binding.costume_reference is not None
    assert binding.costume_reference.path == costume
    assert [ref.role for ref in binding.references] == [
        "character_portrait",
        "identity_costume",
    ]
    assert binding.character_tag == "[AC_7389]"
    assert "中年" in binding.identity_prompt
    assert "深色西装与白衬衫" in binding.identity_prompt
    assert "修长匀称" in binding.identity_prompt


def test_costume_reference_does_not_discard_editor_prompt_fields(tmp_path: Path) -> None:
    _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face",
    )
    _write(
        tmp_path
        / "assets"
        / "characters"
        / "阿诚"
        / "identities"
        / "中年时期_costume.png",
        b"identity-costume",
    )
    identity = _identity(
        appearance_details="深蓝修身西装，银色袖扣",
        body_type="高挑修长",
    )

    binding = resolve_identity_generation_binding(
        project_dir=tmp_path,
        character=_character(),
        identity=identity,
    )

    assert binding.costume_reference is not None
    assert "深蓝修身西装，银色袖扣" in binding.identity_prompt
    assert "高挑修长" in binding.identity_prompt
    assert "服装参考图是视觉主锚" in binding.identity_prompt


def test_identity_portrait_overrides_character_portrait_without_changing_reference_order(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face",
    )
    identity_portrait = _write(
        tmp_path
        / "assets"
        / "characters"
        / "阿诚"
        / "identities"
        / "阿诚_中年时期_portrait.png",
        b"middle-face",
    )

    binding = resolve_identity_generation_binding(
        project_dir=tmp_path,
        character=_character(),
        identity=_identity(),
    )

    assert binding.face_anchor.path == identity_portrait
    assert binding.face_anchor.role == "identity_portrait"
    assert [ref.role for ref in binding.references] == ["identity_portrait"]


def test_identity_generation_requires_a_face_anchor(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="角色.*Portrait"):
        resolve_identity_generation_binding(
            project_dir=tmp_path,
            character=_character(),
            identity=_identity(),
        )


def test_character_identity_tags_share_one_stable_character_lineage() -> None:
    character = _character()
    middle = _identity()
    formal = CharacterIdentity(
        identity_id="阿诚_西装装束",
        character_name="阿诚",
        identity_name="西装装束",
        character_tag="[AC_another_identity]",
        appearance_details="深色西装",
    )
    character.identities = [middle, formal]

    character.ensure_tag()

    assert {item.character_tag for item in character.identities} == {"[AC_7389]"}


def test_provenance_uses_durable_run_task_id() -> None:
    from novelvideo.task_backend.runners.character_image import _provenance_task_id

    assert (
        _provenance_task_id(
            {
                "__run_task_id": "durable-task-id",
                "task_id": "legacy-task-id",
                "task_key": "task-key",
            }
        )
        == "durable-task-id"
    )


def test_identity_portrait_snapshot_rejects_editor_changes_after_enqueue() -> None:
    character = _character()
    identity = _identity()
    character.identities = [identity]
    snapshot = build_identity_editor_snapshot(character, identity)

    assert snapshot["face_prompt_sha256"]
    validate_identity_editor_snapshot(snapshot, character, identity)

    identity.face_prompt = "排队后被修改的新面部描述"
    with pytest.raises(IdentityReferenceChangedError, match="资料已变化"):
        validate_identity_editor_snapshot(snapshot, character, identity)


def test_reference_snapshot_detects_reference_replacement(tmp_path: Path) -> None:
    portrait = _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face-v1",
    )
    binding = resolve_identity_generation_binding(
        project_dir=tmp_path,
        character=_character(),
        identity=_identity(),
    )
    snapshot = binding.to_payload(tmp_path)

    portrait.write_bytes(b"canonical-face-v2")
    current = resolve_identity_generation_binding(
        project_dir=tmp_path,
        character=_character(),
        identity=_identity(),
    )

    with pytest.raises(IdentityReferenceChangedError, match="参考图.*变化"):
        validate_identity_reference_snapshot(snapshot, current, tmp_path)


@pytest.mark.asyncio
async def test_async_runner_persists_exact_reference_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners.character_image import _generate_identity_image
    import novelvideo.generators as generators

    portrait = _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face",
    )
    character = _character()
    identity = _identity()
    character.identities = [identity]
    captured: dict[str, object] = {}

    async def fake_generate_identity_image_unified(**kwargs):
        captured.update(kwargs)
        Path(str(kwargs["output_path"])).write_bytes(b"generated-identity")
        return {"success": True}

    monkeypatch.setattr(
        generators,
        "generate_identity_image_unified",
        fake_generate_identity_image_unified,
    )

    class Store:
        updates: list[tuple[str, str, dict[str, object]]] = []

        async def update_character_identity(self, name: str, identity_id: str, **updates) -> None:
            self.updates.append((name, identity_id, updates))

    store = Store()
    audit: dict[str, object] = {}
    output = await _generate_identity_image(
        store=store,
        character=character,
        ethnicity="Chinese",
        identity_id=identity.identity_id,
        identity_name=identity.identity_name,
        output_dir=tmp_path,
        style="realistic",
        model="test-model",
        task_type="identity_image",
        scope="character:阿诚:identity:中年时期",
        update=lambda *_args: None,
        audit=audit,
    )

    assert output.exists()
    assert captured["reference_image_path"] == str(portrait.resolve())
    assert captured["character_tag"] == "[AC_7389]"
    assert store.updates[-1][2]["reference_images"] == [
        "assets/characters/阿诚/portrait.png"
    ]
    assert store.updates[-1][2]["character_tag"] == "[AC_7389]"
    assert store.updates[-1][2]["updated_at"]
    assert audit["reference_image_count"] == 1
    assert audit["identity_revision"]
    assert audit["output_path"] == "assets/characters/阿诚/identities/中年时期.png"
    assert audit["output_sha256"] == file_sha256(output)
    assert audit["parent_assets"] == audit["reference_images"]
    evidence_path = tmp_path / str(audit["identity_evidence_path"])
    assert evidence_path.exists()

    from novelvideo.freezone.provenance import (
        read_asset_provenance,
        record_generation_provenance,
    )

    provenance_id = record_generation_provenance(
        project_dir=tmp_path,
        history_record={
            "status": "completed",
            "media_type": "image",
            "task_type": "identity_image",
            "task_id": "identity-task-1",
            "project_id": "project-1",
            "model": "test-model",
            "result": {"path": str(output), **audit},
        },
    )
    assert provenance_id
    [provenance] = read_asset_provenance(tmp_path)
    assert provenance["task_id"] == "identity-task-1"
    assert provenance["output_path"] == audit["output_path"]
    assert provenance["output_sha256"] == audit["output_sha256"]
    assert provenance["parent_assets"] == audit["parent_assets"]


@pytest.mark.asyncio
async def test_async_runner_rolls_back_image_and_evidence_when_store_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners.character_image import _generate_identity_image
    import novelvideo.generators as generators

    _write(
        tmp_path / "assets" / "characters" / "阿诚" / "portrait.png",
        b"canonical-face",
    )
    output = _write(
        tmp_path
        / "assets"
        / "characters"
        / "阿诚"
        / "identities"
        / "中年时期.png",
        b"previous-output",
    )
    evidence = _write(output.with_suffix(".identity.json"), b"previous-evidence")
    character = _character()
    identity = _identity()
    character.identities = [identity]

    async def fake_generate_identity_image_unified(**kwargs):
        Path(str(kwargs["output_path"])).write_bytes(b"replacement-output")
        return {"success": True}

    monkeypatch.setattr(
        generators,
        "generate_identity_image_unified",
        fake_generate_identity_image_unified,
    )

    class FailingStore:
        async def update_character_identity(self, *_args, **_kwargs) -> None:
            raise RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        await _generate_identity_image(
            store=FailingStore(),
            character=character,
            ethnicity="Chinese",
            identity_id=identity.identity_id,
            identity_name=identity.identity_name,
            output_dir=tmp_path,
            style="realistic",
            model="test-model",
            task_type="identity_image",
            scope="character:阿诚:identity:中年时期",
            update=lambda *_args: None,
        )

    assert output.read_bytes() == b"previous-output"
    assert evidence.read_bytes() == b"previous-evidence"


@pytest.mark.asyncio
async def test_generation_request_atomically_persists_editor_snapshot() -> None:
    from novelvideo.api.routes.characters import _apply_identity_generation_updates
    from novelvideo.api.schemas import IdentityImageGenRequest

    character = _character()
    identity = _identity(age_group="")
    character.identities = [identity]

    class Store:
        async def update_character_identity(self, _name: str, identity_id: str, **updates) -> None:
            identities = character.identities
            target = next(item for item in identities if item.identity_id == identity_id)
            for key, value in updates.items():
                setattr(target, key, value)
            character.identities = identities

        def get_character(self, _name: str):
            return character

    updated_character, updated_identity = await _apply_identity_generation_updates(
        store=Store(),
        character_name=character.name,
        identity_id=identity.identity_id,
        body=IdentityImageGenRequest(
            appearance_details="最新的黑色高领衫与米白色羊毛大衣",
            face_prompt="最新中年面部特征",
            body_type="修长体型",
            age_group="中年时期",
        ),
    )

    assert updated_character is character
    assert updated_identity is not None
    assert updated_identity.appearance_details == "最新的黑色高领衫与米白色羊毛大衣"
    assert updated_identity.face_prompt == "最新中年面部特征"
    assert updated_identity.body_type == "修长体型"
    assert updated_identity.age_group == "middle"
