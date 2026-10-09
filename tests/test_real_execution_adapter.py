from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "t112_real_execution_adapter.py"
LEGACY_T100_HARNESS = ROOT / "scripts" / "acceptance" / "t100_canvas_script_reuse_ui.py"

if not LEGACY_T100_HARNESS.is_file():
    pytest.skip(
        "T-112 Hermes acceptance harness was retired by T-142; "
        "current production coverage lives in test_t113_paid_l3_preflight.py",
        allow_module_level=True,
    )


def _load_module():
    spec = importlib.util.spec_from_file_location("t112_real_execution_adapter", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = _load_module()


def test_preflight_is_zero_side_effect_and_does_not_claim_connection() -> None:
    report = adapter.build_preflight()

    assert report["schema"] == adapter.PREFLIGHT_SCHEMA
    assert report["executionAdapterImplemented"] is True
    assert report["fullChainImplemented"] is True
    assert report["executionAdapterConnected"] is False
    assert report["paidProvidersConnected"] is False
    assert report["providerCallsStarted"] is False
    assert report["failedChecks"] == []


def test_agent_run_binding_reads_the_persisted_delivery_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_state = tmp_path / "project-state"
    project_state.mkdir()
    chat_db = project_state / "chat.db"
    connection = sqlite3.connect(chat_db)
    connection.execute(
        """
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        "INSERT INTO chat_messages (role, content, metadata_json) VALUES (?, ?, ?)",
        (
            "assistant",
            "任务已经进入后台执行，目前仍在进行中。",
            adapter.json.dumps(
                {
                    "delivery_verification": {
                        "status": "in_progress",
                        "workflow_run_id": "wfr_scoped_12345678",
                    }
                }
            ),
        ),
    )
    connection.commit()
    connection.close()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(adapter, "PROJECT_STATE", project_state)

    binding = adapter._verify_agent_run_binding(evidence_path)

    assert binding["workflow_run_id"] == "wfr_scoped_12345678"
    assert adapter.json.loads(evidence_path.read_text(encoding="utf-8"))[
        "agent_run_binding"
    ] == binding


def test_chat_tool_call_is_bounded_to_canvas_dispatch() -> None:
    body, content_type = adapter._chat_response(
        model="t112-local-agent",
        tool_call=True,
        stream=False,
    )
    payload = adapter.json.loads(body)
    tool_call = payload["choices"][0]["message"]["tool_calls"][0]

    assert content_type == "application/json"
    assert payload["choices"][0]["finish_reason"] == "tool_calls"
    assert tool_call["function"]["name"] == "village_canvas_dispatch_action"
    assert '"run_mode": "draft"' in tool_call["function"]["arguments"]
    assert '"allow_paid_media": false' in tool_call["function"]["arguments"]


def test_chat_final_response_contains_no_tool_call() -> None:
    body, _content_type = adapter._chat_response(
        model="t112-local-agent",
        tool_call=False,
        stream=False,
    )
    payload = adapter.json.loads(body)
    message = payload["choices"][0]["message"]

    assert payload["choices"][0]["finish_reason"] == "stop"
    assert "tool_calls" not in message
    assert "授权门" in message["content"]


def test_full_chain_dispatch_carries_the_bounded_authorization() -> None:
    body, _content_type = adapter._chat_response(
        model="t112-local-agent",
        tool_call=True,
        stream=False,
        run_mode="auto",
        allow_paid_media=True,
        max_paid_starts=4,
    )
    payload = adapter.json.loads(body)
    arguments = adapter.json.loads(
        payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )

    assert arguments["run_mode"] == "auto"
    assert arguments["task_authorization"]["allow_paid_media"] is True
    assert arguments["task_authorization"]["max_paid_starts"] == 4


def test_full_chain_dispatch_carries_requested_shot_sequence() -> None:
    body, _content_type = adapter._chat_response(
        model="t112-local-agent",
        tool_call=True,
        stream=False,
        run_mode="auto",
        allow_paid_media=True,
        max_paid_starts=4,
        shot_count=3,
        video_duration_seconds=2,
    )
    payload = adapter.json.loads(body)
    arguments = adapter.json.loads(
        payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )

    assert arguments["task"]["item_count"] == 3
    assert arguments["director_intent_contract"]["shot_count"] == 3
    assert arguments["inputs"]["video_duration_seconds"] == 2


def test_script_rows_are_distinct_and_match_the_requested_sequence() -> None:
    rows = adapter._script_rows_for_shots(3, 2)

    assert len(rows) == 3
    assert [row["shot_no"] for row in rows] == [1, 2, 3]
    assert {row["duration"] for row in rows} == {2}
    assert len({row["shot_prompt"] for row in rows}) == 3
    assert all("[时长：2s]" in row["video_motion_prompt"] for row in rows)


def test_shot_count_normalization_stays_inside_the_contract() -> None:
    assert adapter._normalize_shot_count(3) == 3
    assert adapter._normalize_shot_count("4") == 4
    assert adapter._normalize_shot_count(0) == 1
    assert adapter._normalize_shot_count(99) == 12
    assert adapter._normalize_shot_count(True) == 1
    assert adapter._normalize_shot_count("bad") == 1


def test_streaming_tool_call_preserves_the_same_dispatch_contract() -> None:
    body, content_type = adapter._chat_response(
        model="t112-local-agent",
        tool_call=True,
        stream=True,
    )
    text = body.decode("utf-8")

    assert content_type == "text/event-stream"
    assert "call_t112_dispatch" in text
    assert "village_canvas_dispatch_action" in text
    assert text.endswith("data: [DONE]\n\n")


def test_current_turn_tool_state_ignores_tool_results_from_earlier_turns() -> None:
    messages = [
        {"role": "user", "content": "start workflow"},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": "dispatch completed"},
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "retry the failed item"},
    ]

    assert adapter._current_turn_needs_tool_call(messages) is True
    assert adapter._current_turn_needs_tool_call(
        [*messages, {"role": "tool", "content": "retry accepted"}]
    ) is False


def test_canvas_checkpoint_uses_the_newest_recovery_block() -> None:
    checkpoint_marker = (
        "[VILLAGE_AGENT_CONTEXT_CHECKPOINT]\n"
        '{"recovery_contract":{"action":"%s"}}\n'
        "[/VILLAGE_AGENT_CONTEXT_CHECKPOINT]"
    )
    checkpoint = adapter._canvas_checkpoint(
        [
            {"role": "user", "content": checkpoint_marker % "old"},
            {"role": "user", "content": checkpoint_marker % "new"},
        ]
    )

    assert checkpoint["recovery_contract"]["action"] == "new"


def test_video_duration_normalization_honors_the_requested_seconds() -> None:
    assert adapter._normalize_video_duration(4) == 4
    assert adapter._normalize_video_duration("4") == 4
    assert adapter._normalize_video_duration(4.4) == 4
    assert adapter._normalize_video_duration(0) == 1
    assert adapter._normalize_video_duration(99) == 30
    assert adapter._normalize_video_duration(True) == 5
    assert adapter._normalize_video_duration("not-a-duration") == 5


def test_requested_video_duration_reads_the_openai_seconds_field() -> None:
    assert adapter._requested_video_duration({"seconds": "2"}) == 2
    assert adapter._requested_video_duration({"duration_seconds": 4}) == 4
    assert adapter._requested_video_duration({"metadata": {"duration": 5}}) == 5
    assert adapter._requested_video_duration({"prompt": "no duration"}) is None


def test_requested_image_contract_reads_nested_provider_fields() -> None:
    assert adapter._requested_image_contract(
        {
            "size": "1024x576",
            "extra_fields": {
                "aspect_ratio": "16:9",
                "image_size": "1K",
                "quality": "low",
            },
        }
    ) == {
        "size": "1024x576",
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "quality": "low",
    }


def test_storyboard_shot_number_comes_from_prompt_not_request_order() -> None:
    assert (
        adapter._requested_storyboard_shot_number(
            {"prompt": "第 2 个镜头：旧照相馆暗房，红灯从左侧切过面部。"},
            shot_count=3,
        )
        == 2
    )
    assert (
        adapter._requested_storyboard_shot_number(
            {"prompt": "Shot 3: wide shot of the darkroom."},
            shot_count=3,
        )
        == 3
    )
    assert (
        adapter._requested_storyboard_shot_number(
            {"prompt": "no explicit shot identity"},
            shot_count=3,
        )
        == 0
    )


def test_request_payload_reads_multipart_storyboard_prompt() -> None:
    boundary = "t112-boundary"
    raw = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="model"\r\n\r\n'
        "t112-local-image\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="prompt"\r\n\r\n'
        "第 2 个镜头：旧照相馆暗房。\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    payload = adapter._request_payload(
        raw,
        f"multipart/form-data; boundary={boundary}",
    )

    assert payload["model"] == "t112-local-image"
    assert payload["prompt"].startswith("第 2 个镜头")
    assert (
        adapter._requested_storyboard_shot_number(
            payload,
            shot_count=3,
        )
        == 2
    )


def test_requested_video_contract_preserves_native_minimax_fields() -> None:
    assert adapter._requested_video_contract(
        {
            "resolution": "768P",
            "ratio": "adaptive",
            "duration": 5,
            "content": [
                {"type": "text", "text": "prompt"},
                {"type": "image_url", "role": "first_frame"},
            ],
        }
    ) == {
        "resolution": "768P",
        "ratio": "adaptive",
        "duration": 5,
        "content_roles": ["first_frame"],
    }


def test_media_assets_are_lazy_and_rebuild_after_an_isolated_reset(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "TARGET_UI_SMOKE", tmp_path)
    server = adapter.LocalUpstreamServer(full_chain=True)

    def build_assets() -> None:
        media_dir = tmp_path / "runtime" / "t112-upstream"
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / "frame.png").write_bytes(b"local-png")
        server.image_png = b"local-png"

    monkeypatch.setattr(server, "_build_media_assets", build_assets)
    frame = tmp_path / "runtime" / "t112-upstream" / "frame.png"
    try:
        assert frame.exists() is False
        server.ensure_media_assets()
        assert frame.read_bytes() == b"local-png"
        frame.unlink()
        server.ensure_media_assets()
        assert frame.read_bytes() == b"local-png"
    finally:
        server.server_close()


def test_video_failure_injection_is_one_shot() -> None:
    server = adapter.LocalUpstreamServer(
        full_chain=True,
        video_failures_remaining=1,
    )
    try:
        assert server.consume_video_failure() is True
        assert server.consume_video_failure() is False
    finally:
        server.server_close()


def _shot_archive_asset_fixture(
    root: Path,
    *,
    marker: str,
    shot_count: int = 3,
) -> list[dict[str, object]]:
    assets: list[dict[str, object]] = []
    for index in range(1, shot_count + 1):
        source_path = root / f"{marker}-source-{index}.png"
        video_path = root / f"{marker}-video-{index}.mp4"
        final_frame_path = root / f"{marker}-final-frame-{index}.png"
        source_path.write_bytes(f"{marker}-source-{index}".encode("ascii"))
        video_path.write_bytes(
            b"\x00\x00\x00\x18ftypmp42"
            + f"{marker}-video-{index}".encode("ascii")
        )
        final_frame_path.write_bytes(f"{marker}-frame-{index}".encode("ascii"))
        assets.append(
            {
                "source_path": str(source_path),
                "source_sha256": adapter._sha256_file(source_path),
                "video_path": str(video_path),
                "video_sha256": adapter._sha256_file(video_path),
                "final_frame_path": str(final_frame_path),
                "final_frame_sha256": adapter._sha256_file(final_frame_path),
            }
        )
    return assets


def _adapter_delivery_qc(
    final_video_path: Path,
    *,
    width: int = 1366,
    height: int = 768,
) -> dict[str, object]:
    statuses = {
        name: (
            "failed"
            if name in {"freeze_frames", "audio_activity"}
            else "not_run"
            if name in {"loudness", "true_peak", "color_space", "bitrate"}
            else "passed"
        )
        for name in adapter.DELIVERY_QC_REQUIRED_CHECKS
    }
    evidence: dict[str, object] = {
        name: {"observed": status} for name, status in statuses.items()
    }
    evidence["dimensions"] = {"width": width, "height": height}
    evidence["sha256"] = adapter._sha256_file(final_video_path)
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


def test_delivery_qc_receipt_is_hash_bound_and_cannot_fake_a_pass(
    tmp_path: Path,
) -> None:
    final_video_path = tmp_path / "final.mp4"
    final_video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42qc")
    final_sha256 = adapter._sha256_file(final_video_path)
    qc = _adapter_delivery_qc(final_video_path)

    assert adapter._valid_delivery_qc_receipt(
        qc,
        final_sha256=final_sha256,
        final_width=1366,
        final_height=768,
    )

    qc["passed"] = True
    assert not adapter._valid_delivery_qc_receipt(
        qc,
        final_sha256=final_sha256,
        final_width=1366,
        final_height=768,
    )
    qc["passed"] = False
    qc["checks"]["sha256"]["evidence"] = "f" * 64
    assert not adapter._valid_delivery_qc_receipt(
        qc,
        final_sha256=final_sha256,
        final_width=1366,
        final_height=768,
    )
    qc["checks"]["sha256"]["evidence"] = final_sha256
    qc["checks"]["color_space"]["evidence"] = None
    assert adapter._valid_delivery_qc_receipt(
        qc,
        final_sha256=final_sha256,
        final_width=1366,
        final_height=768,
    )
    qc["checks"]["color_space"]["status"] = "passed"
    qc["checks"]["color_space"]["passed"] = True
    qc["failed_checks"] = [
        name
        for name in adapter.DELIVERY_QC_REQUIRED_CHECKS
        if qc["checks"][name]["status"] == "failed"
    ]
    qc["not_run_checks"] = [
        name
        for name in adapter.DELIVERY_QC_REQUIRED_CHECKS
        if qc["checks"][name]["status"] == "not_run"
    ]
    qc["passed"] = bool(qc["failed_checks"])
    assert not adapter._valid_delivery_qc_receipt(
        qc,
        final_sha256=final_sha256,
        final_width=1366,
        final_height=768,
    )


def test_stable_evidence_archives_each_run_without_overwriting_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archives"
    evidence_path = tmp_path / "evidence.json"
    screenshot_path = tmp_path / "complete.png"
    final_video_path = tmp_path / "ep001_final.mp4"
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", archive_root)
    archive_keys = iter(
        (
            "20260919T010000000000Z-wfr_first",
            "20260919T020000000000Z-wfr_second",
        )
    )
    monkeypatch.setattr(adapter, "_archive_key", lambda _run_id: next(archive_keys))

    manifests = []
    for index, run_id in enumerate(("wfr_first", "wfr_second"), 1):
        assets = _shot_archive_asset_fixture(tmp_path, marker=run_id)
        final_video_path.write_bytes(
            b"\x00\x00\x00\x18ftypmp42"
            + f"final-video-{index}".encode("ascii")
        )
        final_video_sha256 = adapter._sha256_file(final_video_path)
        evidence_path.write_text(
            adapter.json.dumps(
                {
                    "schema": adapter.STABLE_EVIDENCE_SCHEMA,
                    "run_after": {"id": run_id, "status": "completed"},
                    "paidProvidersConnected": False,
                    "providerCallsStarted": False,
                    "mode_evidence": {
                        "shot_videos": {
                            "first_frame_checks": [
                                {
                                    "shot_id": f"shot:{shot_index}",
                                    "ssim": 0.99,
                                    "threshold": 0.72,
                                    "status": "passed",
                                    "match": True,
                                    "source_image_path": asset["source_path"],
                                    "source_image_sha256": asset["source_sha256"],
                                    "video_path": asset["video_path"],
                                    "video_sha256": asset["video_sha256"],
                                    "video_width": 1366,
                                    "video_height": 768,
                                }
                                for shot_index, asset in enumerate(assets, 1)
                            ]
                        },
                        "final_film": {
                            "delivery_qc": _adapter_delivery_qc(
                                final_video_path
                            ),
                            "final_compose_artifact": {
                                "path": str(final_video_path),
                                "sha256": final_video_sha256,
                                "width": 1366,
                                "height": 768,
                                "duration_seconds": 6.06,
                            },
                            "final_order_checks": [
                                {
                                    "shot_id": f"shot:{shot_index}",
                                    "source_image_path": asset["source_path"],
                                    "source_image_sha256": asset["source_sha256"],
                                    "final_frame_path": asset["final_frame_path"],
                                    "final_frame_sha256": asset[
                                        "final_frame_sha256"
                                    ],
                                    "similarity": 0.99,
                                    "threshold": 0.72,
                                    "status": "passed",
                                    "match": True,
                                }
                                for shot_index, asset in enumerate(assets, 1)
                            ],
                        }
                    },
                    "marker": index,
                }
            ),
            encoding="utf-8",
        )
        screenshot_path.write_bytes(f"screenshot-{index}".encode("ascii"))
        manifests.append(
            adapter._archive_execution_evidence(
                mode="full-chain",
                evidence_path=evidence_path,
                screenshot_path=screenshot_path,
                shot_count=3,
                video_duration_seconds=2,
            )
        )

    first_manifest, second_manifest = manifests
    first_dir = archive_root / "full-chain" / "runs" / first_manifest["archive_key"]
    second_dir = archive_root / "full-chain" / "runs" / second_manifest["archive_key"]
    latest_manifest = adapter.json.loads(
        (archive_root / "full-chain" / "manifest.json").read_text(encoding="utf-8")
    )

    assert first_manifest["run_id"] == "wfr_first"
    assert second_manifest["run_id"] == "wfr_second"
    assert (first_dir / "complete.png").read_bytes() == b"screenshot-1"
    assert (second_dir / "complete.png").read_bytes() == b"screenshot-2"
    assert adapter.json.loads(
        (first_dir / "evidence.json").read_text(encoding="utf-8")
    )["marker"] == 1
    assert adapter.json.loads(
        (second_dir / "evidence.json").read_text(encoding="utf-8")
    )["marker"] == 2
    first_archived_evidence = adapter.json.loads(
        (first_dir / "evidence.json").read_text(encoding="utf-8")
    )
    second_archived_evidence = adapter.json.loads(
        (second_dir / "evidence.json").read_text(encoding="utf-8")
    )
    first_artifact = first_archived_evidence["mode_evidence"]["final_film"][
        "final_compose_artifact"
    ]
    second_artifact = second_archived_evidence["mode_evidence"]["final_film"][
        "final_compose_artifact"
    ]
    assert first_artifact["original_path"] == str(final_video_path)
    assert second_artifact["original_path"] == str(final_video_path)
    assert Path(first_artifact["path"]) == first_dir / "final-film.mp4"
    assert Path(second_artifact["path"]) == second_dir / "final-film.mp4"
    assert first_manifest["final_video_path"] == str(first_dir / "final-film.mp4")
    assert second_manifest["final_video_path"] == str(second_dir / "final-film.mp4")
    assert first_manifest["final_video_sha256"] != second_manifest[
        "final_video_sha256"
    ]
    assert first_manifest["source_image_count"] == 3
    assert first_manifest["shot_video_count"] == 3
    assert first_manifest["final_frame_count"] == 3
    assert first_manifest["delivery_qc_passed"] is False
    assert first_manifest["delivery_qc_failed_checks"] == [
        "freeze_frames",
        "audio_activity",
    ]
    first_check = first_archived_evidence["mode_evidence"]["shot_videos"][
        "first_frame_checks"
    ][0]
    first_order = first_archived_evidence["mode_evidence"]["final_film"][
        "final_order_checks"
    ][0]
    assert Path(first_check["source_image_path"]) == (
        first_dir / "source-images" / "shot-01.png"
    )
    assert Path(first_check["video_path"]) == (
        first_dir / "shot-videos" / "shot-01.mp4"
    )
    assert Path(first_order["source_image_path"]) == (
        first_dir / "source-images" / "shot-01.png"
    )
    assert Path(first_order["final_frame_path"]) == (
        first_dir / "final-frames" / "shot-01.png"
    )
    assert latest_manifest["run_id"] == "wfr_second"
    assert latest_manifest["archive_key"] == second_manifest["archive_key"]


def test_draft_archive_accepts_only_the_expected_paid_media_stop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archives"
    evidence_path = tmp_path / "draft.json"
    screenshot_path = tmp_path / "authorization-gate.png"
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", archive_root)
    monkeypatch.setattr(adapter, "_archive_key", lambda _run_id: "draft-key")
    screenshot_path.write_bytes(b"authorization-gate")
    evidence_path.write_text(
        adapter.json.dumps(
            {
                "run_after": {
                    "id": "wfr_draft",
                    "status": "failed",
                    "error_code": "workflow_storyboard_paid_media_not_authorized",
                },
                "paidProvidersConnected": False,
                "providerCallsStarted": False,
            }
        ),
        encoding="utf-8",
    )

    manifest = adapter._archive_execution_evidence(
        mode="draft",
        evidence_path=evidence_path,
        screenshot_path=screenshot_path,
    )
    assert manifest["run_id"] == "wfr_draft"
    assert manifest["run_status"] == "failed"

    evidence_path.write_text(
        adapter.json.dumps(
            {
                "run_after": {
                    "id": "wfr_unexpected",
                    "status": "failed",
                    "error_code": "unexpected_failure",
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="expected paid-media stop"):
        adapter._archive_execution_evidence(
            mode="draft",
            evidence_path=evidence_path,
            screenshot_path=screenshot_path,
        )


def test_stable_full_chain_archive_rejects_a_missing_or_drifted_final_video(
    monkeypatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archives"
    evidence_path = tmp_path / "evidence.json"
    screenshot_path = tmp_path / "complete.png"
    final_video_path = tmp_path / "ep001_final.mp4"
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", archive_root)
    monkeypatch.setattr(adapter, "_archive_key", lambda _run_id: "archive-key")
    screenshot_path.write_bytes(b"screenshot")

    evidence_path.write_text(
        adapter.json.dumps(
            {
                "run_after": {"id": "wfr_missing", "status": "completed"},
                "paidProvidersConnected": False,
                "providerCallsStarted": False,
                "mode_evidence": {"final_film": {}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="hash-matched final compose artifact"):
        adapter._archive_execution_evidence(
            mode="full-chain",
            evidence_path=evidence_path,
            screenshot_path=screenshot_path,
        )

    final_video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42real-video")
    evidence_path.write_text(
        adapter.json.dumps(
            {
                "run_after": {"id": "wfr_drifted", "status": "completed"},
                "paidProvidersConnected": False,
                "providerCallsStarted": False,
                "mode_evidence": {
                    "final_film": {
                        "final_compose_artifact": {
                            "path": str(final_video_path),
                            "sha256": "f" * 64,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="unverified final compose artifact"):
        adapter._archive_execution_evidence(
            mode="full-chain",
            evidence_path=evidence_path,
            screenshot_path=screenshot_path,
        )


def test_stable_full_chain_archive_rejects_missing_shot_video_assets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archives"
    evidence_path = tmp_path / "evidence.json"
    screenshot_path = tmp_path / "complete.png"
    final_video_path = tmp_path / "ep001_final.mp4"
    assets = _shot_archive_asset_fixture(tmp_path, marker="missing-shot")
    monkeypatch.setattr(adapter, "EVIDENCE_ARCHIVE_ROOT", archive_root)
    monkeypatch.setattr(adapter, "_archive_key", lambda _run_id: "archive-key")
    screenshot_path.write_bytes(b"screenshot")
    final_video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42final")
    Path(str(assets[0]["video_path"])).unlink()
    evidence_path.write_text(
        adapter.json.dumps(
            {
                "run_after": {"id": "wfr_missing_shot", "status": "completed"},
                "paidProvidersConnected": False,
                "providerCallsStarted": False,
                "mode_evidence": {
                    "shot_videos": {
                        "first_frame_checks": [
                            {
                                "shot_id": f"shot:{shot_index}",
                                "ssim": 0.99,
                                "threshold": 0.72,
                                "status": "passed",
                                "match": True,
                                "source_image_path": asset["source_path"],
                                "source_image_sha256": asset["source_sha256"],
                                "video_path": asset["video_path"],
                                "video_sha256": asset["video_sha256"],
                                "video_width": 1366,
                                "video_height": 768,
                            }
                            for shot_index, asset in enumerate(assets, 1)
                        ]
                    },
                    "final_film": {
                        "final_compose_artifact": {
                            "path": str(final_video_path),
                            "sha256": adapter._sha256_file(final_video_path),
                            "width": 1366,
                            "height": 768,
                            "duration_seconds": 6.06,
                        },
                        "final_order_checks": [
                            {
                                "shot_id": f"shot:{shot_index}",
                                "source_image_path": asset["source_path"],
                                "source_image_sha256": asset["source_sha256"],
                                "final_frame_path": asset["final_frame_path"],
                                "final_frame_sha256": asset[
                                    "final_frame_sha256"
                                ],
                                "similarity": 0.99,
                                "threshold": 0.72,
                                "status": "passed",
                                "match": True,
                            }
                            for shot_index, asset in enumerate(assets, 1)
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="unverified shot 1 video"):
        adapter._archive_execution_evidence(
            mode="full-chain",
            evidence_path=evidence_path,
            screenshot_path=screenshot_path,
            shot_count=3,
        )


def test_shot_frame_colors_are_distinct_for_a_three_shot_sequence() -> None:
    colors = [adapter._shot_frame_color(index) for index in (1, 2, 3)]

    assert len(set(colors)) == 3


def test_video_identity_comes_from_reference_frame_not_request_order(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from PIL import Image

    monkeypatch.setattr(adapter, "TARGET_UI_SMOKE", tmp_path)
    server = adapter.LocalUpstreamServer(full_chain=True, shot_count=3)
    try:
        for shot_index in (3, 1, 2):
            image = server.storyboard_image(shot_index)
            encoded = adapter.io.BytesIO()
            with Image.open(adapter.io.BytesIO(image)) as source:
                source.convert("RGB").save(
                    encoded,
                    format="JPEG",
                    quality=90,
                )
            data_url = (
                "data:image/jpeg;base64,"
                f"{adapter.base64.b64encode(encoded.getvalue()).decode('ascii')}"
            )
            payload = {
                "media_inputs": [
                    {
                        "kind": "image",
                        "role": "first_frame",
                        "url": data_url,
                    }
                ]
            }

            assert server.identify_video_shot_index(payload) == shot_index
            task_id = server.register_video_task(2, shot_index=shot_index)
            assert server.video_task(task_id)["shot_index"] == shot_index
    finally:
        server.server_close()


@pytest.mark.skipif(not adapter.FFMPEG.is_file(), reason="bundled FFmpeg required")
def test_local_video_stub_moves_without_losing_storyboard_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(adapter, "TARGET_UI_SMOKE", tmp_path)
    server = adapter.LocalUpstreamServer(full_chain=True, shot_count=3)
    try:
        source_path = tmp_path / "source.png"
        video_path = tmp_path / "shot.mp4"
        first_path = tmp_path / "first.png"
        source_path.write_bytes(server.storyboard_image(1))
        video_path.write_bytes(server.ensure_video(5, shot_index=1))

        subprocess.run(
            [
                str(adapter.FFMPEG),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(first_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert adapter._image_similarity(source_path, first_path) >= 0.72

        detection = subprocess.run(
            [
                str(adapter.FFMPEG),
                "-hide_banner",
                "-nostats",
                "-i",
                str(video_path),
                "-map",
                "0:v:0",
                "-vf",
                "freezedetect=n=0.003:d=0.5",
                "-an",
                "-f",
                "null",
                os.devnull,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        output = f"{detection.stdout}\n{detection.stderr}"
        assert "freeze_start" not in output
    finally:
        server.server_close()


def test_video_reference_images_reads_openai_images_list() -> None:
    references = adapter._video_reference_images(
        {
            "images": [
                "data:image/jpeg;base64,Zmlyc3Q=",
                {"url": "data:image/jpeg;base64,c2Vjb25k"},
            ]
        }
    )

    assert references == [
        {"role": "images", "url": "data:image/jpeg;base64,Zmlyc3Q="},
        {"role": "images", "url": "data:image/jpeg;base64,c2Vjb25k"},
    ]


def test_image_similarity_separates_matching_and_different_solid_frames(
    tmp_path: Path,
) -> None:
    from PIL import Image

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    different = tmp_path / "different.png"
    Image.new("RGB", (32, 18), (229, 57, 53)).save(first)
    Image.new("RGB", (64, 36), (229, 57, 53)).save(second)
    Image.new("RGB", (32, 18), (30, 136, 229)).save(different)

    assert adapter._image_similarity(first, second) == 1.0
    assert adapter._image_similarity(first, different) < 0.72
