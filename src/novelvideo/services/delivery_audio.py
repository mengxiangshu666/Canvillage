"""Shared delivery-audio contracts for final mix and QC."""

from __future__ import annotations

from typing import Any


DELIVERY_INTEGRATED_LUFS = -16.0
DELIVERY_TRUE_PEAK_DBTP = -2.0
DELIVERY_LOUDNESS_RANGE_LU = 11.0
DELIVERY_LOUDNESS_TOLERANCE_LU = 4.0
DELIVERY_LOUDNESS_SAFETY_TRIM_DB = -0.5
DELIVERY_AUDIO_SAMPLE_RATE_HZ = 48000
DELIVERY_AUDIO_CHANNEL_LAYOUT = "stereo"


def delivery_loudnorm_filter(
    *,
    include_safety_trim: bool = True,
    print_format: str | None = None,
) -> str:
    """Return the final audio-bus loudness filter used before AAC encoding."""

    loudnorm = (
        f"loudnorm=I={DELIVERY_INTEGRATED_LUFS:g}:"
        f"TP={DELIVERY_TRUE_PEAK_DBTP:g}:"
        f"LRA={DELIVERY_LOUDNESS_RANGE_LU:g}"
    )
    if print_format:
        loudnorm += f":print_format={print_format}"
    filter_value = loudnorm
    if include_safety_trim:
        # FFmpeg's loudnorm filter upsamples ordinary stereo input to 192 kHz.
        # Re-anchor the delivery bus to the AAC-safe format before encoding.
        filter_value += (
            f",volume={DELIVERY_LOUDNESS_SAFETY_TRIM_DB:g}dB"
            f",aresample={DELIVERY_AUDIO_SAMPLE_RATE_HZ}"
            ",aformat=sample_fmts=fltp:"
            f"sample_rates={DELIVERY_AUDIO_SAMPLE_RATE_HZ}:"
            f"channel_layouts={DELIVERY_AUDIO_CHANNEL_LAYOUT}"
            ",aeval='if(isnan(val(0)),0,val(0))|"
            "if(isnan(val(1)),0,val(1))'"
        )
    return filter_value


def delivery_loudness_target() -> dict[str, Any]:
    return {
        "integrated_lufs": DELIVERY_INTEGRATED_LUFS,
        "loudness_tolerance": DELIVERY_LOUDNESS_TOLERANCE_LU,
        "true_peak_db": DELIVERY_TRUE_PEAK_DBTP,
        "loudness_range_lu": DELIVERY_LOUDNESS_RANGE_LU,
    }


__all__ = [
    "DELIVERY_INTEGRATED_LUFS",
    "DELIVERY_AUDIO_CHANNEL_LAYOUT",
    "DELIVERY_AUDIO_SAMPLE_RATE_HZ",
    "DELIVERY_LOUDNESS_RANGE_LU",
    "DELIVERY_LOUDNESS_SAFETY_TRIM_DB",
    "DELIVERY_LOUDNESS_TOLERANCE_LU",
    "DELIVERY_TRUE_PEAK_DBTP",
    "delivery_loudnorm_filter",
    "delivery_loudness_target",
]
