"""Asset provenance persisted from the shared Freezone history success point."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from novelvideo.freezone.history import (
    append_generation_history,
    build_node_history_record,
    generation_history_path,
)
from novelvideo.freezone.provenance import (
    get_asset_provenance_db_path,
    read_asset_provenance,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record(
    *,
    job_id: str,
    media_type: str,
    result: dict,
    status: str = "completed",
    prompt: str = "cinematic prompt",
    extra: dict | None = None,
) -> dict:
    return build_node_history_record(
        task_type=f"freezone_{media_type}_gen",
        job_id=job_id,
        task_key=f"task:freezone_{media_type}_gen:project:project_123:0:{job_id}",
        status=status,
        media_type=media_type,
        result=result,
        prompt=prompt,
        extra=extra,
    )


def _append(
    project_dir: Path,
    *,
    record: dict,
    canvas_id: str = "canvas_a",
    node_id: str = "node_a",
) -> dict:
    stored = append_generation_history(
        project_dir=project_dir,
        canvas_id=canvas_id,
        node_id=node_id,
        record=record,
    )
    assert stored is not None
    return stored


def test_completed_image_history_persists_full_provenance(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    output = project_dir / "renders" / "shot.png"
    parent = project_dir / "inputs" / "base.png"
    output.parent.mkdir(parents=True)
    parent.parent.mkdir(parents=True)
    output.write_bytes(b"generated-image")
    parent.write_bytes(b"parent-image")

    prompt = "  cinematic portrait with rim light  "
    _append(
        project_dir,
        record=_record(
            job_id="job_image_1",
            media_type="image",
            prompt=prompt,
            result={
                "output_path": str(output),
                "output_url": ("/api/v1/projects/project_123/media/renders/shot.png"),
            },
            extra={
                "task_id": "task_image_1",
                "model": "image-model-v1",
                "provider": "provider-a",
                "channel": "primary",
                "parameters": {"aspect_ratio": "16:9", "steps": 28},
                "seed": 42,
                "parent_assets": [{"path": str(parent), "role": "base"}],
                "usage": {"input_tokens": 12},
                "cost": {"currency": "USD", "amount": 0.03},
                "quality": {"score": 0.94},
            },
        ),
    )

    rows = read_asset_provenance(project_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["project_id"] == "project_123"
    assert row["canvas_id"] == "canvas_a"
    assert row["node_id"] == "node_a"
    assert row["task_id"] == "task_image_1"
    assert row["job_id"] == "job_image_1"
    assert row["model"] == "image-model-v1"
    assert row["provider"] == "provider-a"
    assert row["channel"] == "primary"
    assert row["prompt_sha256"] == _sha256(prompt.strip().encode())
    assert row["prompt_ref"].endswith(
        "freezone/_generation_history/canvas_a/node_a.jsonl#freezone_image_gen:job_image_1"
    )
    assert row["parameters"] == {"aspect_ratio": "16:9", "steps": 28}
    assert row["seed"] == "42"
    assert row["parent_assets"] == [
        {"path": str(parent), "role": "base", "sha256": _sha256(b"parent-image")}
    ]
    assert row["output_path"] == str(output)
    assert row["output_sha256"] == _sha256(b"generated-image")
    assert row["usage"] == {"input_tokens": 12}
    assert row["cost"] == {"currency": "USD", "amount": 0.03}
    assert row["quality"] == {"score": 0.94}
    assert row["created_at"].endswith("Z")

    with sqlite3.connect(get_asset_provenance_db_path(project_dir)) as conn:
        columns = {
            item[1] for item in conn.execute("PRAGMA table_info(asset_provenance)")
        }
    assert "prompt" not in columns
    assert "prompt_text" not in columns


@pytest.mark.parametrize(
    ("media_type", "path_key", "url_key", "suffix"),
    [
        ("video", "video_path", "video_url", ".mp4"),
        ("audio", "audio_path", "audio_url", ".wav"),
    ],
)
def test_video_and_audio_use_the_same_history_success_hook(
    tmp_path: Path,
    media_type: str,
    path_key: str,
    url_key: str,
    suffix: str,
) -> None:
    project_dir = tmp_path / media_type
    output = project_dir / f"result{suffix}"
    output.parent.mkdir(parents=True)
    output.write_bytes(media_type.encode())

    _append(
        project_dir,
        record=_record(
            job_id=f"job_{media_type}",
            media_type=media_type,
            result={path_key: str(output), url_key: f"/static/result{suffix}"},
        ),
    )

    [row] = read_asset_provenance(project_dir)
    assert row["media_type"] == media_type
    assert row["output_path"] == str(output)
    assert row["output_url"] == f"/static/result{suffix}"
    assert row["output_sha256"] == _sha256(media_type.encode())


@pytest.mark.parametrize(
    ("status", "media_type"),
    [("failed", "image"), ("running", "video"), ("completed", "text")],
)
def test_non_success_or_non_media_history_does_not_create_provenance(
    tmp_path: Path,
    status: str,
    media_type: str,
) -> None:
    project_dir = tmp_path / f"{status}-{media_type}"
    _append(
        project_dir,
        record=_record(
            job_id="job_skip",
            media_type=media_type,
            status=status,
            result={"output_url": "/static/result.bin"},
        ),
    )

    assert read_asset_provenance(project_dir) == []


def test_same_task_and_output_is_idempotent(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    output = project_dir / "repeated.png"
    project_dir.mkdir(parents=True)
    output.write_bytes(b"first version")

    for contents in (b"first version", b"overwritten version"):
        output.write_bytes(contents)
        _append(
            project_dir,
            record=_record(
                job_id="job_repeat",
                media_type="image",
                result={
                    "output_path": str(output),
                    "output_url": "/static/repeated.png",
                },
            ),
        )

    rows = read_asset_provenance(project_dir)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "job_repeat"
    assert rows[0]["output_sha256"] == _sha256(b"overwritten version")


def test_provenance_redacts_credentials_and_signed_url_material(tmp_path: Path) -> None:
    project_dir = tmp_path / "redacted"
    secret = "sk-example-secret-value-123456"
    signed_url = "https://user:pass@cdn.example/out.png?token=hidden#fragment"
    _append(
        project_dir,
        record=_record(
            job_id="job_redacted",
            media_type="image",
            result={"output_url": signed_url},
            extra={
                "provider": "provider-a",
                "channel": f"Bearer {secret}",
                "parameters": {
                    "apiKey": secret,
                    "nested": {"access_token": secret},
                    "reference_url": signed_url,
                    "steps": 24,
                },
                "parent_assets": [
                    {"url": signed_url, "authorization": f"Bearer {secret}"}
                ],
                "usage": {"request_token": secret, "images": 1},
            },
        ),
    )

    [row] = read_asset_provenance(project_dir)
    assert row["output_url"] == "https://cdn.example/out.png"
    assert row["channel"] == "[REDACTED]"
    assert row["parameters"] == {
        "apiKey": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]"},
        "reference_url": "https://cdn.example/out.png",
        "steps": 24,
    }
    assert row["parent_assets"] == [
        {"url": "https://cdn.example/out.png", "authorization": "[REDACTED]"}
    ]
    assert row["usage"] == {"request_token": "[REDACTED]", "images": 1}
    assert secret not in str(row)


def test_same_task_with_distinct_outputs_keeps_both_records(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    for name in ("candidate-a.png", "candidate-b.png"):
        _append(
            project_dir,
            record=_record(
                job_id="job_candidates",
                media_type="image",
                result={"output_url": f"/static/{name}"},
            ),
        )

    rows = read_asset_provenance(project_dir)
    assert {row["output_url"] for row in rows} == {
        "/static/candidate-a.png",
        "/static/candidate-b.png",
    }


def test_old_asset_provenance_table_is_migrated_in_place(tmp_path: Path) -> None:
    project_dir = tmp_path / "legacy-project"
    db_path = get_asset_provenance_db_path(project_dir)
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE asset_provenance (id TEXT PRIMARY KEY, job_id TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO asset_provenance (id, job_id) VALUES (?, ?)",
            [
                ("legacy-row-a", "legacy-job-a"),
                ("legacy-row-b", "legacy-job-b"),
            ],
        )

    _append(
        project_dir,
        record=_record(
            job_id="new-job",
            media_type="image",
            result={"output_url": "/static/new.png"},
        ),
    )

    with sqlite3.connect(db_path) as conn:
        columns = {
            item[1] for item in conn.execute("PRAGMA table_info(asset_provenance)")
        }
        job_ids = {
            row[0] for row in conn.execute("SELECT job_id FROM asset_provenance")
        }
        indexes = {
            item[1] for item in conn.execute("PRAGMA index_list(asset_provenance)")
        }
    assert {
        "idempotency_key",
        "project_id",
        "canvas_id",
        "node_id",
        "task_id",
        "job_id",
        "model",
        "provider",
        "channel",
        "prompt_sha256",
        "parameters_json",
        "parent_assets_json",
        "output_sha256",
        "usage_json",
        "cost_json",
        "quality_json",
        "created_at",
        "updated_at",
    } <= columns
    assert job_ids == {"legacy-job-a", "legacy-job-b", "new-job"}
    assert "idx_asset_provenance_idempotency" in indexes


def test_provenance_failure_does_not_break_generation_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from novelvideo.freezone import provenance

    def fail_write(*args, **kwargs):
        raise sqlite3.OperationalError("simulated provenance failure")

    monkeypatch.setattr(provenance, "record_generation_provenance", fail_write)
    project_dir = tmp_path / "project"
    stored = _append(
        project_dir,
        record=_record(
            job_id="job_survives",
            media_type="image",
            result={"output_url": "/static/survives.png"},
        ),
    )

    assert stored["job_id"] == "job_survives"
    history_file = generation_history_path(project_dir, "canvas_a", "node_a")
    assert history_file.exists()
    assert "job_survives" in history_file.read_text(encoding="utf-8")
    assert "asset provenance write failed" in caplog.text


def test_missing_optional_metadata_uses_empty_values(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    _append(
        project_dir,
        record=_record(
            job_id="job_minimal",
            media_type="image",
            result={"output_url": "/static/minimal.png"},
            prompt="",
        ),
    )

    [row] = read_asset_provenance(project_dir)
    assert row["project_id"] == "project_123"
    assert row["task_id"] == ""
    assert row["provider"] == ""
    assert row["channel"] == ""
    assert row["prompt_sha256"] == ""
    assert row["parameters"] == {}
    assert row["seed"] == ""
    assert row["parent_assets"] == []
    assert row["output_sha256"] == ""
    assert row["usage"] == {}
    assert row["cost"] == {}
    assert row["quality"] == {}


def test_zero_seed_is_preserved(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    _append(
        project_dir,
        record=_record(
            job_id="job_seed_zero",
            media_type="image",
            result={"output_url": "/static/seed-zero.png"},
            extra={"seed": 0},
        ),
    )

    [row] = read_asset_provenance(project_dir)
    assert row["seed"] == "0"
