from __future__ import annotations

from urllib.parse import parse_qs, urlparse


import pytest


def test_stage_manifest_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    from novelvideo.director_world import stage_manifest

    original_replace = stage_manifest.os.replace
    attempts = []
    delays = []

    def replace_after_one_lock(source, destination):
        attempts.append((source, destination))
        if len(attempts) == 1:
            raise PermissionError(5, "temporarily locked")
        original_replace(source, destination)

    monkeypatch.setattr(stage_manifest.os, "name", "nt")
    monkeypatch.setattr(stage_manifest.os, "replace", replace_after_one_lock)
    monkeypatch.setattr(stage_manifest.time, "sleep", delays.append)

    saved = stage_manifest.save_manifest(tmp_path, "中庭", {"source": "uploaded_360"})
    manifest = stage_manifest.load_manifest(tmp_path, "中庭")

    assert len(attempts) == 2
    assert delays == [stage_manifest._WINDOWS_REPLACE_RETRY_SECONDS]
    assert manifest is not None
    assert manifest["schema_version"] == stage_manifest.SCHEMA_VERSION
    assert manifest["scene_id"] == "中庭"
    assert manifest["version"] == "v1"
    assert manifest["source"] == "uploaded_360"
    assert saved.exists()
    assert not saved.with_suffix(saved.suffix + ".tmp").exists()


@pytest.mark.parametrize("env_name", ["DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"])
def test_3gs_stage_url_uses_configured_director_viewer_origin(tmp_path, monkeypatch, env_name):
    from novelvideo.director_world import stage_manifest
    from novelvideo.director_world.service import DirectorWorldService

    # Both env vars are honoured by the service (DIRECTOR_VIEWER_URL first),
    # so clear any inherited values and set only the one under test to make
    # the resulting netloc deterministic and machine-independent.
    expected_netloc = "127.0.0.1:9024"
    for name in ("DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, f"http://{expected_netloc}")
    stage_dir = stage_manifest.stage_dir(tmp_path, "地下室")
    stage_dir.mkdir(parents=True)
    (stage_dir / "master_sharp.ply").write_bytes(b"ply")
    (stage_dir / "master_sharp.sog").write_bytes(b"sog")
    stage_manifest.update_manifest(
        tmp_path,
        "地下室",
        source="uploaded_master",
        ply_path="master_sharp.ply",
        master_ply_path="master_sharp.ply",
    )

    url = DirectorWorldService(tmp_path).make_3gs_editor_url(
        episode=1,
        scene_id="地下室",
        user="admin",
        project="shiguangshuwu",
        slate_beat=2,
    )

    parsed = urlparse(url or "")
    params = parse_qs(parsed.query)
    assert parsed.scheme == "http"
    assert parsed.netloc == expected_netloc
    assert parsed.path == "/app/viewer/playcanvas_3gs_stage.html"
    assert params["scene_id"] == ["地下室"]
    assert params["beat"] == ["2"]
    assert params["scene_3gs_ply_fs"][0].endswith("/master_sharp.sog")
    assert params["scene_3gs_master_ply_fs"][0].endswith("/master_sharp.sog")
