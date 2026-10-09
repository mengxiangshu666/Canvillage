"""Account avatar storage and its HTTP contract."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from novelvideo import account_avatar


def _png(size: tuple[int, int] = (64, 64), colour: tuple[int, int, int] = (10, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _bmp(size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="BMP")
    return buffer.getvalue()


def _avatar_dir(tmp_path: Path, username: str = "local") -> Path:
    return account_avatar.account_state_dir(username, state_root=tmp_path)


def test_save_then_read_round_trip(tmp_path):
    payload = _png()

    record = account_avatar.save_avatar("local", payload, state_root=tmp_path)

    assert record.path == _avatar_dir(tmp_path) / "avatar.png"
    assert record.content_type == "image/png"
    assert record.size_bytes == len(payload)
    assert record.path.read_bytes() == payload

    stored = account_avatar.read_avatar("local", state_root=tmp_path)
    assert stored == record
    assert account_avatar.avatar_url("local", record) == (
        f"/static/avatars/local/avatar.png?v={record.version}"
    )


def test_no_avatar_reads_as_none(tmp_path):
    assert account_avatar.read_avatar("local", state_root=tmp_path) is None
    assert account_avatar.delete_avatar("local", state_root=tmp_path) is False


def test_payload_that_is_not_an_image_is_rejected(tmp_path):
    with pytest.raises(account_avatar.AvatarError) as excinfo:
        account_avatar.save_avatar("local", b"not an image", state_root=tmp_path)

    assert "PNG" in str(excinfo.value)
    assert list(_avatar_dir(tmp_path).glob("avatar.*")) == []


def test_empty_and_oversized_payloads_are_rejected(tmp_path):
    with pytest.raises(account_avatar.AvatarError):
        account_avatar.save_avatar("local", b"", state_root=tmp_path)

    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * account_avatar.MAX_AVATAR_BYTES
    with pytest.raises(account_avatar.AvatarError) as excinfo:
        account_avatar.save_avatar("local", oversized, state_root=tmp_path)
    assert "MB" in str(excinfo.value)


def test_declared_content_type_does_not_decide_the_stored_format(tmp_path):
    record = account_avatar.save_avatar(
        "local", _png(), state_root=tmp_path, declared_type="image/jpeg"
    )

    assert record.path.name == "avatar.png"
    assert record.content_type == "image/png"


def test_decodable_but_non_web_format_is_converted_to_png(tmp_path):
    record = account_avatar.save_avatar("local", _bmp(), state_root=tmp_path)

    assert record.path.name == "avatar.png"
    with Image.open(record.path) as stored:
        assert stored.format == "PNG"
        assert stored.size == (32, 32)


def test_absurd_dimensions_are_rejected(tmp_path):
    with pytest.raises(account_avatar.AvatarError) as excinfo:
        account_avatar.save_avatar("local", _png((9000, 4)), state_root=tmp_path)

    assert "边长" in str(excinfo.value)


def test_replacing_an_avatar_leaves_exactly_one_file(tmp_path):
    account_avatar.save_avatar("local", _png(), state_root=tmp_path)
    second = account_avatar.save_avatar("local", _png(colour=(5, 5, 5)), state_root=tmp_path)

    assert [path.name for path in _avatar_dir(tmp_path).glob("avatar.*")] == ["avatar.png"]
    assert second.path.read_bytes() == _png(colour=(5, 5, 5))


def test_reupload_changes_the_cache_busting_version(tmp_path):
    first = account_avatar.save_avatar("local", _png(), state_root=tmp_path)
    second = account_avatar.save_avatar("local", _png(colour=(9, 9, 9)), state_root=tmp_path)

    assert account_avatar.avatar_url("local", first) != account_avatar.avatar_url("local", second)


def test_delete_removes_every_variant(tmp_path):
    directory = _avatar_dir(tmp_path)
    account_avatar.save_avatar("local", _png(), state_root=tmp_path)
    # A file left behind by an older version must not resurrect after delete.
    (directory / "avatar.gif").write_bytes(b"stale")

    assert account_avatar.delete_avatar("local", state_root=tmp_path) is True
    assert list(directory.glob("avatar.*")) == []
    assert account_avatar.read_avatar("local", state_root=tmp_path) is None


def test_account_directory_cannot_escape_the_state_root(tmp_path):
    for hostile in ("../../etc", "..", ".", "a/b", "用户名", "", "  "):
        directory = account_avatar.account_state_dir(hostile, state_root=tmp_path)

        assert directory.parent.parent == tmp_path
        assert ".." not in directory.parts
        assert directory.name == account_avatar.ACCOUNT_DIR_NAME

    # Distinct exotic names must not collapse into one folder.
    assert account_avatar.account_state_dir(
        "用户名", state_root=tmp_path
    ) != account_avatar.account_state_dir("用户名二", state_root=tmp_path)


def test_static_resolution_serves_only_the_sessions_own_file(tmp_path):
    record = account_avatar.save_avatar("local", _png(), state_root=tmp_path)

    resolved = account_avatar.resolve_static_avatar(
        "local", "avatar.png", session_username="local", state_root=tmp_path
    )
    assert resolved == record

    for username, filename in (
        ("someone-else", "avatar.png"),
        ("local", "avatar.jpg"),
        ("local", "../avatar.png"),
        ("local", "avatar.png.bak"),
    ):
        with pytest.raises(account_avatar.AvatarError):
            account_avatar.resolve_static_avatar(
                username,
                filename,
                session_username="local",
                state_root=tmp_path,
            )


def _route_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, username: str = "local"):
    from fastapi import Depends, FastAPI

    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import account as account_route

    monkeypatch.setattr(account_route, "STATE_DIR", tmp_path)
    app = FastAPI()
    app.include_router(account_route.router, prefix="/api/v1")
    # The static route lives in app.py, so mirror just that one registration
    # here to exercise the real serving helper end to end.
    @app.get("/static/avatars/{username}/{filename}", include_in_schema=False)
    async def _static(username: str, filename: str, user: dict = Depends(get_api_user)):
        return await account_route.account_avatar_file_response(username, filename, user)

    app.dependency_overrides[get_api_user] = lambda: {
        "username": username,
        "role": "owner",
    }
    return app


@pytest.mark.asyncio
async def test_avatar_routes_read_upload_and_clear(tmp_path, monkeypatch):
    import httpx

    app = _route_client(tmp_path, monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get("/api/v1/account/avatar")
        assert empty.status_code == 200
        assert empty.json() == {"ok": True, "data": {"avatar_url": None}}

        rejected = await client.post(
            "/api/v1/account/avatar",
            files={"file": ("a.png", b"nope", "image/png")},
        )
        assert rejected.status_code == 400
        assert "图片" in rejected.json()["detail"]

        uploaded = await client.post(
            "/api/v1/account/avatar",
            files={"file": ("a.png", _png(), "image/png")},
        )
        assert uploaded.status_code == 200
        url = uploaded.json()["data"]["avatar_url"]
        assert url.startswith("/static/avatars/local/avatar.png?v=")

        served = await client.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert served.headers["cache-control"].startswith("private")
        assert served.content == _png()

        again = await client.get("/api/v1/account/avatar")
        assert again.json()["data"]["avatar_url"] == url

        cleared = await client.delete("/api/v1/account/avatar")
        assert cleared.status_code == 200
        assert cleared.json()["data"] == {"avatar_url": None, "removed": True}
        assert (await client.get("/api/v1/account/avatar")).json()["data"]["avatar_url"] is None

        gone = await client.get("/static/avatars/local/avatar.png")
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_static_route_hides_other_accounts(tmp_path, monkeypatch):
    import httpx

    account_avatar.save_avatar("victim", _png(), state_root=tmp_path)
    app = _route_client(tmp_path, monkeypatch, username="intruder")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/avatars/victim/avatar.png")
        own = await client.get("/static/avatars/intruder/avatar.png")

    assert response.status_code == 404
    assert own.status_code == 404


def test_avatar_url_is_stable_for_one_file(tmp_path):
    record = account_avatar.save_avatar("local", _png(), state_root=tmp_path)
    again = account_avatar.read_avatar("local", state_root=tmp_path)

    assert again is not None
    assert account_avatar.avatar_url("local", again) == account_avatar.avatar_url(
        "local", record
    )
