"""Shared delivery-video contracts for final compose and QC."""

from __future__ import annotations


DELIVERY_COLOR_SPACE = "bt709"
DELIVERY_COLOR_PRIMARIES = "bt709"
DELIVERY_COLOR_TRANSFER = "bt709"


def delivery_color_params_filter() -> str:
    """Return the filter that stamps encoded frames with the delivery color tags."""

    return (
        f"setparams=colorspace={DELIVERY_COLOR_SPACE}:"
        f"color_primaries={DELIVERY_COLOR_PRIMARIES}:"
        f"color_trc={DELIVERY_COLOR_TRANSFER}"
    )


__all__ = [
    "DELIVERY_COLOR_PRIMARIES",
    "DELIVERY_COLOR_SPACE",
    "DELIVERY_COLOR_TRANSFER",
    "delivery_color_params_filter",
]
