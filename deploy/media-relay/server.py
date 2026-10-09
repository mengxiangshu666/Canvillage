"""Minimal authenticated upload / public-read relay for temporary AI media.

The upload path is protected by a bearer token. Download URLs use random names,
expire automatically, and never expose the upload credential.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = os.environ.get("MEDIA_RELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("MEDIA_RELAY_PORT", "8782"))
TOKEN = os.environ.get("MEDIA_RELAY_TOKEN", "").strip()
PUBLIC_BASE_URL = os.environ.get(
    "MEDIA_RELAY_PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}"
).rstrip("/")
STORAGE_DIR = Path(os.environ.get("MEDIA_RELAY_STORAGE_DIR", "/var/lib/village-canvas-media"))
MAX_UPLOAD_BYTES = int(os.environ.get("MEDIA_RELAY_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
DEFAULT_TTL = int(os.environ.get("MEDIA_RELAY_DEFAULT_TTL", "1800"))
MAX_TTL = int(os.environ.get("MEDIA_RELAY_MAX_TTL", "3600"))
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MEDIA_NAME_RE = re.compile(
    r"^(?P<token>[a-f0-9]{48})_(?P<expires>[0-9]{10,13})\.(?P<ext>[a-z0-9]+)$"
)


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_int(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def _cleanup_expired() -> int:
    removed = 0
    now = int(time.time())
    for path in STORAGE_DIR.iterdir():
        if not path.is_file():
            continue
        match = MEDIA_NAME_RE.match(path.name)
        if match is None or int(match.group("expires")) > now:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _cleanup_loop() -> None:
    while True:
        time.sleep(300)
        _cleanup_expired()


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "Village Infinite Canvas MediaRelay/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return bool(TOKEN) and hmac.compare_digest(supplied, TOKEN)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if not parsed.path.startswith("/media/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        name = parsed.path.removeprefix("/media/")
        match = MEDIA_NAME_RE.match(name)
        if match is None or match.group("ext") not in ALLOWED_EXTENSIONS:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        path = STORAGE_DIR / name
        if int(match.group("expires")) <= int(time.time()):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._send_json(HTTPStatus.GONE, {"error": "expired"})
            return
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = mimetypes.types_map.get(
            f".{match.group('ext')}", "application/octet-stream"
        )
        remaining = max(0, int(match.group("expires")) - int(time.time()))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", f"public, max-age={remaining}, immutable")
        self.send_header("Content-Disposition", f'inline; filename="asset.{match.group("ext")}"')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        query = parse_qs(parsed.query)
        ext = str((query.get("ext") or ["png"])[0]).strip().lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        if ext not in ALLOWED_EXTENSIONS:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unsupported extension"})
            return
        length = _safe_int(self.headers.get("Content-Length"), -1)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid size"})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "incomplete upload"})
            return
        ttl = _safe_int((query.get("ttl") or [str(DEFAULT_TTL)])[0], DEFAULT_TTL)
        ttl = min(MAX_TTL, max(60, ttl))
        expires = int(time.time()) + ttl
        digest = hashlib.sha256(body).hexdigest()[:16]
        random_part = secrets.token_hex(16)
        name = f"{digest}{random_part}_{expires}.{ext}"
        target = STORAGE_DIR / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(body)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        self._send_json(
            HTTPStatus.CREATED,
            {
                "ok": True,
                "url": f"{PUBLIC_BASE_URL}/media/{name}",
                "expires_at": expires,
                "size": len(body),
            },
        )


def main() -> None:
    if not TOKEN:
        raise SystemExit("MEDIA_RELAY_TOKEN is required")
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STORAGE_DIR, 0o700)
    _cleanup_expired()
    threading.Thread(target=_cleanup_loop, name="media-relay-cleanup", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), RelayHandler)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
