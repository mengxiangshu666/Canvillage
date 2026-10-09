from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from novelvideo.api.schemas import IdentityCreate
from novelvideo.models import CharacterIdentity, NovelCharacter


class _CharacterStore:
    def __init__(self, character: NovelCharacter):
        self.character = character
        self.identity_updates: list[tuple[str, str, dict]] = []
        self.added_identities: list[CharacterIdentity] = []

    def get_character(self, name: str):
        if name == self.character.name:
            return self.character
        return None

    async def update_character_identity(self, name: str, identity_id: str, **updates):
        self.identity_updates.append((name, identity_id, updates))
        for identity in self.character.identities:
            if identity.identity_id == identity_id:
                for key, value in updates.items():
                    setattr(identity, key, value)
        return True

    async def add_character_identity(self, name: str, identity: CharacterIdentity):
        self.added_identities.append(identity)
        identities = self.character.identities
        identities.append(identity)
        self.character.identities = identities
        return True


def _patch_character_project(
    monkeypatch: pytest.MonkeyPatch,
    module,
    project_dir: Path,
    store: _CharacterStore,
) -> None:
    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(project_dir), "admin", "demo", project_dir, str(project_dir), store

    monkeypatch.setattr(module, "_resolve_character_project", fake_resolve_character_project)


def _ctx(project_dir: Path):
    return SimpleNamespace(
        project_id="proj_demo",
        owner_username="admin",
        project_name="demo",
        output_dir=project_dir,
        state_dir=project_dir / "_state",
        runtime_dir=project_dir / "_runtime",
        is_home_node=True,
    )


def _png_upload(filename: str = "upload.png") -> UploadFile:
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=(120, 80, 40)).save(payload, format="PNG")
    payload.seek(0)
    return UploadFile(filename=filename, file=payload)


def _write_png(path: Path, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=color).save(payload, format="PNG")
    data = payload.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


@pytest.mark.asyncio
async def test_delete_identity_costume_removes_file_and_clears_store(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    costume_path = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年_costume.png"
    costume_path.parent.mkdir(parents=True)
    costume_path.write_bytes(b"costume")
    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        costume_image=str(costume_path),
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.delete_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response == {"ok": True, "data": {"deleted": True}}
    assert not costume_path.exists()
    assert store.identity_updates == [("秦", "秦_少年", {"costume_image": ""})]


def test_asset_image_source_payload_reads_direct_registry_only(monkeypatch, tmp_path):
    from novelvideo.api.routes import characters

    model = SimpleNamespace(registry_id="portrait", catalog_id="direct/portrait")
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.list_direct_image_models",
        lambda: (model,),
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.direct_image_model_option",
        lambda _model: {
            "id": "direct/portrait",
            "label": "Portrait",
            "enabled": True,
            "runtimeReady": True,
        },
    )
    monkeypatch.setattr(
        characters,
        "load_project_config_file",
        lambda _username, _project: {"character_image_selection": "direct/portrait"},
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _value: model,
    )

    payload = characters._asset_image_source_selection_payload("admin", "demo", "character")

    assert payload == {
        "asset_kind": "character",
        "image_source_selection": "direct/portrait",
        "options": {"direct/portrait": "Portrait"},
    }


def test_character_image_model_does_not_fallback_to_legacy_selection(monkeypatch):
    from novelvideo.api.routes import characters

    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        characters,
        "_character_image_selection_payload",
        lambda *_args: {"character_image_selection": "legacy-hidden-model"},
    )

    with pytest.raises(ValueError, match="角色参考图未配置图片模型"):
        characters._resolve_character_image_model("admin", "demo", None)


@pytest.mark.asyncio
async def test_delete_identity_costume_is_idempotent_when_file_missing(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        costume_image="",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.delete_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response == {"ok": True, "data": {"deleted": False}}
    assert store.identity_updates == [("秦", "秦_少年", {"costume_image": ""})]


@pytest.mark.asyncio
async def test_add_identity_persists_age_group(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    character = NovelCharacter(name="秦")
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.add_identity(
        project="demo",
        name="秦",
        body=IdentityCreate(
            identity_name="幼年",
            age_group="child",
            appearance_details="粗布短衫",
        ),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["age_group"] == "child"
    assert store.added_identities[0].age_group == "child"


@pytest.mark.asyncio
async def test_upload_identity_costume_returns_project_context_url(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)

    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(tmp_path), "admin", "demo", tmp_path, str(tmp_path), store

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_character_project)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.upload_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["costume_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/少年_costume.png"
    )
    assert store.identity_updates == [
        (
            "秦",
            "秦_少年",
            {"costume_image": str(tmp_path / "assets/characters/秦/identities/少年_costume.png")},
        )
    ]


@pytest.mark.asyncio
async def test_upload_identity_portrait_returns_project_context_url(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)

    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(tmp_path), "admin", "demo", tmp_path, str(tmp_path), store

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_character_project)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.upload_identity_portrait(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["portrait_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/秦_少年_portrait.png"
    )
    assert store.identity_updates == [
        (
            "秦",
            "秦_少年",
            {
                "portrait_image": str(
                    tmp_path / "assets/characters/秦/identities/秦_少年_portrait.png"
                )
            },
        )
    ]


@pytest.mark.asyncio
async def test_upload_identity_image_backs_up_existing_file(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    old_bytes = _write_png(target, (10, 20, 30))

    response = await characters.upload_identity_image(
        project="demo",
        name="秦",
        identity_name="少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    backups = list(target.parent.glob("少年_*.png"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_bytes
    assert target.read_bytes() != old_bytes


@pytest.mark.asyncio
async def test_sync_identity_generation_restores_files_when_store_commit_fails(
    tmp_path,
    monkeypatch,
):
    from novelvideo.api.routes import characters
    from novelvideo.api.schemas import IdentityImageGenRequest
    from novelvideo.generators import image_generator

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        face_prompt="少年面部",
        appearance_details="粗布短衫",
    )
    character = NovelCharacter(name="秦", age_group="youth")
    character.identities = [identity]

    class _FailingCommitStore(_CharacterStore):
        async def update_character_identity(self, name: str, identity_id: str, **updates):
            if "reference_images" in updates:
                raise RuntimeError("identity commit failed")
            return await super().update_character_identity(name, identity_id, **updates)

    store = _FailingCommitStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)
    monkeypatch.setattr(characters, "load_project_config", lambda *_args: {"visual_style": "realistic", "ethnicity": "Chinese"})
    monkeypatch.setattr(characters, "_resolve_character_image_model", lambda *_args: "test-model")

    portrait = tmp_path / "assets" / "characters" / "秦" / "portrait.png"
    _write_png(portrait, (90, 80, 70))
    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    old_image = _write_png(target, (10, 20, 30))
    evidence = target.with_suffix(".identity.json")
    evidence.write_bytes(b"old-evidence")

    async def fake_generate_identity_image_unified(**kwargs):
        _write_png(Path(kwargs["output_path"]), (200, 180, 160))
        return {"success": True}

    monkeypatch.setattr(
        image_generator,
        "generate_identity_image_unified",
        fake_generate_identity_image_unified,
    )

    with pytest.raises(RuntimeError, match="identity commit failed"):
        await characters.generate_identity_image(
            project="demo",
            name="秦",
            identity_id="秦_少年",
            body=IdentityImageGenRequest(model="test-model"),
            user={"username": "admin"},
        )

    assert target.read_bytes() == old_image
    assert evidence.read_bytes() == b"old-evidence"


@pytest.mark.asyncio
async def test_character_asset_history_lists_backups(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    _write_png(target, (10, 20, 30))
    backup = target.parent / "少年_20260603112233.png"
    backup.write_bytes(target.read_bytes())

    response = await characters.list_character_asset_history(
        project="demo",
        name="秦",
        kind="identity",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["current_url"]
    assert response["data"]["entries"][0]["history_id"] == backup.name
    assert response["data"]["entries"][0]["url"]


@pytest.mark.asyncio
async def test_restore_character_asset_history_backs_up_current_and_restores_backup(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    current_bytes = _write_png(target, (200, 20, 30))
    backup = target.parent / "少年_20260603112233.png"
    old_bytes = _write_png(backup, (10, 20, 30))

    response = await characters.restore_character_asset_history(
        project="demo",
        name="秦",
        body=SimpleNamespace(
            kind="identity",
            identity_id="秦_少年",
            history_id=backup.name,
        ),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert target.read_bytes() == old_bytes
    new_backups = [
        path for path in target.parent.glob("少年_*.png") if path.name != "少年_20260603112233.png"
    ]
    assert len(new_backups) == 1
    assert new_backups[0].read_bytes() == current_bytes
