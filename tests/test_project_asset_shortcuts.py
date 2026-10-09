from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import projects as projects_route
from novelvideo.embedding_models import (
    EmbeddingModelNotConfiguredError,
    EmbeddingModelSpec,
)
from novelvideo.project_context import ProjectContext
from novelvideo.utils import project_shortcuts


def _symlink_directory(link: Path, target: Path) -> None:
    link.symlink_to(target, target_is_directory=True)


def _entry_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


@pytest.fixture
def project_environment(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.ports.local.project import SQLiteProjectRegistry

    data_root = tmp_path / "aigc"
    output_root = data_root / "output"
    state_root = data_root / "state"
    runtime_root = data_root / "runtime"
    monkeypatch.setenv("NOVELVIDEO_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOVELVIDEO_OUTPUT_DIR", str(output_root))
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_root))
    monkeypatch.setenv("NOVELVIDEO_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setattr(config, "DATA_ROOT", str(data_root), raising=False)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output_root), raising=False)
    monkeypatch.setattr(config, "STATE_DIR", str(state_root), raising=False)
    monkeypatch.setattr(config, "RUNTIME_DIR", str(runtime_root), raising=False)
    monkeypatch.setattr(
        project_shortcuts,
        "_create_directory_link",
        _symlink_directory,
    )
    return data_root, SQLiteProjectRegistry()


def _context(record) -> ProjectContext:
    return ProjectContext(
        project_id=record.id,
        project_name=record.name,
        owner_type=record.owner_type,
        owner_id=record.owner_id,
        owner_username=record.owner_username,
        requester_user_id="local",
        requester_username=record.owner_username,
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id=record.home_node_id,
        output_dir=Path(record.output_dir),
        state_dir=Path(record.state_dir),
        runtime_dir=Path(record.runtime_dir),
        is_home_node=True,
    )


async def _install_route_context(monkeypatch, registry, record) -> ProjectContext:
    ctx = _context(record)

    async def resolve_context(**_kwargs):
        return ctx

    async def ignore_audit(**_kwargs):
        return None

    monkeypatch.setattr(projects_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(projects_route, "get_project_registry", lambda: registry)
    monkeypatch.setattr(projects_route, "emit_project_audit", ignore_audit)
    return ctx


def test_shortcut_lifecycle_uses_links_and_never_deletes_output(tmp_path):
    data_root = tmp_path / "aigc"
    output_dir = data_root / "output" / "local" / "demo"
    output_dir.mkdir(parents=True)
    payload = output_dir / "frame.png"
    payload.write_bytes(b"frame")

    project_shortcuts.sync_project_shortcut(
        project_name="demo",
        output_dir=output_dir,
        deleted=False,
        data_root=data_root,
        link_creator=_symlink_directory,
    )
    active = data_root / "项目" / "demo"
    deleted = data_root / "项目" / "demo（已删除）"
    assert active.is_symlink()
    assert active.resolve() == output_dir.resolve()

    project_shortcuts.sync_project_shortcut(
        project_name="demo",
        output_dir=output_dir,
        deleted=True,
        data_root=data_root,
        link_creator=_symlink_directory,
    )
    assert not _entry_exists(active)
    assert deleted.is_symlink()

    project_shortcuts.sync_project_shortcut(
        project_name="demo",
        output_dir=output_dir,
        deleted=False,
        data_root=data_root,
        link_creator=_symlink_directory,
    )
    assert active.is_symlink()
    assert not _entry_exists(deleted)

    project_shortcuts.remove_project_shortcuts("demo", data_root=data_root)
    assert not _entry_exists(active)
    assert not _entry_exists(deleted)
    assert payload.read_bytes() == b"frame"


def test_remove_project_shortcuts_handles_broken_links(tmp_path):
    data_root = tmp_path / "aigc"
    shortcuts_root = data_root / "项目"
    shortcuts_root.mkdir(parents=True)
    missing_target = data_root / "output" / "local" / "missing"
    active = shortcuts_root / "missing"
    deleted = shortcuts_root / "missing（已删除）"
    _symlink_directory(active, missing_target)
    _symlink_directory(deleted, missing_target)

    assert _entry_exists(active)
    assert _entry_exists(deleted)
    project_shortcuts.remove_project_shortcuts("missing", data_root=data_root)

    assert not _entry_exists(active)
    assert not _entry_exists(deleted)


def test_remove_project_shortcuts_refuses_real_directory(tmp_path):
    data_root = tmp_path / "aigc"
    collision = data_root / "项目" / "demo"
    collision.mkdir(parents=True)
    sentinel = collision / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(project_shortcuts.ProjectShortcutCollisionError):
        project_shortcuts.remove_project_shortcuts("demo", data_root=data_root)

    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_routes_sync_shortcut_for_create_delete_restore_and_purge(
    monkeypatch,
    project_environment,
):
    data_root, registry = project_environment

    async def local_user_id(_user):
        return "local"

    monkeypatch.setattr(projects_route, "user_id_from_api_user", local_user_id)
    monkeypatch.setattr(projects_route, "get_project_registry", lambda: registry)
    monkeypatch.setattr(
        projects_route,
        "embedding_model_binding_for_new_project",
        lambda: EmbeddingModelSpec(
            internal_model="direct/embedding-test",
            dimensions=1024,
            send_dimensions=True,
            gateway="direct",
            upstream_model="embedding-test",
        ),
    )
    created = await projects_route.create_project(
        projects_route.ProjectCreate(name="demo"),
        user={"id": "local", "username": "alice"},
    )
    project_id = created["data"]["project_id"]
    record = await registry.get_project(project_id)
    assert record is not None
    await _install_route_context(monkeypatch, registry, record)

    active = data_root / "项目" / "demo"
    deleted = data_root / "项目" / "demo（已删除）"
    assert active.is_symlink()

    await projects_route.soft_delete_project(project_id, user={"username": "alice"})
    assert not _entry_exists(active)
    assert deleted.is_symlink()

    await projects_route.restore_project(project_id, user={"username": "alice"})
    assert active.is_symlink()
    assert not _entry_exists(deleted)

    await projects_route.soft_delete_project(project_id, user={"username": "alice"})
    result = await projects_route.purge_project(project_id, user={"username": "alice"})

    assert result["ok"] is True
    assert await registry.get_project(project_id) is None
    assert not _entry_exists(active)
    assert not _entry_exists(deleted)


@pytest.mark.asyncio
async def test_create_project_reports_missing_embedding_model_and_cleans_up(
    monkeypatch,
    project_environment,
):
    _data_root, registry = project_environment

    async def local_user_id(_user):
        return "local"

    monkeypatch.setattr(projects_route, "user_id_from_api_user", local_user_id)
    monkeypatch.setattr(projects_route, "get_project_registry", lambda: registry)
    monkeypatch.setattr(
        projects_route,
        "embedding_model_binding_for_new_project",
        lambda: (_ for _ in ()).throw(
            EmbeddingModelNotConfiguredError("请配置 embedding 模型")
        ),
    )

    with pytest.raises(projects_route.HTTPException) as exc:
        await projects_route.create_project(
            projects_route.ProjectCreate(name="needs_embedding"),
            user={"id": "local", "username": "alice"},
        )

    assert exc.value.status_code == 409
    assert "embedding 模型" in exc.value.detail
    assert await registry.list_accessible_projects([("user", "local")]) == []


@pytest.mark.asyncio
async def test_purge_keeps_deleted_registry_row_when_second_directory_fails_then_retries(
    monkeypatch,
    project_environment,
):
    data_root, registry = project_environment
    record = await registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="retry",
    )
    for directory in (record.output_dir, record.state_dir, record.runtime_dir):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "sentinel.txt").write_text("keep until removed", encoding="utf-8")
    record = await registry.update_project_status(record.id, "deleted")
    assert record is not None
    project_shortcuts.sync_project_shortcut(
        project_name=record.name,
        output_dir=record.output_dir,
        deleted=True,
        data_root=data_root,
        link_creator=_symlink_directory,
    )
    await _install_route_context(monkeypatch, registry, record)

    real_rmtree = shutil.rmtree
    state_dir = Path(record.state_dir)
    # The lock must outlast purge_project's own retry window, otherwise the
    # retry (which exists for the Windows WinError 32 case) would absorb it and
    # this test could no longer observe a failed purge.
    lock_held = True

    def fail_second_directory_while_locked(path, *args, **kwargs):
        if Path(path) == state_dir and lock_held:
            raise PermissionError("simulated locked state directory")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        projects_route.shutil, "rmtree", fail_second_directory_while_locked
    )
    with pytest.raises(PermissionError, match="simulated locked state directory"):
        await projects_route.purge_project(record.id, user={"username": "alice"})

    retained = await registry.get_project(record.id)
    assert retained is not None
    assert retained.status == "deleted"
    assert retained.purged_at is None
    assert retained.purge_claimed_at is not None
    assert not Path(record.output_dir).exists()
    assert state_dir.exists()
    assert Path(record.runtime_dir).exists()
    deleted_link = data_root / "项目" / "retry（已删除）"
    assert _entry_exists(deleted_link), "失败后允许保留已经断链、可在重试时清理的入口"

    lock_held = False
    monkeypatch.setattr(projects_route.shutil, "rmtree", real_rmtree)
    result = await projects_route.purge_project(record.id, user={"username": "alice"})

    assert result["ok"] is True
    assert await registry.get_project(record.id) is None
    assert not state_dir.exists()
    assert not Path(record.runtime_dir).exists()
    assert not _entry_exists(deleted_link)


@pytest.mark.asyncio
async def test_purge_absorbs_a_transient_windows_file_lock(
    monkeypatch,
    project_environment,
):
    """WinError 32 lasts milliseconds; a purge should not fail on it."""

    _data_root, registry = project_environment
    record = await registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="locked",
    )
    for directory in (record.output_dir, record.state_dir, record.runtime_dir):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "sentinel.txt").write_text("locked", encoding="utf-8")
    record = await registry.update_project_status(record.id, "deleted")
    assert record is not None
    await _install_route_context(monkeypatch, registry, record)

    real_rmtree = shutil.rmtree
    state_dir = Path(record.state_dir)
    releases = {"count": 0}

    def lock_once_when_delete_attempted(path, *args, **kwargs):
        if Path(path) == state_dir and releases["count"] == 0:
            releases["count"] += 1
            raise PermissionError("[WinError 32] 另一个程序正在使用此文件")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        projects_route.shutil, "rmtree", lock_once_when_delete_attempted
    )

    result = await projects_route.purge_project(record.id, user={"username": "alice"})

    assert result["ok"] is True
    assert releases["count"] == 1, "重试必须真的发生过"
    assert await registry.get_project(record.id) is None
    assert not state_dir.exists()


@pytest.mark.asyncio
async def test_cleanup_of_uncommitted_dirs_absorbs_a_transient_lock(
    monkeypatch,
    tmp_path,
):
    """The create-project compensation path shares the same retry."""

    target = tmp_path / "output"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "file.bin").write_bytes(b"x")

    real_rmtree = shutil.rmtree
    calls = {"count": 0}

    def lock_once(path, *args, **kwargs):
        if calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError("[WinError 32] 另一个程序正在使用此文件")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(projects_route.shutil, "rmtree", lock_once)

    projects_route._cleanup_uncommitted_project_dirs(
        SimpleNamespace(
            output_dir=str(target),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )
    )

    assert calls["count"] == 1
    assert not target.exists()


@pytest.mark.asyncio
async def test_restore_conflicts_after_purge_claim_before_files_are_deleted(
    monkeypatch,
    project_environment,
):
    _data_root, registry = project_environment
    record = await registry.create_project(
        owner_user_id="local",
        owner_username="alice",
        name="claim-race",
    )
    for directory in (record.output_dir, record.state_dir, record.runtime_dir):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / "sentinel.txt").write_text("must not be restored", encoding="utf-8")
    record = await registry.update_project_status(record.id, "deleted")
    assert record is not None
    await _install_route_context(monkeypatch, registry, record)

    real_claim = registry.claim_project_purge
    claim_acquired = asyncio.Event()
    continue_purge = asyncio.Event()

    async def pause_after_claim(project_id):
        claimed = await real_claim(project_id)
        claim_acquired.set()
        await continue_purge.wait()
        return claimed

    monkeypatch.setattr(registry, "claim_project_purge", pause_after_claim)
    purge_task = asyncio.create_task(
        projects_route.purge_project(record.id, user={"username": "alice"})
    )
    await claim_acquired.wait()

    with pytest.raises(projects_route.HTTPException) as exc:
        await projects_route.restore_project(record.id, user={"username": "alice"})
    assert exc.value.status_code == 409
    assert all(
        (Path(directory) / "sentinel.txt").exists()
        for directory in (record.output_dir, record.state_dir, record.runtime_dir)
    )

    continue_purge.set()
    result = await purge_task

    assert result["ok"] is True
    assert await registry.get_project(record.id) is None
    assert all(
        not Path(directory).exists()
        for directory in (record.output_dir, record.state_dir, record.runtime_dir)
    )
