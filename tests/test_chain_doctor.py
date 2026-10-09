from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "chain_doctor.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("chain_doctor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


chain_doctor = _load_module()


def _green(stage: str) -> dict[str, object]:
    return chain_doctor._stage(stage, "green", "verified")


def _t080_receipt(
    *,
    signature: str = "a" * 64,
    asset_count: int = 3,
    ready_count: int = 1,
    reference_counts: list[int] | None = None,
) -> dict[str, object]:
    return {
        "schema": "t083_isolated_final_film_evidence.v1",
        "asset_ledger": {
            "signature": signature,
            "asset_count": asset_count,
            "ready_count": ready_count,
            "roles": ["character"],
            "storyboard_reference_counts": (
                [1, 1] if reference_counts is None else reference_counts
            ),
        },
    }


def _delivery_qc(
    final_video_path: Path,
    *,
    width: int = 1366,
    height: int = 768,
) -> dict[str, object]:
    statuses = {
        "container_allowed": "passed",
        "video_stream_present": "passed",
        "audio_stream_present": "passed",
        "dimensions": "passed",
        "frame_rate": "passed",
        "duration": "passed",
        "black_frames": "passed",
        "freeze_frames": "failed",
        "av_sync": "passed",
        "audio_activity": "failed",
        "loudness": "not_run",
        "true_peak": "not_run",
        "subtitle_stream": "passed",
        "color_space": "not_run",
        "bitrate": "not_run",
        "file_readback": "passed",
        "sha256": "passed",
    }
    evidence: dict[str, object] = {
        "container_allowed": {"container": "mp4"},
        "video_stream_present": True,
        "audio_stream_present": True,
        "dimensions": {"width": width, "height": height},
        "frame_rate": {"fps": 24},
        "duration": {"duration_seconds": 4},
        "black_frames": {"ratio": 0.0},
        "freeze_frames": {"ratio": 1.0},
        "av_sync": {"max_offset_ms": 18},
        "audio_activity": {
            "has_audio": True,
            "measurable": False,
            "all_silent": True,
        },
        "loudness": {"integrated_lufs": None, "input_i": "-inf"},
        "true_peak": {"true_peak_db": None, "input_tp": "-inf"},
        "subtitle_stream": {"present": False, "required": False},
        "color_space": {"color_space": None},
        "bitrate": {"kbps": 73.5},
        "file_readback": True,
        "sha256": chain_doctor._sha256(final_video_path),
    }
    checks = {
        name: {
            "name": name,
            "status": status,
            "passed": (
                True
                if status == "passed"
                else False
                if status == "failed"
                else None
            ),
            "evidence": evidence[name],
        }
        for name, status in statuses.items()
    }
    failed = [name for name, status in statuses.items() if status == "failed"]
    not_run = [name for name, status in statuses.items() if status == "not_run"]
    return {
        "schema": "delivery_qc_contract.v1",
        "target": {"allowed_containers": ["mp4"]},
        "checks": checks,
        "failed_checks": failed,
        "not_run_checks": not_run,
        "passed": False if failed else None if not_run else True,
        "gate_observations": {
            "final_delivery_qc_passed": False if failed else None if not_run else True
        },
    }


def _write_t112_stable_evidence(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    run_id = "wfr_t119"
    evidence_path = tmp_path / "t112-full-chain-evidence.json"
    screenshot_path = tmp_path / "t112-full-chain-complete.png"
    final_video_path = tmp_path / "ep001_final.mp4"
    final_video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42stable-fixture")
    assets: list[dict[str, object]] = []
    for index in (1, 2):
        source_path = tmp_path / f"source-{index}.png"
        video_path = tmp_path / f"shot-{index}.mp4"
        final_frame_path = tmp_path / f"final-frame-{index}.png"
        source_path.write_bytes(f"source-{index}".encode("ascii"))
        video_path.write_bytes(f"video-{index}".encode("ascii"))
        final_frame_path.write_bytes(f"final-frame-{index}".encode("ascii"))
        assets.append(
            {
                "source_path": str(source_path),
                "source_sha256": chain_doctor._sha256(source_path),
                "video_path": str(video_path),
                "video_sha256": chain_doctor._sha256(video_path),
                "final_frame_path": str(final_frame_path),
                "final_frame_sha256": chain_doctor._sha256(final_frame_path),
            }
        )
    evidence: dict[str, object] = {
        "schema": "t112_real_execution_adapter_full_chain.v1",
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
        "run_after": {"id": run_id, "status": "completed"},
        "mode_evidence": {
            "storyboard_images": {
                "status": "completed",
                "shot_count": 2,
                "completed_count": 2,
            },
            "shot_videos": {
                "status": "completed",
                "shot_count": 2,
                "completed_count": 2,
                "first_frame_checks": [
                    {
                        "shot_id": f"shot-{index}",
                        "ssim": 0.98 if index == 1 else 0.91,
                        "threshold": 0.72,
                        "status": "passed",
                        "source_image_path": asset["source_path"],
                        "source_image_sha256": asset["source_sha256"],
                        "video_path": asset["video_path"],
                        "video_sha256": asset["video_sha256"],
                        "video_width": 1366,
                        "video_height": 768,
                        "match": True,
                    }
                    for index, asset in enumerate(assets, 1)
                ],
            },
            "final_film": {
                "status": "completed",
                "delivery_qc": _delivery_qc(final_video_path),
                "final_compose_artifact": {
                    "path": str(final_video_path),
                    "sha256": chain_doctor._sha256(final_video_path),
                    "width": 1366,
                    "height": 768,
                    "duration_seconds": 4,
                },
                "final_order_verified": True,
                "final_order_checks": [
                    {
                        "shot_id": f"shot-{index}",
                        "source_image_path": asset["source_path"],
                        "source_image_sha256": asset["source_sha256"],
                        "final_frame_path": asset["final_frame_path"],
                        "final_frame_sha256": asset["final_frame_sha256"],
                        "sample_seconds": float((index - 1) * 2 + 1),
                        "similarity": 0.97 if index == 1 else 0.95,
                        "threshold": 0.72,
                        "status": "passed",
                        "match": True,
                    }
                    for index, asset in enumerate(assets, 1)
                ],
            },
        },
    }
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    screenshot_path.write_bytes(b"stable-screenshot")
    manifest: dict[str, object] = {
        "schema": "t112_stable_evidence_manifest.v1",
        "mode": "full-chain",
        "run_id": run_id,
        "run_status": "completed",
        "shot_count": 2,
        "video_duration_seconds": 2,
        "expected_total_duration_seconds": 4,
        "evidence_path": str(evidence_path),
        "evidence_sha256": chain_doctor._sha256(evidence_path),
        "screenshot_path": str(screenshot_path),
        "screenshot_sha256": chain_doctor._sha256(screenshot_path),
        "paidProvidersConnected": False,
        "providerCallsStarted": False,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest, evidence


def _rewrite_stable_fixture(
    manifest_path: Path,
    manifest: dict[str, object],
    evidence: dict[str, object],
) -> None:
    evidence_path = Path(str(manifest["evidence_path"]))
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    manifest["evidence_sha256"] = chain_doctor._sha256(evidence_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _stub_basetemp(tmp_path: Path) -> Path:
    from PIL import Image

    basetemp = tmp_path / "pytest-tmp"
    image_dir = basetemp / "case" / "output" / "freezone" / "_outputs" / "freezone_gen"
    video_dir = (
        basetemp
        / "case"
        / "output"
        / "freezone"
        / "_outputs"
        / "freezone_video_gen"
    )
    film_dir = (
        basetemp
        / "case"
        / "output"
        / "freezone"
        / "_outputs"
        / "freezone_video_compose"
    )
    for path in (image_dir, video_dir, film_dir):
        path.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 9), color="black").save(image_dir / "storyboard.png")
    (video_dir / "shot.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    (film_dir / "film.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    return basetemp


def _t113_preflight_evidence() -> dict[str, object]:
    return {
        "schema": "t113_single_shot_paid_l3_preflight.v1",
        "environmentReady": True,
        "executionRunnerImplemented": True,
        "executionRunnerConnected": True,
        "executionRunnerReady": True,
        "providerCallsStarted": False,
        "authorizationRequired": True,
        "failedChecks": [],
        "blockingReasons": ["t113_paid_authorization_required"],
        "authorizationPhrase": (
            "批准 T-113：一个结构化回合；文本和图片按需使用；"
            "视频生成总数最多 500 次；禁止自动重试；"
            "临时项目 / 画布用完清理。同时允许启动本地 8784。"
        ),
        "plan": {
            "workflowId": "freezone-final-film",
            "textModel": "deepseek-flash",
            "imageModel": "gpt-image-2.5-sunburst",
            "videoModel": "MiniMax-H3",
        },
    }


def test_non_green_stage_requires_a_gap() -> None:
    with pytest.raises(ValueError, match="must explain their gap"):
        chain_doctor._stage("script_contract", "yellow", "stub")


def test_exit_code_distinguishes_green_yellow_and_red() -> None:
    all_green = [_green(stage) for stage in chain_doctor.STAGE_ORDER]
    assert chain_doctor.exit_code_for(all_green) == 0

    yellow = [
        chain_doctor._stage(
            "script_contract",
            "yellow",
            "stub",
            gap="not real",
        )
    ]
    assert chain_doctor.exit_code_for(yellow) == 3

    red = [
        chain_doctor._stage(
            "asset_ledger",
            "red",
            "missing",
            gap="broken",
        )
    ]
    assert chain_doctor.exit_code_for(red) == 2


def test_json_output_parser_ignores_process_logs() -> None:
    parsed = chain_doctor._parse_json_object_output(
        'INFO: GET /healthz 200 OK\n'
        '{"schema":"t112","ok":true,"nested":{"status":"failed"}}\n'
    )

    assert parsed == {
        "schema": "t112",
        "ok": True,
        "nested": {"status": "failed"},
    }


def test_real_profile_without_authorization_is_red_without_work() -> None:
    stages = chain_doctor.classify_real(
        receipt=None,
        log_path=Path("unused.log"),
        authorized=False,
    )

    assert [item["stage"] for item in stages] == list(chain_doctor.STAGE_ORDER)
    by_stage = {item["stage"]: item for item in stages}
    assert {item["status"] for item in stages} == {"red"}
    assert by_stage["asset_ledger"]["status"] == "red"
    assert by_stage["asset_ledger"]["structural"] is True
    assert by_stage["script_contract"]["structural"] is False
    assert chain_doctor.exit_code_for(stages) == 2
    assert all(item["gap"] for item in stages)


def test_real_profile_uses_t113_preflight_as_the_target_chain_claim() -> None:
    stages = chain_doctor.classify_real(
        receipt=None,
        log_path=Path("unused.log"),
        authorized=False,
        paid_l3_preflight={
            "returncode": 0,
            "logPath": "paid.log",
            "plan": _t113_preflight_evidence(),
        },
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "yellow"
    assert by_stage["asset_ledger"]["structural"] is False
    assert "T-113" in by_stage["asset_ledger"]["claim"]
    assert "target_chain=freezone-final-film" in by_stage["asset_ledger"]["evidence"]
    assert "agent=village" in by_stage["asset_ledger"]["evidence"]
    assert all(
        item["status"] == "red"
        for stage, item in by_stage.items()
        if stage != "asset_ledger"
    )
    assert next(
        (
            item["stage"]
            for item in stages
            if item["status"] == "red" and item["structural"]
        ),
        None,
    ) is None


def test_runtime_preflight_failure_is_not_a_structural_chain_break() -> None:
    paid_l3 = _t113_preflight_evidence()
    paid_l3["environmentReady"] = False
    paid_l3["failedChecks"] = ["api_health", "text_model"]
    stages = chain_doctor.classify_real(
        receipt=None,
        log_path=Path("unused.log"),
        authorized=False,
        paid_l3_preflight={
            "returncode": 2,
            "logPath": "paid.log",
            "plan": paid_l3,
        },
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "red"
    assert by_stage["asset_ledger"]["structural"] is False
    assert "structurally runnable" in by_stage["asset_ledger"]["gap"]
    assert chain_doctor._next_action(
        stages,
        {"t113PaidL3PreflightEvidence": paid_l3},
    ) == {
        "kind": "start_runtime_and_request_paid_authorization",
        "stage": "asset_ledger",
        "requiresUserAuthorization": True,
        "failedChecks": ["api_health", "text_model"],
        "authorizationPhrase": paid_l3["authorizationPhrase"],
    }


def test_target_chain_preflight_failure_remains_structural() -> None:
    stages = chain_doctor.classify_real(
        receipt=None,
        log_path=Path("unused.log"),
        authorized=False,
        paid_l3_preflight={
            "returncode": 1,
            "logPath": "paid.log",
            "plan": None,
        },
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "red"
    assert by_stage["asset_ledger"]["structural"] is True
    assert chain_doctor._next_action(stages, {}) == {
        "kind": "repair_structural_chain",
        "stage": "asset_ledger",
        "requiresUserAuthorization": False,
    }


def test_ready_paid_l3_environment_only_requests_authorization() -> None:
    paid_l3 = _t113_preflight_evidence()
    action = chain_doctor._next_action(
        [
            chain_doctor._stage(
                "asset_ledger",
                "yellow",
                "ready",
                gap="waiting for authorization",
            )
        ],
        {"t113PaidL3PreflightEvidence": paid_l3},
    )

    assert action["kind"] == "request_paid_authorization"
    assert action["requiresUserAuthorization"] is True
    assert action["authorizationPhrase"] == paid_l3["authorizationPhrase"]


def test_real_asset_ledger_requires_verified_reference_artifacts(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "character.png"
    reference.write_bytes(b"png")
    receipt = {
        "ok": True,
        "storyJob": {"jobId": "story-1"},
        "rowCount": 1,
        "assetLedger": {
            "schema": "workflow_script_asset_ledger.v1",
            "signature": "a" * 64,
            "assetCount": 1,
            "readyCount": 1,
            "roles": {"character": 1},
            "referenceImages": [
                {
                    "path": str(reference),
                    "sha256": chain_doctor._sha256(reference),
                }
            ],
            "shotReferenceCounts": [1],
        },
    }

    stages = chain_doctor.classify_real(
        receipt=receipt,
        log_path=tmp_path / "real.log",
        authorized=True,
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "yellow"
    assert by_stage["asset_ledger"]["structural"] is False

    reference.unlink()
    stages = chain_doctor.classify_real(
        receipt=receipt,
        log_path=tmp_path / "real.log",
        authorized=True,
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "red"
    assert by_stage["asset_ledger"]["structural"] is True


def test_missing_stub_artifacts_fall_back_to_red(tmp_path: Path) -> None:
    stages = chain_doctor.classify_stub(
        returncode=0,
        basetemp=tmp_path / "missing-basetemp",
        log_path=tmp_path / "pytest.log",
        t080_returncode=0,
        t080_receipt=None,
        t080_log_path=tmp_path / "t080.log",
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["script_contract"]["status"] == "yellow"
    assert by_stage["asset_ledger"]["status"] == "red"
    assert by_stage["storyboard"]["status"] == "red"
    assert by_stage["shot_video"]["status"] == "red"
    assert by_stage["final_film"]["status"] == "red"
    assert by_stage["craft"]["status"] == "yellow"


def test_removing_one_artifact_downgrades_only_that_stage(tmp_path: Path) -> None:
    from PIL import Image

    basetemp = tmp_path / "pytest-tmp"
    image_dir = basetemp / "case" / "output" / "freezone" / "_outputs" / "freezone_gen"
    video_dir = basetemp / "case" / "output" / "freezone" / "_outputs" / "freezone_video_gen"
    film_dir = basetemp / "case" / "output" / "freezone" / "_outputs" / "freezone_video_compose"
    for path in (image_dir, video_dir, film_dir):
        path.mkdir(parents=True, exist_ok=True)

    Image.new("RGB", (16, 9), color="black").save(image_dir / "storyboard.png")
    (video_dir / "shot.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    (film_dir / "film.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)

    before = {
        item["stage"]: item
        for item in chain_doctor.classify_stub(
            returncode=0,
            basetemp=basetemp,
            log_path=tmp_path / "pytest.log",
            t080_returncode=0,
            t080_receipt=_t080_receipt(),
            t080_log_path=tmp_path / "t080.log",
        )
    }
    assert before["asset_ledger"]["status"] == "yellow"
    assert before["storyboard"]["status"] == "yellow"
    assert before["shot_video"]["status"] == "yellow"
    assert before["final_film"]["status"] == "yellow"

    (film_dir / "film.mp4").unlink()
    after = {
        item["stage"]: item
        for item in chain_doctor.classify_stub(
            returncode=0,
            basetemp=basetemp,
            log_path=tmp_path / "pytest.log",
            t080_returncode=0,
            t080_receipt=_t080_receipt(),
            t080_log_path=tmp_path / "t080.log",
        )
    }
    assert after["asset_ledger"]["status"] == "yellow"
    assert after["storyboard"]["status"] == "yellow"
    assert after["shot_video"]["status"] == "yellow"
    assert after["final_film"]["status"] == "red"


def test_t112_stable_evidence_replaces_unverified_stub_shot_claim(
    tmp_path: Path,
) -> None:
    basetemp = _stub_basetemp(tmp_path)
    manifest_path, _manifest, _evidence = _write_t112_stable_evidence(
        tmp_path / "stable"
    )
    stable = chain_doctor._load_t112_full_chain_evidence(manifest_path)
    assert stable is not None

    stages = chain_doctor.classify_stub(
        returncode=0,
        basetemp=basetemp,
        log_path=tmp_path / "pytest.log",
        t080_returncode=0,
        t080_receipt=_t080_receipt(),
        t080_log_path=tmp_path / "t080.log",
        t112_full_chain_evidence=stable,
    )
    shot_video = {item["stage"]: item for item in stages}["shot_video"]
    final_film = {item["stage"]: item for item in stages}["final_film"]

    assert shot_video["status"] == "yellow"
    assert "T-112 full-chain first-frame QC passed" in shot_video["claim"]
    assert "run=wfr_t119" in shot_video["claim"]
    assert "min_ssim=0.910000" in shot_video["claim"]
    assert "verified_shots=2" in shot_video["evidence"]
    assert final_film["status"] == "yellow"
    assert "T-112 full-chain final-film order and engineering QC executed" in (
        final_film["claim"]
    )
    assert "run=wfr_t119" in final_film["claim"]
    assert "min_similarity=0.950000" in final_film["claim"]
    assert "verified_final_order_shots=2" in final_film["evidence"]
    assert "engineering_qc_passed=False" in final_film["evidence"]
    assert "engineering_qc_failed=[\"freeze_frames\", \"audio_activity\"]" in (
        final_film["evidence"]
    )


def test_t112_stable_evidence_missing_falls_back_to_unverified_shot(
    tmp_path: Path,
) -> None:
    stages = chain_doctor.classify_stub(
        returncode=0,
        basetemp=_stub_basetemp(tmp_path),
        log_path=tmp_path / "pytest.log",
        t080_returncode=0,
        t080_receipt=_t080_receipt(),
        t080_log_path=tmp_path / "t080.log",
        t112_full_chain_evidence=None,
    )
    shot_video = {item["stage"]: item for item in stages}["shot_video"]

    assert shot_video["status"] == "yellow"
    assert shot_video["gap"] == (
        "local deterministic video provider; first frame not verified"
    )


def test_t112_stable_evidence_rejects_partial_shot_sequence(
    tmp_path: Path,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(
        tmp_path / "partial"
    )
    shot_videos = evidence["mode_evidence"]["shot_videos"]
    shot_videos["completed_count"] = 1
    shot_videos["first_frame_checks"] = shot_videos["first_frame_checks"][:1]
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


def test_t112_stable_evidence_rejects_hash_or_lock_file_drift(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "manifest.json"
    assert chain_doctor._load_t112_full_chain_evidence(missing) is None

    manifest_path, manifest, _evidence = _write_t112_stable_evidence(
        tmp_path / "hash-drift"
    )
    manifest["evidence_sha256"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None

    manifest_path, manifest, _evidence = _write_t112_stable_evidence(
        tmp_path / "screenshot-drift"
    )
    screenshot_path = Path(str(manifest["screenshot_path"]))
    screenshot_path.write_bytes(b"tampered")
    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None

    manifest_path, manifest, _evidence = _write_t112_stable_evidence(
        tmp_path / "final-video-drift"
    )
    final_video_path = Path(
        str(manifest["evidence_path"])
    ).parent / "ep001_final.mp4"
    final_video_path.write_bytes(b"tampered-final-video")
    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_status", "failed"),
        ("paidProvidersConnected", True),
        ("providerCallsStarted", True),
    ],
)
def test_t112_stable_evidence_rejects_unfinished_or_paid_runs(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest_path, manifest, _evidence = _write_t112_stable_evidence(
        tmp_path / field
    )
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


@pytest.mark.parametrize("mode", ["empty", "partial", "bad-threshold", "bad-ssim"])
def test_t112_stable_evidence_requires_every_first_frame_check(
    tmp_path: Path,
    mode: str,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(
        tmp_path / mode
    )
    mode_evidence = evidence["mode_evidence"]
    assert isinstance(mode_evidence, dict)
    shot_videos = mode_evidence["shot_videos"]
    assert isinstance(shot_videos, dict)
    checks = shot_videos["first_frame_checks"]
    assert isinstance(checks, list)
    if mode == "empty":
        shot_videos["first_frame_checks"] = []
    elif mode == "partial":
        checks[0]["status"] = "failed"
    elif mode == "bad-threshold":
        checks[0]["threshold"] = 0.0
    else:
        checks[0]["ssim"] = "not-a-number"
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


def test_t112_stable_evidence_requires_distinct_shot_source_images(
    tmp_path: Path,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(
        tmp_path / "duplicate-source"
    )
    mode_evidence = evidence["mode_evidence"]
    assert isinstance(mode_evidence, dict)
    shot_videos = mode_evidence["shot_videos"]
    assert isinstance(shot_videos, dict)
    checks = shot_videos["first_frame_checks"]
    assert isinstance(checks, list)
    checks[1]["source_image_sha256"] = checks[0]["source_image_sha256"]
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


@pytest.mark.parametrize("mode", ["missing", "failed", "bad-threshold"])
def test_t112_stable_evidence_requires_final_compose_order(
    tmp_path: Path,
    mode: str,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(
        tmp_path / mode
    )
    mode_evidence = evidence["mode_evidence"]
    assert isinstance(mode_evidence, dict)
    final_film = mode_evidence["final_film"]
    assert isinstance(final_film, dict)
    checks = final_film["final_order_checks"]
    assert isinstance(checks, list)
    if mode == "missing":
        final_film.pop("final_order_checks")
    elif mode == "failed":
        checks[0]["match"] = False
    else:
        checks[0]["threshold"] = 0.0
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


@pytest.mark.parametrize("mode", ["missing", "fake-pass", "wrong-sha"])
def test_t112_stable_evidence_requires_engineering_qc_receipt(
    tmp_path: Path,
    mode: str,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(
        tmp_path / mode
    )
    mode_evidence = evidence["mode_evidence"]
    assert isinstance(mode_evidence, dict)
    final_film = mode_evidence["final_film"]
    assert isinstance(final_film, dict)
    delivery_qc = final_film["delivery_qc"]
    assert isinstance(delivery_qc, dict)
    if mode == "missing":
        final_film.pop("delivery_qc")
    elif mode == "fake-pass":
        delivery_qc["passed"] = True
    else:
        checks = delivery_qc["checks"]
        assert isinstance(checks, dict)
        checks["sha256"]["evidence"] = "f" * 64
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is None


def test_t112_stable_evidence_accepts_explicit_null_for_not_run_check(
    tmp_path: Path,
) -> None:
    manifest_path, manifest, evidence = _write_t112_stable_evidence(tmp_path)
    final_film = evidence["mode_evidence"]["final_film"]
    delivery_qc = final_film["delivery_qc"]
    delivery_qc["checks"]["color_space"]["evidence"] = None
    _rewrite_stable_fixture(manifest_path, manifest, evidence)

    assert chain_doctor._load_t112_full_chain_evidence(manifest_path) is not None


def test_asset_ledger_requires_ready_referenced_assets(tmp_path: Path) -> None:
    stages = chain_doctor.classify_stub(
        returncode=0,
        basetemp=tmp_path / "missing-basetemp",
        log_path=tmp_path / "pytest.log",
        t080_returncode=0,
        t080_receipt=_t080_receipt(reference_counts=[1, 0]),
        t080_log_path=tmp_path / "t080.log",
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "red"
    assert "per-shot references" in by_stage["asset_ledger"]["gap"]


def test_t080_failure_turns_asset_ledger_red(tmp_path: Path) -> None:
    stages = chain_doctor.classify_stub(
        returncode=0,
        basetemp=tmp_path / "missing-basetemp",
        log_path=tmp_path / "pytest.log",
        t080_returncode=1,
        t080_receipt=_t080_receipt(),
        t080_log_path=tmp_path / "t080.log",
    )
    by_stage = {item["stage"]: item for item in stages}

    assert by_stage["asset_ledger"]["status"] == "red"
    assert "T-080 workflow chain failed" in by_stage["asset_ledger"]["claim"]


@pytest.mark.parametrize(
    "schema",
    sorted(chain_doctor.T080_EVIDENCE_SCHEMAS),
)
def test_t080_receipt_parser_accepts_current_authorization_evidence(
    schema: str,
) -> None:
    receipt = _t080_receipt()
    receipt["schema"] = schema

    assert (
        chain_doctor._parse_t080_receipt(json.dumps(receipt))
        == receipt
    )


def test_report_is_machine_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain_doctor, "ARTIFACT_ROOT", tmp_path / "reports")
    stages = [
        chain_doctor._stage(
            "script_contract",
            "yellow",
            "stub",
            gap="not real",
        )
    ]
    report = chain_doctor._write_report(
        profile="stub",
        stages=stages,
        evidence={"example": True},
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["schema"] == "chain_doctor.v1"
    assert payload["profile"] == "stub"
    assert payload["exitCode"] == 3
    assert payload["stages"][0]["stage"] == "script_contract"
    assert payload["nextStructuralRed"] is None


def test_report_exposes_next_structural_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chain_doctor, "ARTIFACT_ROOT", tmp_path / "reports")
    stages = chain_doctor._real_unauthorized()
    report = chain_doctor._write_report(
        profile="real",
        stages=stages,
        evidence={"example": True},
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["exitCode"] == 2
    assert payload["nextStructuralRed"] == "asset_ledger"


def test_authorized_real_profile_stays_fail_closed_without_paid_provider_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chain_doctor, "ARTIFACT_ROOT", tmp_path / "reports")

    def fake_run_logged(
        command: list[str],
        *,
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("planned", encoding="utf-8")
        if "t113_single_shot_paid_l3.py" in command[1]:
            payload = _t113_preflight_evidence()
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(chain_doctor, "_run_logged", fake_run_logged)

    stages, evidence = chain_doctor._run_real(
        chain_doctor._parse_args(["--profile", "real", "--allow-paid-generation"])
    )

    by_stage = {item["stage"]: item for item in stages}
    assert by_stage["asset_ledger"]["status"] == "yellow"
    assert all(
        item["status"] == "red"
        for stage, item in by_stage.items()
        if stage != "asset_ledger"
    )
    assert evidence["providerCallsStarted"] is False
    assert evidence["paidGenerationAuthorized"] is True
    assert evidence["environmentReady"] is True
    assert evidence["authorizedExecutionBlocked"] is True
    assert (
        evidence["blockingReason"]
        == "t113_paid_authorization_required"
    )


def test_unpaid_real_profile_runs_native_t113_preflight_without_paid_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chain_doctor, "ARTIFACT_ROOT", tmp_path / "reports")
    commands: list[list[str]] = []

    def fake_run_logged(
        command: list[str],
        *,
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        commands.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("planned", encoding="utf-8")
        if "t113_single_shot_paid_l3.py" in command[1]:
            payload = _t113_preflight_evidence()
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(chain_doctor, "_run_logged", fake_run_logged)

    stages, evidence = chain_doctor._run_real(
        chain_doctor._parse_args(["--profile", "real"])
    )

    assert len(commands) == 1
    assert "t113_single_shot_paid_l3.py" in commands[0][1]
    assert evidence["providerCallsStarted"] is False
    assert {item["stage"]: item["status"] for item in stages}["asset_ledger"] == "yellow"
    assert (
        evidence["t113PaidL3PreflightEvidence"]["schema"]
        == "t113_single_shot_paid_l3_preflight.v1"
    )
