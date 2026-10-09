import pytest

from novelvideo.ports.local.project import SQLiteProjectRegistry


@pytest.fixture
def local_registry(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state))
    import novelvideo.config as config

    monkeypatch.setattr(config, "STATE_DIR", str(state), raising=False)
    return SQLiteProjectRegistry()


@pytest.mark.asyncio
async def test_purge_deletes_registry_row_and_releases_owner_name(local_registry):
    first = await local_registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="agent",
    )
    await local_registry.update_project_status(first.id, "deleted")
    claimed = await local_registry.claim_project_purge(first.id)
    purged = await local_registry.mark_project_purged(first.id)

    second = await local_registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="agent",
    )
    resolved = await local_registry.get_project_by_owner_name("local", "agent")

    assert purged is not None
    assert claimed is not None
    assert claimed.purge_claimed_at is not None
    assert purged.id == first.id
    assert purged.purged_at is not None
    assert await local_registry.get_project(first.id) is None
    assert second.id != first.id
    assert resolved is not None
    assert resolved.id == second.id


@pytest.mark.asyncio
async def test_registry_refuses_to_purge_active_project(local_registry):
    project = await local_registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="active",
    )

    claimed = await local_registry.claim_project_purge(project.id)
    purged = await local_registry.mark_project_purged(project.id)

    assert claimed is None
    assert purged is None
    assert await local_registry.get_project(project.id) == project


@pytest.mark.asyncio
async def test_purge_claim_blocks_restore_status_and_keeps_deleted_list_contract(local_registry):
    project = await local_registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="claimed",
    )
    await local_registry.update_project_status(project.id, "deleted")

    claimed = await local_registry.claim_project_purge(project.id)
    restored = await local_registry.update_project_status(project.id, "active")
    listed = await local_registry.list_accessible_projects([("user", "local")])

    assert claimed is not None
    assert claimed.purge_claimed_at is not None
    assert restored is None
    assert [(item.id, item.status) for item in listed] == [(project.id, "deleted")]


@pytest.mark.asyncio
async def test_create_project_route_returns_409_for_duplicate_name(monkeypatch):
    from novelvideo.api.routes import projects as projects_route

    class DuplicateRegistry:
        async def create_project(self, **_kwargs):
            raise ValueError("Project 'agent' already exists")

    async def fake_user_id_from_api_user(_user):
        return "local"

    monkeypatch.setattr(projects_route, "validate_project_name", lambda _name: None)
    monkeypatch.setattr(projects_route, "user_id_from_api_user", fake_user_id_from_api_user)
    monkeypatch.setattr(projects_route, "get_project_registry", lambda: DuplicateRegistry())

    with pytest.raises(projects_route.HTTPException) as exc:
        await projects_route.create_project(
            projects_route.ProjectCreate(name="agent"),
            user={"id": "local", "username": "alice"},
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Project 'agent' already exists"
