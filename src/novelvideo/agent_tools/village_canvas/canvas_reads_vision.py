"""Safe image-source resolution for the canvas vision tool."""

from __future__ import annotations

import ipaddress
from pathlib import Path
import socket
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .core import _agent_context_value


_VISION_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_VISION_MAX_BYTES = 25 * 1024 * 1024
_VISION_STATIC_PREFIX = "/static/projects/"

# Clash-style fake-ip resolvers answer for public domains out of these ranges.
# They must stay fetchable or every ordinary image URL would be judged internal.
_FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("fdfe:dcba:9876::/48"),
)


def _vision_url_is_internal(address_text: str) -> bool:
    """Return whether a resolved address must not be fetched by the vision reader."""

    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        # Not a literal address (a hostname mid-resolution): treat as internal so
        # an unresolvable name cannot slip through to a second, unchecked lookup.
        return True
    if any(address in network for network in _FAKE_IP_NETWORKS):
        return False
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return True
    return False


def _validate_vision_remote_url(value: str) -> None:
    """Reject a remote vision source that resolves into private address space."""

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("image_url must be an HTTP(S) URL")
    host = parsed.hostname
    if not host:
        raise ValueError("image_url must include a host")
    if host.rstrip(".").casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("image_url must not target a local service")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"image_url host could not be resolved: {host}") from exc
    for info in infos:
        if _vision_url_is_internal(str(info[4][0])):
            raise ValueError("image_url resolves to a private address")


def _vision_project_media_dir() -> Path | None:
    """Return the on-disk media root backing this session's project."""

    project_dir_raw = _agent_context_value("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR")
    if not project_dir_raw:
        return None
    project_dir = Path(project_dir_raw)
    return project_dir if project_dir.is_dir() else None


def _vision_source_from_project_static(value: str) -> tuple[bytes, str] | None:
    """Resolve a `/static/projects/{id}/...` URL to bytes, or None."""

    if not value.startswith(_VISION_STATIC_PREFIX):
        return None
    media_dir = _vision_project_media_dir()
    if media_dir is None:
        raise ValueError(
            "project media root is unavailable in this agent session; "
            "the turn must supply VILLAGE_CANVAS_PROJECT_OUTPUT_DIR"
        )
    from novelvideo.freezone.paths import resolve_static_url_to_path

    try:
        candidate = resolve_static_url_to_path(value, media_dir)
    except ValueError as exc:
        raise ValueError("image_url escapes the project media directory") from exc
    if not candidate.is_file():
        return None
    if candidate.stat().st_size > _VISION_MAX_BYTES:
        raise ValueError("image exceeds the 25 MiB vision limit")
    media_type = _VISION_MEDIA_TYPES.get(candidate.suffix.lower(), "image/png")
    return candidate.read_bytes(), media_type


def _read_vision_source(source: str) -> tuple[bytes, str]:
    value = str(source or "").strip()
    if not value:
        raise ValueError("image_url is required")
    if value.startswith("file://"):
        value = value[7:]
    path = Path(value).expanduser()
    if path.is_file():
        data = path.read_bytes()
        media_type = _VISION_MEDIA_TYPES.get(path.suffix.lower(), "image/png")
        return data, media_type
    # Canvas nodes give project-static URLs; resolve them against the project
    # directory before falling back to a network fetch.
    if value.startswith(_VISION_STATIC_PREFIX):
        resolved = _vision_source_from_project_static(value)
        if resolved is None:
            raise ValueError("image_url does not resolve to a project image")
        return resolved
    _validate_vision_remote_url(value)
    request = Request(
        value, headers={"User-Agent": "Village Infinite Canvas-Hermes-Vision/1.0"}
    )
    # Redirects stay disabled: a public host could otherwise bounce the request
    # into private space after the address check above already passed.
    with urlopen(request, timeout=60) as response:  # noqa: S310 - guarded above
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > _VISION_MAX_BYTES:
            raise ValueError("image exceeds the 25 MiB vision limit")
        data = response.read(_VISION_MAX_BYTES + 1)
        if len(data) > _VISION_MAX_BYTES:
            raise ValueError("image exceeds the 25 MiB vision limit")
        media_type = str(response.headers.get_content_type() or "image/png")
    if not media_type.startswith("image/"):
        raise ValueError("image_url did not return an image")
    return data, media_type


__all__ = ["_read_vision_source"]
