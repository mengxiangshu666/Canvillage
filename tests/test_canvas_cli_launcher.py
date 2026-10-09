from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_portable_cli_batch_uses_console_free_relay():
    batch = (ROOT / "village-canvas.bat").read_text(encoding="utf-8")
    relay = (ROOT / "village_canvas_cli_hidden.ps1").read_text(encoding="utf-8")

    assert "powershell.exe" in batch.lower()
    assert "-windowstyle hidden" in batch.lower()
    assert "village_canvas_cli_hidden.ps1" in batch
    assert '"runtime\\python\\python.exe" "%~dp0village_canvas_cli.py"' not in batch
    assert "$CliArguments" in relay
    assert "@CliArguments" in relay
    assert "$LASTEXITCODE" in relay
