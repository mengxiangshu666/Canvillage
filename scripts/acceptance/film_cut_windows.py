"""Offline cut review evidence; timestamps are supplied, not artistic judgments."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]


def run(binary: str, args: list[str]) -> bytes:
    result = subprocess.run([str(ROOT / 'runtime' / 'ffmpeg' / f'{binary}.exe'), *args], capture_output=True, timeout=60, check=True)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--cuts', type=float, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notes', type=Path, help='Already reviewed notes, bound to the exact source SHA-256')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if any(not math.isfinite(cut) or cut <= 0 for cut in args.cuts):
        parser.error('cut timestamps must be finite and positive')
    probe = json.loads(run('ffprobe', ['-v', 'error', '-show_entries', 'stream=index,codec_type,codec_name,start_time,duration:format=duration', '-of', 'json', str(source)]))
    duration = float(probe['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        parser.error('media duration must be finite and positive')
    if any(cut + 0.4 >= duration for cut in args.cuts):
        parser.error('each cut requires 0.4 seconds of media after it')
    args.output.mkdir(parents=True, exist_ok=True)
    offsets = [-0.4, -0.08, 0.08, 0.4]
    sheet = Image.new('RGB', (1920, 302 * len(args.cuts)), '#ffffff')
    draw = ImageDraw.Draw(sheet)
    frames = []
    for index, cut in enumerate(args.cuts):
        entries = []
        for column, offset in enumerate(offsets):
            requested = max(0, cut + offset)
            data = run('ffmpeg', ['-v', 'error', '-ss', f'{requested:.6f}', '-i', str(source), '-frames:v', '1', '-vf', 'scale=480:270', '-f', 'image2pipe', '-vcodec', 'png', '-'])
            frame = Image.open(io.BytesIO(data)).convert('RGB')
            sheet.paste(frame, (480 * column, 302 * index + 32))
            label = f'cut {index + 1}: {cut:.6f}s | frame request {requested:.6f}s'
            draw.text((480 * column + 8, 302 * index + 8), label, fill='#000000')
            entries.append({'requested_seconds': requested, 'png_sha256': hashlib.sha256(data).hexdigest()})
        frames.append({'cut_seconds': cut, 'frames': entries})
    image_path = args.output / 'cut-windows.png'
    sheet.save(image_path)
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    evidence = {'source': str(source), 'source_sha256': digest, 'probe': probe, 'cuts': frames, 'audio_content_reviewed': False, 'artistic_quality_verified': False, 'timestamp_basis': 'requested decode time; not a frame-exact edit decision list', 'contact_sheet': str(image_path.resolve())}
    (args.output / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.notes:
        from novelvideo.production.screening_repair import build_screening_feedback, plan_screening_repairs

        notes = json.loads(args.notes.read_text(encoding='utf-8'))
        if notes.get('source_sha256') != digest:
            raise ValueError('screening notes source hash does not match the reviewed video')
        feedback = build_screening_feedback(screening_id=notes['screening_id'], audience=notes.get('audience', ''), issues=notes.get('issues', []))
        repair = plan_screening_repairs(feedback)
        (args.output / 'screening-and-repair.json').write_text(json.dumps({'source_sha256': digest, 'screening_feedback': feedback, 'repair_plan': repair, 'executed_media_actions': 0}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'source_sha256': digest, 'cuts': len(frames), 'contact_sheet': str(image_path.resolve())}))


if __name__ == '__main__':
    main()
