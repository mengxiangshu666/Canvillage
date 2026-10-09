from __future__ import annotations


def test_private_gateway_urls_bypass_environment_proxy() -> None:
    from novelvideo.gateway_transport import (
        is_private_gateway_url,
        newapi_httpx_client_kwargs,
    )

    for url in (
        "http://10.66.66.1:3000/v1",
        "http://127.0.0.1:8781/v1",
        "http://[::1]:3000/v1",
        "http://localhost:3000/v1",
    ):
        assert is_private_gateway_url(url)
        kwargs = newapi_httpx_client_kwargs(base_url=url, timeout=12)
        assert kwargs["trust_env"] is False
        assert str(kwargs["headers"]["User-Agent"]).startswith("Mozilla/5.0")


def test_public_gateway_urls_keep_environment_proxy_compatibility() -> None:
    from novelvideo.gateway_transport import (
        is_private_gateway_url,
        newapi_httpx_client_kwargs,
    )

    url = "https://api.example.test/v1"
    assert not is_private_gateway_url(url)
    kwargs = newapi_httpx_client_kwargs(base_url=url, timeout=12)
    assert kwargs["trust_env"] is True
    assert str(kwargs["headers"]["User-Agent"]).startswith("Mozilla/5.0")


def test_gateway_user_agent_can_be_pinned(monkeypatch) -> None:
    from novelvideo.gateway_transport import newapi_httpx_client_kwargs

    monkeypatch.setenv(
        "VILLAGE_CANVAS_OPENAI_COMPAT_USER_AGENT",
        "OpenAI/Python 2.43.0",
    )
    kwargs = newapi_httpx_client_kwargs(
        base_url="https://api.example.test/v1",
        timeout=12,
    )
    assert kwargs["headers"]["User-Agent"] == "OpenAI/Python 2.43.0"
