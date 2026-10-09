"""Invalid review timestamps must fail before FFmpeg or evidence creation."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("timestamp", ["nan", "inf", "0", "-1"])
def test_invalid_window_does_not_decode_or_create_evidence(tmp_path, timestamp):
    source = tmp_path / "not-a-video.mp4"
    source.write_bytes(b"not media")
    output = tmp_path / "evidence"
    script = Path(__file__).resolve().parents[1] / "scripts/acceptance/film_cut_windows.py"
    result = subprocess.run(
        [sys.executable, str(script), str(source), f"--cuts={timestamp}", "--output", str(output)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert "finite and positive" in result.stderr
    assert not output.exists()
