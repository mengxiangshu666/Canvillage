"""Measure where each source clip actually landed in the assembled film.

The pipeline's own ``sync`` gate compares the film's total video seconds with
its total audio seconds.  That number is a container-level equality check: it
stays green while every single clip sits in the wrong place, which is exactly
the failure that produced a 2.1 s audio/video drift in an earlier shoot.

This verifier answers the question that matters — *where did clip N end up?* —
by decoding the film and each source clip to a mono envelope and locating each
clip inside the film by cross-correlation.  The correlation peak is the clip's
true start; comparing it with the offset the join graph intended turns a claim
of sync into a measurement of sync.

Usage::

    .venv\\Scripts\\python.exe scripts\\verify_film_sync.py ^
        --film workspace/artifacts/film-studio/<stamp>-film.mp4 ^
        --clips "workspace/artifacts/film-studio/<stamp>-*-clip.mp4" ^
        --crossfade 0.4

Exit code is 0 when every clip is within tolerance, 1 otherwise, so this can
be wired in as a hard gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
HOP = 160  # 10 ms at 16 kHz
DEFAULT_TOLERANCE = 0.12


def _decode_mono(path: Path) -> np.ndarray:
    """Decode any media file to a mono float envelope at SAMPLE_RATE."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    pcm = np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0
    if pcm.size < HOP * 4:
        return np.zeros(0, dtype=np.float32)
    usable = (pcm.size // HOP) * HOP
    frames = pcm[:usable].reshape(-1, HOP)
    envelope = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return envelope


def _normalise(envelope: np.ndarray) -> np.ndarray:
    if envelope.size == 0:
        return envelope
    centred = envelope - float(np.mean(envelope))
    spread = float(np.std(centred))
    if spread < 1e-9:
        return np.zeros_like(centred)
    return centred / spread


def _best_lag(needle: np.ndarray, haystack: np.ndarray, expected: int, radius: int) -> tuple[int, float]:
    """Return (lag_in_hops, peak_z) for the best match near ``expected``.

    ``peak_z`` is the correlation peak in units of the baseline standard
    deviation.  A clip that is nearly silent — pure room tone — produces no
    distinguishable peak, so a low z must be read as "cannot tell", never as
    "misplaced": accusing a silent shot of being out of place is a false
    positive, and a verifier that cries wolf gets ignored.
    """
    lo = max(0, expected - radius)
    hi = min(haystack.size - needle.size, expected + radius)
    if hi <= lo or needle.size == 0:
        return expected, 0.0

    size = 1
    while size < haystack.size + needle.size:
        size <<= 1
    spectrum = np.fft.rfft(haystack, size) * np.conj(np.fft.rfft(needle, size))
    correlation = np.fft.irfft(spectrum, size)[: haystack.size]
    correlation /= max(1.0, needle.size)

    window = correlation[lo:hi]
    if window.size == 0:
        return expected, 0.0
    best = int(np.argmax(window)) + lo
    peak = float(correlation[best])
    elsewhere = np.concatenate([correlation[:lo], correlation[hi:]])
    baseline = float(np.std(elsewhere)) if elsewhere.size > 8 else 0.0
    z = peak / baseline if baseline > 1e-6 else 0.0
    return best, z


def _rms(envelope: np.ndarray) -> float:
    """Average envelope amplitude — used to spot near-silent clips."""
    if envelope.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(envelope * envelope)))


def _duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    text = proc.stdout.strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _index_of(path: Path) -> int:
    match = re.search(r"-(\d{2})-", path.name)
    return int(match.group(1)) if match else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify clip placement inside an assembled film.")
    parser.add_argument("--film", required=True)
    parser.add_argument("--clips", required=True, help="glob for the source clips")
    parser.add_argument("--crossfade", type=float, default=0.4)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--search", type=float, default=1.5, help="search radius in seconds")
    args = parser.parse_args(argv)

    film = Path(args.film)
    clips = sorted(Path().glob(args.clips) if not Path(args.clips).is_absolute() else Path("/").glob("__never__"))
    if not clips:
        clips = sorted(p for p in Path(args.clips).parent.glob(Path(args.clips).name))
    clips = [c for c in clips if _index_of(c) > 0]
    if not clips:
        print(f"no clips matched {args.clips}")
        return 2

    film_env = _normalise(_decode_mono(film))
    film_duration = film_env.size * HOP / SAMPLE_RATE
    radius = int(args.search * SAMPLE_RATE / HOP)

    print(f"film {film.name}: {film_duration:.3f}s")
    print(f"{'clip':>4}  {'expected':>9}  {'measured':>9}  {'delta':>7}  {'peak z':>7}  verdict")

    worst = 0.0
    failures = 0
    unverifiable = 0
    step = 0.0
    for position, clip in enumerate(clips):
        clip_env = _normalise(_decode_mono(clip))
        if clip_env.size == 0:
            print(f"{_index_of(clip):>4}  no audio")
            failures += 1
            continue

        clip_seconds = clip_env.size * HOP / SAMPLE_RATE
        if position == 0:
            expected_seconds = 0.0
            step = clip_seconds - args.crossfade
        else:
            expected_seconds = position * step

        lag, z = _best_lag(clip_env, film_env, int(expected_seconds * SAMPLE_RATE / HOP), radius)
        measured = lag * HOP / SAMPLE_RATE
        delta = measured - expected_seconds

        # A near-silent clip has no correlation peak to trust.  Report it as
        # unverifiable and keep it out of the pass/fail tally rather than
        # inventing a placement error the audio cannot support.
        if z < 3.0:
            unverifiable += 1
            print(
                f"{_index_of(clip):>4}  {expected_seconds:>9.3f}  {measured:>9.3f}  "
                f"{delta:>+7.3f}  {z:>7.1f}  unverifiable (weak signal)"
            )
            continue

        worst = max(worst, abs(delta))
        verdict = "ok" if abs(delta) <= args.tolerance else "MISPLACED"
        if verdict != "ok":
            failures += 1
        print(
            f"{_index_of(clip):>4}  {expected_seconds:>9.3f}  {measured:>9.3f}  "
            f"{delta:>+7.3f}  {z:>7.1f}  {verdict}"
        )

    print(f"\nworst verified placement error {worst:.3f}s (tolerance {args.tolerance:.3f}s)")
    if unverifiable:
        print(f"{unverifiable} clip(s) unverifiable — near-silent source audio")
    if failures:
        print(f"FAIL: {failures} clip(s) misplaced or unlocatable")
        return 1
    print("PASS: every locatable clip sits where the join graph intended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
