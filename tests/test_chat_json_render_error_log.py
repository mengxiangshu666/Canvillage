"""The json-render error log must stay bounded.

``chat/service.py`` appends up to 12 000 characters of *model output* to
``jr_error.log`` every time a model returns a ui-spec that is not canonical JSON.
That path is user-visible only as a short Chinese apology, so the file is the
sole record — and nothing rotated it.  ``village_canvas_rotate_log.py`` only acts
above 32 MiB and is wired to the launcher's stdout log, so a model stuck in a bad
format grew a file in the source root without limit.  These tests pin the bound
and the entry-boundary behaviour that makes the tail readable.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from novelvideo.chat import service as chat_service  # noqa: E402


def _entries(count: int, body: str = "x") -> list[str]:
    return [
        f"\n--- 2026-09-11T00:00:{index:02d}.000000+00:00 ---\nerror: boom\nbody:\n{body}\n"
        for index in range(count)
    ]


def test_log_stays_under_the_cap_once_it_grows_past_it(tmp_path: Path) -> None:
    log_path = tmp_path / "jr_error.log"
    cap = 4096
    entry = _entries(1, body="y" * 512)[0]

    for _ in range(40):
        chat_service._append_bounded_log(log_path, entry, cap)

    assert log_path.stat().st_size <= cap
    assert log_path.stat().st_size > 0


def test_the_retained_tail_starts_at_an_entry_boundary(tmp_path: Path) -> None:
    """A truncated first line reads as corruption and wastes the whole window."""

    log_path = tmp_path / "jr_error.log"
    cap = 4096
    entry = _entries(1, body="y" * 512)[0]

    for _ in range(40):
        chat_service._append_bounded_log(log_path, entry, cap)

    text = log_path.read_text(encoding="utf-8")

    assert text.startswith("--- "), text[:80]
    assert "error: boom" in text


def test_the_newest_entry_survives_the_trim(tmp_path: Path) -> None:
    """The most recent failure is the one being diagnosed."""

    log_path = tmp_path / "jr_error.log"
    cap = 4096
    # Fill past the cap with an old error, then write one distinguishable one.
    for _ in range(40):
        chat_service._append_bounded_log(log_path, _entries(1, body="old" * 200)[0], cap)
    marker = "\n--- 2026-09-11T09:09:09.000000+00:00 ---\nerror: newest\nbody:\nNEWEST\n"
    chat_service._append_bounded_log(log_path, marker, cap)

    text = log_path.read_text(encoding="utf-8")

    assert "NEWEST" in text
    assert text.startswith("--- ")


def test_a_log_within_the_cap_is_left_alone(tmp_path: Path) -> None:
    """No rewrite when nothing is over budget — mtime and inode stay put."""

    log_path = tmp_path / "jr_error.log"
    chat_service._append_bounded_log(log_path, _entries(1)[0], 1024 * 1024)
    before = log_path.stat()

    chat_service._append_bounded_log(log_path, _entries(1)[0], 1024 * 1024)

    after = log_path.stat()
    assert after.st_size > before.st_size
    assert not list(tmp_path.glob(".*tmp")), "a temp file was left behind"


def test_the_log_writer_uses_the_cap(tmp_path: Path, monkeypatch) -> None:
    """Wire check: the helper is bounded, but is it the one being called?"""

    log_path = tmp_path / "jr_error.log"
    monkeypatch.setenv("JR_ERROR_LOG", str(log_path))
    monkeypatch.setattr(chat_service, "_JSON_RENDER_ERROR_LOG_MAX_BYTES", 2048, raising=True)

    for _ in range(30):
        chat_service._log_json_render_error(ValueError("not canonical"), "z" * 400)

    assert log_path.stat().st_size <= 2048
    assert log_path.stat().st_size > 0


def test_an_unwritable_log_path_is_swallowed(tmp_path: Path, monkeypatch) -> None:
    """Diagnostics must never turn a format error into a request failure."""

    monkeypatch.setenv("JR_ERROR_LOG", str(tmp_path / "nope" / "x.log"))
    # Make the parent path a *file* so mkdir raises OSError.
    blocker = tmp_path / "nope"
    blocker.write_text("not a directory", encoding="utf-8")

    chat_service._log_json_render_error(ValueError("not canonical"), "{not json}")
