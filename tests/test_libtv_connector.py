from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novelvideo.production import libtv_connector


def test_libtv_connector_uses_official_cli_and_summarizes_canvas(
    tmp_path: Path, monkeypatch
):
    executable = tmp_path / "libtv.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setenv("LIBTV_CLI_PATH", str(executable))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "projectUuid": "canvas-1",
                    "nodes": [
                        {"id": "image-1", "type": "image"},
                        {"id": "video-1", "type": "video"},
                        {"id": "video-2", "type": "video"},
                    ],
                    "edges": [{"id": "edge-1"}],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(libtv_connector.subprocess, "run", run)
    canvas = libtv_connector.get_canvas("canvas-1")

    assert calls[0][0] == [str(executable), "project", "canvas-1"]
    assert calls[0][1]["creationflags"] == libtv_connector._creation_flags()
    assert canvas["summary"] == {
        "node_count": 3,
        "edge_count": 1,
        "node_types": {"image": 1, "video": 2},
    }
    assert canvas["external_url"].endswith("projectId=canvas-1")


def test_libtv_connector_reports_missing_cli(monkeypatch):
    monkeypatch.delenv("LIBTV_CLI_PATH", raising=False)
    monkeypatch.setattr(libtv_connector.Path, "is_file", lambda _self: False)

    assert libtv_connector.get_status() == {
        "available": False,
        "authenticated": False,
        "error": "CLI not installed",
    }
