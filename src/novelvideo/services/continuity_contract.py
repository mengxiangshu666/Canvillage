"""Neutral facade for the provider-independent storyboard continuity contract."""

from __future__ import annotations

from typing import Any


def compile_storyboard_continuity(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from novelvideo.production.continuity_contract import (
        compile_storyboard_continuity as compile_contract,
    )

    return compile_contract(*args, **kwargs)


def lint_storyboard_continuity(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from novelvideo.production.continuity_contract import (
        lint_storyboard_continuity as lint_contract,
    )

    return lint_contract(*args, **kwargs)


def shot_transition(*args: Any, **kwargs: Any) -> str:
    from novelvideo.production.continuity_contract import shot_transition as resolve

    return resolve(*args, **kwargs)


def transition_preserves_frame(value: object) -> bool:
    from novelvideo.production.continuity_contract import transition_preserves_frame as preserves

    return preserves(value)


__all__ = [
    "compile_storyboard_continuity", "lint_storyboard_continuity",
    "shot_transition", "transition_preserves_frame",
]
