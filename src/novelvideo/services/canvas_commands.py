"""Composition helpers for the workflow-to-canvas command boundary.

Workflow code depends on the port contract. The concrete Freezone gateway is
loaded lazily here so the adapter choice stays out of the workflow runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from novelvideo.ports.canvas_commands import (
    CanvasCommandPort,
    CanvasCommandPortError,
)


class _CanvasCommandAdapter:
    """Translate the legacy gateway error into the port-level contract."""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def apply(self, **kwargs: Any) -> dict[str, Any]:
        from novelvideo.freezone.canvas_command_gateway import CanvasCommandError

        try:
            return self._gateway.apply(**kwargs)
        except CanvasCommandError as exc:
            raise CanvasCommandPortError(
                str(exc),
                code=exc.code,
                op_index=exc.op_index,
                current_revision=exc.current_revision,
                details=exc.details,
            ) from exc


def make_canvas_command_port(
    *,
    project_dir: str | Path,
    project_id: str,
    actor_id: str = "xiaoshu-runtime",
) -> CanvasCommandPort:
    """Build the current canvas adapter without leaking it to callers."""
    from novelvideo.freezone.canvas_command_gateway import CanvasCommandGateway

    return _CanvasCommandAdapter(
        CanvasCommandGateway(
            project_dir=project_dir,
            project_id=project_id,
            actor_id=actor_id,
        )
    )


def read_canvas_snapshot(state_dir: str | Path, canvas_id: str) -> dict[str, Any] | None:
    """Read the authoritative canvas snapshot through the canvas adapter boundary."""
    from novelvideo.freezone import canvas_store

    snapshot = canvas_store.read_canvas(Path(state_dir), str(canvas_id))
    return snapshot if isinstance(snapshot, dict) else None


def fingerprint_canvas_script_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """Fingerprint script rows without letting workflow code import Freezone."""
    from novelvideo.freezone.script_contract import (
        script_rows_fingerprint as _fingerprint,
    )

    return _fingerprint(rows)


def validate_canvas_script_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    camera_names: Sequence[str] | None = None,
    director_plan: Mapping[str, Any] | None = None,
) -> Any:
    """Run the script contract rules through the canvas adapter boundary."""
    from novelvideo.freezone.script_contract import (
        validate_script_rows as _validate,
    )

    return _validate(rows, camera_names=camera_names, director_plan=director_plan)


def bind_canvas_script_source(
    state_dir: str | Path, *, canvas_id: str, node_id: str, revision: int
) -> dict[str, Any]:
    """Bind an asynchronous script result to a checked, persisted source."""
    snapshot = read_canvas_snapshot(state_dir, canvas_id)
    if not snapshot or snapshot.get("revision") != revision:
        raise ValueError("画布版本已变化，请重新读取脚本后再提交")
    node = next(
        (node for node in snapshot.get("nodes", [])
         if isinstance(node, dict) and node.get("id") == node_id),
        None,
    )
    if not node or node.get("type") != "scriptNode":
        raise ValueError("来源必须是当前画布中真实存在的脚本节点")
    return {"canvas_id": canvas_id, "node_id": node_id, "revision": revision}


__all__ = [
    "bind_canvas_script_source",
    "fingerprint_canvas_script_rows",
    "make_canvas_command_port",
    "read_canvas_snapshot",
    "validate_canvas_script_rows",
]
