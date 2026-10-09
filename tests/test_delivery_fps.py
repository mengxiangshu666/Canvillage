from __future__ import annotations

import pytest

from novelvideo.services import delivery_fps


def test_delivery_fps_prefers_explicit_spec_then_requested_then_server_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novelvideo.config.get_video_config",
        lambda: {"fps": 25},
    )

    explicit = delivery_fps.resolve_delivery_fps(
        {"fps": 24},
        requested_fps=30,
    )
    requested = delivery_fps.resolve_delivery_fps(requested_fps=30)
    fallback = delivery_fps.resolve_delivery_fps()

    assert explicit == {
        "schema": "delivery_fps_contract.v1",
        "fps": 24,
        "source": "delivery_spec",
    }
    assert requested["fps"] == 30
    assert requested["source"] == "requested_fps"
    assert fallback["fps"] == 25
    assert fallback["source"] == "server_default"
    assert (
        delivery_fps.resolve_delivery_fps(fallback_to_server=False)
        is None
    )


@pytest.mark.parametrize(
    "value",
    [True, False, 0, -1, 1.5, 241, "nan", "infinity", "not-a-number"],
)
def test_delivery_fps_rejects_invalid_explicit_values(value: object) -> None:
    with pytest.raises(ValueError, match="delivery_fps_invalid"):
        delivery_fps.resolve_delivery_fps(requested_fps=value)


def test_delivery_fps_ignores_malformed_spec_without_hiding_requested_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novelvideo.config.get_video_config",
        lambda: {"fps": 30},
    )

    resolved = delivery_fps.resolve_delivery_fps({}, requested_fps=24)

    assert resolved["fps"] == 24
    assert resolved["source"] == "requested_fps"


def test_persisted_delivery_fps_receipt_is_projected_without_resolving() -> None:
    receipt = delivery_fps.project_delivery_fps_receipt(
        {
            "schema": "delivery_fps_contract.v1",
            "fps": 24,
            "source": "delivery_spec",
        }
    )

    assert receipt == {
        "schema": "delivery_fps_contract.v1",
        "fps": 24,
        "source": "delivery_spec",
    }
    assert delivery_fps.project_delivery_fps_receipt({"fps": 24}) is None
