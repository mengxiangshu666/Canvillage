from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.architecture.export_archify_snapshot import build_snapshot


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_has_stable_unique_components_and_repository_evidence() -> None:
    snapshot = build_snapshot()
    component_ids = [component["id"] for component in snapshot["components"]]

    assert snapshot["schema_version"] == 1
    assert snapshot["diagram_type"] == "architecture"
    assert snapshot["meta"]["quality_profile"] == "showcase"
    assert len(component_ids) == len(set(component_ids))
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value) for value in component_ids)
    assert re.fullmatch(r"https://github\.com/[^/]+/[^/]+", snapshot["meta"]["repository"]["url"])
    assert re.fullmatch(r"[0-9a-f]{40}", snapshot["meta"]["repository"]["revision"])

    for component in snapshot["components"]:
        sources = component["sources"]
        assert len(sources) == 1
        assert (ROOT / sources[0]["path"]).is_file()
        assert "C:" not in sources[0]["path"]
        assert "\\" not in sources[0]["path"]


def test_snapshot_reads_frontend_build_identity_without_private_state() -> None:
    snapshot = build_snapshot()
    version_file = json.loads((ROOT / "frontend" / "dist" / "version.json").read_text(encoding="utf-8"))
    subtitle = snapshot["meta"]["subtitle"]

    assert version_file["version"] in subtitle
    assert version_file["buildId"] in subtitle
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "项目资产" not in serialized
    assert not re.search(r"(?i)(api[_-]?key|token|secret|password|cookie|authorization)\s*[:=]", serialized)
