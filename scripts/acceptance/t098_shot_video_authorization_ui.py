from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
SOURCE_UI_SMOKE = WORKSPACE / "ui-smoke-t097"
TARGET_UI_SMOKE = WORKSPACE / "ui-smoke-t098"
SOURCE_STATE = SOURCE_UI_SMOKE / "state"
TARGET_STATE = TARGET_UI_SMOKE / "state"

PROJECT_ID = "01M2TNN3XXK2D5CRXDBKTTFM4W"
CANVAS_ID = "ui_smoke_canvas"
RUN_ID = "wfr_94698b0b6eca4085aff7bab39e5b7a88"
API_PORT = int(os.environ.get("T098_API_PORT", "8787"))
VITE_PORT = int(os.environ.get("T098_VITE_PORT", "5177"))
API_BASE = f"http://127.0.0.1:{API_PORT}/api/v1"
UI_BASE = f"http://127.0.0.1:{VITE_PORT}"
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"


def _load_t097() -> Any:
    path = ROOT / "scripts" / "acceptance" / "t097_media_authorization_ui.py"
    spec = importlib.util.spec_from_file_location("t097_acceptance_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load T-097 acceptance harness: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


T097 = _load_t097()
T097_ORIGINAL_INSTALL_RUNTIME_PATCHES = T097._install_runtime_patches
T097_ORIGINAL_BUILD_STATS_PAYLOAD = T097._build_stats_payload


def _assert_inside_workspace(path: Path) -> None:
    resolved = path.resolve()
    workspace = WORKSPACE.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise RuntimeError(f"refusing to modify path outside workspace: {resolved}")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rewrite_text_columns(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    old: str,
    new: str,
) -> None:
    for column in columns:
        connection.execute(
            f"UPDATE {table} SET {column}=REPLACE({column}, ?, ?) "
            f"WHERE {column} LIKE ?",
            (old, new, f"%{old}%"),
        )


def _rewrite_isolated_paths() -> None:
    projects_db = TARGET_STATE / "local" / "projects.db"
    connection = sqlite3.connect(projects_db)
    try:
        for column in ("output_dir", "state_dir", "runtime_dir"):
            connection.execute(
                f"UPDATE projects SET {column}=REPLACE({column}, ?, ?)",
                ("ui-smoke-t097", "ui-smoke-t098"),
            )
        connection.commit()
    finally:
        connection.close()

    workflow_db = TARGET_STATE / "local" / "ui_smoke_t091" / "workflow_runs.db"
    connection = sqlite3.connect(workflow_db)
    try:
        _rewrite_text_columns(
            connection,
            table="canvas_workflow_runs",
            columns=(
                "current_frontier_json",
                "step_states_json",
                "inputs_json",
                "artifacts_json",
                "success_criteria_json",
                "checkpoint_json",
                "project_context_json",
                "model_plan_snapshot_json",
            ),
            old="ui-smoke-t097",
            new="ui-smoke-t098",
        )
        connection.commit()
    finally:
        connection.close()


def _extend_shot_video_contract() -> None:
    """Add the motion fields required by the next durable workflow step.

    T-097 intentionally stopped at the storyboard grant and used the smallest
    executable script contract for that stage.  T-098 begins at the next real
    stage, so the isolated script must carry a real motion prompt and duration
    before the browser can authorize video generation.
    """

    workflow_db = TARGET_STATE / "local" / "ui_smoke_t091" / "workflow_runs.db"
    connection = sqlite3.connect(workflow_db)
    try:
        row = connection.execute(
            "SELECT artifacts_json FROM canvas_workflow_runs WHERE id=?",
            (RUN_ID,),
        ).fetchone()
        if row is None:
            raise RuntimeError("T-097 seed run is missing")
        artifacts = json.loads(row[0])
        script = artifacts.get("script_contract")
        if not isinstance(script, dict) or not isinstance(script.get("rows"), list):
            raise RuntimeError("T-097 seed script contract is missing")
        rows = script["rows"]
        if not rows:
            raise RuntimeError("T-097 seed script contract has no rows")
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                raise RuntimeError("T-097 seed script row is invalid")
            raw_row.setdefault("duration", 2)
            raw_row.setdefault(
                "video_motion_prompt",
                (
                    "[运镜轨迹] 镜头缓慢推进。"
                    "[主体动作] 摄影师抬起相机并稳定呼吸。"
                    "[环境动态] 红色暗房灯光轻微闪烁。"
                    "[音效氛围] 雨声与快门声。"
                    "[对话台词] 无对白。"
                    "[时长] [时长：2s]"
                ),
            )
        rows_fingerprint = _canonical_sha256(rows)
        ledger = script.get("asset_ledger")
        ledger_signature = (
            str(ledger.get("signature") or "")
            if isinstance(ledger, dict)
            else ""
        )
        if not ledger_signature:
            raise RuntimeError("T-097 seed asset ledger signature is missing")
        script["result_signature"] = _canonical_sha256(
            {
                "rows_fingerprint": rows_fingerprint,
                "asset_ledger_signature": ledger_signature,
            }
        )
        report = script.setdefault("contract_report", {})
        if isinstance(report, dict):
            report["rows_fingerprint"] = rows_fingerprint
        connection.execute(
            "UPDATE canvas_workflow_runs SET artifacts_json=? WHERE id=?",
            (
                json.dumps(artifacts, ensure_ascii=False, separators=(",", ":")),
                RUN_ID,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _reset_isolated_site() -> None:
    if not SOURCE_STATE.is_dir():
        raise RuntimeError(f"T-097 isolated state is missing: {SOURCE_STATE}")
    _assert_inside_workspace(TARGET_UI_SMOKE)
    if TARGET_UI_SMOKE.exists():
        shutil.rmtree(TARGET_UI_SMOKE)
    shutil.copytree(SOURCE_UI_SMOKE, TARGET_UI_SMOKE)
    _rewrite_isolated_paths()
    T097._reset_paid_grants()
    _extend_shot_video_contract()
    _assert_seed_state()


def _assert_seed_state() -> None:
    workflow_db = TARGET_STATE / "local" / "ui_smoke_t091" / "workflow_runs.db"
    connection = sqlite3.connect(workflow_db)
    try:
        row = connection.execute(
            "SELECT status, next_action, artifacts_json "
            "FROM canvas_workflow_runs WHERE id=?",
            (RUN_ID,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("T-097 seed run is missing")
    status, next_action, artifacts_json = row
    if "ui-smoke-t097" in artifacts_json or "ui-smoke-t096" in artifacts_json:
        raise RuntimeError("T-098 isolated run still references an old smoke root")
    artifacts = json.loads(artifacts_json)
    script = artifacts.get("script_contract") or {}
    storyboard = artifacts.get("storyboard_images") or {}
    shot_videos = artifacts.get("shot_videos") or {}
    recovery = shot_videos.get("recovery") or {}
    rows = script.get("rows")
    if (
        status != "failed"
        or next_action != "recover:request_media_authorization:shot_videos"
        or script.get("status") != "completed"
        or not isinstance(rows, list)
        or not rows
        or not all(
            isinstance(item, dict) and item.get("video_motion_prompt")
            for item in rows
        )
        or storyboard.get("status") != "completed"
        or int(storyboard.get("completed_count") or 0) != len(rows)
        or shot_videos.get("status") != "failed"
        or recovery.get("action") != "request_media_authorization"
        or shot_videos.get("media_authorization")
    ):
        raise RuntimeError("T-098 seed run is not at a clean shot-video gate")


class T098MediaProvider(T097.AcceptanceMediaProvider):
    def __init__(self, ffmpeg: Path = FFMPEG) -> None:
        super().__init__()
        self.ffmpeg = ffmpeg
        self.image_calls = 0
        self.video_calls = 0
        self.video_paths: list[str] = []

    async def __call__(self, *, output_path: str, **_kwargs: Any) -> None:
        with self._lock:
            self.image_calls += 1
        await super().__call__(output_path=output_path, **_kwargs)

    async def generate(
        self,
        *,
        image_path: str | None,
        output_path: str,
        duration: float = 5.0,
        **_kwargs: Any,
    ) -> Any:
        from novelvideo.generators.video.base import (
            VideoGenResult,
            VideoGenStatus,
        )

        if not image_path or not Path(image_path).is_file():
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="local video provider requires a real first frame",
            )
        with self._lock:
            self.calls += 1
            self.video_calls += 1
            self.video_paths.append(str(output_path))
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(self.ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                str(duration),
                "-vf",
                "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                "-r",
                "24",
                "-c:v",
                "mpeg4",
                "-q:v",
                "3",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return VideoGenResult(
            status=VideoGenStatus.DONE,
            video_path=str(target),
            duration_seconds=float(duration),
        )


def _install_runtime_patches(
    backend: Any,
    provider: T098MediaProvider,
) -> None:
    T097_ORIGINAL_INSTALL_RUNTIME_PATCHES(backend, provider)
    from novelvideo.generators import video_generator
    from novelvideo.workflow_runtime import freezone_videos

    freezone_videos.get_task_backend = lambda: backend.backend
    freezone_videos.resolve_snapshot_model_ref = (
        lambda _snapshot, role: (
            ("video", "local-deterministic")
            if role == "video"
            else ("text", "local-story-script")
        )
    )
    video_generator.create_video_generator = (
        lambda **_kwargs: provider
    )


def _build_stats_payload(
    *,
    backend: Any,
    provider: T098MediaProvider,
    agent: Any,
) -> dict[str, Any]:
    payload = T097_ORIGINAL_BUILD_STATS_PAYLOAD(
        backend=backend,
        provider=provider,
        agent=agent,
    )
    return {
        **payload,
        "schema": "t098_acceptance_stats.v1",
        "image_provider_calls": provider.image_calls,
        "video_provider_calls": provider.video_calls,
        "video_provider_paths": provider.video_paths,
    }


def _run_browser() -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not available")
    env = os.environ.copy()
    env["NODE_PATH"] = str(T097.PLAYWRIGHT_NODE_MODULES)
    env.update(
        {
            "T097_PROJECT_ID": PROJECT_ID,
            "T097_CANVAS_ID": CANVAS_ID,
            "T097_RUN_ID": RUN_ID,
            "T097_UI_BASE": UI_BASE,
            "T097_API_BASE": API_BASE,
            "T097_STATS_URL": (
                API_BASE.removesuffix("/api/v1") + "/__acceptance__/stats"
            ),
            "T097_EVIDENCE_PATH": str(
                TARGET_UI_SMOKE / "t098-browser-evidence.json"
            ),
            "T097_SCREENSHOT_PATH": str(
                TARGET_UI_SMOKE / "t098-authorized-complete.png"
            ),
            "T097_AUTHORIZATION_STAGE": "shot_videos",
        }
    )
    subprocess.run(
        [
            node,
            str(ROOT / "scripts" / "acceptance" / "t097_media_authorization_ui.cjs"),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def _install_t098_harness() -> None:
    if not FFMPEG.is_file() or not FFPROBE.is_file():
        raise RuntimeError("bundled ffmpeg/ffprobe are required for T-098")
    T097.SOURCE_UI_SMOKE = SOURCE_UI_SMOKE
    T097.TARGET_UI_SMOKE = TARGET_UI_SMOKE
    T097.SOURCE_STATE = SOURCE_STATE
    T097.TARGET_STATE = TARGET_STATE
    T097.API_PORT = API_PORT
    T097.VITE_PORT = VITE_PORT
    T097.API_BASE = API_BASE
    T097.UI_BASE = UI_BASE
    T097.AcceptanceMediaProvider = T098MediaProvider
    T097._reset_isolated_site = _reset_isolated_site
    T097._install_runtime_patches = _install_runtime_patches
    T097._build_stats_payload = _build_stats_payload
    T097._run_browser = _run_browser


def main() -> int:
    _install_t098_harness()
    os.environ["PATH"] = os.pathsep.join(
        [str(FFMPEG.parent), os.environ.get("PATH", "")]
    )
    return int(T097.main())


if __name__ == "__main__":
    raise SystemExit(main())
