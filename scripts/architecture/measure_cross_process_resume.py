"""Measure that a new process resumes a persisted video task without submit."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def _worker(counter_path: Path, output_path: Path, project_dir: Path) -> int:
    from novelvideo.freezone.jobs import run_freezone_video_gen
    from novelvideo.generators.video_generator import NewApiVideoGenerator
    from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus
    import novelvideo.generators.video_generator as video_generator_module

    class ProbeGenerator(NewApiVideoGenerator):
        def __init__(self) -> None:
            self.model = "cross-process-fixture"

        async def generate(self, **_kwargs):
            counter = json.loads(counter_path.read_text(encoding="utf-8"))
            counter["generate"] += 1
            counter_path.write_text(json.dumps(counter), encoding="utf-8")
            raise AssertionError("resume path submitted a new provider task")

        async def recover_task(self, **kwargs):
            counter = json.loads(counter_path.read_text(encoding="utf-8"))
            counter["recover"] += 1
            counter_path.write_text(json.dumps(counter), encoding="utf-8")
            output = Path(kwargs["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"cross-process recovered mp4")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=str(output))

    video_generator_module.create_video_generator = lambda **_kwargs: ProbeGenerator()
    result = asyncio.run(
        run_freezone_video_gen(
            project_dir=project_dir,
            job_id="cross-process-resume",
            prompt="ignored during recovery",
            reference_items=[],
            duration_seconds=5,
            backend="newapi_video-fixture",
            resume_provider_task_id="persisted-provider-task",
        )
    )
    output_path.write_text(
        json.dumps({"ok": result.exists(), "result": str(result)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--counter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.worker:
        return _worker(args.counter, args.output, args.project_dir)

    with tempfile.TemporaryDirectory(prefix="t255-cross-process-") as raw_dir:
        root = Path(raw_dir)
        counter = root / "counter.json"
        output = root / "worker-result.json"
        project_dir = root / "project"
        counter.write_text(json.dumps({"generate": 0, "recover": 0}), encoding="utf-8")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--counter",
            str(counter),
            "--output",
            str(output),
            "--project-dir",
            str(project_dir),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise SystemExit(completed.stderr or completed.stdout)
        result = json.loads(output.read_text(encoding="utf-8"))
        counts = json.loads(counter.read_text(encoding="utf-8"))
        report = {
            "schema": "cross_process_video_resume.v1",
            "worker_returncode": completed.returncode,
            "persisted_provider_task_id": "persisted-provider-task",
            "provider_generate_calls": counts["generate"],
            "provider_recover_calls": counts["recover"],
            "worker_result": result,
            "assertion": counts == {"generate": 0, "recover": 1},
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["assertion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
