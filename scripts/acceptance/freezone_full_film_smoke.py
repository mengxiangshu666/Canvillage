"""Generate one real short film end to end through the product's own endpoints.

Chain (all real upstream calls, no mocks):

    story script (text model)
      -> storyboard image per shot (direct image model)
      -> shot video per image (image-to-video, dmc.cc MiniMax-H3 channel)
      -> compose the clips into one mp4 (ffmpeg)

Paid generation is fail-closed: pass ``--allow-paid-generation`` to authorize the
run. The script prints a JSON receipt, copies every artifact under
``workspace/artifacts/freezone-film/`` and (unless ``--keep-project``) purges the
temporary project it created.

Usage:

    .venv\\Scripts\\python.exe scripts\\acceptance\\freezone_full_film_smoke.py ^
        --allow-paid-generation --shots 2 --duration 5
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from novelvideo.workflow_runtime.script_asset_ledger import (
    SCRIPT_REFERENCE_IMAGE_CAP,
    asset_references_for_shot,
    build_script_asset_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "freezone-film"
_LOG_LOCK = threading.Lock()
_ASSET_TAG_SPLIT_RE = re.compile(r"[、,，;；/|]+")

# dmc.cc MiniMax-H3 direct channel (protocol minimax-video-v2). This is the same
# registry id the canvas model picker hands to /freezone/video/i2v.
DEFAULT_VIDEO_BACKEND = "direct_video-8b01a39b81951012"
DEFAULT_VIDEO_RESOLUTION = "768p"
DEFAULT_ASPECT_RATIO = "16:9"

TERMINAL_FAILURES = {"failed", "cancelled", "error"}

# Mirrors `_DURATION_PATTERNS` in src/novelvideo/freezone/video_request_contract.py.
# The product rejects a request whose prompt states a total duration different from
# the node duration, so the driver strips those phrases and lets the requested
# duration be the single authority.
_DURATION_MENTION_PATTERNS = (
    re.compile(
        r"(?:视频|影片|镜头|片段|全片|总时长|时长|持续(?:时间)?|duration|video|shot|clip|lasts?)"
        r"[^\n。；;]{0,12}?\d+(?:\.\d+)?\s*(?:秒|s|sec(?:ond)?s?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\d+(?:\.\d+)?\s*(?:秒|s|sec(?:ond)?s?)"
        r"\s*(?:视频|影片|镜头|片段|时长|duration|video|shot|clip)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\d+(?:\.\d+)?[- ](?:second|sec|s)\s*(?:video|clip|shot)",
        re.IGNORECASE,
    ),
)


def _strip_duration_mentions(text: str) -> str:
    cleaned = str(text or "")
    for pattern in _DURATION_MENTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return " ".join(cleaned.split())


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _log(message: str) -> None:
    with _LOG_LOCK:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned non-JSON "
            f"HTTP {response.status_code}: {response.text[:400]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{type(payload).__name__}, expected an object"
        )
    return payload


def _data(payload: dict[str, Any]) -> Any:
    if payload.get("ok") is not True or "data" not in payload:
        raise RuntimeError(f"runtime response is not successful: {payload}")
    return payload["data"]


class RuntimeClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_counts: dict[str, int] = {}
        self.request_durations: list[float] = []
        self._request_started_at: dict[int, float] = {}
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=True,
            event_hooks={
                "request": [self._record_request_start],
                "response": [self._record_request_end],
            },
        )

    def _record_request_start(self, request: httpx.Request) -> None:
        self._request_started_at[id(request)] = time.perf_counter()
        key = f"{request.method} {request.url.path}"
        self.request_counts[key] = self.request_counts.get(key, 0) + 1

    def _record_request_end(self, response: httpx.Response) -> None:
        started = self._request_started_at.pop(id(response.request), None)
        if started is not None:
            self.request_durations.append(time.perf_counter() - started)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        payload = _json(response)
        if response.status_code not in expected:
            raise RuntimeError(
                f"{method} {path} returned HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:1200]}"
            )
        return payload

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("DELETE", path, **kwargs)


def _project_path(project_id: str, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}/{suffix.lstrip('/')}"


def _poll_job(
    client: RuntimeClient,
    project_id: str,
    *,
    task_type: str,
    job_id: str,
    label: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Poll a freezone job until it yields a result or fails."""
    path = _project_path(project_id, f"freezone/jobs/{task_type}/{job_id}/result")
    deadline = time.monotonic() + timeout_seconds
    started = time.monotonic()
    last_note = ""
    while time.monotonic() < deadline:
        response = client.client.get(path)
        payload = _json(response)
        if response.status_code not in (200, 202):
            raise RuntimeError(
                f"{label}: polling returned HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:1200]}"
            )
        if payload.get("ok") is True and isinstance(payload.get("data"), dict):
            elapsed = time.monotonic() - started
            _log(f"{label}: done in {elapsed:.0f}s")
            return payload["data"]
        status = str(payload.get("status") or "")
        if status in TERMINAL_FAILURES:
            raise RuntimeError(
                f"{label}: job failed: {json.dumps(payload, ensure_ascii=False)[:1600]}"
            )
        note = f"{status}/{payload.get('current_task') or payload.get('info') or ''}"
        if note != last_note:
            last_note = note
            _log(f"{label}: waiting ({note})")
        time.sleep(3.0)
    raise RuntimeError(f"{label}: timed out after {timeout_seconds:.0f}s")


def _select_image_model(
    client: RuntimeClient,
    *,
    require_image_to_image: bool = False,
) -> dict[str, Any]:
    config = _data(client.get("/api/v1/model-gateway/config"))
    direct_models = config.get("directModels")
    image_models = direct_models.get("image") if isinstance(direct_models, dict) else None
    if not isinstance(image_models, list):
        raise RuntimeError("model gateway config has no image model list")
    usable = [
        item
        for item in image_models
        if isinstance(item, dict)
        and item.get("usable") is True
        and item.get("enabled") is not False
        and (
            not require_image_to_image
            or "imageToImage"
            in {
                str(mode)
                for mode in (
                    item.get("supportedModes")
                    or item.get("supported_modes")
                    or []
                )
            }
        )
    ]
    for item in usable:
        if item.get("isDefault") is True:
            return item
    if usable:
        return usable[0]
    requirement = (
        " with imageToImage support" if require_image_to_image else ""
    )
    raise RuntimeError(f"no usable direct image model{requirement} is configured")


def _generate_asset_references(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    ledger: dict[str, Any],
    image_model: str,
    canvas_id: str,
    nonce: str,
    stamp: str,
) -> list[dict[str, Any]]:
    assets = [
        asset
        for asset in (ledger.get("assets") or [])
        if isinstance(asset, dict)
    ][:SCRIPT_REFERENCE_IMAGE_CAP]
    if not assets:
        raise RuntimeError("script contract produced no reference assets")

    generated: list[dict[str, Any]] = []
    for index, asset in enumerate(assets, start=1):
        role = str(asset.get("role") or "")
        name = str(asset.get("name") or f"asset-{index}")
        label = f"asset {index}/{len(assets)} {role}:{name}"
        _log(f"{label}: reference image submitted")
        job = _data(
            client.post(
                _project_path(project_id, "freezone/gen"),
                json={
                    "prompt": _asset_reference_prompt(asset),
                    "aspect_ratio": _asset_reference_aspect_ratio(
                        asset, args.aspect_ratio
                    ),
                    "image_size": args.image_size,
                    "quality": args.image_quality,
                    "provider": "direct",
                    "model": image_model,
                    "model_id": image_model,
                    "gen_mode": "textToImage",
                    "canvas_id": canvas_id,
                    "node_id": f"film-asset-{index}-{nonce}",
                },
            )
        )
        result = _poll_job(
            client,
            project_id,
            task_type=str(job.get("task_type") or "freezone_gen"),
            job_id=str(job.get("job_id") or ""),
            label=label,
            timeout_seconds=args.image_timeout,
        )
        url = str(result.get("url") or "")
        if not url:
            raise RuntimeError(f"{label}: image result has no url: {result}")
        path = (
            ARTIFACT_DIR
            / f"{stamp}-{index:02d}-asset-{_asset_reference_slug(name)}.png"
        )
        image_bytes, image_sha = _download(client, url, path)
        generated.append(
            {
                "assetId": str(asset.get("asset_id") or ""),
                "role": role,
                "name": name,
                "description": str(asset.get("description") or ""),
                "shotNumbers": list(asset.get("shot_numbers") or []),
                "url": url,
                "path": str(path),
                "bytes": image_bytes,
                "sha256": image_sha,
            }
        )
        _log(f"{label}: ready ({image_bytes} bytes)")
    return generated


def _download(client: RuntimeClient, url: str, target: Path) -> tuple[int, str]:
    absolute = urljoin(client.base_url + "/", url)
    response = client.client.get(absolute)
    if response.status_code != 200:
        raise RuntimeError(
            f"artifact download returned HTTP {response.status_code}: {response.text[:300]}"
        )
    content = response.content
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    suffix = target.suffix or (
        mimetypes.guess_extension(
            response.headers.get("content-type", "").split(";", 1)[0].strip()
        )
        or ".bin"
    )
    if suffix != target.suffix:
        target = target.with_suffix(suffix)
        target.write_bytes(content)
    return len(content), hashlib.sha256(content).hexdigest()


def _upload_image(client: RuntimeClient, project_id: str, path: Path) -> str:
    with path.open("rb") as handle:
        response = client.client.post(
            _project_path(project_id, "freezone/upload"),
            files={"file": (path.name, handle, "image/png")},
        )
    payload = _json(response)
    if response.status_code != 200:
        raise RuntimeError(
            f"upload of {path.name} returned HTTP {response.status_code}: {payload}"
        )
    url = str(_data(payload).get("url") or "")
    if not url:
        raise RuntimeError(f"upload of {path.name} returned no url: {payload}")
    return url


def _existing_storyboard(index: int) -> Path | None:
    matches = sorted(ARTIFACT_DIR.glob(f"*-{index:02d}-storyboard.png"))
    return matches[-1] if matches else None


def _submit_i2v(
    client: RuntimeClient,
    project_id: str,
    payload: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    """Submit an image-to-video job, adopting the prompt's duration if it insists.

    The driver normally strips duration phrases so ``duration_seconds`` wins, but
    if the live contract still reports a mismatch we follow the prompt rather than
    editing the model's own words.
    """
    path = _project_path(project_id, "freezone/video/i2v")
    try:
        return _data(client.post(path, json=payload))
    except RuntimeError as exc:
        message = str(exc)
        if "prompt_duration_mismatch" not in message:
            raise
        match = re.search(r'"promptDurations":\s*\[(\d+)', message)
        if not match:
            raise
        adopted = int(match.group(1))
        _log(f"{label}: prompt states {adopted}s; retrying with that duration")
        return _data(client.post(path, json={**payload, "duration_seconds": adopted}))


def _is_mp4(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(12)
    return len(header) >= 8 and header[4:8] == b"ftyp"


def _story_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Story-script results may come back inline or behind a URL."""
    rows = result.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    payload = result.get("data")
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return [row for row in payload["rows"] if isinstance(row, dict)]
    raise RuntimeError(f"story script result has no rows: {json.dumps(result, ensure_ascii=False)[:800]}")


def _shot_prompt(row: dict[str, Any]) -> str:
    for key in ("shot_prompt", "visual_description", "video_motion_prompt"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "cinematic wide shot"


def _motion_prompt(row: dict[str, Any]) -> str:
    for key in ("video_motion_prompt", "shot_prompt", "visual_description"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "slow cinematic camera push in"


def _meaningful_text(value: object) -> str:
    text = str(value or "").strip()
    if text.casefold() in {"", "无", "none", "null", "n/a", "na", "-", "—"}:
        return ""
    return text


def _dialogue_and_speaker(row: dict[str, Any]) -> tuple[str, str]:
    """Return one clean spoken turn and its on-screen speaker."""

    dialogue = _meaningful_text(row.get("dialogue"))
    speakers = [
        text
        for text in (
            _meaningful_text(row.get("character_1")),
            _meaningful_text(row.get("character_2")),
        )
        if text
    ]
    if not dialogue:
        return "", speakers[0] if speakers else ""

    lines: list[str] = []
    for raw_line in dialogue.splitlines():
        line = raw_line.strip()
        for speaker in speakers:
            for separator in ("：", ":"):
                prefix = f"{speaker}{separator}"
                if line.startswith(prefix):
                    line = line[len(prefix) :].strip()
        if line:
            lines.append(line)
    dialogue = " ".join(lines).strip()
    return dialogue, speakers[0] if speakers else ""


def _asset_shot_no(row: dict[str, Any], index: int) -> str:
    return str(
        row.get("display_shot_no")
        or row.get("shot_no")
        or index
    ).strip()


def _asset_belongs_to_row(
    asset: dict[str, Any],
    row: dict[str, Any],
    index: int,
) -> bool:
    shot_numbers = {
        str(value).strip()
        for value in (asset.get("shotNumbers") or [])
        if str(value).strip()
    }
    return _asset_shot_no(row, index) in shot_numbers


def _split_asset_tags(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        candidates = [str(item) for item in value]
    else:
        candidates = _ASSET_TAG_SPLIT_RE.split(str(value or ""))
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = candidate.strip()
        folded = token.casefold()
        if not token or folded in seen:
            continue
        seen.add(folded)
        out.append(token)
    return out


def _asset_reference_aspect_ratio(asset: dict[str, Any], fallback: str) -> str:
    return "1:1" if str(asset.get("role") or "") in {"character", "prop"} else fallback


def _asset_reference_prompt(asset: dict[str, Any]) -> str:
    role = str(asset.get("role") or "")
    name = _meaningful_text(asset.get("name")) or "未命名资产"
    description = _meaningful_text(asset.get("description"))
    if role == "character":
        detail = description or "外形、年龄、发型、服装与气质保持稳定"
        return (
            "电影角色定妆参考图，只出现一个角色，正面与三分之四侧之间，"
            "中性背景、均匀柔光、全身可见，面部和服装细节清晰。"
            f"角色名：{name}。不可变化的身份特征：{detail}。"
            "不要多人，不要文字，不要拼图。"
        )
    if role == "scene":
        detail = description or "空间结构、材质、色调与时代感保持稳定"
        return (
            "电影场景概念参考图，空场景环境全貌，构图清楚，空间关系可信。"
            f"场景：{name}。稳定特征：{detail}。"
            "不要主要人物，不要文字，不要拼图。"
        )
    detail = description or "轮廓、材质、颜色与磨损细节保持稳定"
    return (
        "电影道具设定参考图，只出现一件道具，三分之四视角，中性背景，"
        "材质和磨损细节清晰。"
        f"道具：{name}。稳定特征：{detail}。"
        "不要人物，不要文字，不要拼图。"
    )


def _asset_reference_slug(value: object) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-龥]+", "-", str(value or "")).strip("-")
    return slug[:32] or "asset"


def _character_slot(
    row: dict[str, Any],
    asset: dict[str, Any],
) -> int | None:
    asset_id = str(asset.get("assetId") or "")
    asset_name = _meaningful_text(asset.get("name")).casefold()
    for slot in (1, 2):
        explicit_id = _meaningful_text(
            row.get(f"character_asset_id_{slot}")
            or row.get(f"character_id_{slot}")
        )
        if explicit_id and explicit_id == asset_id:
            return slot
        if asset_name and _meaningful_text(
            row.get(f"character_{slot}")
        ).casefold() == asset_name:
            return slot
    return None


def _attach_asset_reference_urls(
    rows: list[dict[str, Any]],
    generated_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    updated = [dict(row) for row in rows]
    for index, row in enumerate(updated, start=1):
        for asset in generated_assets:
            if not _asset_belongs_to_row(asset, row, index):
                continue
            if str(asset.get("role") or "") == "character":
                slot = _character_slot(row, asset)
                if slot is not None:
                    row[f"character_image_{slot}"] = str(asset.get("url") or "")

        for role, tag_key, refs_key in (
            ("scene", "scene_tags", "scene_reference_urls"),
            ("prop", "prop_tags", "prop_reference_urls"),
        ):
            tags = _split_asset_tags(row.get(tag_key))
            if not tags:
                continue
            existing = row.get(refs_key)
            references = list(existing) if isinstance(existing, list) else []
            references.extend([""] * max(0, len(tags) - len(references)))
            by_name = {
                _meaningful_text(asset.get("name")).casefold(): str(
                    asset.get("url") or ""
                )
                for asset in generated_assets
                if str(asset.get("role") or "") == role
                and _asset_belongs_to_row(asset, row, index)
                and _meaningful_text(asset.get("name"))
            }
            for tag_index, tag in enumerate(tags):
                url = by_name.get(tag.casefold())
                if url:
                    references[tag_index] = url
            if any(str(value).strip() for value in references):
                row[refs_key] = [str(value or "") for value in references]
    return updated


def _prepare_referenced_ledger(
    rows: list[dict[str, Any]],
    generated_assets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    updated_rows = _attach_asset_reference_urls(rows, generated_assets)
    ledger = build_script_asset_ledger(updated_rows)
    shot_references: list[dict[str, Any]] = []
    missing: list[int] = []
    for index, row in enumerate(updated_rows, start=1):
        references = asset_references_for_shot(ledger, row, index)
        if not references:
            missing.append(index)
        shot_references.append(
            {
                "shotIndex": index,
                "shotNo": _asset_shot_no(row, index),
                "assetIds": [
                    str(asset.get("asset_id") or "") for asset in references
                ],
                "referenceUrls": [
                    str(asset.get("reference_url") or "") for asset in references
                ],
                "assets": [dict(asset) for asset in references],
            }
        )
    if missing:
        raise RuntimeError(
            "asset ledger left shots without a ready reference asset: "
            + ", ".join(str(index) for index in missing)
        )
    return updated_rows, ledger, shot_references


def _build_video_payload(
    *,
    args: argparse.Namespace,
    canvas_id: str,
    nonce: str,
    index: int,
    row: dict[str, Any],
    image_url: str,
) -> dict[str, Any]:
    dialogue, speaker = _dialogue_and_speaker(row)
    payload: dict[str, Any] = {
        "image_urls": [image_url],
        "prompt": _strip_duration_mentions(_motion_prompt(row)),
        "aspect_ratio": args.aspect_ratio,
        "resolution": args.resolution,
        "duration_seconds": args.duration,
        "generate_audio": True,
        "native_audio_strategy": "native",
        "model": args.video_model,
        "model_id": args.video_model,
        "gen_mode": "imageToVideo",
        "canvas_id": canvas_id,
        "node_id": f"film-video-{index}-{nonce}",
    }
    if dialogue:
        payload.update(
            {
                "dialogue_text": dialogue,
                "spoken_dialogue": [dialogue],
                "audio_type": "dialogue",
                "speaker": speaker,
            }
        )
    return payload


def _render_storyboard(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    image_model: str,
    canvas_id: str,
    nonce: str,
    stamp: str,
    index: int,
    row: dict[str, Any],
    asset_references: list[dict[str, Any]],
) -> dict[str, Any]:
    shot_label = f"shot {index}"
    started = time.monotonic()
    asset_reference_ids = [
        str(asset.get("asset_id") or "") for asset in asset_references
    ]
    reference_urls = [
        str(asset.get("reference_url") or "") for asset in asset_references
    ]
    if not reference_urls:
        raise RuntimeError(f"{shot_label}: no ready asset reference is available")
    reused = _existing_storyboard(index) if args.reuse_storyboards else None
    if reused is not None:
        image_path = reused
        image_url = _upload_image(client, project_id, reused)
        image_bytes = reused.stat().st_size
        image_sha = hashlib.sha256(reused.read_bytes()).hexdigest()
        _log(
            f"{shot_label}: reusing storyboard {reused.name} "
            f"({len(reference_urls)} asset references recorded)"
        )
    else:
        image_payload: dict[str, Any] = {
            "prompt": _shot_prompt(row),
            "reference_urls": reference_urls,
            "aspect_ratio": args.aspect_ratio,
            "image_size": args.image_size,
            "quality": args.image_quality,
            "provider": "direct",
            "model": image_model,
            "model_id": image_model,
            "gen_mode": "imageToImage",
            "canvas_id": canvas_id,
            "node_id": f"film-image-{index}-{nonce}",
        }
        image_job = _data(
            client.post(
                _project_path(project_id, "freezone/gen"),
                json=image_payload,
            )
        )
        _log(
            f"{shot_label}: storyboard image submitted "
            f"({image_job.get('job_id')}) with {len(reference_urls)} reference(s)"
        )
        image_result = _poll_job(
            client,
            project_id,
            task_type=str(image_job.get("task_type") or "freezone_gen"),
            job_id=str(image_job.get("job_id") or ""),
            label=f"{shot_label} image",
            timeout_seconds=args.image_timeout,
        )
        image_url = str(image_result.get("url") or "")
        if not image_url:
            raise RuntimeError(f"{shot_label}: image result has no url: {image_result}")
        image_path = ARTIFACT_DIR / f"{stamp}-{index:02d}-storyboard.png"
        image_bytes, image_sha = _download(client, image_url, image_path)
    elapsed = time.monotonic() - started
    _log(f"{shot_label}: storyboard ready in {elapsed:.0f}s ({image_bytes} bytes)")
    return {
        "index": index,
        "row": row,
        "imageUrl": image_url,
        "imageBytes": image_bytes,
        "imageSha256": image_sha,
        "imagePath": str(image_path),
        "imageSeconds": round(elapsed, 2),
        "assetReferenceIds": asset_reference_ids,
        "referenceUrls": reference_urls,
        "referenceCount": len(reference_urls),
        "referencesApplied": reused is None,
    }


def _run_storyboards_parallel(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    image_model: str,
    canvas_id: str,
    nonce: str,
    stamp: str,
    shot_references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    workers = max(1, min(args.image_concurrency, len(rows)))
    receipt_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="storyboard") as pool:
        futures = {
            pool.submit(
                _render_storyboard,
                client,
                project_id,
                args,
                image_model=image_model,
                canvas_id=canvas_id,
                nonce=nonce,
                stamp=stamp,
                index=index,
                row=row,
                asset_references=[
                    asset
                    for asset in (
                        shot_references[index - 1].get("assets") or []
                    )
                    if isinstance(asset, dict)
                ],
            ): index
            for index, row in enumerate(rows, start=1)
        }
        for future in as_completed(futures):
            receipt_rows.append(future.result())
    return sorted(receipt_rows, key=lambda item: item["index"])


def _submit_video_job(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    canvas_id: str,
    nonce: str,
    storyboard: dict[str, Any],
) -> dict[str, Any]:
    index = int(storyboard["index"])
    row = storyboard["row"]
    shot_label = f"shot {index}"
    payload = _build_video_payload(
        args=args,
        canvas_id=canvas_id,
        nonce=nonce,
        index=index,
        row=row,
        image_url=str(storyboard["imageUrl"]),
    )
    dialogue = str(payload.get("dialogue_text") or "")
    started = time.monotonic()
    job = _submit_i2v(client, project_id, payload, label=shot_label)
    elapsed = time.monotonic() - started
    _log(
        f"{shot_label}: video submitted ({job.get('job_id')})"
        + (f", dialogue={dialogue[:32]}" if dialogue else ", no dialogue")
    )
    return {
        **storyboard,
        "videoJob": job,
        "videoSubmittedAt": _stamp(),
        "videoSubmitSeconds": round(elapsed, 2),
        "dialogue": dialogue,
        "speaker": str(payload.get("speaker") or ""),
    }


def _wait_video_job(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    stamp: str,
    submitted: dict[str, Any],
) -> dict[str, Any]:
    index = int(submitted["index"])
    shot_label = f"shot {index}"
    job = submitted["videoJob"]
    started = time.monotonic()
    video_result = _poll_job(
        client,
        project_id,
        task_type=str(job.get("task_type") or "freezone_video_gen"),
        job_id=str(job.get("job_id") or ""),
        label=f"{shot_label} video",
        timeout_seconds=args.video_timeout,
    )
    video_url = str(video_result.get("url") or "")
    if not video_url:
        raise RuntimeError(f"{shot_label}: video result has no url: {video_result}")
    video_path = ARTIFACT_DIR / f"{stamp}-{index:02d}-shot.mp4"
    video_bytes, video_sha = _download(client, video_url, video_path)
    if not _is_mp4(video_path):
        raise RuntimeError(f"{shot_label}: downloaded video is not an mp4")
    elapsed = time.monotonic() - started
    _log(f"{shot_label}: clip saved in {elapsed:.0f}s ({video_bytes} bytes)")
    return {
        "index": index,
        "prompt": _shot_prompt(submitted["row"]),
        "dialogue": submitted["dialogue"],
        "speaker": submitted["speaker"],
        "imageUrl": submitted["imageUrl"],
        "imageBytes": submitted["imageBytes"],
        "imageSha256": submitted["imageSha256"],
        "imageSeconds": submitted["imageSeconds"],
        "videoJobId": str(job.get("job_id") or ""),
        "videoUrl": video_url,
        "videoBytes": video_bytes,
        "videoSha256": video_sha,
        "videoSeconds": round(elapsed, 2),
        "mp4": str(video_path),
    }


def _run_videos_parallel(
    client: RuntimeClient,
    project_id: str,
    args: argparse.Namespace,
    *,
    storyboards: list[dict[str, Any]],
    canvas_id: str,
    nonce: str,
    stamp: str,
) -> list[dict[str, Any]]:
    submitted = [
        _submit_video_job(
            client,
            project_id,
            args,
            canvas_id=canvas_id,
            nonce=nonce,
            storyboard=storyboard,
        )
        for storyboard in storyboards
    ]
    workers = max(1, min(args.video_concurrency, len(submitted)))
    _log(
        f"all {len(submitted)} video jobs submitted; waiting with "
        f"{workers} concurrent pollers"
    )
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video") as pool:
        futures = {
            pool.submit(
                _wait_video_job,
                client,
                project_id,
                args,
                stamp=stamp,
                submitted=item,
            ): item["index"]
            for item in submitted
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - aggregate every failed shot
                errors.append(f"shot {futures[future]}: {exc}")
    if errors:
        raise RuntimeError("parallel video generation failed:\n" + "\n".join(errors))
    return sorted(results, key=lambda item: item["index"])


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_paid_generation:
        raise RuntimeError(
            "paid generation is disabled; pass --allow-paid-generation to authorize "
            "the image and video requests"
        )

    stamp = _stamp()
    nonce = uuid.uuid4().hex[:10]
    project_name = f"zz_codex_film_{stamp.lower()}_{nonce}"
    canvas_id = f"film_smoke_{nonce}"
    client = RuntimeClient(args.base_url, args.timeout_seconds)
    project_id = ""
    receipt: dict[str, Any] = {
        "ok": False,
        "startedAt": stamp,
        "projectName": project_name,
        "videoBackend": args.video_model,
        "resolution": args.resolution,
        "aspectRatio": args.aspect_ratio,
        "durationSeconds": args.duration,
        "shots": [],
    }
    try:
        _log(f"health {client.get('/healthz')}")
        project = _data(client.post("/api/v1/projects", json={"name": project_name}))
        project_id = str(project.get("project_id") or project.get("id") or "")
        if not project_id:
            raise RuntimeError(f"project creation returned no id: {project}")
        receipt["projectId"] = project_id
        _log(f"project {project_id} ({project_name})")

        # 1) story script ---------------------------------------------------
        accepted = _data(
            client.post(
                _project_path(project_id, "freezone/text/story-script"),
                json={
                    "source_text": args.story,
                    "prompt": (
                        f"严格改编成恰好 {args.shots} 个连续镜头，总目标时长约 "
                        f"{args.shots * args.duration} 秒。"
                        "这是高密度电影短片，不是分镜摘要：每一镜都要完成一个明确动作、"
                        "一个情绪转折和一次摄影机运动。"
                        "source_text 中已经写出的关键对白必须逐字保留在 dialogue 字段；"
                        "有台词时不要写“无”，不要加说话人前缀，每镜最多一句。"
                        "shot_prompt 必须按构图、角色卡、空间关系、微表情、环境道具、"
                        "光影、统一视觉风格、统一技术参数八段写全；"
                        "video_motion_prompt 必须按运镜、主体动作、环境动态、音效、"
                        "对白语气、时长六段写全。"
                        "全片角色卡、视觉风格和技术参数必须逐字一致。"
                    ),
                    "max_frames": 20,
                    "scene_threshold": 0.3,
                    "canvas_id": canvas_id,
                    "node_id": f"film-script-{nonce}",
                },
            )
        )
        receipt["storyJob"] = {
            "taskType": accepted.get("task_type"),
            "jobId": accepted.get("job_id"),
        }
        _log(f"story script submitted ({accepted.get('job_id')})")
        story_result = _poll_job(
            client,
            project_id,
            task_type=str(accepted.get("task_type") or "freezone_story_script"),
            job_id=str(accepted.get("job_id") or ""),
            label="story script",
            timeout_seconds=args.story_timeout,
        )
        rows = _story_rows(story_result)
        rows = rows[: args.shots]
        receipt["rowCount"] = len(rows)
        receipt["storyTitle"] = str(story_result.get("title") or "")
        if not rows:
            raise RuntimeError("story script produced no rows")
        for index, row in enumerate(rows, start=1):
            dialogue, speaker = _dialogue_and_speaker(row)
            suffix = f" | {speaker}: {dialogue[:38]}" if dialogue else " | no dialogue"
            _log(f"  shot {index}: {_shot_prompt(row)[:70]}{suffix}")

        if len(rows) != args.shots:
            raise RuntimeError(
                f"story script returned {len(rows)} rows for requested {args.shots} shots"
            )

        # 2) storyboard images, then parallel shot videos --------------------
        image_model = _select_image_model(
            client,
            require_image_to_image=True,
        )
        image_registry = str(image_model.get("id") or "")
        catalog_model = f"direct/{image_registry}"
        _log(f"image model {catalog_model}")

        initial_ledger = build_script_asset_ledger(rows)
        generated_assets = _generate_asset_references(
            client,
            project_id,
            args,
            ledger=initial_ledger,
            image_model=catalog_model,
            canvas_id=canvas_id,
            nonce=nonce,
            stamp=stamp,
        )
        rows, ledger, shot_references = _prepare_referenced_ledger(
            rows,
            generated_assets,
        )
        receipt["assetLedger"] = {
            "schema": str(ledger.get("schema") or ""),
            "signature": str(ledger.get("signature") or ""),
            "assetCount": len(ledger.get("assets") or []),
            "readyCount": sum(
                1
                for asset in (ledger.get("assets") or [])
                if isinstance(asset, dict)
                and asset.get("readiness") == "ready"
            ),
            "roles": dict(ledger.get("counts") or {}),
            "referenceImageCount": len(generated_assets),
            "referenceImages": generated_assets,
            "shotReferenceCounts": [
                len(item.get("assets") or []) for item in shot_references
            ],
        }

        receipt.update(
            {
                "imageConcurrency": args.image_concurrency,
                "videoConcurrency": args.video_concurrency,
            }
        )
        storyboards = _run_storyboards_parallel(
            client,
            project_id,
            args,
            rows=rows,
            image_model=catalog_model,
            canvas_id=canvas_id,
            nonce=nonce,
            stamp=stamp,
            shot_references=shot_references,
        )
        receipt["storyboardCount"] = len(storyboards)
        shots = _run_videos_parallel(
            client,
            project_id,
            args,
            storyboards=storyboards,
            canvas_id=canvas_id,
            nonce=nonce,
            stamp=stamp,
        )
        receipt["shots"] = shots
        clips = [
            {"url": str(shot["videoUrl"]), "duration": float(args.duration)}
            for shot in shots
        ]

        # 3) compose --------------------------------------------------------
        cursor = 0.0
        items = []
        for index, clip in enumerate(clips, start=1):
            items.append(
                {
                    "item_id": f"clip-{index:02d}",
                    "source_url": clip["url"],
                    "timeline_start": cursor,
                    "source_start": 0,
                    "source_end": clip["duration"],
                    "volume": 1,
                    "muted": False,
                }
            )
            cursor += clip["duration"]
        compose_job = _data(
            client.post(
                _project_path(project_id, "freezone/video/compose"),
                json={
                    "title": str(receipt.get("storyTitle") or "村长无限画布一分钟成片"),
                    "canvas_id": canvas_id,
                    "resolution": args.compose_resolution,
                    "fps": args.fps,
                    "background_color": "#000000",
                    "keep_original_audio": True,
                    "tracks": [{"track_id": "video-1", "kind": "video", "items": items}],
                },
            )
        )
        _log(f"compose submitted ({compose_job.get('job_id')})")
        compose_result = _poll_job(
            client,
            project_id,
            task_type=str(compose_job.get("task_type") or "freezone_video_compose"),
            job_id=str(compose_job.get("job_id") or ""),
            label="compose",
            timeout_seconds=args.compose_timeout,
        )
        film_url = str(compose_result.get("url") or "")
        if not film_url:
            raise RuntimeError(f"compose result has no url: {compose_result}")
        film_path = ARTIFACT_DIR / f"{stamp}-film.mp4"
        film_bytes, film_sha = _download(client, film_url, film_path)
        if not _is_mp4(film_path):
            raise RuntimeError("composed film is not an mp4")
        _log(f"film saved ({film_bytes} bytes)")

        receipt.update(
            {
                "ok": True,
                "finishedAt": _stamp(),
                "clipCount": len(clips),
                "filmSeconds": cursor,
                "filmUrl": film_url,
                "filmBytes": film_bytes,
                "filmSha256": film_sha,
                "filmPath": str(film_path),
            }
        )
    finally:
        if project_id and not args.keep_project:
            try:
                # Both cleanup routes are POST (see api/routes/projects.py).
                client.post(_project_path(project_id, "delete"))
                client.post(_project_path(project_id, "purge"))
                receipt["projectPurged"] = True
                _log(f"project {project_id} deleted and purged")
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the run
                receipt["projectPurged"] = False
                receipt["purgeError"] = str(exc)
                _log(f"project cleanup failed: {exc}")
        client.close()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "latest-film.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--story", default=(
        "雨夜，老照相馆即将拆迁。年轻店主最后一次打开暗房红灯，"
        "墙上挂满了这条街三十年的面孔。他拿起相机，对着空荡荡的街道按下快门。"
    ))
    parser.add_argument(
        "--story-file",
        default="",
        help="read the source treatment from a UTF-8 text or Markdown file",
    )
    parser.add_argument("--shots", type=int, default=14)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--aspect-ratio", default=DEFAULT_ASPECT_RATIO)
    parser.add_argument("--resolution", default=DEFAULT_VIDEO_RESOLUTION)
    parser.add_argument("--video-model", default=DEFAULT_VIDEO_BACKEND)
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--image-quality", default="low")
    parser.add_argument("--compose-resolution", default="720p", choices=["720p", "1080p"])
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--story-timeout", type=float, default=600.0)
    parser.add_argument("--image-timeout", type=float, default=900.0)
    parser.add_argument("--video-timeout", type=float, default=2400.0)
    parser.add_argument("--compose-timeout", type=float, default=900.0)
    parser.add_argument("--image-concurrency", type=int, default=6)
    parser.add_argument("--video-concurrency", type=int, default=12)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument(
        "--reuse-storyboards",
        action="store_true",
        help="re-upload locally saved storyboards instead of paying to regenerate them",
    )
    parser.add_argument("--allow-paid-generation", action="store_true")
    args = parser.parse_args()
    if args.story_file:
        story_path = Path(args.story_file).expanduser()
        if not story_path.is_absolute():
            story_path = (Path.cwd() / story_path).resolve()
        args.story = story_path.read_text(encoding="utf-8")
    args.image_concurrency = max(1, int(args.image_concurrency))
    args.video_concurrency = max(1, int(args.video_concurrency))
    return args


def main() -> int:
    args = _parse_args()
    if not args.allow_paid_generation:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "paid_generation_not_authorized",
                    "hint": (
                        "re-run with --allow-paid-generation to authorize the real "
                        "image and video requests"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    receipt = run(args)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
