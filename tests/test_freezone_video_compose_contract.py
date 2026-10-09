from novelvideo.api.schemas import FreezoneVideoComposeItem, FreezoneVideoComposeRequest
from novelvideo.freezone.jobs import _atempo_filter
import pytest


def test_compose_contract_preserves_speed_and_cover() -> None:
    item = FreezoneVideoComposeItem(
        item_id="clip-1",
        source_url="/static/clip.mp4",
        source_end=4.0,
        speed=1.5,
    )
    request = FreezoneVideoComposeRequest(
        cover_url="/static/cover.jpg",
        tracks=[{"track_id": "video", "kind": "video", "items": [item]}],
    )

    assert request.cover_url == "/static/cover.jpg"
    assert request.tracks[0].items[0].speed == 1.5


def test_atempo_filter_handles_compose_speed_extremes() -> None:
    assert _atempo_filter(0.25) == "atempo=0.5,atempo=0.500000"
    assert _atempo_filter(1.5) == "atempo=1.500000"
    assert _atempo_filter(4.0) == "atempo=2.0,atempo=2.000000"


@pytest.mark.asyncio
async def test_zero_volume_audio_is_not_rendered(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs

    async def forbidden(**kwargs):
        raise AssertionError('silent audio must not be rendered')

    monkeypatch.setattr(jobs, '_render_audio_clip', forbidden)
    source = tmp_path / 'base.mp4'
    source.write_bytes(b'local fixture')
    output = tmp_path / 'final.mp4'
    await jobs._mix_audio_tracks(base_video_path=source, final_output_path=output, temp_dir=tmp_path, audio_items=[{'volume': 0, 'source_start': 0, 'source_end': 1, 'source_path': 'unused.wav'}])
    assert output.read_bytes() == b'local fixture'


@pytest.mark.asyncio
async def test_zero_volume_video_reaches_renderer(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs

    volumes = []

    async def render(**kwargs):
        volumes.append(kwargs['volume'])
        kwargs['output_path'].write_bytes(b'fixture')

    async def concat(paths, output):
        output.write_bytes(b'fixture')

    monkeypatch.setattr(jobs.shutil, 'which', lambda _name: 'local-test')
    monkeypatch.setattr(jobs, '_render_video_clip', render)
    monkeypatch.setattr(jobs, '_concat_media_segments', concat)
    output = await jobs.run_freezone_video_compose(project_dir=tmp_path, job_id='zero', tracks=[{'kind': 'video', 'items': [{'source_path': 'fixture.mp4', 'source_end': 1, 'volume': 0}]}])
    assert volumes == [0.0]
    assert output.is_file()
