"""角色资产每个角色两张图：正面全身照 + 四视图设定表。

覆盖三条合同：
1. 「portraits」任务为每个角色同时写出 ``portrait.png`` 与 ``four_view.png``。
2. 只写出一张时「角色资产完成」判据必须为 False，不能跳过四视图。
3. 四视图出图口径与正面全身照不同（横构图的分屏设定表，且不禁拼贴 / 分屏）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novelvideo.generators.image_generator import (
    CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW,
    CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
    normalize_character_prompt_template,
)
from novelvideo.production.character_assets import (
    character_asset_slots,
    character_assets_complete,
)
from novelvideo.task_backend.runners import character_image as runner


def _character(**overrides: Any) -> SimpleNamespace:
    fields = dict(
        name="村长", face_prompt="方脸浓眉，络腮胡", description="青云宗外门弟子"
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _patched_generation(monkeypatch: pytest.MonkeyPatch, *, fail_on: str = ""):
    calls: list[dict[str, Any]] = []

    async def fake_generate(**kwargs: Any) -> list[str]:
        calls.append(kwargs)
        template = str(kwargs.get("prompt_template") or "")
        if fail_on and template == fail_on:
            raise RuntimeError(f"{template} 生成失败")
        slot_dir = Path(str(kwargs["output_dir"]))
        slot_dir.mkdir(parents=True, exist_ok=True)
        path = slot_dir / f"{template or 'default'}.png"
        path.write_bytes(b"PNGDATA")
        return [str(path)]

    monkeypatch.setattr(
        "novelvideo.generators.generate_character_reference_unified", fake_generate
    )
    return calls


def _run_portrait(tmp_path: Path):
    progress: list[str] = []

    def update(progress_value: float, message: str) -> None:
        progress.append(message)

    output = asyncio.run(
        runner._generate_character_portrait(
            character=_character(),
            ethnicity="Chinese",
            output_dir=tmp_path,
            style="",
            model="direct/test-model",
            task_type="character_portrait",
            scope="character:村长:portrait",
            update=update,
        )
    )
    return output, progress


def test_portrait_task_writes_both_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patched_generation(monkeypatch)

    output, progress = _run_portrait(tmp_path)

    portrait_path, four_view_path = character_asset_slots(tmp_path, "村长")
    assert Path(str(output)) == portrait_path
    assert portrait_path.is_file()
    assert four_view_path.is_file()
    assert [c["prompt_template"] for c in calls] == [
        CHARACTER_PROMPT_TEMPLATE_PORTRAIT,
        CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW,
    ]
    assert calls[0]["reference_image_path"] == ""
    assert calls[1]["reference_image_path"] == str(
        Path(calls[0]["output_dir"]) / "portrait.png"
    )
    assert any("正面全身照" in line for line in progress)
    assert any("四视图设定表" in line for line in progress)


def test_four_view_failure_leaves_both_slots_unwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四视图失败时不能只落一张图，否则会把半成品当成完成。"""
    _patched_generation(monkeypatch, fail_on=CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW)

    with pytest.raises(RuntimeError, match="生成失败"):
        _run_portrait(tmp_path)

    portrait_path, four_view_path = character_asset_slots(tmp_path, "村长")
    assert not portrait_path.exists()
    assert not four_view_path.exists()


def test_pair_install_failure_restores_previous_assets(tmp_path, monkeypatch):
    _patched_generation(monkeypatch)
    portrait, four_view = character_asset_slots(tmp_path, "村长")
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"old-portrait")
    four_view.write_bytes(b"old-four-view")

    def fail_portrait_install(*_args):
        raise OSError("install failed")

    monkeypatch.setattr(runner, "_replace_canonical_asset", fail_portrait_install)
    with pytest.raises(OSError, match="install failed"):
        _run_portrait(tmp_path)
    assert portrait.read_bytes() == b"old-portrait"
    assert four_view.read_bytes() == b"old-four-view"


def test_character_assets_complete_needs_both_slots(tmp_path: Path) -> None:
    snapshot = {"mode": "auto", "fingerprint": "fp"}
    portrait_path, four_view_path = character_asset_slots(tmp_path, "村长")

    portrait_path.parent.mkdir(parents=True, exist_ok=True)
    portrait_path.write_bytes(b"portrait")
    assert character_assets_complete(tmp_path, "村长", snapshot) is False

    four_view_path.write_bytes(b"four-view")
    assert character_assets_complete(tmp_path, "村长", snapshot) is True


def test_unknown_template_falls_back_to_the_single_portrait() -> None:
    assert (
        normalize_character_prompt_template("bogus")
        == CHARACTER_PROMPT_TEMPLATE_PORTRAIT
    )
    assert (
        normalize_character_prompt_template(None) == CHARACTER_PROMPT_TEMPLATE_PORTRAIT
    )
    assert (
        normalize_character_prompt_template("FOUR_VIEW")
        == CHARACTER_PROMPT_TEMPLATE_FOUR_VIEW
    )
