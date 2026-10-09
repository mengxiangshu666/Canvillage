"""Budgeted, transparent HTTP proxy for the T-113 paid sample.

The proxy keeps provider protocols intact: it only rewrites the destination
origin, persists an allow/deny decision before forwarding, and records
redacted request metadata. It never retries a request.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


ReserveStart = Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def merge_upstream_url(base_url: str, incoming_path: str, query: str = "") -> str:
    """Join an incoming proxy path with a configured provider base URL."""

    target = urlparse(str(base_url or "").strip())
    if target.scheme not in {"http", "https"} or not target.netloc:
        raise ValueError("provider base URL must be an absolute http(s) URL")
    path = str(incoming_path or "/")
    if not path.startswith("/"):
        path = f"/{path}"
    base_path = target.path.rstrip("/")
    if base_path and not path.startswith(f"{base_path}/"):
        path = f"{base_path}{path}"
    return urlunparse(
        (
            target.scheme,
            target.netloc,
            path,
            "",
            str(query or ""),
            "",
        )
    )


def proxy_target_base_url(base_url: str, *, protocol: str) -> str:
    """Return the upstream root that matches the product-side request path.

    The MiniMax v2 adapter reads ``/v1`` from the saved model row but deliberately
    promotes native requests to the station root before adding ``/v2``.  A
    transparent proxy must therefore forward those requests to the origin root,
    not to the saved OpenAI-compatible ``/v1`` prefix.
    """

    configured = str(base_url or "").strip().rstrip("/")
    if str(protocol or "").strip().lower() != "minimax-video-v2":
        return configured
    target = urlparse(configured)
    if target.scheme not in {"http", "https"} or not target.netloc:
        raise ValueError("provider base URL must be an absolute http(s) URL")
    return urlunparse((target.scheme, target.netloc, "", "", "", ""))


def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _requested_video_duration(payload: dict[str, Any]) -> int | None:
    containers = [payload]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        containers.append(metadata)
    for container in containers:
        for key in ("duration", "duration_seconds", "durationSeconds", "seconds"):
            value = container.get(key)
            if value is None or value == "" or isinstance(value, bool):
                continue
            try:
                return int(round(float(value)))
            except (TypeError, ValueError):
                continue
    return None


def _first_text(value: Any) -> str:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _nested_text(payload: dict[str, Any], *keys: str) -> str:
    containers = [payload]
    for name in ("extra_fields", "extraFields", "metadata", "parameters"):
        nested = payload.get(name)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in keys:
            value = _first_text(container.get(key))
            if value:
                return value
    return ""


def _requested_image_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "size": _nested_text(payload, "size", "image_size", "imageSize"),
            "aspect_ratio": _nested_text(
                payload,
                "aspect_ratio",
                "aspectRatio",
                "image_aspect_ratio",
            ),
            "image_size": _nested_text(payload, "image_size", "imageSize"),
            "quality": _nested_text(payload, "quality"),
        }.items()
        if value
    }


def _requested_video_contract(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    roles = (
        [
            str(item.get("role") or "").strip()
            for item in content
            if isinstance(item, dict) and str(item.get("role") or "").strip()
        ]
        if isinstance(content, list)
        else []
    )
    media_urls: list[dict[str, str]] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type") or "").strip()
            if media_type not in {"image_url", "video_url", "audio_url"}:
                continue
            holder = item.get(media_type)
            url = (
                str(holder.get("url") or "").strip()
                if isinstance(holder, dict)
                else ""
            )
            if not url:
                continue
            parsed = urlparse(url)
            media_urls.append(
                {
                    "type": media_type,
                    "role": str(item.get("role") or "").strip(),
                    "scheme": parsed.scheme.casefold(),
                    "host": str(parsed.hostname or "").casefold(),
                }
            )
    return {
        key: value
        for key, value in {
            "resolution": _nested_text(
                payload,
                "resolution",
                "video_resolution",
                "videoResolution",
            ),
            "ratio": _nested_text(payload, "ratio", "aspect_ratio", "aspectRatio"),
            "duration": _requested_video_duration(payload),
            "content_roles": roles,
            "media_urls": media_urls,
        }.items()
        if value not in (None, "", [])
    }


class BudgetJournal:
    """Persist provider-start counters before any request reaches the network."""

    def __init__(
        self,
        path: Path,
        *,
        plan: dict[str, Any],
        reserve_start: ReserveStart,
    ) -> None:
        self.path = Path(path)
        self.plan = deepcopy(plan)
        self.reserve_start = reserve_start
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        counters = payload.get("counters") if isinstance(payload, dict) else {}
        events = payload.get("events") if isinstance(payload, dict) else []
        if not isinstance(counters, dict):
            counters = {}
        if not isinstance(events, list):
            events = []
        return {
            "schema": "t113_provider_budget.v1",
            "counters": counters,
            "events": events[-200:],
            "providerCallsStarted": payload.get("providerCallsStarted") is True,
        }

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def reserve(self, kind: str, request_meta: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = deepcopy(self._data)
            decision = self.reserve_start(
                self.plan,
                current.get("counters") or {},
                kind,
            )
            event = {
                "at": _now_iso(),
                "kind": kind,
                "request": {
                    key: request_meta.get(key)
                    for key in ("method", "path", "model", "stream")
                    if request_meta.get(key) is not None
                },
                "ok": decision.get("ok") is True,
                "reason": str(decision.get("reason") or ""),
                "current": decision.get("current"),
                "next": decision.get("next"),
                "limit": decision.get("limit"),
            }
            current.setdefault("events", []).append(event)
            current["events"] = current["events"][-200:]
            if decision.get("ok") is True:
                counter_key = str(decision.get("counterKey") or "")
                if not counter_key:
                    return {
                        "ok": False,
                        "reason": "provider_start_counter_missing",
                        "providerCallsStarted": False,
                    }
                counters = dict(current.get("counters") or {})
                counters[counter_key] = int(decision["next"])
                current["counters"] = counters
                current["providerCallsStarted"] = True
            try:
                self._write(current)
            except OSError as exc:
                return {
                    "ok": False,
                    "reason": "provider_budget_persist_failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "providerCallsStarted": False,
                }
            self._data = current
            return dict(decision)


class ProviderProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *,
        role: str,
        base_url: str,
        api_key: str,
        budget_journal: BudgetJournal | None = None,
        budget_kind: str = "",
        timeout_seconds: float = 180.0,
    ) -> None:
        super().__init__(("127.0.0.1", 0), _ProviderProxyHandler)
        self.role = str(role)
        self.target_base_url = str(base_url)
        self.api_key = str(api_key or "")
        self.budget_journal = budget_journal
        self.budget_kind = str(budget_kind or "")
        self.timeout_seconds = float(timeout_seconds)
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def record(self, item: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(dict(item))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.requests]

    def budget_kind_for_request(self, method: str, path: str) -> str:
        """Classify billable task starts before any request leaves the proxy."""

        if not self.budget_journal or str(method).upper() != "POST":
            return ""
        normalized = urlparse(path).path.rstrip("/").lower()
        if normalized.endswith("/chat/completions"):
            return "textRequests"
        if normalized.endswith(("/images/generations", "/images/edits")):
            return "imageTaskStarts"
        if "/query/" in normalized:
            return ""
        if normalized.endswith(
            (
                "/videos",
                "/video/generations",
                "/video_generation",
            )
        ):
            return "videoTaskStarts"
        return ""


class _ProviderProxyHandler(BaseHTTPRequestHandler):
    server: ProviderProxyServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_request_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _relay(self, method: str) -> None:
        parsed = urlparse(self.path)
        raw = self._read_request_body()
        payload = _json_body(raw)
        request_meta = {
            "method": method,
            "path": parsed.path,
            "model": str(payload.get("model") or ""),
            "stream": bool(payload.get("stream")),
        }
        normalized_path = parsed.path.rstrip("/").lower()
        if method == "POST" and normalized_path.endswith(
            ("/images/generations", "/images/edits")
        ):
            request_meta.update(_requested_image_contract(payload))
        if method == "POST" and (
            "/videos" in parsed.path or "video_generation" in parsed.path
        ):
            requested_duration = _requested_video_duration(payload)
            if requested_duration is not None:
                request_meta["requested_duration_seconds"] = requested_duration
            request_meta.update(_requested_video_contract(payload))

        reserved = False
        budget_kind = self.server.budget_kind_for_request(method, parsed.path)
        if budget_kind:
            assert self.server.budget_journal is not None
            decision = self.server.budget_journal.reserve(
                budget_kind,
                request_meta,
            )
            if decision.get("ok") is not True:
                self.server.record(
                    {
                        **request_meta,
                        "status": 429,
                        "budgetDenied": True,
                        "reason": decision.get("reason"),
                    }
                )
                self._send_json(
                    429,
                    {
                        "error": {
                            "message": "T-113 provider budget denied before upstream dispatch",
                            "reason": decision.get("reason"),
                        }
                    },
                )
                return
            reserved = True

        try:
            target_url = merge_upstream_url(
                self.server.target_base_url,
                parsed.path,
                parsed.query,
            )
        except ValueError as exc:
            self._send_json(502, {"error": {"message": str(exc)}})
            return

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower()
            not in {
                "authorization",
                "accept-encoding",
                "connection",
                "content-length",
                "host",
                "proxy-connection",
            }
        }
        if self.server.api_key:
            headers["Authorization"] = f"Bearer {self.server.api_key}"
        headers["Content-Type"] = self.headers.get(
            "Content-Type",
            "application/json",
        )
        request = Request(
            target_url,
            data=raw if method in {"POST", "PUT", "PATCH"} else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.server.timeout_seconds) as response:
                body = response.read()
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "application/json")
                content_encoding = response.headers.get("Content-Encoding", "")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except HTTPError as exc:
            body = exc.read()
            status = int(exc.code)
            self.send_response(status)
            self.send_header(
                "Content-Type",
                exc.headers.get("Content-Type", "application/json"),
            )
            content_encoding = exc.headers.get("Content-Encoding", "")
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (URLError, TimeoutError, OSError) as exc:
            status = 502
            self._send_json(
                status,
                {"error": {"message": f"{type(exc).__name__}: {str(exc)[:300]}"}},
            )
        self.server.record(
            {
                **request_meta,
                "status": status,
                "budgetReserved": reserved,
            }
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._relay("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._relay("POST")


class ProviderProxyCluster:
    """Three loopback proxies with one lifecycle, matching T-112's server API."""

    def __init__(
        self,
        *,
        text_base_url: str,
        text_api_key: str,
        image_base_url: str,
        image_api_key: str,
        video_base_url: str,
        video_api_key: str,
        budget_journal: BudgetJournal,
        full_chain: bool = True,
    ) -> None:
        self.full_chain = bool(full_chain)
        self.text = ProviderProxyServer(
            role="text",
            base_url=text_base_url,
            api_key=text_api_key,
            budget_journal=budget_journal,
        )
        self.image = ProviderProxyServer(
            role="image",
            base_url=image_base_url,
            api_key=image_api_key,
            budget_journal=budget_journal,
        )
        self.video = ProviderProxyServer(
            role="video",
            base_url=video_base_url,
            api_key=video_api_key,
            budget_journal=budget_journal,
        )
        self._servers = (self.text, self.image, self.video)
        self._threads = [
            threading.Thread(
                target=server.serve_forever,
                name=f"t113-{server.role}-proxy",
                daemon=True,
            )
            for server in self._servers
        ]
        for thread in self._threads:
            thread.start()

    @property
    def base_url(self) -> str:
        return self.text.base_url

    def serve_forever(self) -> None:
        for thread in self._threads:
            thread.join()

    def shutdown(self) -> None:
        for server in self._servers:
            server.shutdown()

    def server_close(self) -> None:
        for server in self._servers:
            server.server_close()

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            item
            for server in self._servers
            for item in server.snapshot()
        ]
