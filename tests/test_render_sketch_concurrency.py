"""并发网格渲染不再共用同一个草图切片文件。

42 个 selected_regen 任务同时把切片写进 grids/epNNN/render/temp_sub_sketch.jpg
时，后写的任务会把先写的 JPEG 截断：读附件拿到半截文件，表现成
WinError 32 / broken data stream / "Render sketch attachment is unreadable"，
整批首帧因此被判死。每个任务必须写自己的切片，读入内存后立即删除。
"""

from __future__ import annotations

import threading

from PIL import Image

from novelvideo.generators import nanobanana_grid
from novelvideo.generators.nanobanana_grid import NanoBananaGridGenerator


def _panel(path, color):
    Image.new("RGB", (48, 72), color).save(path)


def _generator():
    gen = NanoBananaGridGenerator.__new__(NanoBananaGridGenerator)
    gen.provider = "openai"
    return gen


class _ScriptedUuid:
    """按调用顺序发号，用来断言每个任务拿到了不同的文件名。"""

    def __init__(self, hexes):
        self._hexes = list(hexes)
        self._lock = threading.Lock()

    def uuid4(self):
        with self._lock:
            value = self._hexes.pop(0)
        return type("U", (), {"hex": value})()


def test_concurrent_renders_get_distinct_sketch_files(tmp_path, monkeypatch):
    red = tmp_path / "beat_01.png"
    blue = tmp_path / "beat_02.png"
    _panel(red, (255, 0, 0))
    _panel(blue, (0, 0, 255))
    monkeypatch.setattr(
        nanobanana_grid.uuid, "uuid4", _ScriptedUuid(["aaaa", "bbbb"]).uuid4
    )

    written: dict[str, bytes] = {}
    gate = threading.Event()

    def record_and_wait(self, image_path, *args, **kwargs):
        # 写完不立刻读：真实链路里中间还隔着提示词拼装，给并发写手留足窗口。
        written[image_path] = open(image_path, "rb").read()
        gate.wait(timeout=10)
        return object()

    monkeypatch.setattr(NanoBananaGridGenerator, "_load_image_as_part", record_and_wait)

    errors: list[BaseException] = []

    def render(beat_num, sketch):
        try:
            from novelvideo.generators.nanobanana_grid import crop_sketch_panels

            out = tmp_path / f"temp_sub_sketch_{nanobanana_grid.uuid.uuid4().hex}.jpg"
            crop_sketch_panels(
                sketch_path=str(tmp_path),
                beat_numbers=[beat_num],
                target_rows=1,
                target_cols=1,
                output_path=str(out),
                beat_sketch_paths={beat_num: str(sketch)},
            )
            _generator()._append_required_render_sketch([], str(out))
        except BaseException as exc:  # noqa: BLE001 - 收集并发异常再断言
            errors.append(exc)
        finally:
            gate.set()

    threads = [
        threading.Thread(target=render, args=(1, red)),
        threading.Thread(target=render, args=(2, blue)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(written) == 2, written.keys()
    assert len(set(written.values())) == 2
    for payload in written.values():
        assert payload[:2] == b"\xff\xd8"
        assert len(payload) > 100


def test_render_sketch_temp_file_is_removed_after_load(tmp_path):
    sketch = tmp_path / "beat_03.png"
    _panel(sketch, (0, 255, 0))
    out = tmp_path / "temp_sub_sketch_abc123.jpg"
    nanobanana_grid.crop_sketch_panels(
        sketch_path=str(tmp_path),
        beat_numbers=[3],
        target_rows=1,
        target_cols=1,
        output_path=str(out),
        beat_sketch_paths={3: str(sketch)},
    )
    assert out.exists()

    _generator()._append_required_render_sketch([], str(out))

    assert not out.exists()


def test_concurrent_regen_tasks_write_distinct_grid_files(tmp_path, monkeypatch):
    """两个任务同时重画时，各自的成品必须落到不同文件。

    文件名以前只带任务内序号，两个任务的第一张都叫 regen_..._g01.png，
    后写完的覆盖先写完的，保存环节就把同一张图存给了好几个镜头。
    """
    seen: list[str] = []

    class _FakeGenerator:
        async def generate_grid(self, **kwargs):
            path = kwargs["output_path"]
            seen.append(path)
            return nanobanana_grid.GridGenerationResult(
                success=True, grid_image_path=path
            )

    monkeypatch.setattr(
        nanobanana_grid, "create_grid_generator", lambda *a, **k: _FakeGenerator()
    )

    async def regen(beat_number):
        return await nanobanana_grid.regenerate_selected_beats(
            selected_beats=[{"beat_number": beat_number}],
            mode_key="1x1_2-3",
            character_map={},
            style="xianxia",
            output_dir=str(tmp_path),
            is_sketch=True,
        )

    async def run_all():
        return await asyncio.gather(regen(2), regen(18), regen(108))

    import asyncio

    results = asyncio.run(run_all())

    paths = [r[0].grid_image_path for r in results]
    assert len(set(paths)) == 3, paths
    assert paths == seen
    assert all(p.endswith((".png")) and "_g01.png" in p for p in paths)
    assert {2, 18, 108} == {int(p.split("_")[-2].split("-")[0]) for p in paths}


def test_render_sketch_keeps_user_material(tmp_path):
    sketch = tmp_path / "beat_04.png"
    _panel(sketch, (255, 255, 0))
    # 不是本任务独占的临时切片（用户素材、历史草图）读完不能删。
    kept = tmp_path / "sketch_grid_01.jpg"
    nanobanana_grid.crop_sketch_panels(
        sketch_path=str(tmp_path),
        beat_numbers=[4],
        target_rows=1,
        target_cols=1,
        output_path=str(kept),
        beat_sketch_paths={4: str(sketch)},
    )

    _generator()._append_required_render_sketch([], str(kept))

    assert kept.exists()
