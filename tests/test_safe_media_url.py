"""Outbound media URL guard tests."""

from __future__ import annotations

import socket

import pytest

from novelvideo.shared.safe_media_url import (
    download_public_media,
    is_internal_address,
    validate_public_http_url,
)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "::1", "0.0.0.0"],
)
def test_private_and_special_addresses_are_internal(address: str) -> None:
    assert is_internal_address(address) is True


@pytest.mark.parametrize("address", ["93.184.216.34", "198.18.0.5", "fdfe:dcba:9876::1"])
def test_public_and_fake_ip_addresses_are_fetchable(address: str) -> None:
    assert is_internal_address(address) is False


def test_public_http_url_accepts_public_literals() -> None:
    validate_public_http_url("https://93.184.216.34/image.png")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/image.png",
        "http://127.0.0.1/image.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/image.png",
    ],
)
def test_public_http_url_rejects_internal_targets(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_http_url(url, field_name="media_url")


def test_public_http_url_rejects_hostname_resolving_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0))
        ],
    )
    with pytest.raises(ValueError, match="private"):
        validate_public_http_url("https://evil.example/image.png")


def test_relay_validation_skips_dns_but_still_rejects_private_literals() -> None:
    validate_public_http_url(
        "https://cdn.example/image.png",
        field_name="media_url",
        resolve_dns=False,
    )
    with pytest.raises(ValueError, match="private"):
        validate_public_http_url(
            "http://127.0.0.1/image.png",
            field_name="media_url",
            resolve_dns=False,
        )


class _NoNetworkSession:
    def get(self, *_args, **_kwargs):
        raise AssertionError("network must not be touched for private URLs")


async def test_download_rejects_private_url_before_network() -> None:
    with pytest.raises(ValueError, match="private"):
        await download_public_media(_NoNetworkSession(), "http://127.0.0.1/image.png")


class _RedirectResponse:
    status = 302
    headers = {"Location": "http://127.0.0.1/private.png"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _RedirectSession:
    def get(self, _url, **kwargs):
        assert kwargs["allow_redirects"] is False
        return _RedirectResponse()


async def test_download_validates_redirect_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    with pytest.raises(ValueError, match="private"):
        await download_public_media(
            _RedirectSession(),
            "https://public.example/image.png",
        )
