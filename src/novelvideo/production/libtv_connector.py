"""Read-only LibTV connector backed exclusively by the official CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


class LibTVConnectorError(RuntimeError):
    pass


def find_libtv_cli() -> Path | None:
    configured = str(os.environ.get("LIBTV_CLI_PATH") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path.home() / ".libtv" / ("libtv.exe" if os.name == "nt" else "libtv"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _run_json(*args: str, timeout: int = 45) -> Any:
    executable = find_libtv_cli()
    if executable is None:
        raise LibTVConnectorError("LibTV official CLI is not installed")
    try:
        result = subprocess.run(
            [str(executable), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise LibTVConnectorError("LibTV CLI timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "LibTV CLI failed").strip()
        raise LibTVConnectorError(detail[-2000:])
    raw = result.stdout.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LibTVConnectorError("LibTV CLI returned invalid JSON") from exc


def get_status() -> dict[str, Any]:
    executable = find_libtv_cli()
    if executable is None:
        return {
            "available": False,
            "authenticated": False,
            "error": "CLI not installed",
        }
    try:
        account = _run_json("account", "info", timeout=20)
        return {
            "available": True,
            "authenticated": True,
            "cli_path": str(executable),
            "account": account,
        }
    except LibTVConnectorError as exc:
        return {
            "available": True,
            "authenticated": False,
            "cli_path": str(executable),
            "error": str(exc),
        }


def list_canvases(*, page: int = 1, page_size: int = 50) -> Any:
    return _run_json(
        "project",
        "list",
        "-p",
        str(max(1, page)),
        "-s",
        str(min(max(1, page_size), 100)),
    )


def get_canvas(canvas_uuid: str) -> dict[str, Any]:
    data = _run_json("project", canvas_uuid)
    if not isinstance(data, dict):
        raise LibTVConnectorError("LibTV canvas response is not an object")
    nodes = list(data.get("nodes") or [])
    edges = list(data.get("edges") or [])
    type_counts: dict[str, int] = {}
    for node in nodes:
        node_type = str((node or {}).get("type") or "unknown")
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
    return {
        **data,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_types": type_counts,
        },
        "external_url": f"https://www.liblib.tv/canvas?projectId={canvas_uuid}",
    }
