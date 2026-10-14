"""Guard outbound media URLs against SSRF into local or private networks."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


# Clash-style fake-ip resolvers answer for public domains from these ranges.
_FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("fdfe:dcba:9876::/48"),
)


class MediaDownloadError(RuntimeError):
    """A public media download failed after URL validation."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str = "",
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url


def is_internal_address(address_text: str) -> bool:
    """Return whether a resolved address must not be fetched."""

    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        # An unresolved hostname must not slip through to a second lookup.
        return True
    if any(address in network for network in _FAKE_IP_NETWORKS):
        return False
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return True
    return False


def validate_public_http_url(
    value: str,
    *,
    field_name: str = "url",
    resolve_dns: bool = True,
) -> None:
    """Reject HTTP(S) URLs that target or resolve into private address space.

    ``resolve_dns=False`` is for URLs that are only handed to an upstream
    provider, where a local DNS lookup would add latency without blocking a
    local fetch. Literal private hosts are still rejected.
    """

    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must be an HTTP(S) URL")
    host = parsed.hostname
    if not host:
        raise ValueError(f"{field_name} must include a host")
    if host.rstrip(".").casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError(f"{field_name} must not target a local service")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if not resolve_dns:
            return
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise ValueError(f"{field_name} host could not be resolved: {host}") from exc
        for info in infos:
            if is_internal_address(str(info[4][0])):
                raise ValueError(f"{field_name} resolves to a private address")
        return
    if is_internal_address(str(literal)):
        raise ValueError(f"{field_name} resolves to a private address")


async def download_public_media(
    session: Any,
    url: str,
    *,
    timeout: Any = None,
    max_redirects: int = 5,
) -> tuple[bytes, str, str]:
    """Download a media URL after validating every redirect hop."""

    current = str(url or "").strip()
    if not current:
        raise MediaDownloadError("media URL is empty")
    for _ in range(max_redirects + 1):
        validate_public_http_url(current, field_name="media URL")
        async with session.get(current, timeout=timeout, allow_redirects=False) as response:
            if 300 <= int(response.status) < 400:
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    raise MediaDownloadError(
                        f"media redirect missing Location: HTTP {response.status}",
                        status=int(response.status),
                        url=current,
                    )
                current = urljoin(current, location)
                continue
            if int(response.status) >= 400:
                body = await response.text()
                raise MediaDownloadError(
                    f"media download failed: HTTP {response.status}",
                    status=int(response.status),
                    body=body,
                    url=current,
                )
            content = await response.read()
            content_type = (
                str(response.headers.get("Content-Type") or "")
                .split(";", 1)[0]
                .strip()
            )
            filename = Path(urlsplit(current).path).name or "reference"
            return content, filename, content_type
    raise MediaDownloadError("media URL redirected too many times", url=current)


__all__ = [
    "MediaDownloadError",
    "download_public_media",
    "is_internal_address",
    "validate_public_http_url",
]
