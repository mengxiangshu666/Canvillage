import json
import sys

import pytest

from scripts.architecture.measure_canvas_build_pair import build_versions, fixture_nodes, measure, positions


def dist(root, build_id):
    root.mkdir()
    (root / "index.html").write_text("fixture", encoding="utf-8")
    (root / "version.json").write_text(json.dumps({"buildId": build_id}), encoding="utf-8")
    return root


def test_dist_pair_requires_real_different_builds(tmp_path):
    old = dist(tmp_path / "old", "old-id")
    new = dist(tmp_path / "new", "new-id")
    assert build_versions(old, new)[0]["buildId"] == "old-id"
    with pytest.raises(ValueError, match="differ"):
        build_versions(old, old)
    (new / "index.html").unlink()
    with pytest.raises(ValueError, match="index"):
        build_versions(old, new)


def test_fixture_is_forty_unique_media_free_script_nodes():
    nodes = fixture_nodes()
    assert len(nodes) == len({node["id"] for node in nodes}) == 40
    assert {node["type"] for node in nodes} == {"scriptNode"}
    assert "Url" not in json.dumps(nodes)
    assert positions({"nodes": nodes}) == {node["id"]: node["position"] for node in nodes}


def test_less_than_three_rounds_rejected_before_side_effects(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    with pytest.raises(ValueError, match="rounds"):
        measure(tmp_path, tmp_path, tmp_path / "report.json", rounds=2)
