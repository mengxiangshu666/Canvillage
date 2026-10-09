"""Measure which direct-video family a configured model's own credential can use.

Why this exists: `dmc.cc`-style NewAPI stations advertise `MiniMax-H3` in the
relay catalog, answer `503 No available channel for model ... under group ...`
on `/v1/video/generations`, and still expose a native MiniMax v2 family at
`/v2/video_generation`.  Which family a credential may use cannot be read from
the model catalog, so it is measured here.

The probe is zero-billing by construction: it only reads `GET /models` and
polls a *synthetic* task id, so no task can be created and nothing is charged.
The credential stays inside this process and is never printed or written.

Usage:

    .venv\\Scripts\\python.exe scripts\\acceptance\\direct_video_family_probe.py `
        --registry-id direct_video-8b01a39b81951012
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from novelvideo.generators.video.direct_models import list_direct_video_models  # noqa: E402
from novelvideo.generators.video.direct_video_protocol_contracts import (  # noqa: E402
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    get_direct_video_protocol_contract,
    protocol_base_url,
)


ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "direct-video-family"
SNIPPET_LIMIT = 240


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _redact(value: object, secret: str) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if secret:
        text = text.replace(secret, "***")
    return text[:SNIPPET_LIMIT]


def _request(
    client: httpx.Client, method: str, url: str, *, api_key: str
) -> dict[str, Any]:
    try:
        response = client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        return {"url": url, "status": None, "error": type(exc).__name__}
    return {
        "url": url,
        "status": response.status_code,
        "body": _redact(response.text, api_key),
    }


def _model_ids(body: str) -> set[str]:
    try:
        payload = json.loads(body)
    except ValueError:
        return set()
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return set()
    return {
        str(item.get("id") or "").strip().casefold()
        for item in entries
        if isinstance(item, dict)
    }


def _family_report(
    client: httpx.Client,
    *,
    family: str,
    base_url: str,
    query_paths: tuple[str, ...],
    api_key: str,
    upstream_model: str,
    task_id: str,
) -> dict[str, Any]:
    root = base_url.rstrip("/")
    models = _request(client, "GET", f"{root}/models", api_key=api_key)
    listed = upstream_model.strip().casefold() in _model_ids(models.get("body", ""))
    return {
        "family": family,
        "root": root,
        "models": {**models, "listsRequestedModel": listed},
        "queryProbes": [
            _request(client, "GET", f"{root}{path.format(task_id=task_id)}", api_key=api_key)
            for path in query_paths
        ],
    }


def _select_model(models: tuple[Any, ...], registry_id: str, upstream_model: str) -> Any:
    if registry_id:
        return next(
            (item for item in models if item.registry_id == registry_id),
            None,
        )
    wanted = upstream_model.strip().casefold()
    return next(
        (
            item
            for item in models
            if item.upstream_model.strip().casefold() == wanted
        ),
        None,
    )


def _submit_route_probes(
    client: httpx.Client,
    *,
    model: Any,
    openai_contract: Any,
    minimax_contract: Any,
    native_root: str,
) -> list[dict[str, Any]]:
    """Send a create whose model name cannot exist, so no task can be charged.

    The point is the *error shape*: a family that owns the route answers with a
    schema/model validation error, while a family that does not own it answers
    with a routing error such as `no available channel`.
    """

    invalid_model = f"__probe_invalid_{uuid.uuid4().hex[:8]}__"
    body = {"model": invalid_model, "content": [{"type": "text", "text": "probe"}]}
    targets = [
        (
            "relay-openai-video",
            f"{model.base_url.rstrip('/')}{openai_contract.submit_path}",
        ),
        (
            "relay-openai-video",
            f"{model.base_url.rstrip('/')}{openai_contract.ordered_submit_routes[-1]}",
        ),
        (
            "native-minimax-v2",
            f"{native_root.rstrip('/')}{minimax_contract.submit_path}",
        ),
    ]
    probes: list[dict[str, Any]] = []
    for family, url in dict.fromkeys(targets):
        try:
            response = client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {model.api_key}",
                    "Accept": "application/json",
                },
            )
            probes.append(
                {
                    "family": family,
                    "url": url,
                    "status": response.status_code,
                    "body": _redact(response.text, model.api_key),
                }
            )
        except httpx.HTTPError as exc:
            probes.append(
                {"family": family, "url": url, "status": None, "error": type(exc).__name__}
            )
    return probes


def _native_schema_probes(
    client: httpx.Client, *, model: Any, native_root: str, submit_path: str
) -> list[dict[str, Any]]:
    """Send deliberately incomplete bodies; none of them can render a video."""

    bodies = [
        {"model": model.upstream_model},
        {"model": model.upstream_model, "content": "invalid"},
    ]
    url = f"{native_root.rstrip('/')}{submit_path}"
    probes: list[dict[str, Any]] = []
    for body in bodies:
        try:
            response = client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {model.api_key}",
                    "Accept": "application/json",
                },
            )
            probes.append(
                {
                    "request": sorted(body),
                    "status": response.status_code,
                    "body": _redact(response.text, model.api_key),
                }
            )
        except httpx.HTTPError as exc:
            probes.append(
                {"request": sorted(body), "status": None, "error": type(exc).__name__}
            )
    return probes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-id", default="")
    parser.add_argument("--upstream-model", default="")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--probe-invalid-submit",
        action="store_true",
        help=(
            "also POST an intentionally non-existent model name to the create "
            "routes to read their error shape; no task can be created"
        ),
    )
    parser.add_argument(
        "--probe-native-schema",
        action="store_true",
        help=(
            "also POST incomplete bodies to the native create route to read its "
            "field requirements; none of them can render a video"
        ),
    )
    args = parser.parse_args()

    models = list_direct_video_models()
    model = _select_model(models, args.registry_id, args.upstream_model)
    if model is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "model_not_found",
                    "configured": [
                        {
                            "registryId": item.registry_id,
                            "upstreamModel": item.upstream_model,
                        }
                        for item in models
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    openai_contract = get_direct_video_protocol_contract(DIRECT_VIDEO_PROTOCOL_OPENAI)
    minimax_contract = get_direct_video_protocol_contract(
        DIRECT_VIDEO_PROTOCOL_MINIMAX_V2
    )
    task_id = f"probe-{uuid.uuid4().hex[:12]}"
    native_root = protocol_base_url(model.base_url, minimax_contract)

    with httpx.Client(timeout=max(2.0, float(args.timeout)), follow_redirects=False) as client:
        families = [
            _family_report(
                client,
                family="relay-openai-video",
                base_url=model.base_url,
                query_paths=(
                    openai_contract.query_path_template,
                    "/video/generations/{task_id}",
                    "/tasks/{task_id}",
                ),
                api_key=model.api_key,
                upstream_model=model.upstream_model,
                task_id=task_id,
            )
        ]
        if native_root.rstrip("/") != model.base_url.rstrip("/"):
            families.append(
                _family_report(
                    client,
                    family="native-minimax-v2",
                    base_url=native_root,
                    query_paths=(minimax_contract.query_path_template,),
                    api_key=model.api_key,
                    upstream_model=model.upstream_model,
                    task_id=task_id,
                )
            )

    report = {
        "ok": True,
        "probedAt": _stamp(),
        "registryId": model.registry_id,
        "upstreamModel": model.upstream_model,
        "configuredProtocol": model.protocol,
        "baseUrl": model.base_url,
        "probeTaskId": task_id,
        "families": families,
    }
    if args.probe_invalid_submit:
        with httpx.Client(
            timeout=max(2.0, float(args.timeout)), follow_redirects=False
        ) as client:
            report["invalidSubmitProbes"] = _submit_route_probes(
                client,
                model=model,
                openai_contract=openai_contract,
                minimax_contract=minimax_contract,
                native_root=native_root,
            )
    if args.probe_native_schema:
        with httpx.Client(
            timeout=max(2.0, float(args.timeout)), follow_redirects=False
        ) as client:
            report["nativeSchemaProbes"] = _native_schema_probes(
                client,
                model=model,
                native_root=native_root,
                submit_path=minimax_contract.submit_path,
            )
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (ARTIFACT_DIR / f"{report['probedAt']}-{model.registry_id}.json").write_text(
        payload, encoding="utf-8"
    )
    (ARTIFACT_DIR / "latest.json").write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
