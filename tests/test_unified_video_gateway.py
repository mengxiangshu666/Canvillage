from __future__ import annotations

from novelvideo import config
from novelvideo.model_gateway_settings import EffectiveNewApiConfig, MODE_UNIFIED


def test_unified_gateway_is_the_only_video_submit_candidate(
    monkeypatch,
) -> None:
    """A migrated canary must not retry stale official/custom credentials."""

    unified = EffectiveNewApiConfig(
        mode=MODE_UNIFIED,
        source="unified-environment",
        base_url="https://gateway.example/v1",
        api_key="test-key",
    )
    observed_modes: list[str] = []

    def resolve_gateway(mode: str) -> EffectiveNewApiConfig:
        observed_modes.append(mode)
        return unified

    import novelvideo.model_gateway_settings as gateway_settings

    monkeypatch.setenv("VILLAGE_CANVAS_GATEWAY_OFFICIAL_FIRST", "false")
    monkeypatch.setenv("VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY", "false")
    monkeypatch.setattr(config, "get_effective_newapi_gateway_config", lambda: unified)
    monkeypatch.setattr(gateway_settings, "get_ce_newapi_config_for_mode", resolve_gateway)

    assert config.get_newapi_video_runtime_gateway_candidates(
        include_dedicated_gateway=False
    ) == [
        {
            "name": "unified",
            "source": "unified-environment",
            "mode": MODE_UNIFIED,
            "api_key": "test-key",
            "base_url": "https://gateway.example/v1",
        }
    ]
    assert observed_modes == [MODE_UNIFIED]
