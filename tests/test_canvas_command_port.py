from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.ports.canvas_commands import CanvasCommandPortError
from novelvideo.services.canvas_commands import make_canvas_command_port


def test_canvas_command_port_factory_keeps_receipt_shape(monkeypatch, tmp_path: Path):
    from novelvideo.freezone import canvas_command_gateway

    class FakeGateway:
        def __init__(self, **kwargs):
            assert kwargs == {
                "project_dir": tmp_path,
                "project_id": "project-1",
                "actor_id": "workflow-runtime",
            }

        def apply(self, **kwargs):
            return {"schema": "canvas_command_receipt.v2", "revision": 7, **kwargs}

    monkeypatch.setattr(canvas_command_gateway, "CanvasCommandGateway", FakeGateway)
    port = make_canvas_command_port(
        project_dir=tmp_path,
        project_id="project-1",
        actor_id="workflow-runtime",
    )

    receipt = port.apply(
        canvas_id="canvas-1",
        envelope={"command_id": "command-1"},
    )

    assert receipt["schema"] == "canvas_command_receipt.v2"
    assert receipt["revision"] == 7
    assert receipt["canvas_id"] == "canvas-1"


def test_canvas_command_port_translates_legacy_gateway_errors(monkeypatch, tmp_path: Path):
    from novelvideo.freezone import canvas_command_gateway

    class FailingGateway:
        def __init__(self, **_kwargs):
            pass

        def apply(self, **_kwargs):
            raise canvas_command_gateway.CanvasCommandError(
                "revision conflict",
                code="canvas_revision_conflict",
                current_revision=4,
                details={"expected_revision": 3},
            )

    monkeypatch.setattr(canvas_command_gateway, "CanvasCommandGateway", FailingGateway)
    port = make_canvas_command_port(project_dir=tmp_path, project_id="project-1")

    with pytest.raises(CanvasCommandPortError) as raised:
        port.apply(canvas_id="canvas-1", envelope={"command_id": "command-1"})

    assert raised.value.code == "canvas_revision_conflict"
    assert raised.value.current_revision == 4
    assert raised.value.to_dict()["details"] == {"expected_revision": 3}
