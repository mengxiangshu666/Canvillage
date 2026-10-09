"""T-112 native-chain evidence archiving.

T-142 retired the Hermes acceptance harness, so the browser step of T-112 is the
native ``t112_real_execution_adapter.cjs`` chain. These tests cover the step that
was retired with it: freezing the evidence the native chain writes into the
hash-pinned manifest that ``chain_doctor`` reads.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "scripts" / "acceptance"
ADAPTER_SCRIPT = ACCEPTANCE / "t112_real_execution_adapter.py"
CHAIN_DOCTOR_SCRIPT = ACCEPTANCE / "chain_doctor.py"
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"
RETIRED_HARNESS_STEMS = (
    "t100_canvas_script_reuse_ui",
    "t097_media_authorization_ui",
)
VIDEO_WIDTH = 128
VIDEO_HEIGHT = 72
SHOT_COLORS = ("#e53935", "#1e88e5", "#43a047")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load_module("t112_adapter_for_archive_test", ADAPTER_SCRIPT)
chain_doctor = _load_module("chain_doctor_for_archive_test", CHAIN_DOCTOR_SCRIPT)


def _source_image(path: Path, color: str) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), color=color).save(path)


def _write_screenshot(path: Path) -> Path:
    """Write a real PNG. The browser capture itself is out of scope here."""

    _source_image(path, "#37474f")
    return path


def _render_shot_video(ffmpeg: Path, source_image: Path, target: Path, seconds: int) -> None:
    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(source_image),
            "-t",
            str(seconds),
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


def _concat_videos(ffmpeg: Path, videos: list[Path], target: Path) -> None:
    list_path = target.with_name("concat-list.txt")
    list_path.write_text(
        "".join(f"file '{video.as_posix()}'\n" for video in videos),
        encoding="utf-8",
    )
    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _build_shot_media(
    root: Path,
    *,
    shots: int,
    duration_seconds: int,
    real: bool,
) -> tuple[list[dict[str, Path]], Path]:
    records: list[dict[str, Path]] = []
    for index in range(1, shots + 1):
        source_path = root / f"source-{index}.png"
        video_path = root / f"shot-{index}.mp4"
        _source_image(source_path, SHOT_COLORS[(index - 1) % len(SHOT_COLORS)])
        if real:
            _render_shot_video(FFMPEG, source_path, video_path, duration_seconds)
        else:
            video_path.write_bytes(
                b"\x00\x00\x00\x18ftypmp42" + f"shot-{index}".encode("ascii")
            )
        records.append({"source_path": source_path, "video_path": video_path})
    final_video_path = root / "ep001_final.mp4"
    if real:
        _concat_videos(FFMPEG, [record["video_path"] for record in records], final_video_path)
    else:
        final_video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42final")
    return records, final_video_path


def _delivery_qc(
    final_video_path: Path,
    *,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> dict[str, object]:
    checks = {
        name: {
            "name": name,
            "status": "passed",
            "passed": True,
            "evidence": (
                {"width": width, "height": height}
                if name == "dimensions"
                else adapter._sha256_file(final_video_path)
                if name == "sha256"
                else {"verified": True}
            ),
        }
        for name in adapter.DELIVERY_QC_REQUIRED_CHECKS
    }
    return {
        "schema": "delivery_qc_contract.v1",
        "target": {"allowed_containers": ["mp4"]},
        "checks": checks,
        "failed_checks": [],
        "not_run_checks": [],
        "passed": True,
        "gate_observations": {"final_delivery_qc_passed": True},
    }


def _write_full_chain_evidence(
    root: Path,
    *,
    records: list[dict[str, Path]],
    final_video_path: Path,
    shots: int,
    duration_seconds: int,
    run_status: str = "completed",
    with_artifact: bool = True,
    with_order_checks: bool = True,
    final_frame_dir: Path | None = None,
) -> Path:
    """Write evidence shaped exactly like the native ``.cjs`` chain writes it."""

    first_frame_checks = [
        {
            "shot_id": f"shot:{index}",
            "ssim": 0.99,
            "threshold": 0.72,
            "status": "passed",
            "match": True,
            "source_image_path": str(record["source_path"]),
            "source_image_sha256": adapter._sha256_file(record["source_path"]),
            "video_path": str(record["video_path"]),
            "video_sha256": adapter._sha256_file(record["video_path"]),
            "video_width": VIDEO_WIDTH,
            "video_height": VIDEO_HEIGHT,
        }
        for index, record in enumerate(records, 1)
    ]
    final_film: dict[str, object] = {"status": "completed"}
    if with_artifact:
        final_film["final_compose_artifact"] = {
            "path": str(final_video_path),
            "sha256": adapter._sha256_file(final_video_path),
            "width": VIDEO_WIDTH,
            "height": VIDEO_HEIGHT,
            "duration_seconds": float(shots * duration_seconds),
        }
        final_film["delivery_qc"] = _delivery_qc(final_video_path)
    if with_order_checks:
        frame_root = final_frame_dir if final_frame_dir is not None else root
        order_checks = []
        for index, record in enumerate(records, 1):
            frame_path = frame_root / "final-frames" / f"shot-{index:02d}.png"
            _source_image(frame_path, SHOT_COLORS[(index - 1) % len(SHOT_COLORS)])
            order_checks.append(
                {
                    "shot_id": f"shot:{index}",
                    "source_image_path": str(record["source_path"]),
                    "source_image_sha256": adapter._sha256_file(
                        record["source_path"]
                    ),
                    "final_frame_path": str(frame_path),
                    "final_frame_sha256": adapter._sha256_file(frame_path),
                    "sample_seconds": float(duration_seconds * (index - 0.5)),
                    "similarity": 0.97,
                    "threshold": 0.72,
                    "status": "passed",
                    "match": True,
                }
            )
        final_film["final_order_verified"] = True
        final_film["final_order_checks"] = order_checks
    evidence = {
        "schema": "t112_real_execution_adapter_full_chain.v1",
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "run_after": {"id": "wfr_t112_native", "status": run_status},
        "mode_evidence": {
            "storyboard_images": {
                "status": "completed",
                "shot_count": shots,
                "completed_count": shots,
            },
            "shot_videos": {
                "status": "completed",
                "shot_count": shots,
                "completed_count": shots,
                "first_frame_checks": first_frame_checks,
            },
            "final_film": final_film,
            "requested_shot_count": shots,
            "requested_video_duration_seconds": duration_seconds,
            "expected_total_duration_seconds": shots * duration_seconds,
        },
    }
    evidence_path = root / "t112-full-chain-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return evidence_path


def _archive_with_cli(
    *,
    evidence_path: Path,
    screenshot_path: Path,
    shots: int,
    duration_seconds: int,
) -> None:
    exit_code = adapter.main(
        [
            "--archive-only",
            "--evidence",
            str(evidence_path),
            "--screenshot",
            str(screenshot_path),
            "--mode",
            "full-chain",
            "--shots",
            str(shots),
            "--duration",
            str(duration_seconds),
        ]
    )
    assert exit_code == 0


def _archive_and_load_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    shots: int = 2,
    duration_seconds: int = 2,
    real: bool = False,
    run_status: str = "completed",
    with_artifact: bool = True,
    with_order_checks: bool = True,
    verify_side_effects: bool = False,
) -> tuple[Path, dict[str, object]]:
    records, final_video_path = _build_shot_media(
        tmp_path,
        shots=shots,
        duration_seconds=duration_seconds,
        real=real,
    )
    evidence_path = _write_full_chain_evidence(
        tmp_path,
        records=records,
        final_video_path=final_video_path,
        shots=shots,
        duration_seconds=duration_seconds,
        run_status=run_status,
        with_artifact=with_artifact,
        with_order_checks=with_order_checks,
    )
    screenshot_path = _write_screenshot(tmp_path / "t112-full-chain-complete.png")
    archive_root = tmp_path / "archives"
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", archive_root)
    monkeypatch.setattr(
        adapter,
        "TARGET_UI_SMOKE",
        tmp_path / "ui-smoke-t112",
    )
    _archive_with_cli(
        evidence_path=evidence_path,
        screenshot_path=screenshot_path,
        shots=shots,
        duration_seconds=duration_seconds,
    )
    return evidence_path, screenshot_path


def test_import_does_not_need_the_retired_hermes_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for stem in RETIRED_HARNESS_STEMS:
        assert not (ACCEPTANCE / f"{stem}.py").is_file()
    requested: list[str] = []
    real_spec_from_file_location = importlib.util.spec_from_file_location

    def spy(name: str, location: object, *args: object, **kwargs: object):
        requested.append(str(location))
        return real_spec_from_file_location(name, location, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", spy)
    module = _load_module("t112_adapter_import_probe", ADAPTER_SCRIPT)

    assert requested == [str(ADAPTER_SCRIPT)]
    assert not any(stem in item for item in requested for stem in RETIRED_HARNESS_STEMS)
    assert "T100" not in vars(module)
    assert "T097" not in vars(module)
    assert module.PREFLIGHT_SCHEMA == "t112_real_execution_adapter_preflight.v1"
    with pytest.raises(RuntimeError, match="retired by T-142"):
        module.T100
    with pytest.raises(RuntimeError, match="retired by T-142"):
        module.T097


def test_preflight_cli_reports_the_native_chain_without_claiming_connection() -> None:
    completed = subprocess.run(
        [sys.executable, str(ADAPTER_SCRIPT), "--preflight"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert "FileNotFoundError" not in completed.stderr
    assert "Traceback" not in completed.stderr
    report = json.loads(completed.stdout)
    assert report["schema"] == adapter.PREFLIGHT_SCHEMA
    assert report["executionAdapterImplemented"] is True
    assert report["fullChainImplemented"] is True
    assert report["executionAdapterConnected"] is False
    assert report["paidProvidersConnected"] is False
    assert report["providerCallsStarted"] is False
    assert report["ok"] is (not report["failedChecks"])
    check_names = {str(item["name"]) for item in report["checks"]}
    assert "hermes_cli" not in check_names
    assert "browser_driver" in check_names
    assert set(report["failedChecks"]) <= check_names
    assert completed.returncode == (0 if report["ok"] else 2)


def test_archive_cli_turns_native_chain_evidence_into_an_accepted_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shots, duration_seconds = 2, 2
    _archive_and_load_manifest(
        monkeypatch,
        tmp_path,
        shots=shots,
        duration_seconds=duration_seconds,
    )
    manifest = json.loads(capsys.readouterr().out)
    archive_root = tmp_path / "archives"
    archive_dir = archive_root / "full-chain" / "runs" / str(manifest["archive_key"])

    assert manifest["schema"] == adapter.STABLE_EVIDENCE_SCHEMA
    assert manifest["mode"] == "full-chain"
    assert manifest["run_id"] == "wfr_t112_native"
    assert manifest["run_status"] == "completed"
    assert manifest["shot_count"] == shots
    assert manifest["video_duration_seconds"] == duration_seconds
    assert manifest["expected_total_duration_seconds"] == shots * duration_seconds
    assert manifest["paidProvidersConnected"] is False
    assert manifest["providerCallsStarted"] is False
    assert Path(str(manifest["evidence_path"])) == (
        archive_dir / "t112-full-chain-evidence.json"
    )
    assert Path(str(manifest["screenshot_path"])) == (
        archive_dir / "t112-full-chain-complete.png"
    )
    assert manifest["evidence_sha256"] == adapter._sha256_file(
        Path(str(manifest["evidence_path"]))
    )
    assert manifest["screenshot_sha256"] == adapter._sha256_file(
        Path(str(manifest["screenshot_path"]))
    )
    assert len(str(manifest["evidence_sha256"])) == 64
    latest_path = archive_root / "full-chain" / "manifest.json"
    assert json.loads(latest_path.read_text(encoding="utf-8")) == manifest

    stable = chain_doctor._load_t112_full_chain_evidence(latest_path)

    assert stable is not None
    assert stable["run_id"] == "wfr_t112_native"
    assert stable["shot_count"] == shots
    assert stable["video_duration_seconds"] == duration_seconds
    assert stable["expected_total_duration_seconds"] == shots * duration_seconds
    assert stable["delivery_qc_passed"] is True
    assert stable["final_order_min_similarity"] >= 0.72
    assert stable["first_frame_min_ssim"] >= 0.72
    assert len(stable["final_order_checks"]) == shots
    assert len(stable["first_frame_checks"]) == shots
    assert Path(str(stable["final_video_path"])) == archive_dir / "final-film.mp4"


def test_chain_doctor_rejects_a_manifest_whose_pinned_hash_drifted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _archive_and_load_manifest(monkeypatch, tmp_path)
    manifest = json.loads(capsys.readouterr().out)
    latest_path = tmp_path / "archives" / "full-chain" / "manifest.json"

    assert chain_doctor._load_t112_full_chain_evidence(latest_path) is not None

    archived_evidence = Path(str(manifest["evidence_path"]))
    archived_evidence.write_text("{}", encoding="utf-8")
    assert chain_doctor._load_t112_full_chain_evidence(latest_path) is None
    archived_evidence.write_text(
        json.dumps({"schema": manifest["schema"]}),
        encoding="utf-8",
    )
    assert chain_doctor._load_t112_full_chain_evidence(latest_path) is None

    archived_screenshot = Path(str(manifest["screenshot_path"]))
    archived_screenshot.write_bytes(b"tampered")
    assert chain_doctor._load_t112_full_chain_evidence(latest_path) is None


def test_archive_rejects_evidence_without_a_final_compose_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records, final_video_path = _build_shot_media(
        tmp_path,
        shots=2,
        duration_seconds=2,
        real=False,
    )
    evidence_path = _write_full_chain_evidence(
        tmp_path,
        records=records,
        final_video_path=final_video_path,
        shots=2,
        duration_seconds=2,
        with_artifact=False,
    )
    screenshot_path = _write_screenshot(tmp_path / "complete.png")
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", tmp_path / "archives")

    with pytest.raises(RuntimeError, match="hash-matched final compose artifact"):
        adapter.main(
            [
                "--archive-only",
                "--evidence",
                str(evidence_path),
                "--screenshot",
                str(screenshot_path),
                "--mode",
                "full-chain",
                "--shots",
                "2",
                "--duration",
                "2",
            ]
        )


def test_archive_rejects_a_run_that_never_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records, final_video_path = _build_shot_media(
        tmp_path,
        shots=2,
        duration_seconds=2,
        real=False,
    )
    evidence_path = _write_full_chain_evidence(
        tmp_path,
        records=records,
        final_video_path=final_video_path,
        shots=2,
        duration_seconds=2,
        run_status="failed",
    )
    screenshot_path = _write_screenshot(tmp_path / "complete.png")
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", tmp_path / "archives")

    with pytest.raises(RuntimeError, match="non-completed run"):
        adapter.main(
            [
                "--archive-only",
                "--evidence",
                str(evidence_path),
                "--screenshot",
                str(screenshot_path),
                "--mode",
                "full-chain",
                "--shots",
                "2",
                "--duration",
                "2",
            ]
        )


def test_archive_rejects_a_drifted_final_compose_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records, final_video_path = _build_shot_media(
        tmp_path,
        shots=2,
        duration_seconds=2,
        real=False,
    )
    evidence_path = _write_full_chain_evidence(
        tmp_path,
        records=records,
        final_video_path=final_video_path,
        shots=2,
        duration_seconds=2,
    )
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["mode_evidence"]["final_film"]["final_compose_artifact"]["sha256"] = "f" * 64
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    screenshot_path = _write_screenshot(tmp_path / "complete.png")
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", tmp_path / "archives")

    with pytest.raises(RuntimeError, match="unverified final compose artifact"):
        adapter.main(
            [
                "--archive-only",
                "--evidence",
                str(evidence_path),
                "--screenshot",
                str(screenshot_path),
                "--mode",
                "full-chain",
                "--shots",
                "2",
                "--duration",
                "2",
            ]
        )


def test_archive_cli_requires_evidence_and_screenshot(tmp_path: Path) -> None:
    screenshot_path = _write_screenshot(tmp_path / "complete.png")

    with pytest.raises(SystemExit, match="--evidence"):
        adapter.main(["--archive-only", "--screenshot", str(screenshot_path)])


@pytest.mark.skipif(
    not FFMPEG.is_file() or not FFPROBE.is_file(),
    reason="bundled FFmpeg required",
)
def test_archive_cli_verifies_the_final_film_order_the_cjs_chain_cannot_freeze(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shots, duration_seconds = 3, 2
    records, final_video_path = _build_shot_media(
        tmp_path,
        shots=shots,
        duration_seconds=duration_seconds,
        real=True,
    )
    evidence_path = _write_full_chain_evidence(
        tmp_path,
        records=records,
        final_video_path=final_video_path,
        shots=shots,
        duration_seconds=duration_seconds,
        with_order_checks=False,
    )
    assert "final_order_checks" not in json.loads(
        evidence_path.read_text(encoding="utf-8")
    )["mode_evidence"]["final_film"]
    screenshot_path = _write_screenshot(tmp_path / "t112-full-chain-complete.png")
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", tmp_path / "archives")
    monkeypatch.setattr(adapter, "TARGET_UI_SMOKE", tmp_path / "ui-smoke-t112")

    assert (
        adapter.main(
            [
                "--archive-only",
                "--evidence",
                str(evidence_path),
                "--screenshot",
                str(screenshot_path),
                "--mode",
                "full-chain",
                "--shots",
                str(shots),
                "--duration",
                str(duration_seconds),
            ]
        )
        == 0
    )
    manifest = json.loads(capsys.readouterr().out)

    assert manifest["final_video_duration_seconds"] == float(shots * duration_seconds)
    assert manifest["final_frame_count"] == shots
    stable = chain_doctor._load_t112_full_chain_evidence(
        tmp_path / "archives" / "full-chain" / "manifest.json"
    )

    assert stable is not None
    assert stable["shot_count"] == shots
    assert stable["final_order_min_similarity"] >= 0.72
    assert len(stable["final_order_checks"]) == shots
    assert [
        check["sample_seconds"] for check in stable["final_order_checks"]
    ] == [1.0, 3.0, 5.0]
