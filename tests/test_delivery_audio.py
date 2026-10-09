from __future__ import annotations

from novelvideo.services.delivery_audio import (
    DELIVERY_AUDIO_CHANNEL_LAYOUT,
    DELIVERY_AUDIO_SAMPLE_RATE_HZ,
    DELIVERY_INTEGRATED_LUFS,
    DELIVERY_LOUDNESS_RANGE_LU,
    DELIVERY_LOUDNESS_SAFETY_TRIM_DB,
    DELIVERY_LOUDNESS_TOLERANCE_LU,
    DELIVERY_TRUE_PEAK_DBTP,
    delivery_loudnorm_filter,
    delivery_loudness_target,
)


def test_delivery_loudness_target_has_a_single_source() -> None:
    assert delivery_loudness_target() == {
        "integrated_lufs": DELIVERY_INTEGRATED_LUFS,
        "loudness_tolerance": DELIVERY_LOUDNESS_TOLERANCE_LU,
        "true_peak_db": DELIVERY_TRUE_PEAK_DBTP,
        "loudness_range_lu": DELIVERY_LOUDNESS_RANGE_LU,
    }


def test_delivery_loudnorm_filter_restores_aac_safe_format() -> None:
    filter_value = delivery_loudnorm_filter()

    assert "loudnorm=I=-16:TP=-2:LRA=11" in filter_value
    assert f"volume={DELIVERY_LOUDNESS_SAFETY_TRIM_DB:g}dB" in filter_value
    assert f"aresample={DELIVERY_AUDIO_SAMPLE_RATE_HZ}" in filter_value
    assert (
        "aformat=sample_fmts=fltp:"
        f"sample_rates={DELIVERY_AUDIO_SAMPLE_RATE_HZ}:"
        f"channel_layouts={DELIVERY_AUDIO_CHANNEL_LAYOUT}"
    ) in filter_value
    assert (
        "aeval='if(isnan(val(0)),0,val(0))|"
        "if(isnan(val(1)),0,val(1))'"
    ) in filter_value


def test_measurement_filter_keeps_the_source_bus_unchanged() -> None:
    filter_value = delivery_loudnorm_filter(
        include_safety_trim=False,
        print_format="json",
    )

    assert filter_value == "loudnorm=I=-16:TP=-2:LRA=11:print_format=json"
