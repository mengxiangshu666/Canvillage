"""Regression coverage for create-route discovery on NewAPI video gateways.

Discovery only reads ``GET /models``, so a relay's registered create route is
invisible until a submission proves it: some NewAPI stations serve video
creation from ``/v1/video/generations`` while the OpenAI-compatible default is
``/v1/videos``.  A refused route used to be followed silently, and aiohttp
rewrites ``POST`` to ``GET`` on 301/302 and drops the body — so the gateway
answered an unfollowed GET for a path the caller never requested and the real
cause (a moved create route) surfaced as a bare 404.
"""

from __future__ import annotations

import base64
import threading
import time
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import pytest

from novelvideo import config
from novelvideo.generators.video.direct_video_capability_cache import (
    get_cached_capability_for_model,
    record_capability,
    record_runtime_resolution_rejection,
    record_runtime_submit_route,
)
from novelvideo.generators.video.newapi_video_diagnostics import NewApiVideoError
from novelvideo.generators.video.direct_video_h3_routing import (
    h3_safe_resolution,
    is_h3_size_rejection,
)
from novelvideo.generators.video_generator import (
    NewApiVideoGenerator,
    VideoGenStatus,
)


#: Placeholder for a gateway that never leaves this process.  The constructor
#: only requires a non-empty string to assemble its candidate list.
_GATEWAY_FIXTURE = "fixture-key"
_DEFAULT_ROUTE = "/videos"
_ALTERNATE_ROUTE = "/video/generations"


class _UsageMeter:
    async def reserve_current_model_call_credit(self, **_kwargs):
        return "reservation"

    async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
        return None

    async def bump_model_call(self, **_kwargs):
        return None


def _generator(**overrides) -> NewApiVideoGenerator:
    options = {
        "api_key": _GATEWAY_FIXTURE,
        "endpoint": "https://gateway.invalid/v1",
        "model": "sd-2.0-fast-v1",
        "resolution": "720p",
        "protocol": "openai-video",
        "create_path": _DEFAULT_ROUTE,
        "preserve_upstream_model": True,
        "allow_result_gateway_fallback": False,
    }
    options.update(overrides)
    return NewApiVideoGenerator(**options)


def _route_refused(status: int | None, *, location: str = "") -> NewApiVideoError:
    return NewApiVideoError(
        f"视频提交路由被拒绝：HTTP {status}",
        http_status=status,
        response_text="",
        stage="submit",
        url_path=_DEFAULT_ROUTE,
        redirect_location=location,
    )


def _completed_payload() -> dict[str, object]:
    return {
        "status": "completed",
        "url": "data:video/mp4;base64," + base64.b64encode(b"ok").decode(),
    }


def _native_completed_payload() -> dict[str, object]:
    return {
        "task": {
            "status": "succeeded",
            "content": {"url": "data:video/mp4;base64," + base64.b64encode(b"ok").decode()},
        }
    }


def _h3_size_rejection(detail: str) -> NewApiVideoError:
    body = '{"error":{"http_code":"400","message":"%s","type":"bad_request_error"}}' % detail
    return NewApiVideoError(
        f"视频提交失败：HTTP 400 - {detail}",
        http_status=400,
        response_text=body,
        stage="submit",
        url_path="/video_generation",
    )


def _submitted_payloads(generator: NewApiVideoGenerator) -> list[dict[str, object]]:
    return [call.args[1] for call in generator._post_json.await_args_list]


async def _run(generator: NewApiVideoGenerator, tmp_path):
    with patch(
        "novelvideo.generators.video_generator.get_usage_meter",
        return_value=_UsageMeter(),
    ):
        return await generator.generate(
            image_path=None,
            prompt="route fixture",
            output_path=str(tmp_path / "route.mp4"),
            duration=10,
            poll_interval=0,
            max_polls=1,
        )


def _submitted_urls(generator: NewApiVideoGenerator) -> list[str]:
    return [call.args[0] for call in generator._post_json.await_args_list]


def _submitted_keys(generator: NewApiVideoGenerator) -> list[str]:
    return [call.kwargs["idempotency_key"] for call in generator._post_json.await_args_list]


@pytest.mark.asyncio
async def test_refused_create_route_walks_to_the_registered_one(
    monkeypatch, tmp_path
) -> None:
    """A 301 on ``/videos`` must fall through to ``/video/generations``."""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = _generator()
    generator._post_json = AsyncMock(
        side_effect=[_route_refused(301), {"id": "task-1"}]
    )
    generator._get_json = AsyncMock(return_value=_completed_payload())

    result = await _run(generator, tmp_path)

    assert result.status is VideoGenStatus.DONE
    assert _submitted_urls(generator) == [
        "https://gateway.invalid/v1/videos",
        "https://gateway.invalid/v1/video/generations",
    ]
    # The refused route never reached a create handler, so no task can exist
    # under a second key; reusing it is what makes the walk safe.
    keys = _submitted_keys(generator)
    assert keys[0] == keys[1] and keys[0]


@pytest.mark.asyncio
async def test_no_route_switch_when_the_submit_result_is_unknown(
    monkeypatch, tmp_path
) -> None:
    """A missing submit response may mean the task was already accepted.

    Walking to another route there could create a second paid render, so the
    existing "result unknown" policy must outrank route discovery.
    """

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS",
        0.0,
    )
    generator = _generator()
    generator._post_json = AsyncMock(side_effect=_route_refused(None))

    result = await _run(generator, tmp_path)

    assert result.status is VideoGenStatus.FAILED
    assert set(_submitted_urls(generator)) == {"https://gateway.invalid/v1/videos"}
    assert result.error_metadata["error_code"] == "VIDEO_SUBMIT_RESULT_UNKNOWN"


@pytest.mark.asyncio
async def test_accepted_route_is_remembered_for_the_next_submit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = _generator(cache_runtime_contract=True)
    generator._post_json = AsyncMock(
        side_effect=[_route_refused(404), {"id": "task-2"}]
    )
    generator._get_json = AsyncMock(return_value=_completed_payload())

    await _run(generator, tmp_path)

    transport = get_cached_capability_for_model(
        base_url="https://gateway.invalid/v1",
        upstream_model="sd-2.0-fast-v1",
    )["transportContract"]
    assert transport["submit"] == _ALTERNATE_ROUTE
    assert transport["submitObserved"] is True
    assert generator._submit_route_paths()[0] == _ALTERNATE_ROUTE

    # A later submission pays no refused hop: the measured route goes first.
    fresh = _generator(cache_runtime_contract=True)
    fresh._post_json = AsyncMock(return_value={"id": "task-3"})
    fresh._get_json = AsyncMock(return_value=_completed_payload())
    await _run(fresh, tmp_path)

    assert _submitted_urls(fresh)[0] == (
        "https://gateway.invalid/v1/video/generations"
    )


@pytest.mark.asyncio
async def test_every_route_refused_reports_the_routes_that_were_walked(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = _generator()
    generator._post_json = AsyncMock(side_effect=_route_refused(404))

    result = await _run(generator, tmp_path)

    assert result.status is VideoGenStatus.FAILED
    metadata = result.error_metadata
    assert metadata["error_code"] == "VIDEO_ENDPOINT_NOT_FOUND"
    assert metadata["attempted_routes"] == [_DEFAULT_ROUTE, _ALTERNATE_ROUTE]
    # The old advice was a dead loop: re-probing only re-reads GET /models.
    assert _DEFAULT_ROUTE in metadata["suggested_action"]
    assert _ALTERNATE_ROUTE in metadata["suggested_action"]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 404, 405])
def test_refusal_statuses_are_the_ones_that_cannot_have_created_a_task(
    status: int,
) -> None:
    assert NewApiVideoGenerator._submit_route_is_refused(_route_refused(status))


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, None])
def test_other_statuses_keep_their_existing_diagnosis(status: int | None) -> None:
    assert not NewApiVideoGenerator._submit_route_is_refused(_route_refused(status))


def test_declared_primary_route_is_unchanged_for_every_caller(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = _generator()

    assert generator.create_path == _DEFAULT_ROUTE
    assert generator._submit_route_paths() == (_DEFAULT_ROUTE, _ALTERNATE_ROUTE)


def test_a_measured_route_reorders_but_never_replaces_the_declared_set(
    monkeypatch, tmp_path
) -> None:
    """Cache evidence may only move a route forward, never strand the walk."""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_runtime_submit_route(
        base_url="https://gateway.invalid/v1",
        protocol="openai-video",
        upstream_model="sd-2.0-fast-v1",
        submit_path=_ALTERNATE_ROUTE,
    )

    assert _generator(cache_runtime_contract=True)._submit_route_paths() == (
        _ALTERNATE_ROUTE,
        _DEFAULT_ROUTE,
    )


def test_runtime_route_and_resolution_updates_are_one_atomic_merge(
    monkeypatch, tmp_path
) -> None:
    """A slow route write must not erase a resolution update that started beside it."""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    from novelvideo.generators.video import direct_video_capability_cache as cache

    base_url = "https://gateway.invalid/v1"
    upstream_model = "sd-2.0-fast-v1"
    record_capability(
        base_url=base_url,
        protocol="openai-video",
        upstream_model=upstream_model,
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "resolutionOptions": ["768p", "2k"],
        },
    )

    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    original_save = cache.save_capability_cache
    write_count = 0
    write_count_lock = threading.Lock()

    def blocking_save(payload):
        nonlocal write_count
        with write_count_lock:
            write_count += 1
            current_write = write_count
        if current_write == 1:
            first_write_entered.set()
            assert release_first_write.wait(5)
        original_save(payload)

    monkeypatch.setattr(cache, "save_capability_cache", blocking_save)
    failures: list[BaseException] = []

    def record_route() -> None:
        try:
            record_runtime_submit_route(
                base_url=base_url,
                protocol="openai-video",
                upstream_model=upstream_model,
                submit_path=_ALTERNATE_ROUTE,
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            failures.append(exc)

    def record_rejection() -> None:
        try:
            record_runtime_resolution_rejection(
                base_url=base_url,
                protocol="openai-video",
                upstream_model=upstream_model,
                resolution="2K",
                note="ComfyUI H3 rejects 2K",
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            failures.append(exc)

    route_thread = threading.Thread(target=record_route)
    rejection_thread = threading.Thread(target=record_rejection)
    route_thread.start()
    assert first_write_entered.wait(2)
    rejection_thread.start()
    # In the old read-merge-write shape the second thread could read stale
    # state and block on the write lock here; the fixed transaction blocks
    # before its read. Either way, this gives both threads time to overlap.
    time.sleep(0.05)
    release_first_write.set()
    route_thread.join(5)
    rejection_thread.join(5)

    assert not failures
    assert not route_thread.is_alive()
    assert not rejection_thread.is_alive()
    cached = get_cached_capability_for_model(
        base_url=base_url,
        upstream_model=upstream_model,
    )
    assert cached["transportContract"]["submit"] == _ALTERNATE_ROUTE
    assert cached["runtimeRejectedResolutionOptions"] == ["2k"]
    assert cached["resolutionOptions"] == ["768p"]


@pytest.mark.asyncio
async def test_submit_never_follows_a_refusal_redirect(monkeypatch) -> None:
    """Following the redirect would resend the body as a GET on a new route."""

    captured: dict[str, object] = {}
    secret = "".join(("se", "cret-in-location"))
    location = f"https://gateway.invalid/v1/videos/?api_key={secret}"

    class FakeResponse:
        status = 301
        headers = {"Location": location}

        async def text(self):
            return "<html>301</html>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.aiohttp.ClientSession",
        FakeSession,
    )
    generator = _generator()

    with pytest.raises(NewApiVideoError) as exc_info:
        await generator._post_json(
            "https://gateway.invalid/v1/videos",
            {"model": "sd-2.0-fast-v1", "prompt": "route fixture"},
            idempotency_key="job-1",
        )

    assert captured["allow_redirects"] is False
    error = exc_info.value
    assert error.http_status == 301
    assert secret not in error.redirect_location
    assert unquote(error.redirect_location).endswith("?api_key=***")

    contract = error.diagnostic_contract(protocol="openai-video")
    assert contract["error_code"] == "VIDEO_SUBMIT_ROUTE_REDIRECTED"
    assert secret not in str(contract["redirect_location"])


@pytest.mark.asyncio
async def test_native_h3_rejection_retries_with_the_768p_spelling(
    monkeypatch, tmp_path
) -> None:
    """The native MiniMax v2 service wants ``768P``, not the relay's ``768``."""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    generator = _generator(
        protocol="minimax-video-v2",
        resolution="2k",
        create_path="/video_generation",
        preserve_upstream_model=True,
    )
    generator._post_json = AsyncMock(
        side_effect=[
            _h3_size_rejection("resolution must be 768P"),
            {"task_id": "task-native-1"},
        ]
    )
    generator._get_json = AsyncMock(return_value=_native_completed_payload())

    result = await _run(generator, tmp_path)

    assert result.status is VideoGenStatus.DONE
    payloads = _submitted_payloads(generator)
    assert [item["resolution"] for item in payloads] == ["2K", "768P"]
    keys = _submitted_keys(generator)
    assert keys[0] == keys[1] and keys[0]
    assert _submitted_urls(generator) == ["https://gateway.invalid/v2/video_generation"] * 2


@pytest.mark.asyncio
async def test_plugin_backed_h3_promotes_native_v2_before_billing(
    monkeypatch, tmp_path
) -> None:
    """A direct H3 relay with a native route must never submit to the empty group."""

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_video_h3_routing.native_minimax_v2_available",
        lambda **_kwargs: True,
    )
    generator = _generator(
        model="MiniMax-H3",
        resolution="768p",
        cache_runtime_contract=True,
    )
    generator._post_json = AsyncMock(return_value={"task_id": "task-native-preflight"})
    generator._get_json = AsyncMock(return_value=_native_completed_payload())

    result = await _run(generator, tmp_path)

    assert result.status is VideoGenStatus.DONE
    assert generator.protocol == "minimax-video-v2"
    assert generator.create_path == "/video_generation"
    assert generator.query_path_template == (
        "/query/video_generation/{task_id}"
    )
    assert _submitted_urls(generator) == [
        "https://gateway.invalid/v2/video_generation"
    ]
    assert _submitted_payloads(generator)[0]["resolution"] == "768P"


def test_h3_size_rejection_recognizes_both_families() -> None:
    """The relay keeps ``768``; the native service requires ``768P``."""

    relay = _generator(resolution="2k")
    assert (
        is_h3_size_rejection(
            _h3_size_rejection("unsupported comfyui h3 size"),
            {"resolution": "2K", "version": "video.v1"},
        )
        is True
    )
    assert h3_safe_resolution(relay.protocol) == "768"

    native = _generator(protocol="minimax-video-v2", resolution="2k")
    assert (
        is_h3_size_rejection(
            _h3_size_rejection("resolution must be 768P"),
            {"resolution": "2K"},
        )
        is True
    )
    assert h3_safe_resolution(native.protocol) == "768P"
    # A request that never asked for the high-resolution tier is not this case.
    assert (
        is_h3_size_rejection(
            _h3_size_rejection("resolution must be 768P"),
            {"resolution": "768P"},
        )
        is False
    )
