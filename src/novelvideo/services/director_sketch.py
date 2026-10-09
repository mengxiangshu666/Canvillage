"""Task-facing facade for the Director control-frame sketch bridge."""

from __future__ import annotations

from typing import Any


async def convert_control_frame_to_sketch(**kwargs: Any) -> dict[str, Any]:
    """Delegate the conversion while keeping task code implementation-neutral."""

    from novelvideo.director_world.control_frame_to_sketch import (
        convert_control_frame_to_sketch as convert,
    )

    return await convert(**kwargs)


__all__ = ["convert_control_frame_to_sketch"]
