"""同一 Beat 重出时的上游幂等键：同请求复用、换内容换键。

上游把幂等键和请求体绑定，键相同但请求体不同会被判成
``Idempotency-Key was already used for a different request`` 直接拒单。
早期只按 Beat 身份生成键，一旦修好参数（例如补上模型原生音轨）重试就会
撞上这个错误，整条 Run 被拖停。
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from novelvideo.generators.video_generator import (
    NewApiVideoError,
    NewApiVideoGenerator,
    VideoGenStatus,
)
from novelvideo.task_backend.runners.video_request_keys import (
    idempotency_content_token,
    single_video_idempotency_key,
)
from novelvideo.services.video_submission_keys import video_media_input_token


pytestmark = pytest.mark.m09


class _UsageMeter:
    async def reserve_current_model_call_credit(self, **_kwargs):
        return "reservation"

    async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
        return None

    async def bump_model_call(self, **_kwargs):
        return None


def _key(**overrides):
    payload = {
        "scope": "task:single_video:project:proj_123:1:1",
        "prompt": "镜头一：少年抬眼看雨",
        "duration": 6,
        "generation_mode": "imageToVideo",
        "generate_audio": True,
        "media_inputs": [],
    }
    payload.update(overrides)
    return single_video_idempotency_key(**payload)


def test_same_request_shape_reuses_one_key():
    assert _key() == _key()


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt": "镜头一：少年抬眼看雨，雨更大"},
        {"duration": 8},
        {"generation_mode": "firstLastFrame"},
        {"generate_audio": False},
    ],
)
def test_changed_request_shape_gets_a_new_key(overrides):
    assert _key() != _key(**overrides)


def test_frame_content_change_gets_a_new_key(tmp_path: Path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"first-render")
    first = _key(media_inputs=[str(frame)])
    # 同一路径重新出图：文件名没变，内容变了，键必须跟着变。
    frame.write_bytes(b"second-render")
    assert first != _key(media_inputs=[str(frame)])
    # 远程地址（中转链接）无法按内容取指纹，按原样折进键里。
    assert idempotency_content_token("https://media.test/a.png") == (
        "https://media.test/a.png"
    )


def test_reference_role_is_part_of_the_media_identity():
    source = "/tmp/reference.png"
    first_frame = video_media_input_token(source, kind="image", role="first_frame")
    identity = video_media_input_token(source, kind="image", role="identity")
    assert first_frame != identity


def test_key_stays_inside_gateway_format_limits():
    key = _key(scope="task:" + "x" * 400, media_inputs=["/nowhere/" + "y" * 300])
    assert 16 <= len(key) <= 128
    assert all(char.isalnum() or char in "_-" for char in key)


@pytest.mark.parametrize("duration", [None, "bad duration", -1, 0])
def test_unusable_duration_has_one_stable_key(duration):
    assert _key(duration=duration) == _key(duration=0)


def test_unreadable_reference_keeps_identity_without_new_random_key(tmp_path, monkeypatch):
    frame = tmp_path / "unreadable.png"
    frame.write_bytes(b"frame")

    def denied(_path):
        raise PermissionError("fixture permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert idempotency_content_token(frame) == str(frame)
    assert _key(media_inputs=[str(frame)]) == _key(media_inputs=[str(frame)])


def test_idempotency_conflict_detection_matches_gateway_2013():
    conflict = NewApiVideoError(
        "Village Infinite Canvas API submit failed: HTTP 400 - "
        '{"error":{"message":"Idempotency-Key was already used for a '
        'different request (2013)"}}',
        http_status=400,
        stage="submit",
        response_text=(
            '{"error":{"message":"Idempotency-Key was already used for a '
            'different request (2013)"}}'
        ),
    )
    assert NewApiVideoGenerator._is_idempotency_key_conflict(conflict) is True
    # 网关明确说「请求体不同」，不属于「任务可能已创建但响应丢了」。
    assert NewApiVideoGenerator._submission_result_is_unknown(conflict) is False
    assert (
        NewApiVideoGenerator._is_idempotency_key_conflict(
            NewApiVideoError(
                "HTTP 400 - 参数不合法",
                http_status=400,
                stage="submit",
                response_text="参数不合法",
            )
        )
        is False
    )
    assert NewApiVideoGenerator._is_idempotency_key_conflict(None) is False


def test_refresh_key_keeps_gateway_format():
    original = "single_video_proj_123_1_1-deadbeefdeadbeef"
    refreshed = NewApiVideoGenerator._refresh_submit_idempotency_key(
        original, attempt=1
    )
    assert refreshed != original
    assert 16 <= len(refreshed) <= 128
    assert all(char.isalnum() or char in "_-" for char in refreshed)
    long_key = "k" * 200
    assert len(
        NewApiVideoGenerator._refresh_submit_idempotency_key(long_key, attempt=2)
    ) <= 128


@pytest.mark.asyncio
async def test_conflict_retries_once_with_a_fresh_key(tmp_path: Path) -> None:
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://gateway.invalid/v1",
        model="minimax_h3",
        resolution="2k",
        preserve_upstream_model=True,
        allow_result_gateway_fallback=False,
    )
    conflict = NewApiVideoError(
        "Village Infinite Canvas API submit failed: HTTP 400 - "
        '{"error":{"message":"Idempotency-Key was already used for a '
        'different request (2013)"}}',
        http_status=400,
        stage="submit",
        url_path="/videos",
        response_text=(
            '{"error":{"message":"Idempotency-Key was already used for a '
            'different request (2013)"}}'
        ),
    )
    generator._post_json = AsyncMock(
        side_effect=[conflict, {"id": "task-after-key-refresh"}]
    )
    generator._get_json = AsyncMock(
        return_value={
            "id": "task-after-key-refresh",
            "status": "completed",
            "url": "data:video/mp4;base64,"
            + base64.b64encode(b"refreshed-key-result").decode(),
        }
    )
    submitted_key = "video-timeout-fixture-01"

    with (
        patch(
            "novelvideo.generators.video_generator.get_usage_meter",
            return_value=_UsageMeter(),
        ),
        patch(
            "novelvideo.generators.video_generator.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await generator.generate(
            image_path=None,
            prompt="idempotency conflict fixture",
            output_path=str(tmp_path / "conflict.mp4"),
            duration=6,
            poll_interval=0,
            max_polls=1,
            idempotency_key=submitted_key,
        )

    assert result.status is VideoGenStatus.DONE
    keys = [
        call.kwargs["idempotency_key"]
        for call in generator._post_json.await_args_list
    ]
    assert keys[0] == submitted_key
    assert keys[1] != submitted_key
    assert (tmp_path / "conflict.mp4").read_bytes() == b"refreshed-key-result"
