"""Per-account avatar storage behind ``/api/v1/account/avatar``.

The SPA has always been able to read, upload and clear an avatar, but the local
edition never shipped the backend for it: ``GET /api/v1/account/avatar``
answered 404, the auth store swallowed that silently, and the account menu hid
the entry.  This module is the missing backend.

Where a request is allowed to touch is decided here, once:

* the directory is derived from the **authenticated session**, never from the
  request path, and the account name is slugged before it becomes a directory
  name so an exotic or hostile username cannot escape the state root;
* the bytes are decoded before they are stored, so a file that is merely named
  ``.png`` is rejected, and the stored extension follows the real format;
* one canonical file per account: saving replaces whatever was there, so a stale
  avatar can never be served alongside the current one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError


ACCOUNT_DIR_NAME = "_account"
AVATAR_STEM = "avatar"
# The avatar is drawn in a 26 px circle, so this cap is not a quality bar: it
# stops a stray 40 MP scan from being decoded.  4 MB takes a normal phone photo
# and stays under the 5 MB request-body limit the API middleware puts on this
# route (``api.app.MAX_REQUEST_BODY_BYTES``).  That relationship is the point:
# a file above the middleware limit is answered with a 413 by a layer this
# dialog cannot explain, so this check has to fire first.
MAX_AVATAR_BYTES = 4 * 1024 * 1024
MAX_AVATAR_EDGE = 8192
MAX_AVATAR_PIXELS = 32 * 1024 * 1024
STATIC_PREFIX = "/static/avatars"

# Formats we hand straight to a browser.  Anything else Pillow can decode is
# re-encoded to PNG first, which keeps a BMP / TIFF / ICO / AVIF export from
# bouncing.  HEIC needs a codec this build does not ship (``pillow-heif``), so a
# HEIC upload is rejected with the supported-formats hint rather than silently
# stored as something the browser cannot paint.
_WEB_SAFE_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
_CONTENT_TYPE_BY_EXTENSION = {
    extension: content_type
    for extension, content_type in _WEB_SAFE_FORMATS.values()
}
_SUPPORTED_HINT = "PNG, JPG, WebP, GIF"


class AvatarError(ValueError):
    """Raised when an upload cannot become a stored avatar."""


@dataclass(frozen=True)
class AvatarRecord:
    """One stored avatar file, already resolved on disk."""

    path: Path
    content_type: str
    size_bytes: int
    version: str


def account_state_dir(username: object, *, state_root: Path) -> Path:
    """Return the account's private state directory inside ``state_root``."""

    return Path(state_root) / _account_dir_name(username) / ACCOUNT_DIR_NAME


def _account_dir_name(username: object) -> str:
    """Turn a session username into a safe single path segment.

    Real usernames are alphanumeric, but this runs on whatever the auth layer
    reports, so anything else is replaced and disambiguated with a short hash.
    Without the hash two different exotic names could collapse into one folder.
    """

    raw = str(username or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    if not cleaned or cleaned != raw or cleaned.startswith("."):
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", cleaned).strip("._") or "account"
        return f"{cleaned[:48]}-{digest}"
    return cleaned[:64]


def _read_record(path: Path) -> AvatarRecord | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    extension = path.suffix.lstrip(".").lower()
    content_type = _CONTENT_TYPE_BY_EXTENSION.get(extension)
    if content_type is None:
        return None
    return AvatarRecord(
        path=path,
        content_type=content_type,
        size_bytes=stat.st_size,
        version=f"{stat.st_mtime_ns:x}-{stat.st_size:x}",
    )


def read_avatar(
    username: object,
    *,
    state_root: Path,
) -> AvatarRecord | None:
    """Return the stored avatar, or ``None`` when the account has none."""

    directory = account_state_dir(username, state_root=state_root)
    try:
        candidates = sorted(directory.glob(f"{AVATAR_STEM}.*"))
    except OSError:
        return None
    for candidate in candidates:
        record = _read_record(candidate)
        if record is not None:
            return record
    return None


def avatar_url(username: object, record: AvatarRecord) -> str:
    """Public URL for a stored avatar, versioned so the browser cannot go stale."""

    return (
        f"{STATIC_PREFIX}/{_account_dir_name(username)}/"
        f"{record.path.name}?v={record.version}"
    )


def save_avatar(
    username: object,
    payload: bytes,
    *,
    state_root: Path,
    declared_type: str = "",
) -> AvatarRecord:
    """Validate, normalise and store one avatar, replacing any previous file."""

    _ = declared_type  # content type is decorative; the bytes decide the format
    if not payload:
        raise AvatarError("头像文件是空的")
    if len(payload) > MAX_AVATAR_BYTES:
        raise AvatarError(
            f"头像不能超过 {MAX_AVATAR_BYTES // (1024 * 1024)} MB"
        )

    extension, stored = _normalise(payload)
    directory = account_state_dir(username, state_root=state_root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{AVATAR_STEM}.{extension}"
    temporary = directory / f".{AVATAR_STEM}.{extension}.tmp"
    temporary.write_bytes(stored)
    temporary.replace(target)
    for stale in directory.glob(f"{AVATAR_STEM}.*"):
        if stale != target:
            try:
                stale.unlink()
            except OSError:
                pass
    record = _read_record(target)
    if record is None:  # pragma: no cover - write succeeded, stat did not
        raise AvatarError("头像写入后无法回读，请重试")
    return record


def delete_avatar(username: object, *, state_root: Path) -> bool:
    """Remove every stored avatar for the account; ``True`` when one existed."""

    directory = account_state_dir(username, state_root=state_root)
    removed = False
    try:
        candidates = sorted(directory.glob(f"{AVATAR_STEM}.*"))
    except OSError:
        return False
    for candidate in candidates:
        try:
            candidate.unlink()
            removed = True
        except OSError:
            continue
    return removed


def _normalise(payload: bytes) -> tuple[str, bytes]:
    """Return ``(extension, bytes_to_store)`` for a decodable image."""

    try:
        with Image.open(BytesIO(payload)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise AvatarError(
            f"这不是能识别的图片，请上传 {_SUPPORTED_HINT}"
        ) from exc

    if width <= 0 or height <= 0:
        raise AvatarError("图片尺寸无效")
    if width > MAX_AVATAR_EDGE or height > MAX_AVATAR_EDGE:
        raise AvatarError(f"图片边长不能超过 {MAX_AVATAR_EDGE} 像素")
    if width * height > MAX_AVATAR_PIXELS:
        raise AvatarError("图片像素总量过大，请先压缩后上传")

    web_safe = _WEB_SAFE_FORMATS.get(image_format)
    if web_safe is not None:
        return web_safe[0], payload

    converted = _to_png(payload)
    if len(converted) > MAX_AVATAR_BYTES:
        raise AvatarError(
            f"这张 {image_format or '图片'} 转成 PNG 后超过 "
            f"{MAX_AVATAR_BYTES // (1024 * 1024)} MB，请先压缩后上传"
        )
    return "png", converted


def _to_png(payload: bytes) -> bytes:
    try:
        with Image.open(BytesIO(payload)) as image:
            frame = image.convert("RGBA")
        buffer = BytesIO()
        frame.save(buffer, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AvatarError("图片转换失败，请换一张图片试试") from exc
    return buffer.getvalue()


def resolve_static_avatar(
    username: str,
    filename: str,
    *,
    session_username: object,
    state_root: Path,
) -> AvatarRecord:
    """Resolve a ``/static/avatars/<user>/<file>`` request for one session.

    The request may only name the session's own folder and the canonical file
    name, so the route cannot be turned into a directory listing or a way to
    read somebody else's avatar.  A mismatch reads as "not here" rather than
    "forbidden": callers learn nothing about other accounts.
    """

    if filename not in {f"{AVATAR_STEM}.{ext}" for ext in _CONTENT_TYPE_BY_EXTENSION}:
        raise AvatarError("未知的头像文件名")
    if _account_dir_name(username) != _account_dir_name(session_username):
        raise AvatarError("头像不属于当前账号")
    record = read_avatar(session_username, state_root=state_root)
    if record is None or record.path.name != filename:
        raise AvatarError("头像不存在")
    return record
