"""Film studio — a gate-enforcing production pipeline for real short films.

``scripts/acceptance/freezone_full_film_smoke.py`` answers "does the pipeline
connect?" — it renders every shot from bare text, hard-concatenates the clips
and calls it a day.  That is the right bar for an acceptance probe and the
wrong bar for a film.

This driver is the production path: it turns the film-craft rules into hard
constraints instead of prose suggestions.

  1. ASSET LOCK        scene + character + prop sheets render first; nothing
                       downstream runs until they exist.
  2. REFERENCED FRAMES storyboards render with the locked assets attached via
                       ``reference_urls``, not from text alone.
  3. REFERENCED CLIPS  clips render through ``freezone/video/omni-gen`` with
                       ``gen_mode=allReference`` — MiniMax-H3 takes up to 9
                       image refs, so characters and set travel with every shot.
  4. SEAM HANDLING     clips join with a real audio+video crossfade, so no cut
                       is a bare butt-joint.
  5. QC                loudness normalised to the delivery target, and a report
                       records every gate before the film is called done.

Shot lists are hand-authored JSON — the judgment layer is not delegated to a
text model.

Subprocesses go through the repository's own killable wrapper
(``novelvideo.task_backend.subprocesses.run_project_subprocess``), which takes
an argument list and never a shell string.  Filter graphs are passed to ffmpeg
through script files, and every numeric parameter is coerced and range-checked
before it reaches a render command.

Usage::

    .venv\\Scripts\\python.exe scripts\\film_studio.py ^
        --shots-file workspace/artifacts/film-source/一个音符-shots.json ^
        --assets-only --allow-paid-generation
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from novelvideo.task_backend.subprocesses import run_project_subprocess  # noqa: E402


ARTIFACT_DIR = ROOT / "workspace" / "artifacts" / "film-studio"

# Keep generated deliverables inside the repository workspace so a moved
# checkout does not depend on a machine-specific output drive.
DEFAULT_OUTPUT_DIR = ARTIFACT_DIR / "deliverables"

DEFAULT_VIDEO_BACKEND = "direct_video-8b01a39b81951012"
DEFAULT_SCENE_MODEL = "gemini-3-pro-image"
DEFAULT_FRAME_MODEL = "gpt-image-2.5-sunburst"

# Delivery target — EBU R128 programme loudness (film-craft lib, 18-声音制作全流程).
TARGET_LUFS = -23.0
TARGET_TP = -2.0
TARGET_LRA = 11.0
CROSSFADE_SECONDS = 0.40

# Per-clip level matching.  The delivery loudnorm runs once over the finished
# programme, so a clip the model generated far quieter than its neighbours
# stays quieter than its neighbours: the film just drops out for that shot.
# Shots are levelled to a common integrated loudness first, and the gate fails
# if any pair is still too far apart.
LEVEL_TARGET_LUFS = -20.0
LEVEL_MAX_GAIN_DB = 14.0
LEVEL_SPREAD_TOLERANCE_DB = 6.0


# --------------------------------------------------------------------------- #
# numbers — the boundary that keeps anything but a plain number out of a render
# --------------------------------------------------------------------------- #

def _number(value: object, name: str, *, low: float, high: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(result) or not (low <= result <= high):
        raise ValueError(f"{name} out of range [{low}, {high}]: {result!r}")
    return result


def _index(value: object, name: str) -> int:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    if not 0 <= result <= 4096:
        raise ValueError(f"{name} out of range: {result!r}")
    return result


def _write_filter_script(graph: str, *, stem: str) -> Path:
    """Persist a filter graph and hand ffmpeg the path, never the text."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ffgraph", prefix=f"{stem}-", delete=False, encoding="utf-8"
    )
    with handle:
        handle.write(graph)
    return Path(handle.name)


def _run_ffmpeg(args: list[str]) -> None:
    """Invoke ffmpeg through the repository's killable subprocess wrapper."""
    result = run_project_subprocess(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr or "")[-600:]
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {tail}")


class ResolutionLadder:
    """Settle on the highest resolution the channel will actually accept.

    Channels refuse clearances without warning and the accepted set cannot be
    read from the catalog, so the ladder starts at the top and steps down the
    first time a submission is rejected.  All workers share the outcome, so the
    cost is one rejected request rather than one per shot.
    """

    def __init__(self, ladder: list[str], requested: str) -> None:
        options = [item.strip() for item in ladder if item.strip()]
        if requested and requested not in options:
            options.insert(0, requested)
        self._options = options or [requested or "768p"]
        self._index = 0
        self._lock = threading.Lock()

    @property
    def current(self) -> str:
        with self._lock:
            return self._options[self._index]

    def step_down(self, rejected: str) -> str | None:
        """Retire ``rejected`` and return the rung to retry on, or None if done.

        A worker that read ``current`` just before another worker stepped down
        will submit the higher value and be refused; that is not an error, it
        simply needs to retry on the rung the ladder has already settled on.
        """
        with self._lock:
            if rejected in self._options:
                self._index = max(self._index, self._options.index(rejected))
            if self._index + 1 >= len(self._options):
                # Already at the bottom: if this worker is simply behind, tell
                # it to retry at the current rung instead of giving up.
                if rejected in self._options and self._options.index(rejected) < self._index:
                    return self._options[self._index]
                return None
            self._index += 1
            return self._options[self._index]

    @property
    def tried(self) -> list[str]:
        with self._lock:
            return self._options[: self._index + 1]


RESOLUTION_REJECTIONS = (
    "resolution",
    "清晰度",
    "unsupported",
    "not supported",
    "invalid_parameter",
    "invalid parameter",
    "不支持",
)


def _is_resolution_rejection(error: BaseException, attempted: str = "") -> bool:
    """Is this refusal about the clearance we asked for?

    The channels do not agree on wording — one says ``unsupported resolution``,
    another ``清晰度``, a third just echoes the value it will not accept — so
    the attempted value itself counts as evidence.
    """
    text = str(error).lower()
    if attempted and attempted.lower() in text:
        return True
    return any(marker in text for marker in RESOLUTION_REJECTIONS)
# Upstream channels drop connections mid-generation often enough that a single
# disconnect must never end a production run.  These are the signatures that
# mean "try again" rather than "this shot is broken".
TRANSIENT_MARKERS = (
    "RemoteProtocolError",
    "Server disconnected",
    "Connection reset",
    "ReadTimeout",
    "ConnectTimeout",
    "Read timed out",
    "502",
    "503",
    "504",
    "temporarily unavailable",
    "upstream connect error",
    # Raised by the resolution ladder to force a retry at a lower clearance.
    "resolution fallback",
)


def _is_transient(error: BaseException) -> bool:
    text = str(error)
    return any(marker in text for marker in TRANSIENT_MARKERS)


def _attempt(operation, *, label: str, attempts: int, on_retry=None):
    """Run ``operation`` with backoff, retrying only transient failures.

    A durable failure (bad prompt, unsupported mode, contract rejection) is
    re-raised immediately — retrying it just burns budget.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - classified below
            if not _is_transient(exc) or attempt == attempts:
                raise
            last = exc
            delay = min(30.0, 5.0 * (2 ** (attempt - 1)))
            _log(
                f"{label}: transient failure on attempt {attempt}/{attempts} "
                f"({str(exc)[:90]}); retrying in {delay:.0f}s"
            )
            if on_retry is not None:
                on_retry(attempt)
            time.sleep(delay)
    if last is not None:
        raise last
    raise RuntimeError(f"{label}: no attempts were made")


# --------------------------------------------------------------------------- #
# runtime client
# --------------------------------------------------------------------------- #

class SubmitRejected(RuntimeError):
    """The endpoint refused the request outright; the body says why."""


class RuntimeClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout)

    def get(self, path: str) -> dict[str, Any]:
        return _json(self.client.get(path))

    def post(
        self, path: str, body: dict[str, Any] | None = None, *, expect_job: bool = False
    ) -> dict[str, Any]:
        """Submit a request; with ``expect_job``, refuse a body that has no job id.

        A rejected job submit (clearance refused, content blocked, contract
        error) answers with an error object and **no** ``job_id``.  Returning
        that silently makes the caller poll an empty id and report a confusing
        404, so the refusal is raised here where the reason is still intact.
        """
        response = self.client.post(path, json=body or {})
        payload = _json(response)
        if response.status_code >= 400:
            raise SubmitRejected(
                f"HTTP {response.status_code} from {path}: "
                f"{json.dumps(payload, ensure_ascii=False)[:500]}"
            )
        if expect_job:
            data = _data(payload)
            if isinstance(data, dict) and not (data.get("job_id") or data.get("jobId")):
                raise SubmitRejected(
                    f"{path} accepted no job: {json.dumps(payload, ensure_ascii=False)[:500]}"
                )
        return payload

    def bytes(self, url: str) -> bytes:
        return self.client.get(url).content


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - surface the body verbatim
        return {"_status": response.status_code, "_body": response.text[:600]}


def _data(payload: dict[str, Any]) -> Any:
    value = payload.get("data")
    return value if value is not None else payload


def _project_path(project_id: str, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}/freezone/{suffix}"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _layer(step: str, message: str) -> None:
    """Log a gate decision so the receipt reads as a production log."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{step}] {message}", flush=True)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #

TERMINAL_FAILURES = {"failed", "error", "cancelled", "canceled", "timeout", "timed_out"}


def _poll_job(
    client: RuntimeClient,
    project_id: str,
    *,
    task_type: str,
    job_id: str,
    label: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Poll a freezone job result until it lands, fails, or times out.

    The result route wraps its payload as ``{"ok": true, "data": {...}}``; the
    interesting fields (``url`` and friends) live under ``data``.
    """
    path = _project_path(project_id, f"jobs/{task_type}/{job_id}/result")
    deadline = time.monotonic() + timeout_seconds
    started = time.monotonic()
    # A job record can take a moment to become visible after submit; a 404 or
    # 202 in that window means "not yet", not "gone".
    settle_deadline = started + 30.0
    last = ""
    while time.monotonic() < deadline:
        response = client.client.get(path)
        payload = _json(response)
        if response.status_code in (200, 202):
            if payload.get("ok") is True and isinstance(payload.get("data"), dict):
                _log(f"{label}: done in {time.monotonic() - started:.0f}s")
                return payload["data"]
            status = str(payload.get("status") or "")
            if status in TERMINAL_FAILURES:
                raise RuntimeError(
                    f"{label}: job failed: {json.dumps(payload, ensure_ascii=False)[:900]}"
                )
            note = f"{status}/{payload.get('current_task') or payload.get('info') or ''}"
        elif response.status_code == 404 and time.monotonic() < settle_deadline:
            note = "submitted/record not visible yet"
        else:
            raise RuntimeError(
                f"{label}: polling returned HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:800]}"
            )
        if note != last:
            _log(f"{label}: waiting ({note[:110]})")
            last = note
        time.sleep(3.0)
    raise TimeoutError(f"{label} timed out after {timeout_seconds:.0f}s")


def _upload(client: RuntimeClient, project_id: str, path: Path) -> str:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        response = client.client.post(
            _project_path(project_id, "upload"),
            files={"file": (path.name, handle, content_type)},
        )
    payload = _json(response)
    if response.status_code != 200:
        raise RuntimeError(f"upload {path.name} -> HTTP {response.status_code}: {payload}")
    url = str(_data(payload).get("url") or "")
    if not url:
        raise RuntimeError(f"upload {path.name} returned no url: {payload}")
    return url


def _is_mp4(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(12)
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _image_model_ref(client: RuntimeClient, name: str) -> str:
    """Resolve a model label to the reference the image endpoint expects.

    The image route addresses configured models as ``direct/<registry-id>``,
    not by their display label, so a bare label has to be looked up in the
    gateway config first.
    """
    if name.startswith("direct/") or name.startswith("direct_"):
        return name
    config = _data(client.get("/api/v1/model-gateway/config"))
    direct = config.get("directModels") if isinstance(config, dict) else None
    entries = direct.get("image") if isinstance(direct, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("model gateway config has no image model list")
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get("label") or "") == name or str(item.get("modelId") or "") == name:
            registry_id = str(item.get("id") or "")
            if registry_id:
                return f"direct/{registry_id}"
    usable = [
        item for item in entries
        if isinstance(item, dict) and item.get("usable") is True and item.get("enabled") is not False
    ]
    if usable:
        return f"direct/{usable[0]['id']}"
    raise RuntimeError(f"no configured image model matches {name!r} and none are usable")


# --------------------------------------------------------------------------- #
# stage 1 — asset lock
# --------------------------------------------------------------------------- #

def generate_asset(
    client: RuntimeClient,
    project_id: str,
    *,
    canvas_id: str,
    asset: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    label = str(asset["label"])
    _log(f"asset {label}: rendering ({asset['kind']})")

    # A re-run should not pay twice for an asset that already landed on disk.
    if args.reuse_frames:
        existing = _existing_asset(label)
        if existing is not None:
            url = _upload(client, project_id, existing)
            _layer("ASSET", f"{label} reused {existing.name}")
            return {
                "label": label,
                "kind": asset["kind"],
                "role": asset.get("role", "参考"),
                "url": url,
                "path": str(existing),
                "bytes": existing.stat().st_size,
                "reused": True,
            }

    model = _image_model_ref(client, str(asset.get("model") or args.scene_model))
    body = {
        "prompt": asset["prompt"],
        "aspect_ratio": asset.get("aspectRatio", "16:9"),
        "image_size": args.asset_image_size,
        "quality": args.asset_image_quality,
        "provider": "direct",
        "model": model,
        "model_id": model,
        "gen_mode": "textToImage",
        "canvas_id": canvas_id,
        "node_id": f"asset-{label}-{uuid.uuid4().hex[:8]}",
    }

    def once() -> str:
        accepted = _data(client.post(_project_path(project_id, "gen"), body, expect_job=True))
        result = _poll_job(
            client,
            project_id,
            task_type=str(accepted.get("task_type") or "freezone_gen"),
            job_id=str(accepted.get("job_id") or ""),
            label=f"asset {label}",
            timeout_seconds=args.image_timeout,
        )
        url = str(result.get("url") or "")
        if not url:
            raise RuntimeError(f"asset {label} returned no url: {result}")
        return url

    url = _attempt(once, label=f"asset {label}", attempts=args.attempts)
    target = ARTIFACT_DIR / f"{args.stamp}-asset-{label}.png"
    target.write_bytes(client.bytes(url))
    _layer("ASSET", f"{label} locked -> {target.name} ({target.stat().st_size} bytes)")
    return {
        "label": label,
        "kind": asset["kind"],
        "role": asset.get("role", "参考"),
        "url": url,
        "path": str(target),
        "bytes": target.stat().st_size,
        "reused": False,
    }


# --------------------------------------------------------------------------- #
# stage 2 — referenced storyboards
# --------------------------------------------------------------------------- #

def _slug(value: str, *, limit: int = 24) -> str:
    """Reduce a title to something safe to put in a filename."""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", str(value or "").strip())
    return (cleaned.strip("-") or "film")[:limit]


def _frame_name(index: int, title: str) -> str:
    """Frame filenames carry the film's own slug.

    Without it, ``*-05-frame.png`` matches the fifth shot of *any* earlier film
    and a re-run silently reuses another production's pictures.
    """
    return f"-{index:02d}-{_slug(title)}-frame.png"


def _existing_frame(index: int, title: str) -> Path | None:
    """Find an already-rendered frame for this shot *of this film*."""
    matches = sorted(ARTIFACT_DIR.glob(f"*{_frame_name(index, title)}"))
    return matches[-1] if matches else None


def _existing_asset(label: str) -> Path | None:
    matches = sorted(ARTIFACT_DIR.glob(f"*-asset-{label}.png"))
    return matches[-1] if matches else None


def _clip_name(index: int, title: str) -> str:
    return f"-{index:02d}-{_slug(title)}-clip.mp4"


def _existing_clip(index: int, title: str) -> Path | None:
    """Find an already-rendered clip for this shot *of this film*.

    Re-rendering twelve clips to recover one failed shot is the most expensive
    mistake this pipeline can make, so clips are reused the same way frames are.
    """
    matches = sorted(ARTIFACT_DIR.glob(f"*{_clip_name(index, title)}"))
    return matches[-1] if matches else None


def _newest_clip_stamp() -> str:
    """The most recent batch stamp that actually has clips on disk."""
    stamps = sorted({p.name.split("-")[0] for p in ARTIFACT_DIR.glob("*-clip.mp4")})
    return stamps[-1] if stamps else ""


def _index_set(value: str) -> set[int]:
    """Parse ``"6,7,8"`` into ``{6, 7, 8}``; anything unparseable is ignored."""
    return {int(item) for item in (value or "").split(",") if item.strip().isdigit()}


def render_storyboard(
    client: RuntimeClient,
    project_id: str,
    *,
    canvas_id: str,
    shot: dict[str, Any],
    asset_urls: list[str],
    assets: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    index = _index(shot["index"], "shot.index")

    # A re-run should not pay twice for a frame that already landed on disk.
    # ``--reshoot-frames 7,8`` is the exception: those shots are defective in the
    # frame itself (wardrobe, colour), and since the frame now leads the clip's
    # reference list, fixing the clip without fixing the frame would just bake
    # the same defect back in.
    forced = index in _index_set(getattr(args, "reshoot_frames", ""))
    if args.reuse_frames and not forced:
        existing = _existing_frame(index, args.title_slug)
        if existing is not None:
            _layer("FRAME", f"shot {index} reused {existing.name}")
            return {
                "index": index,
                "shot": shot,
                "url": "",
                "path": str(existing),
                "reused": True,
            }

    frame_model = _image_model_ref(client, args.frame_model)
    shot_refs = [a["url"] for a in references_for_shot(shot, assets)]
    body: dict[str, Any] = {
        "prompt": _storyboard_prompt(shot, args, assets),
        "aspect_ratio": args.aspect_ratio,
        "image_size": args.image_size,
        "quality": args.image_quality,
        "provider": "direct",
        "model": frame_model,
        "model_id": frame_model,
        "gen_mode": "imageToImage" if shot_refs else "textToImage",
        "canvas_id": canvas_id,
        "node_id": f"shot-{index:02d}-frame-{uuid.uuid4().hex[:8]}",
    }
    if shot_refs:
        body["reference_urls"] = shot_refs

    def once() -> str:
        accepted = _data(client.post(_project_path(project_id, "gen"), body, expect_job=True))
        result = _poll_job(
            client,
            project_id,
            task_type=str(accepted.get("task_type") or "freezone_gen"),
            job_id=str(accepted.get("job_id") or ""),
            label=f"shot {index} frame",
            timeout_seconds=args.image_timeout,
        )
        url = str(result.get("url") or "")
        if not url:
            raise RuntimeError(f"shot {index} frame returned no url: {result}")
        return url

    url = _attempt(once, label=f"shot {index} frame", attempts=args.attempts)
    target = ARTIFACT_DIR / f"{args.stamp}{_frame_name(index, args.title_slug)}"
    target.write_bytes(client.bytes(url))
    _layer("FRAME", f"shot {index} rendered with {len(asset_urls)} locked reference(s)")
    return {"index": index, "shot": shot, "url": url, "path": str(target), "reused": False}


def references_for_shot(
    shot: dict[str, Any], assets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Only the assets this shot actually contains.

    Attaching a character who is not in the scene is a documented failure mode
    in the prompt compiler's rules — the model force-inserts them.  A shot with
    no cast gets the scene alone.
    """
    cast = {str(name) for name in (shot.get("cast") or [])}
    out: list[dict[str, Any]] = []
    for asset in assets:
        if asset["kind"] == "character":
            if asset["label"] in cast:
                out.append(asset)
        else:
            out.append(asset)
    return out


def _reference_block(
    shot: dict[str, Any], assets: list[dict[str, Any]], *, storyboard: bool = False
) -> str:
    """State what each attached reference may and may not control.

    This is the fix for the worst failure this pipeline has produced: attaching
    a whole multi-view character sheet made the model composite the *document*
    into the frame — faces appeared in the walls and a third person walked into
    a two-person shot.  Two rules follow from that:

    * only characters present in the shot get their reference attached;
    * every reference declares what it provides and what it must not provide,
      because the model does not infer that from the image.

    ``storyboard`` prepends reference 1, the approved frame for this shot.  It
    goes first so the composition that was reviewed is the composition that
    renders; before this existed the frame was uploaded and then attached only
    when a shot had no references at all, so on any shot with a character the
    approved frame never reached the model.
    """
    attached = references_for_shot(shot, assets)
    characters = [a for a in attached if a["kind"] == "character"]
    scene = next((a for a in attached if a["kind"] == "scene"), None)

    lines: list[str] = []
    offset = 0
    if storyboard:
        lines.append(
            "参考图1是本镜已通过审核的分镜画面，提供构图、机位、焦段、人物站位、"
            "画面内物体的相对位置与光线方向；不得参考它的人物面部细节"
            "（以角色参考为准），也不得照抄它的任何瑕疵、斑点或多余纹样。"
        )
        offset = 1
    for position, asset in enumerate(characters, start=1 + offset):
        lines.append(
            f"参考图{position}提供「{asset['label']}」的脸型、五官、发型、肤色与体型；"
            "不得参考它的背景、构图与光线，也不得把它当画面内容。"
        )
    if scene is not None:
        lines.append(
            f"参考图{len(characters) + 1 + offset}提供场景「{scene['label']}」的空间关系与固定陈设；"
            "不得参考它的人物、光线方向与构图。"
        )
    absent = [
        a["label"] for a in assets
        if a["kind"] == "character" and a["label"] not in {str(n) for n in (shot.get("cast") or [])}
    ]
    if absent:
        lines.append(
            "本镜只有上述人物在场——" + "、".join(absent) + "不在本镜中，不得出现在画面里。"
        )
    return "\n".join(lines)


def _storyboard_prompt(
    shot: dict[str, Any], args: argparse.Namespace, assets: list[dict[str, Any]]
) -> str:
    """Assemble a shot prompt as ordered blocks, not free prose.

    Block order comes from the film-craft library (10-提示词工程规范 §三):
    context and references first, then space and time, then action, then the
    descriptive layers, then locks last.
    """
    head = [
        f"画面中恰好 {_index(shot.get('people', 1), 'shot.people')} 个人 —— 不允许出现重复的人。",
        f"场景：{args.scene_line}",
        f"镜头目的：{shot.get('purpose', '')}",
        f"本镜只做一件事：{shot.get('action', '')}",
    ]
    cast = [str(name) for name in (shot.get("cast") or [])]
    if len(cast) >= 2:
        # Two people in one frame have to be tellable apart.  Measured failure:
        # both costumes rendered near-black, so the fight read as one dark shape
        # hitting another and the audience could not follow who was who.  The
        # colours themselves come from the shot's own wardrobe line, so this
        # rule carries no production's character names.
        head.append(
            f"人物可分辨（{len(cast)} 人同框，这条是硬要求）：{', '.join(cast)} 的服装颜色"
            "必须在画面上能一眼分开，两条衣服不得都是同一种深色、都趋近黑色或都趋近同一个明度；"
            "即使两人都处在暗部，也要靠环境光或湿面反光让两种颜色分得开。"
            f"本镜的服装设定：{shot.get('wardrobe', '')}"
        )
    body = [
        _reference_block(shot, assets),
        f"构图：{shot.get('composition', '')}",
        f"光学：{shot.get('optics', '')}",
        f"机身：{shot.get('camera', '')}",
        f"动作：{shot.get('beats', '')}",
        f"表演：{shot.get('performance', '')}",
        f"光照：{args.lighting_line}",
        f"色彩配额：{args.color_line}",
        f"服装：{shot.get('wardrobe', '')}",
        f"风格：{args.style_line}",
        "正向锁：画面中所有表面干净无文字；不出现角色卡之外的人；物件数量与描述一致，不增多。",
    ]
    return "\n".join(line for line in head + body if line and not line.endswith("："))


# --------------------------------------------------------------------------- #
# stage 3 — referenced clips
# --------------------------------------------------------------------------- #

def render_clip(
    client: RuntimeClient,
    project_id: str,
    *,
    canvas_id: str,
    storyboard: dict[str, Any],
    assets: list[dict[str, Any]],
    args: argparse.Namespace,
    ladder: ResolutionLadder,
) -> dict[str, Any]:
    index = _index(storyboard["index"], "shot.index")
    shot = storyboard["shot"]

    # ``--reshoot 6,7,8`` re-renders exactly those shots and reuses the rest, so
    # repairing three defective clips never means paying again for the five that
    # were already approved.
    reshoot = _index_set(getattr(args, "reshoot", ""))
    may_reuse = not reshoot or index not in reshoot

    if may_reuse and (args.reuse_frames or reshoot):
        reusable = _existing_clip(index, args.title_slug)
        if reusable is not None:
            _layer("CLIP", f"shot {index} reused {reusable.name}")
            return {
                "index": index,
                "path": str(reusable),
                "bytes": reusable.stat().st_size,
                "dialogue": shot_dialogue(shot)[1],
                "duration": args.duration,
                "resolution": ladder.current,
                "chain": str(shot.get("chain") or "none"),
                "hiddenCut": bool(shot.get("hiddenCut")),
                "reused": True,
            }

    image_url = _upload(client, project_id, Path(storyboard["path"]))
    # The approved frame leads the reference list.  AllReference takes up to 9
    # images, so composition rides along with the characters and the set; the
    # frame is not merely an approval artifact any more.
    shot_references = [
        {
            "type": "image",
            "url": image_url,
            "role": "分镜构图参考",
            "label": f"shot {index} storyboard",
        }
    ] + [
        {
            "type": "image",
            "url": asset["url"],
            "role": asset["role"],
            "label": asset["label"],
        }
        for asset in references_for_shot(shot, assets)
    ]
    payload: dict[str, Any] = {
        "prompt": _motion_prompt(shot, args) + "\n" + _reference_block(shot, assets, storyboard=True),
        "references": shot_references,
        "aspect_ratio": args.aspect_ratio,
        "resolution": ladder.current,
        "duration_seconds": args.duration,
        "model": args.video_model,
        "model_id": args.video_model,
        "gen_mode": "allReference",
        "canvas_id": canvas_id,
        "node_id": f"shot-{index:02d}-clip-{uuid.uuid4().hex[:8]}",
    }
    payload["image_urls"] = [image_url]
    payload.update(_audio_payload(shot))
    endpoint = "video/omni-gen" if shot_references else "video/i2v"
    started = time.monotonic()

    def once() -> str:
        attempted = ladder.current
        payload["resolution"] = attempted
        try:
            accepted = _data(
                client.post(_project_path(project_id, endpoint), payload, expect_job=True)
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            if _is_resolution_rejection(exc, attempted):
                nxt = ladder.step_down(attempted)
                if nxt:
                    _log(f"shot {index}: resolution {attempted} refused; stepping down to {nxt}")
                    raise RuntimeError(f"resolution fallback to {nxt}") from exc
            raise
        result = _poll_job(
            client,
            project_id,
            task_type=str(accepted.get("task_type") or "freezone_video_gen"),
            job_id=str(accepted.get("job_id") or ""),
            label=f"shot {index} clip",
            timeout_seconds=args.video_timeout,
        )
        url = str(result.get("url") or "")
        if not url:
            raise RuntimeError(f"shot {index} clip returned no url: {result}")
        return url

    url = _attempt(once, label=f"shot {index} clip", attempts=args.attempts)
    clip_stamp = getattr(args, "clip_stamp", "") or args.stamp
    target = ARTIFACT_DIR / f"{clip_stamp}{_clip_name(index, args.title_slug)}"
    target.write_bytes(client.bytes(url))
    if not _is_mp4(target):
        raise RuntimeError(f"shot {index} clip is not an mp4: {target}")
    _layer(
        "CLIP",
        f"shot {index} done in {time.monotonic() - started:.0f}s "
        f"({target.stat().st_size} bytes, {len(shot_references)} refs, chain={shot.get('chain')})",
    )
    return {
        "index": index,
        "path": str(target),
        "bytes": target.stat().st_size,
        "dialogue": shot_dialogue(shot)[1],
        "duration": args.duration,
        "resolution": ladder.current,
        "chain": str(shot.get("chain") or "none"),
        "hiddenCut": bool(shot.get("hiddenCut")),
    }


def _shot_mentions(shot: dict[str, Any], needle: str) -> bool:
    """True when the shot's own text names the thing, not merely a shared character.

    The mirror rule below used to fire on any ``镜`` in the shot JSON — which
    every Chinese shot list contains, because 镜头 ("shot") and 运镜 ("camera
    move") both carry it.  A rain-night fight got the mirror constraint injected
    into all six prompts purely because they were described as shots.  Matching
    on the actual words keeps the rule for films that really have a mirror.
    """
    for value in shot.values():
        if isinstance(value, str) and needle in value:
            return True
        if isinstance(value, (list, dict)) and needle in json.dumps(value, ensure_ascii=False):
            return True
    return False


def _global_rules(shot: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Constraints that ride on every clip, each one earned by a real render.

    These are not style preferences.  Every line here is a defect that reached
    a finished film and had to be paid for twice:

    * lettering — a fluorescent tube came back with "AA" on it and a chalk box
      came back with pseudo-Cyrillic, so "surfaces are clean" was too vague and
      the banned marks have to be enumerated surface by surface.
    * one palette — a declared accent colour rendered as a neighbouring
      saturated hue, which broke the locked colour system in a punchline shot.
    * mirror physics — a shot held one subject in pure profile while the mirror
      in the same frame showed his front, and let the mirror's oxidation spots
      spill onto the clean wall beside it.
    * prop identity — a prop carries one colour, material and shape across the
      whole film; a colour change between shots reads as a continuity error.

    Everything here is film-independent: the palette line comes from the shot
    list's own ``color`` field, and the surface list no longer names one
    production's props.  A rule that only makes sense for one film belongs in
    that film's shot list, not in the driver.
    """
    rules = [
        "禁字（逐面检查）：灯管、灯罩、包装、票据、招牌、书脊、标签、匾额等"
        "一切物件表面都必须是素面，没有字母、文字、数字、刻字、商标、品牌名"
        "与仿文字笔画；画面中出现的任何纸张、布料、木料与金属表面都不得带有"
        "印刷字、手写字、编号或仿文字笔画。",
        f"颜色落地：画面中所有颜色都必须落在本片体系内（{args.color_line}）；"
        "任何线条、布料、金属、木料与纸张的颜色都不得偏离该体系，"
        "尤其不得出现荧光色、高饱和色或与体系无关的邻近色。",
        "道具同一性：同一件道具在每一镜里保持同一颜色、同一材质、同一形状与"
        "同一磨损程度，不在镜头之间变色或变形。",
    ]
    if _shot_mentions(shot, "镜面") or _shot_mentions(shot, "镜子") or _shot_mentions(shot, "倒影"):
        rules.append(
            "镜面：镜中的倒影与实物是同一个人、同一朝向、同一动作、同一时刻；"
            "镜面之外的墙面与地面保持干净的素色，没有任何斑点、污渍、飞溅或剥落。"
        )
    return rules


_SPEAKER_PREFIX = re.compile(r"^\s*(?:@(?P<at>[^：:]{1,40})|(?P<named>[^：:]{1,40}))\s*[：:]\s*(?P<text>.+)$")


def shot_dialogue(shot: dict[str, Any]) -> tuple[str, str]:
    """Return ``(speaker, line)`` for this shot, from wherever the line lives.

    Two carriers exist in this repo and the driver only knew one of them:

    * the shot list's own top-level ``dialogue`` (how the first film wrote it);
    * ``cinematic.combat.dialogue_lines`` — the carrier the combat film
      contract reads, and the one its own test fixture uses, written as
      ``"刀客：你只有这一招。"``.

    Reading only the top-level field silently turned every fight shot into a
    dialogue-free shot: the film rendered mute while the contract, which reads
    the other carrier, reported the lines as declared.  Lines are found here
    from either carrier, with an optional ``说话人：`` prefix.
    """

    candidates: list[str] = []
    top = shot.get("dialogue")
    if isinstance(top, list):
        candidates.extend(str(item) for item in top)
    elif str(top or "").strip():
        candidates.append(str(top))
    cinematic = shot.get("cinematic")
    if isinstance(cinematic, dict):
        combat = cinematic.get("combat")
        if isinstance(combat, dict):
            lines = combat.get("dialogue_lines")
            if isinstance(lines, list):
                candidates.extend(str(item) for item in lines)

    speaker = str(shot.get("speaker") or "").strip()
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        match = _SPEAKER_PREFIX.fullmatch(text)
        if match and match.group("text"):
            # An explicit ``speaker`` field still wins over the inline prefix.
            return speaker or str(match.group("at") or match.group("named") or "").strip(), match.group("text").strip()
        return speaker, text
    return speaker, ""


def _audio_payload(shot: dict[str, Any]) -> dict[str, Any]:
    """Build the audio half of a clip submission.

    Three defects are fixed here, each one measured rather than reasoned about:

    * ``native_audio_strategy: "silent"`` is not a legal value — the API takes
      only ``external`` or ``native``, so *every* shot without dialogue was
      rejected with HTTP 422 before it could be billed.  It is now ``external``,
      which is both legal and the honest description of this pipeline's audio
      plan (there is no external track yet, but the provider's own voice is not
      the deliverable).
    * A shot with no dialogue used to send ``generate_audio: False``, and this
      repo's own artifact gate **strips the provider audio track** for such a
      shot.  A rain fight would therefore open completely silent — no rain, no
      room tone — because "no dialogue" had been conflated with "no sound".
      Ambience is not dialogue: the switch now follows the shot's own declared
      ``cinematic.sound`` layers, so a fight keeps its rain and a declared line
      still gets spoken.
    * The spoken line is sent in the structured fields the provider prompt
      builder reads (``spoken_dialogue``/``dialogue_text``), never left in the
      visual prompt, so the model reads the words once, in one slot.
    """

    speaker, line = shot_dialogue(shot)
    has_dialogue = bool(line)
    cinematic = shot.get("cinematic")
    sound = cinematic.get("sound") if isinstance(cinematic, dict) else None
    # Ambience is a picture-level property, not a speech decision: a shot that
    # declares rain keeps its rain whether or not anyone speaks in it.
    wants_sound = has_dialogue or _declares_sound(sound)

    payload: dict[str, Any] = {
        "generate_audio": wants_sound,
        "generate_audio_explicit": wants_sound,
        "native_audio_strategy": "native" if wants_sound else "external",
    }
    if has_dialogue:
        payload.update(
            {
                "dialogue_text": line,
                "spoken_dialogue": [line],
                "audio_type": "dialogue",
                "speaker": speaker,
            }
        )
    else:
        # ``silence`` here means "nothing is scripted to be said", which is what
        # the field documents; the ambience still comes back on the track.
        payload["audio_type"] = "silence"
    return payload


def _declares_sound(sound: object) -> bool:
    """True when the shot declares any ambience, source or effect to hear."""

    if not isinstance(sound, dict):
        return False
    for key in ("ambience", "diegetic_sources", "sfx_cues"):
        value = sound.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, tuple)) and any(str(item).strip() for item in value):
            return True
    return False


def _motion_prompt(shot: dict[str, Any], args: argparse.Namespace) -> str:
    """The clip prompt: camera, action, physics, sound.

    No duration phrases — ``duration_seconds`` is the authority, and a stray
    duration in prose makes the upstream contract complain.
    """
    parts = [
        f"运镜：{shot.get('cameraMove', shot.get('camera', ''))}",
        f"主体动作：{shot.get('action', '')}",
        f"动作节拍：{shot.get('beats', '')}",
        f"环境动态：{shot.get('environment', '')}",
        f"物理：{shot.get('physics', '')}",
        f"光照：{args.lighting_line}",
        f"色彩配额：{args.color_line}",
        f"服装：{shot.get('wardrobe', '')}",
        f"风格：{args.style_line}",
    ]
    parts.append(_sound_line(shot))
    parts.append("正向锁：人物面部、发型、服装与参考保持一致。")
    parts.extend(_global_rules(shot, args))
    return "\n".join(p for p in parts if p and not p.endswith("："))


def _sound_line(shot: dict[str, Any]) -> str:
    """What the model should put on the track, stated as a positive instruction.

    A native-audio model reads this prompt as a screenplay and fills whatever
    sound the text leaves open, so the shot has to say which of the two jobs it
    is doing.  Measured failure: an unmarked visual-only shot came back with an
    invented spoken line; the constraint has to name the sound that *is* wanted
    rather than forbid the sound that is not (a negative sentence reads as more
    performable copy).
    """

    _speaker, line = shot_dialogue(shot)
    if line:
        return (
            "声音：本镜只有人物说出的那一句台词，与画面内的环境声；"
            "没有配乐，没有旁白，没有第二个人说话。"
        )
    cues = ",".join(str(item) for item in _sound_cues(shot))
    if cues:
        return f"声音：只有环境声与画面内声源（{cues}），全程无人说话，无配乐，无旁白。"
    return "声音：只有环境声与画面内声源，全程无人说话，无配乐，无旁白。"


def _sound_cues(shot: dict[str, Any]) -> list[str]:
    """The shot's own declared sounds, used to name what the model may render."""

    cinematic = shot.get("cinematic")
    sound = cinematic.get("sound") if isinstance(cinematic, dict) else None
    if not isinstance(sound, dict):
        return []
    cues: list[str] = []
    for key in ("ambience", "diegetic_sources", "sfx_cues"):
        value = sound.get(key)
        if isinstance(value, str) and value.strip():
            cues.append(value.strip())
        elif isinstance(value, (list, tuple)):
            cues.extend(str(item).strip() for item in value if str(item).strip())
    return cues[:6]


# --------------------------------------------------------------------------- #
# stage 4 — seams
# --------------------------------------------------------------------------- #

def _probe_duration(path: Path) -> float:
    """Read a media file's real duration in seconds.

    Never assume a requested ``duration_seconds`` equals what the upstream
    returns: a 5-second request comes back as 5.167s (124 frames at 24fps).
    Seam offsets computed from the request instead of the file drift by the
    difference at every cut, which desynchronises picture from sound.
    """
    result = run_project_subprocess(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(f"could not probe {path.name}: {exc}") from exc
    for stream in payload.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("duration"):
            return float(stream["duration"])
    duration = (payload.get("format") or {}).get("duration")
    if duration:
        return float(duration)
    raise RuntimeError(f"could not determine duration of {path.name}")


def _probe_streams(path: Path) -> dict[str, float | int | None]:
    """Return the video and audio stream durations plus the frame count."""
    result = run_project_subprocess(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-count_frames", "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    payload = json.loads(result.stdout or "{}")
    out: dict[str, float | int | None] = {"video": None, "audio": None, "frames": None}
    for stream in payload.get("streams", []):
        duration = stream.get("duration")
        value = float(duration) if duration else None
        if stream.get("codec_type") == "video" and out["video"] is None:
            out["video"] = value
            frames = stream.get("nb_read_frames") or stream.get("nb_frames")
            out["frames"] = int(frames) if frames and str(frames).isdigit() else None
        elif stream.get("codec_type") == "audio" and out["audio"] is None:
            out["audio"] = value
    if out["video"] is None:
        out["video"] = float((payload.get("format") or {}).get("duration") or 0.0) or None
    return out


def _seam_graph(durations: list[float], fade: float, has_audio: list[bool]) -> tuple[str, str, str]:
    """Build the xfade / acrossfade graph for clips of the given real lengths.

    Two things have to be exact for picture and sound to share one clock:

    * **Offsets come from probed durations**, not the requested ones — a "5
      second" clip is 5.167s (124 frames at 24fps), and using the request at
      every cut accumulates a visible drift.
    * **Each input is trimmed to its own measured length and its timestamps
      reset to zero.**  Encoders leave a little padding on every audio stream;
      ``acrossfade`` consumes real samples, so that padding would otherwise
      stack up into a growing offset.  Trimming and ``asetpts`` removes it.

    Inputs without audio get silence generated at the exact same length.
    """
    count = _index(len(durations), "clip count")
    if count < 2:
        raise ValueError("a seam needs at least two clips")
    fade = _number(fade, "crossfade", low=0.05, high=min(durations) / 2)

    heads: list[str] = []
    v_prev, a_prev = "v0", "a0"
    for index, raw in enumerate(durations):
        length = _number(raw, "clip duration", low=0.05, high=3600.0)
        heads.append(
            f"[{index}:v]trim=start=0:end={length:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
        if has_audio[index]:
            heads.append(
                f"[{index}:a]atrim=start=0:end={length:.6f},asetpts=PTS-STARTPTS[a{index}]"
            )
        else:
            heads.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=start=0:end={length:.6f},asetpts=PTS-STARTPTS[a{index}]"
            )

    parts: list[str] = list(heads)
    consumed = 0.0
    for step in range(1, count):
        consumed += _number(durations[step - 1], "clip duration", low=0.05, high=3600.0)
        offset = consumed - fade * step
        if offset <= 0:
            raise ValueError(f"seam {step} would start before zero ({offset:.3f})")
        v_out, a_out = f"vx{step}", f"ax{step}"
        parts.append(
            f"[{v_prev}][v{step}]xfade=transition=fade:duration={fade:.3f}"
            f":offset={offset:.3f}[{v_out}]"
        )
        parts.append(f"[{a_prev}][a{step}]acrossfade=d={fade:.3f}[{a_out}]")
        v_prev, a_prev = v_out, a_out
    return ";".join(parts), v_prev, a_prev


def join_clips(clips: list[dict[str, Any]], *, stamp: str, fade: float) -> tuple[Path, dict[str, Any]]:
    """Join clips with a video + audio crossfade at every boundary.

    A bare concat makes every cut a butt-joint: room tone jumps, and one shot's
    last frame slams into the next shot's first frame.  The film-craft library
    additionally wants each cut to land on a motion beat, which is authored in
    the shot list (``hiddenCut``) rather than fixed here.
    """
    ordered = sorted(clips, key=lambda item: item["index"])
    if len(ordered) == 1:
        return Path(ordered[0]["path"]), {"mode": "single-clip"}

    durations = [_probe_duration(Path(c["path"])) for c in ordered]
    has_audio = [bool(_probe_streams(Path(c["path"]))["audio"]) for c in ordered]
    graph, v_last, a_last = _seam_graph(durations, fade, has_audio)
    script = _write_filter_script(graph, stem="seam")
    joined = ARTIFACT_DIR / f"{stamp}-joined.mp4"

    args = ["ffmpeg", "-v", "error", "-y"]
    for clip in ordered:
        args += ["-i", str(Path(clip["path"]))]
    args += [
        "-filter_complex_script", str(script),
        "-map", f"[{v_last}]", "-map", f"[{a_last}]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        str(joined),
    ]
    try:
        _run_ffmpeg(args)
    finally:
        script.unlink(missing_ok=True)

    expected = sum(durations) - fade * (len(durations) - 1)
    hidden = sum(1 for c in ordered if c.get("hiddenCut"))
    _layer(
        "SEAM",
        f"joined {len(ordered)} clips with {fade:.2f}s crossfade "
        f"({hidden} hidden cut(s)); inputs trimmed to probed lengths, "
        f"expected runtime {expected:.2f}s -> {joined.name}",
    )
    seams = {
        "mode": "crossfade",
        "fadeSeconds": fade,
        "clipDurations": [round(d, 3) for d in durations],
        "clipsWithoutAudio": [c["index"] for c, ok in zip(ordered, has_audio) if not ok],
        "requestedDuration": ordered[0].get("requestedDuration"),
        "expectedRuntime": round(expected, 3),
        "hiddenCuts": hidden,
    }
    return joined, seams


def verify_sync(path: Path) -> dict[str, Any]:
    """Check that picture and sound still share one clock after the seams.

    The failure this catches is silent: if the xfade offsets are computed from
    the requested duration rather than the probed one, the streams end on
    slightly different lengths and drift apart across the film.
    """
    streams = _probe_streams(path)
    video, audio, frames = streams["video"], streams["audio"], streams["frames"]
    delta = abs((video or 0.0) - (audio or 0.0)) if video and audio else None
    # AAC stream padding puts a few tens of milliseconds between the stream
    # durations even on a perfectly aligned file, so allow ~2 frames.
    tolerance = 0.1
    return {
        "videoSeconds": round(video, 3) if video else None,
        "audioSeconds": round(audio, 3) if audio else None,
        "deltaSeconds": round(delta, 4) if delta is not None else None,
        "videoFrames": frames,
        "toleranceSeconds": tolerance,
        "withinTolerance": bool(delta is not None and delta <= tolerance),
    }


def measure_clip_alignment(
    film: Path,
    clips: list[dict[str, Any]],
    *,
    fade: float,
    sample_rate: int = 8000,
    probe_seconds: float = 2.0,
    search_seconds: float = 0.8,
) -> dict[str, Any]:
    """Locate where each clip's sound actually landed in the finished film.

    Stream durations can agree while the content is still shifted, and a
    crossfade chain can accumulate a small error at every transition.  This
    cross-correlates each clip's own audio against the film to measure the real
    placement, so "in sync" is a number rather than an opinion.
    """
    import wave

    ordered = sorted(clips, key=lambda item: item["index"])
    durations = [_probe_duration(Path(c["path"])) for c in ordered]
    expected: list[float] = []
    cursor = 0.0
    for position, duration in enumerate(durations):
        expected.append(cursor - fade * position)
        cursor += duration

    with tempfile.TemporaryDirectory(prefix="align-") as tmp:
        tmp_dir = Path(tmp)

        def decode(source: Path, name: str) -> list[int]:
            target = tmp_dir / f"{name}.wav"
            result = run_project_subprocess(
                [
                    "ffmpeg", "-v", "error", "-y", "-i", str(source),
                    "-ac", "1", "-ar", str(sample_rate), "-f", "wav", str(target),
                ],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0 or not target.exists():
                return []
            with wave.open(str(target), "rb") as handle:
                raw = handle.readframes(handle.getnframes())
            return memoryview(raw).cast("h").tolist() if raw else []

        film_samples = decode(film, "film")
        if not film_samples:
            return {"measured": False, "reason": "could not decode the film audio"}

        span = int(probe_seconds * sample_rate)
        reach = int(search_seconds * sample_rate)
        rows: list[dict[str, Any]] = []
        for position, clip in enumerate(ordered):
            clip_samples = decode(Path(clip["path"]), f"clip{position:02d}")
            if len(clip_samples) < span * 2:
                continue
            # Reference = the clip's own loudest window.  Anchoring on a fixed
            # early slice fails on shots that open on quiet room tone, where the
            # match is noise.
            step = max(1, sample_rate // 4)
            best_offset, best_energy = 0, -1.0
            for start in range(0, max(1, len(clip_samples) - span), step):
                energy = sum(s * s for s in clip_samples[start : start + span])
                if energy > best_energy:
                    best_energy, best_offset = energy, start
            reference = clip_samples[best_offset : best_offset + span]
            if len(reference) < span:
                continue
            ref_energy = math.sqrt(best_energy) or 1.0

            anchor = expected[position] + best_offset / sample_rate
            best_lag, best_score = 0, -1.0
            centre = int(anchor * sample_rate)
            for lag in range(-reach, reach + 1, max(1, sample_rate // 200)):
                start = centre + lag
                if start < 0 or start + span > len(film_samples):
                    continue
                window = film_samples[start : start + span]
                energy = math.sqrt(sum(s * s for s in window)) or 1.0
                score = sum(a * b for a, b in zip(reference, window)) / (ref_energy * energy)
                if score > best_score:
                    best_score, best_lag = score, lag
            measured_start = (centre + best_lag) / sample_rate - best_offset / sample_rate
            rows.append(
                {
                    "index": clip["index"],
                    "expectedStart": round(expected[position], 3),
                    "measuredStart": round(measured_start, 3),
                    "errorSeconds": round(measured_start - expected[position], 3),
                    "referenceOffset": round(best_offset / sample_rate, 2),
                    "confidence": round(best_score, 3),
                }
            )

    errors = [abs(r["errorSeconds"]) for r in rows if r.get("confidence", 0) > 0.15]
    worst = max(errors) if errors else None
    return {
        "measured": True,
        "sampleRate": sample_rate,
        "toleranceSeconds": 0.1,
        "worstErrorSeconds": worst,
        "withinTolerance": bool(worst is not None and worst <= 0.1),
        "perClip": rows,
    }


def speech_presence(path: Path, *, hop: float = 0.02, win: float = 0.04) -> dict[str, Any]:
    """Measure whether the track carries speech-shaped sound, without an ASR.

    There is no speech recogniser on this machine and the hosted route refuses
    the account, so "did the actor actually say the line" cannot be answered by
    transcribing.  It can still be answered *statistically*: speech is built
    from syllables, so its short-time energy swings hard (loud vowel nuclei
    separated by quieter consonants and pauses), while rain, room tone and a
    held sword ring are near-stationary.  Measured here:

    * ``modulationIndex`` — spread of the short-time RMS around its own median,
      as a fraction of that median.  Steady ambience sits near zero.
    * ``peakToMedianDb`` — how far above the ambient floor the loudest syllable
      rises.  A voice over rain clears several dB; rain alone does not.
    * ``silenceRatio`` — share of the track under a floor derived from its own
      20th percentile, which separates a speaking shot (gaps between phrases)
      from a continuous one.

    This is a proxy, not proof: it can confirm a track is *shaped like speech*
    and cannot confirm *which words*.  The report says so in as many words.
    """

    import wave

    with tempfile.TemporaryDirectory(prefix="speech-") as tmp:
        wav_path = Path(tmp) / "probe.wav"
        result = run_project_subprocess(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(path),
                "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path),
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not wav_path.exists():
            return {"measured": False, "reason": "could not decode the clip audio"}
        with wave.open(str(wav_path), "rb") as handle:
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        if not raw:
            return {"measured": False, "reason": "clip audio is empty"}

        samples = memoryview(raw).cast("h")
        step = max(1, int(rate * hop))
        span = max(step, int(rate * win))
        levels: list[float] = []
        for start in range(0, len(samples) - span, step):
            chunk = samples[start:start + span]
            total = sum(int(value) * int(value) for value in chunk)
            levels.append(math.sqrt(total / span))
        if not levels:
            return {"measured": False, "reason": "clip audio is too short"}

    ordered = sorted(levels)
    median = ordered[len(ordered) // 2] or 1e-9
    floor = ordered[max(0, int(len(ordered) * 0.20))] or 1e-9
    peak = ordered[-1]
    deviations = [abs(value - median) for value in levels]
    deviations.sort()
    spread = deviations[len(deviations) // 2] if deviations else 0.0
    quiet = sum(1 for value in levels if value <= floor * 1.5)

    return {
        "measured": True,
        "medianRms": round(median, 2),
        "peakRms": round(peak, 2),
        "modulationIndex": round(spread / median, 3),
        "peakToMedianDb": round(20 * math.log10(peak / median), 2) if median > 0 else None,
        "ambientFloorRms": round(floor, 2),
        "silenceRatio": round(quiet / len(levels), 3),
        "windows": len(levels),
        "isSpeechShaped": bool(spread / median >= 0.35 and peak / median >= 1.6),
        "caveat": (
            "语音包络代理，不是转录：能证明音轨的形状像说话（有音节起伏与句间停顿），"
            "不能证明说的是哪句台词。"
        ),
    }


def speech_report(clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Check every clip's audio against what the shot list declared it would do.

    The check is two-sided on purpose, because a one-sided check passes for the
    wrong reason: a dialogue shot only counts as passing if its track is shaped
    like speech, and a declared-silent shot only counts as passing if it is
    *not*.  That second half is what catches the model filling an unspeaking
    shot with an invented line — the failure this pipeline has already paid for
    once.

    The verdict is deliberately weak-worded: this proves a track is shaped like
    speech, never that the authored words were the ones spoken.  No recogniser
    is available on this machine.
    """

    rows: list[dict[str, Any]] = []
    for clip in sorted(clips, key=lambda item: item["index"]):
        declared = str(clip.get("dialogue") or "").strip()
        probe = speech_presence(Path(clip["path"]))
        shaped = probe.get("isSpeechShaped")
        if not probe.get("measured"):
            verdict = "unmeasured"
        elif declared:
            verdict = "dialogue_present" if shaped else "dialogue_missing"
        else:
            verdict = "silent_ok" if not shaped else "invented_speech"
        rows.append(
            {
                "index": clip["index"],
                "declaredLine": declared,
                "verdict": verdict,
                **{key: value for key, value in probe.items() if key not in {"caveat"}},
            }
        )

    failed = [row["index"] for row in rows if row["verdict"] in {"dialogue_missing", "invented_speech"}]
    unmeasured = [row["index"] for row in rows if row["verdict"] == "unmeasured"]
    return {
        "perClip": rows,
        "failed": failed,
        "unmeasured": unmeasured,
        "passed": not failed and not unmeasured,
        "method": "音节级音量包络（有台词的镜头须像说话；声明无台词的镜头须不像）",
        "caveat": (
            "这是代理判据，不是转录：能证明音轨形状像说话，不能证明说的是剧本里的那句话。"
        ),
    }


def speech_windows(path: Path, *, window: float = 0.5, top: int = 10) -> list[tuple[float, float]]:
    """Locate the loudest audio windows — used to eyeball dialogue placement.

    This is a coarse envelope, not a transcript: it answers "does the sound
    land where the shot list says the lines are?" without a speech model.
    """
    import wave

    with tempfile.TemporaryDirectory(prefix="sync-") as tmp:
        wav_path = Path(tmp) / "probe.wav"
        result = run_project_subprocess(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(path),
                "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path),
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not wav_path.exists():
            return []
        with wave.open(str(wav_path), "rb") as handle:
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        samples = memoryview(raw).cast("h") if raw else []
        step = max(1, int(rate * window))
        levels: list[tuple[float, float]] = []
        for start in range(0, len(samples) - step, step):
            chunk = samples[start:start + step]
            total = sum(int(s) * int(s) for s in chunk)
            levels.append((start / rate, math.sqrt(total / step)))
    levels.sort(key=lambda item: item[1], reverse=True)
    picked = sorted(levels[:top], key=lambda item: item[0])
    return [(round(t, 2), round(t + window, 2)) for t, _ in picked]


# --------------------------------------------------------------------------- #
# stage 5 — QC
# --------------------------------------------------------------------------- #

def normalise_loudness(
    source: Path, *, stamp: str, target_lufs: float
) -> tuple[Path, dict[str, Any]]:
    """Two-pass loudnorm.

    A single pass has to guess at the signal and lands a few LU off target; the
    first pass measures, the second applies those measurements linearly.  The
    target matters because delivery specs are gates, not preferences.
    """
    loudness = _number(target_lufs, "target LUFS", low=-60.0, high=0.0)
    base = f"loudnorm=I={loudness:.1f}:TP={TARGET_TP:.1f}:LRA={TARGET_LRA:.1f}"

    measured = _measure_pass(source, base)
    graph = base
    if measured:
        graph = (
            f"{base}:measured_I={measured.get('input_i', loudness)}"
            f":measured_TP={measured.get('input_tp', TARGET_TP)}"
            f":measured_LRA={measured.get('input_lra', TARGET_LRA)}"
            f":measured_thresh={measured.get('input_thresh', -30.0)}"
            f":offset={measured.get('target_offset', 0.0)}:linear=true"
        )
    script = _write_filter_script(graph, stem="loudnorm")

    out = ARTIFACT_DIR / f"{stamp}-film.mp4"
    args = [
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-filter_script:a", str(script),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    try:
        _run_ffmpeg(args)
    finally:
        script.unlink(missing_ok=True)

    after = measure_loudness(out)
    _layer(
        "QC",
        f"loudness target {loudness:.1f} LUFS -> measured {after.get('input_i')} LUFS "
        f"(after {measured.get('input_i')} LUFS)",
    )
    return out, after


def _measure_pass(source: Path, filter_text: str) -> dict[str, float]:
    """First loudnorm pass: print measurements as JSON instead of encoding."""
    result = run_project_subprocess(
        [
            "ffmpeg", "-v", "info", "-i", str(source),
            "-af", f"{filter_text}:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    text = result.stderr or ""
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            out[key] = float(payload[key])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def measure_loudness(path: Path) -> dict[str, Any]:
    result = run_project_subprocess(
        ["ffmpeg", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    text = result.stderr or ""
    measured: dict[str, Any] = {}
    for key, pattern in (
        ("input_i", r"I" + r":\s*(-?[\d.]+)\s*LUFS"),
        ("input_tp", r"Peak:\s*(-?[\d.]+)\s*dBFS"),
        ("input_lra", r"LRA:\s*(-?[\d.]+)\s*LU"),
    ):
        match = re.search(pattern, text)
        if match:
            measured[key] = float(match.group(1))
    return measured


def match_clip_levels(
    clips: list[dict[str, Any]], *, stamp: str, target: float = LEVEL_TARGET_LUFS
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Level every clip to a common loudness before the seams are joined.

    The programme-level ``loudnorm`` in stage 5 normalises the film as a whole,
    so a clip the upstream model happened to generate 16 dB quiet remains 16 dB
    quieter than its neighbours — the finished film just drops out for that
    shot.  Measuring each clip and applying a clamped gain turns that into a
    number, and the QC gate can then fail on the spread instead of passing it.

    A clip is only rewritten when the correction is worth an encode; otherwise
    the original file is kept so no generation-adjacent quality is touched.
    """
    ordered = sorted(clips, key=lambda item: item["index"])
    rows: list[dict[str, Any]] = []
    levelled: list[dict[str, Any]] = []

    for clip in ordered:
        source = Path(clip["path"])
        measured = measure_loudness(source)
        current = measured.get("input_i")
        row: dict[str, Any] = {
            "index": clip["index"],
            "source": source.name,
            "measuredLufs": round(current, 2) if isinstance(current, float) else None,
        }

        gain = None
        if isinstance(current, float):
            gain = round(target - current, 2)
            gain = max(-LEVEL_MAX_GAIN_DB, min(LEVEL_MAX_GAIN_DB, gain))

        updated = dict(clip)
        if gain is not None and abs(gain) >= 0.3:
            target_path = ARTIFACT_DIR / f"{stamp}-{clip['index']:02d}-lvl-clip.mp4"
            filter_text = f"volume={gain:.2f}dB,alimiter=limit=0.95"
            script = _write_filter_script(filter_text, stem=f"level{clip['index']:02d}")
            _run_ffmpeg(
                [
                    "ffmpeg", "-v", "error", "-y", "-i", str(source),
                    "-filter_script:a", str(script),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    str(target_path),
                ]
            )
            after = measure_loudness(target_path)
            row["appliedGainDb"] = gain
            row["clamped"] = abs(gain) >= LEVEL_MAX_GAIN_DB - 0.01
            row["levelledLufs"] = round(after.get("input_i", 0.0), 2)
            updated["path"] = str(target_path)
        else:
            row["appliedGainDb"] = 0.0
            row["levelledLufs"] = row["measuredLufs"]

        levelled.append(updated)
        rows.append(row)

    finals = [r["levelledLufs"] for r in rows if isinstance(r["levelledLufs"], float)]
    spread = round(max(finals) - min(finals), 2) if len(finals) > 1 else 0.0
    report = {
        "targetLufs": target,
        "maxGainDb": LEVEL_MAX_GAIN_DB,
        "spreadToleranceDb": LEVEL_SPREAD_TOLERANCE_DB,
        "spreadDb": spread,
        "withinTolerance": spread <= LEVEL_SPREAD_TOLERANCE_DB,
        "perClip": rows,
    }
    return levelled, report


def consistency_report(clips: list[dict[str, Any]], assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Record what the gate can prove mechanically — and what it cannot."""
    dialogue_lines = [c["dialogue"] for c in clips if c.get("dialogue")]
    return {
        "clipCount": len(clips),
        "allMp4": all(_is_mp4(Path(c["path"])) for c in clips),
        "lockedAssets": [a["label"] for a in assets],
        "referencesPerClip": len(assets),
        "assetLockUsed": bool(assets),
        "dialogueLineCount": len(dialogue_lines),
        "dialogueLines": dialogue_lines,
        "seamMode": "crossfade",
        "crossfadeSeconds": CROSSFADE_SECONDS,
        "hiddenCutsAuthored": sum(1 for c in clips if c.get("hiddenCut")),
        "unverified": (
            "参考图把角色与场景带进了每一镜，但“脸和服装还是不是同一个人”"
            "仍须人工过目 —— 本报告不替代肉眼。"
        ),
    }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_paid_generation:
        raise RuntimeError("paid generation is disabled; pass --allow-paid-generation")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    args.stamp = _stamp()

    # A reshoot must overwrite clips inside the batch the other shots live in,
    # otherwise the join would glob one stamp and miss half the film.
    args.clip_stamp = args.stamp
    if args.reshoot:
        args.clip_stamp = args.clips_stamp or _newest_clip_stamp()
        if not args.clip_stamp:
            raise RuntimeError("--reshoot needs an existing clip batch; none found")
        args.reuse_frames = True
        _log(f"reshoot {args.reshoot} into clip batch {args.clip_stamp}")
        if args.reshoot_frames:
            _log(f"also re-rendering frames {args.reshoot_frames}")

    plan = json.loads(Path(args.shots_file).read_text(encoding="utf-8"))
    args.scene_line = plan["sceneLine"]
    args.lighting_line = plan["lighting"]
    args.color_line = plan["color"]
    args.style_line = plan["style"]
    args.title_slug = str(plan.get("title") or "film")
    shots = plan["shots"]

    # Reuse is the default and the default has to be visible: a paid run that
    # silently re-renders assets and frames which are already approved is the
    # most expensive mistake this driver makes, and it is invisible until the
    # receipt is read.  The check is deliberately loud about what it will NOT
    # re-buy, and ``--no-reuse`` is the explicit way to say "render it again".
    if args.no_reuse:
        _layer("REUSE", "--no-reuse: every paid stage will render again")
    else:
        args.reuse_frames = True
        reusable_assets = [a["label"] for a in plan.get("assets", []) if _existing_asset(str(a["label"]))]
        reusable_shots = [int(s["index"]) for s in shots if _existing_frame(int(s["index"]), args.title_slug)]
        reusable_clips = [int(s["index"]) for s in shots if _existing_clip(int(s["index"]), args.title_slug)]
        if reusable_assets:
            _layer("REUSE", f"assets on disk, not re-billed: {', '.join(reusable_assets)}")
        if reusable_shots:
            _layer("REUSE", f"frames on disk, not re-billed: {reusable_shots}")
        if reusable_clips:
            _layer("REUSE", f"clips on disk, not re-billed: {reusable_clips}")

    client = RuntimeClient(args.base_url, args.timeout_seconds)
    receipt: dict[str, Any] = {
        "ok": False,
        "startedAt": args.stamp,
        "title": plan.get("title", ""),
        "videoModel": args.video_model,
        "duration": args.duration,
        "aspectRatio": args.aspect_ratio,
        "resolution": args.resolution,
        "assets": [],
        "clips": [],
        "gates": [],
    }
    project_id = ""
    try:
        _log(f"health {client.get('/healthz')}")
        project = _data(
            client.post("/api/v1/projects", {"name": f"zz_film_studio_{args.stamp.lower()}"})
        )
        project_id = str(project.get("project_id") or project.get("id") or "")
        if not project_id:
            raise RuntimeError(f"project creation returned no id: {project}")
        canvas_id = f"film_studio_{uuid.uuid4().hex[:10]}"
        receipt["projectId"] = project_id
        _log(f"project {project_id}")

        # ---- stage 1: asset lock --------------------------------------- #
        _layer("GATE", "1/5 asset lock — nothing downstream renders before this closes")
        assets: list[dict[str, Any]] = []
        for asset in plan["assets"]:
            assets.append(
                generate_asset(client, project_id, canvas_id=canvas_id, asset=asset, args=args)
            )
        receipt["assets"] = assets
        asset_urls = [a["url"] for a in assets]
        receipt["gates"].append(
            {"gate": "asset-lock", "passed": len(assets) == len(plan["assets"]), "count": len(assets)}
        )
        _layer("GATE", f"1/5 passed — {len(assets)} assets locked")

        if args.assets_only:
            receipt["ok"] = True
            receipt["stoppedAt"] = "assets-only"
            return receipt

        # ---- stage 2: referenced storyboards --------------------------- #
        _layer("GATE", "2/5 storyboards — every frame carries the locked references")
        frames: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=args.image_concurrency) as pool:
            futures = [
                pool.submit(
                    render_storyboard,
                    client, project_id,
                    canvas_id=canvas_id, shot=shot,
                    asset_urls=asset_urls, assets=assets, args=args,
                )
                for shot in shots
            ]
            for future in as_completed(futures):
                item = future.result()
                frames[item["index"]] = item
        receipt["gates"].append(
            {"gate": "storyboards", "passed": len(frames) == len(shots), "count": len(frames)}
        )
        _layer("GATE", f"2/5 passed — {len(frames)}/{len(shots)} frames")

        if args.frames_only:
            receipt["ok"] = True
            receipt["stoppedAt"] = "frames-only"
            receipt["frames"] = [
                {"index": item["index"], "path": item["path"]}
                for item in sorted(frames.values(), key=lambda x: x["index"])
            ]
            return receipt

        # ---- stage 3: referenced clips --------------------------------- #
        _layer("GATE", "3/5 clips — allReference carries characters + set into every shot")
        ladder = ResolutionLadder(
            [item for item in args.resolution_ladder.split(",") if item.strip()],
            args.resolution,
        )
        _log(f"resolution ladder: {' > '.join(ladder.tried)} (first accepted wins)")
        clips: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=args.video_concurrency) as pool:
            futures = [
                pool.submit(
                    render_clip,
                    client, project_id,
                    canvas_id=canvas_id,
                    storyboard=frames[int(shot["index"])],
                    assets=assets, args=args, ladder=ladder,
                )
                for shot in shots
            ]
            for future in as_completed(futures):
                clips.append(future.result())
        receipt["clips"] = clips
        receipt["resolutionUsed"] = ladder.current
        receipt["resolutionTried"] = ladder.tried
        receipt["gates"].append(
            {"gate": "clips", "passed": len(clips) == len(shots), "count": len(clips)}
        )
        _layer("GATE", f"3/5 passed — {len(clips)}/{len(shots)} clips at {ladder.current}")

        # ---- stage 4: seams -------------------------------------------- #
        _layer("GATE", "4/5 seams — level-match each shot, then crossfade from probed durations")
        clips, level_report = match_clip_levels(clips, stamp=args.stamp)
        receipt["levelMatch"] = level_report
        joined, seams = join_clips(clips, stamp=args.stamp, fade=args.crossfade)
        receipt["gates"].append(
            {
                "gate": "seams",
                "passed": bool(level_report["withinTolerance"]),
                "mode": "crossfade",
                "levelSpreadDb": level_report["spreadDb"],
            }
        )

        # ---- stage 5: QC ----------------------------------------------- #
        _layer("GATE", "5/5 QC — loudness + measured clip placement")
        final, measured = normalise_loudness(joined, stamp=args.stamp, target_lufs=args.target_lufs)
        report = consistency_report(clips, assets)
        report["loudness"] = measured
        report["seams"] = seams
        report["sync"] = verify_sync(final)
        report["placement"] = measure_clip_alignment(final, clips, fade=args.crossfade)
        report["loudestWindows"] = speech_windows(final)
        report["speechPresence"] = speech_report(clips)
        receipt["qc"] = report
        receipt["filmPath"] = str(final)
        receipt["filmBytes"] = final.stat().st_size
        measured_lufs = measured.get("input_i")
        receipt["gates"].append(
            {
                "gate": "loudness",
                "passed": isinstance(measured_lufs, float)
                and abs(measured_lufs - args.target_lufs) <= 1.0,
                "target": args.target_lufs,
                "measured": measured_lufs,
            }
        )
        receipt["gates"].append(
            {
                "gate": "placement",
                "passed": bool(report["placement"].get("withinTolerance")),
                "worstErrorSeconds": report["placement"].get("worstErrorSeconds"),
            }
        )
        receipt["gates"].append(
            {
                "gate": "speech",
                "passed": bool(report["speechPresence"].get("passed")),
                "failed": report["speechPresence"].get("failed"),
                "unmeasured": report["speechPresence"].get("unmeasured"),
                "method": report["speechPresence"].get("method"),
            }
        )
        receipt["ok"] = True
        receipt["deliveredTo"] = str(
            deliver(
                final,
                plan=plan,
                receipt=receipt,
                assets=assets,
                output_dir=Path(args.output_dir),
                stamp=args.stamp,
            )
        )
        _layer(
            "GATE",
            f"5/5 passed — level spread {level_report['spreadDb']}dB, "
            f"placement error {report['placement'].get('worstErrorSeconds')}s, film at {final}",
        )
        return receipt
    finally:
        if project_id and not args.keep_project:
            try:
                client.client.delete(f"/api/v1/projects/{project_id}")
                _log(f"purged temp project {project_id}")
            except Exception:  # noqa: BLE001
                _log(f"could not purge project {project_id}")


def join_only(args: argparse.Namespace) -> dict[str, Any]:
    """Rebuild the film from clips already on disk: no upstream calls, no cost.

    This is the repair path after a seam or loudness defect — the expensive
    generation work is already done, so a bad join must never mean a re-render.
    """
    if args.clips_stamp:
        stamp = args.clips_stamp
    else:
        stamp = _newest_clip_stamp()
        if not stamp:
            raise RuntimeError(f"no clips found in {ARTIFACT_DIR}")
    paths = sorted(ARTIFACT_DIR.glob(f"{stamp}-*-clip.mp4"))
    if not paths:
        raise RuntimeError(f"no clips for stamp {stamp}")

    plan = json.loads(Path(args.shots_file).read_text(encoding="utf-8"))
    args.title_slug = str(plan.get("title") or "film")
    by_index = {int(shot["index"]): shot for shot in plan["shots"]}
    clips: list[dict[str, Any]] = []
    for path in paths:
        index = int(path.name.split("-")[1])
        shot = by_index.get(index, {})
        clips.append(
            {
                "index": index,
                "path": str(path),
                "dialogue": shot_dialogue(shot)[1],
                "requestedDuration": args.duration,
                "chain": str(shot.get("chain") or "none"),
                "hiddenCut": bool(shot.get("hiddenCut")),
            }
        )

    _layer("GATE", f"rebuild from {len(clips)} clips (stamp {stamp}) — no upstream calls")
    out_stamp = _stamp()
    clips, level_report = match_clip_levels(clips, stamp=out_stamp)
    joined, seams = join_clips(clips, stamp=out_stamp, fade=args.crossfade)
    final, measured = normalise_loudness(joined, stamp=out_stamp, target_lufs=args.target_lufs)
    sync = verify_sync(final)
    alignment = measure_clip_alignment(final, clips, fade=args.crossfade)
    receipt = {
        "ok": bool(
            sync.get("withinTolerance")
            and alignment.get("withinTolerance")
            and level_report["withinTolerance"]
        ),
        "mode": "join-only",
        "sourceStamp": stamp,
        "filmPath": str(final),
        "filmBytes": final.stat().st_size,
        "qc": {
            "seams": seams,
            "loudness": measured,
            "levelMatch": level_report,
            "sync": sync,
            "alignment": alignment,
            "loudestWindows": speech_windows(final),
            "dialogueLines": [c["dialogue"] for c in clips if c["dialogue"]],
            "sourceClips": [Path(c["path"]).name for c in clips],
        },
    }
    receipt["deliveredTo"] = str(
        deliver(
            final,
            plan=plan,
            receipt=receipt,
            assets=[],
            output_dir=Path(args.output_dir),
            stamp=out_stamp,
        )
    )
    return receipt


def deliver(
    final: Path,
    *,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    assets: list[dict[str, Any]],
    output_dir: Path,
    stamp: str,
) -> Path:
    """Copy the finished film and its paperwork to the output drive.

    The film, the shot list it came from, the locked asset sheets and the QC
    receipt travel together — a deliverable that cannot be traced back to its
    inputs is not a deliverable.
    """
    title = str(plan.get("title") or "film").strip() or "film"
    target_dir = output_dir / f"{stamp}-{title}"
    target_dir.mkdir(parents=True, exist_ok=True)

    film_target = target_dir / f"{title}.mp4"
    shutil.copy2(final, film_target)
    for asset in assets:
        source = Path(asset["path"])
        if source.exists():
            shutil.copy2(source, target_dir / f"资产-{asset['label']}{source.suffix}")

    (target_dir / "分镜脚本.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target_dir / "质检回执.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _layer("DELIVER", f"导出到 {film_target}")
    return film_target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--shots-file", required=True, help="hand-authored shot list (JSON)")
    parser.add_argument("--video-model", default=DEFAULT_VIDEO_BACKEND)
    parser.add_argument("--scene-model", default=DEFAULT_SCENE_MODEL)
    parser.add_argument("--frame-model", default=DEFAULT_FRAME_MODEL)
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--resolution", default="768p")
    parser.add_argument(
        "--resolution-ladder", default="1080p,768p",
        help="resolutions to try in order; a channel may refuse the highest",
    )
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR),
        help="where finished films are delivered (defaults to the E: drive)",
    )
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--image-quality", default="high")
    parser.add_argument("--asset-image-size", default="1K")
    parser.add_argument("--asset-image-quality", default="high")
    parser.add_argument("--crossfade", type=float, default=CROSSFADE_SECONDS)
    parser.add_argument("--target-lufs", type=float, default=TARGET_LUFS)
    parser.add_argument("--image-timeout", type=float, default=900.0)
    parser.add_argument("--video-timeout", type=float, default=2400.0)
    parser.add_argument("--image-concurrency", type=int, default=6)
    parser.add_argument("--video-concurrency", type=int, default=6)
    parser.add_argument("--assets-only", action="store_true", help="stop after the asset gate")
    parser.add_argument(
        "--frames-only", action="store_true",
        help="render storyboards and stop — inspect the pictures before paying for video",
    )
    parser.add_argument(
        "--join-only", action="store_true",
        help="rebuild the film from clips already on disk (no upstream calls, no cost)",
    )
    parser.add_argument(
        "--clips-stamp", default="",
        help="with --join-only, which clip batch to rebuild from (default: newest)",
    )
    parser.add_argument(
        "--no-reuse", action="store_true",
        help=(
            "render every paid stage again even when the artifact is already on "
            "disk; reuse is the default because re-buying approved assets and "
            "frames is the most expensive mistake this driver makes"
        ),
    )
    parser.add_argument(
        "--reshoot", default="",
        help=(
            "comma-separated shot indices to re-render, e.g. 6,7,8; every other "
            "shot is reused, so a repair costs only the shots named"
        ),
    )
    parser.add_argument(
        "--reshoot-frames", default="",
        help=(
            "comma-separated shot indices whose storyboard must be re-rendered "
            "too, e.g. 7,8; use when the defect is in the frame (wardrobe, colour)"
        ),
    )
    parser.add_argument(
        "--attempts", type=int, default=3,
        help="attempts per shot before giving up (transient upstream drops retry)",
    )
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--allow-paid-generation", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    receipt = join_only(args) if args.join_only else run(args)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
