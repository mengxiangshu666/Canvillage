"""Shared provider dispatch mechanics for video submissions.

Callers decide whether a fallback is allowed and which backend to use. This
module only owns generator creation, one primary attempt, and one explicit
fallback attempt so those mechanics stay identical across entry points.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VideoDispatchOutcome:
    result: Any
    effective_backend: str
    fallback_from: str = ""


async def dispatch_video_generation(
    *,
    create_generator: Callable[..., Any],
    backend: str,
    generator_kwargs: Mapping[str, Any],
    generate_kwargs: Mapping[str, Any],
    fallback_backend: str = "",
    fallback_selector: Callable[[Any], str] | None = None,
    fallback_generator_kwargs: Mapping[str, Any] | None = None,
    fallback_generate_kwargs: Mapping[str, Any] | None = None,
    on_fallback: Callable[[str], object] | None = None,
) -> VideoDispatchOutcome:
    """Submit a video once, then optionally retry through one chosen backend.

    Fallback selection is intentionally outside this helper. It must be an
    explicit, already-authorized decision from the caller so this function
    cannot silently introduce a second paid submission path.
    """

    primary = create_generator(backend=backend, **dict(generator_kwargs))
    result = await primary.generate(**dict(generate_kwargs))
    selected_fallback = str(
        fallback_selector(result) if fallback_selector is not None else fallback_backend
    ).strip()
    if not selected_fallback or getattr(result.status, "value", result.status) == "done":
        return VideoDispatchOutcome(result=result, effective_backend=backend)

    if on_fallback is not None:
        message_result = on_fallback(selected_fallback)
        if isinstance(message_result, Awaitable):
            await message_result
    fallback = create_generator(
        backend=selected_fallback,
        **dict(fallback_generator_kwargs or generator_kwargs),
    )
    fallback_result = await fallback.generate(
        **dict(fallback_generate_kwargs or generate_kwargs)
    )
    if getattr(fallback_result.status, "value", fallback_result.status) == "done":
        return VideoDispatchOutcome(
            result=fallback_result,
            effective_backend=selected_fallback,
            fallback_from=backend,
        )
    return VideoDispatchOutcome(result=fallback_result, effective_backend=backend)


__all__ = ["VideoDispatchOutcome", "dispatch_video_generation"]
