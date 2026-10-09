from __future__ import annotations

from dataclasses import dataclass
import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.services.video_dispatch import dispatch_video_generation


@dataclass
class _Result:
    status: str
    error: str = ""


class _Generator:
    def __init__(self, backend: str, calls: list[tuple[str, dict]]) -> None:
        self.backend = backend
        self.calls = calls

    async def generate(self, **kwargs):
        self.calls.append((self.backend, kwargs))
        return _Result("done" if self.backend == "fallback" else "failed")


@pytest.mark.asyncio
async def test_dispatch_uses_one_explicit_fallback_and_reports_effective_backend():
    created: list[str] = []
    calls: list[tuple[str, dict]] = []

    def create_generator(*, backend: str, **_kwargs):
        created.append(backend)
        return _Generator(backend, calls)

    outcome = await dispatch_video_generation(
        create_generator=create_generator,
        backend="primary",
        generator_kwargs={"resolution": "720p"},
        generate_kwargs={"prompt": "shot"},
        fallback_selector=lambda result: "fallback" if result.status == "failed" else "",
        fallback_generate_kwargs={"prompt": "shot", "idempotency_key": "fallback-key"},
    )

    assert created == ["primary", "fallback"]
    assert calls[0][1] == {"prompt": "shot"}
    assert calls[1][1]["idempotency_key"] == "fallback-key"
    assert outcome.effective_backend == "fallback"
    assert outcome.fallback_from == "primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,fallback", [("done", "backup"), ("failed", "")])
async def test_dispatch_never_submits_backup_for_success_or_without_authorization(status, fallback):
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        return _Result(status)

    def create_generator(**kwargs):
        assert kwargs == {"backend": "primary", "resolution": "720p"}
        return SimpleNamespace(generate=generate)

    def on_fallback(_backend):
        pytest.fail("no backup submission was authorized or necessary")

    outcome = await dispatch_video_generation(
        create_generator=create_generator, backend="primary",
        generator_kwargs={"resolution": "720p"}, generate_kwargs={"idempotency_key": "same-key"},
        fallback_backend=fallback, on_fallback=on_fallback,
    )
    assert calls == [{"idempotency_key": "same-key"}]
    assert outcome.result.status == status
    assert outcome.effective_backend == "primary"
    assert outcome.fallback_from == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("async_notification", [False, True])
async def test_failed_backup_stops_after_two_attempts_and_awaits_notification(async_notification):
    events = []

    def create_generator(*, backend, **kwargs):
        events.append(("create", backend, kwargs))

        async def generate(**payload):
            events.append(("submit", backend, payload))
            return _Result("failed", error=backend)

        return SimpleNamespace(generate=generate)

    def notify(backend):
        events.append(("notice", backend))

    async def async_notify(backend):
        await asyncio.sleep(0)
        notify(backend)

    outcome = await dispatch_video_generation(
        create_generator=create_generator, backend="primary",
        generator_kwargs={"resolution": "720p"}, generate_kwargs={"idempotency_key": "primary-key"},
        fallback_backend="backup", fallback_generator_kwargs={"resolution": "480p"},
        fallback_generate_kwargs={"idempotency_key": "backup-key"},
        on_fallback=async_notify if async_notification else notify,
    )
    assert events == [
        ("create", "primary", {"resolution": "720p"}),
        ("submit", "primary", {"idempotency_key": "primary-key"}),
        ("notice", "backup"),
        ("create", "backup", {"resolution": "480p"}),
        ("submit", "backup", {"idempotency_key": "backup-key"}),
    ]
    assert outcome.result.error == "backup"
    assert outcome.effective_backend == "primary"
    assert outcome.fallback_from == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("ambiguous upstream acceptance"), asyncio.CancelledError()])
async def test_exception_or_cancellation_does_not_automatically_retry_paid_submission(error):
    created = []

    def create_generator(*, backend, **_kwargs):
        created.append(backend)

        async def generate(**_payload):
            raise error

        return SimpleNamespace(generate=generate)

    with pytest.raises(type(error)) as observed:
        await dispatch_video_generation(
            create_generator=create_generator, backend="primary", generator_kwargs={},
            generate_kwargs={}, fallback_backend="backup",
        )
    assert observed.value is error
    assert created == ["primary"]
