from __future__ import annotations

from novelvideo.services.delivery_video import (
    DELIVERY_COLOR_PRIMARIES,
    DELIVERY_COLOR_SPACE,
    DELIVERY_COLOR_TRANSFER,
    delivery_color_params_filter,
)


def test_delivery_color_filter_uses_one_bt709_contract() -> None:
    assert delivery_color_params_filter() == (
        f"setparams=colorspace={DELIVERY_COLOR_SPACE}:"
        f"color_primaries={DELIVERY_COLOR_PRIMARIES}:"
        f"color_trc={DELIVERY_COLOR_TRANSFER}"
    )
