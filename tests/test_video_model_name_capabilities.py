# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""Relay model-name capability parsing and its effect on the video contract.

Relay stations publish no capability fields in ``GET /models``; the contract is
written into the model *name* instead (``S-2.5-10图-内置过脸``).  These tests
pin the parsing rules and — just as importantly — pin that every vendor-named
model keeps the exact contract it had before this parsing existed.
"""

from __future__ import annotations

import pytest

from novelvideo.generators.video.capabilities import VideoMode
from novelvideo.generators.video.direct_video_profiles import (
    _GENERIC_DIRECT_VIDEO_PROFILE,
    resolve_direct_video_profile,
)
from novelvideo.generators.video.model_name_capabilities import (
    parse_model_name_capabilities,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_reads_reference_image_count_from_chinese_segment() -> None:
    parsed = parse_model_name_capabilities("S-2.5-10图-内置过脸")

    assert parsed.reference_images == 10
    assert parsed.supports_face_handling is True
    assert parsed.declares_reference_mode is True


def test_reads_every_declared_segment_together() -> None:
    parsed = parse_model_name_capabilities("S-2.5-30秒-10图-3视频-2音频-内置过脸")

    assert parsed.reference_images == 10
    assert parsed.reference_videos == 3
    assert parsed.reference_audios == 2
    assert parsed.fixed_duration_seconds == 30
    assert parsed.supports_face_handling is True


@pytest.mark.parametrize(
    "name",
    [
        "10图",
        "10 张图",
        "支持10张参考图",
        "全能参考10图",
        "某模型-5幅图",
    ],
)
def test_accepts_the_common_ways_a_count_is_written(name: str) -> None:
    assert parse_model_name_capabilities(name).reference_images is not None


def test_name_without_capability_segments_declares_nothing() -> None:
    parsed = parse_model_name_capabilities("S-2.5")

    assert parsed.is_empty
    assert parsed.reference_images is None
    assert parsed.supports_face_handling is False


def test_empty_and_blank_names_are_safe() -> None:
    for value in ("", "   ", None):
        assert parse_model_name_capabilities(value).is_empty


# ---------------------------------------------------------------------------
# Vendor names must not be disturbed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vendor_name",
    [
        "sd2.0-480p",
        "sd2.0-fast",
        "sd-2.5-M-720P-v1",
        "jimeng-seedance-2.5",
        "doubao-seedance-1-5-pro-251015",
        "seedance-2.5",
        "minimax-h3",
        "mini-h3",
        "kling-v3-omni-v2v-create",
        "s-videos-f-933-fast-480-2",
        "veo-3.1",
        "firefly-seedance2-fast-480p",
        "s-2.5db",
        "gemini-omni-flash",
    ],
)
def test_version_numbers_are_never_read_as_reference_counts(vendor_name: str) -> None:
    """``sd2.0``/``h3``/``933`` must not become capability declarations.

    The numeric patterns are anchored to CJK classifiers precisely so a version
    or resolution number cannot be mistaken for a reference-image count.
    """

    parsed = parse_model_name_capabilities(vendor_name)

    assert parsed.reference_images is None
    assert parsed.reference_videos is None
    assert parsed.reference_audios is None
    assert parsed.fixed_duration_seconds is None
    assert parsed.supports_face_handling is False


def test_vendor_named_models_keep_their_declared_profile() -> None:
    """A name with no capability segments resolves exactly as before."""

    profile = resolve_direct_video_profile("sd-2.5-M-720P-v1")

    assert profile.name == "sd-2.5-720p-v1"
    assert profile.reference_limits.reference_images == 30
    assert profile.reference_limits.reference_videos == 10
    assert profile.duration == (5, 10, 15, 30)


def test_unmatched_vendor_name_still_falls_back_to_generic() -> None:
    profile = resolve_direct_video_profile("some-unknown-vendor-model")

    assert profile.name == _GENERIC_DIRECT_VIDEO_PROFILE.name


# ---------------------------------------------------------------------------
# Effect on the resolved contract
# ---------------------------------------------------------------------------


def test_declared_reference_count_replaces_the_generic_fallback() -> None:
    """The reported defect: ``10图`` used to be served by the 1-image default."""

    profile = resolve_direct_video_profile("S-2.5-10图-内置过脸")

    assert profile.reference_limits.reference_images == 10
    assert VideoMode.REFERENCE_TO_VIDEO in profile.modes


def test_a_larger_declared_count_wins_over_a_smaller_family_profile() -> None:
    """A name that spells out more must never be capped by the family default.

    ``seedance`` matches a family profile with 9 reference images; the relay
    name declares 15, so the picker must offer 15.
    """

    profile = resolve_direct_video_profile("seedance-2.5-15图")

    assert profile.reference_limits.reference_images == 15


def test_declared_count_never_shrinks_an_existing_family_contract() -> None:
    """A name declaring fewer than the family profile must not reduce it."""

    profile = resolve_direct_video_profile("sd-2.5-M-720P-v1-3图")

    assert profile.reference_limits.reference_images == 30


def test_fixed_duration_segment_pins_one_legal_duration() -> None:
    profile = resolve_direct_video_profile("S-2.5-30秒-10图")

    assert profile.duration == (30,)
    assert profile.default_duration_seconds == 30


def test_face_handling_alone_does_not_invent_reference_slots() -> None:
    """``内置过脸`` is an annotation; it must not fabricate a reference count."""

    profile = resolve_direct_video_profile("S-2.5-内置过脸")

    assert profile.reference_limits.reference_images == 0
    assert VideoMode.REFERENCE_TO_VIDEO not in profile.modes


def test_declared_reference_mode_always_keeps_an_input_slot() -> None:
    """Every offered mode must have the transport slot it requires."""

    profile = resolve_direct_video_profile("S-2.5-10图")

    assert profile.reference_limits.input_images >= 1
    assert VideoMode.TEXT_TO_VIDEO in profile.modes
    assert VideoMode.IMAGE_TO_VIDEO in profile.modes


def test_first_last_frame_segment_adds_the_two_image_slot() -> None:
    profile = resolve_direct_video_profile("seedance-2.5-9图-首尾帧")

    assert VideoMode.FIRST_LAST_FRAME in profile.modes
    assert profile.reference_limits.input_images >= 2


def test_absurd_declared_counts_are_clamped() -> None:
    """A typo in a relay name must not publish an unusable picker."""

    parsed = parse_model_name_capabilities("999图")

    assert parsed.reference_images == 100


# ---------------------------------------------------------------------------
# Capability written into the display name instead of the model ID
# ---------------------------------------------------------------------------


def test_reads_capability_from_display_name_when_id_is_opaque() -> None:
    """A station may keep the ID opaque and put the contract in the label.

    ``minimax_h3_zm_u24`` says nothing; ``H3多图多音频生视频15秒`` says the
    model is a fixed 15-second generator.
    """

    profile = resolve_direct_video_profile(
        "minimax_h3_zm_u24",
        display_name="H3多图多音频生视频15秒",
    )

    assert profile.duration == (15,)
    assert profile.default_duration_seconds == 15


def test_display_name_is_ignored_when_it_declares_nothing() -> None:
    plain = resolve_direct_video_profile("minimax_h3_zm_u24")
    labelled = resolve_direct_video_profile(
        "minimax_h3_zm_u24",
        display_name="H3 视频",
    )

    assert labelled == plain


def test_display_name_cannot_shrink_a_contract_declared_by_the_id() -> None:
    """The two names merge; a smaller count in the label never reduces it."""

    profile = resolve_direct_video_profile(
        "S-2.5-10图",
        display_name="S-2.5-3图",
    )

    assert profile.reference_limits.reference_images == 10


def test_display_name_can_add_a_count_the_id_omits() -> None:
    profile = resolve_direct_video_profile(
        "opaque-relay-id-7",
        display_name="某模型-8图-全能参考",
    )

    assert profile.reference_limits.reference_images == 8
    assert VideoMode.REFERENCE_TO_VIDEO in profile.modes


def test_count_free_multi_reference_wording_is_recognized() -> None:
    """``多图`` declares reference support even without a number."""

    parsed = parse_model_name_capabilities("H3多图多音频生视频")

    assert parsed.declares_reference_mode is True
    assert parsed.reference_images is None
