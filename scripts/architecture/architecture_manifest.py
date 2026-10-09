"""Load and query the machine-readable AI architecture manifest."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_MANIFEST = Path(__file__).with_name("architecture_manifest.json")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("architecture manifest must use schema_version 1")
    owners = payload.get("owners")
    contracts = payload.get("critical_contracts")
    gates = payload.get("gates")
    if not isinstance(owners, list) or not owners:
        raise ValueError("architecture manifest owners must be a non-empty list")
    if not isinstance(contracts, list):
        raise ValueError("architecture manifest critical_contracts must be a list")
    if not isinstance(gates, dict) or not gates:
        raise ValueError("architecture manifest gates must be a non-empty object")
    return payload


def owner_paths(owner: dict[str, Any]) -> tuple[str, ...]:
    paths = owner.get("paths")
    if not isinstance(paths, list) or not paths:
        return ()
    return tuple(
        sorted(
            {
                PurePosixPath(str(path).replace("\\", "/")).as_posix()
                for path in paths
                if str(path).strip()
            },
            key=len,
            reverse=True,
        )
    )


def resolve_owner(manifest: dict[str, Any], path: str) -> dict[str, Any] | None:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    matches: list[tuple[int, dict[str, Any]]] = []
    for owner in manifest.get("owners", []):
        if not isinstance(owner, dict):
            continue
        for owner_path in owner_paths(owner):
            if normalized == owner_path or normalized.startswith(owner_path + "/"):
                matches.append((len(owner_path), owner))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def contract_for_path(manifest: dict[str, Any], path: str) -> dict[str, Any] | None:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    for contract in manifest.get("critical_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_path = PurePosixPath(
            str(contract.get("path") or "").replace("\\", "/")
        ).as_posix()
        if normalized == contract_path:
            return contract
    return None
