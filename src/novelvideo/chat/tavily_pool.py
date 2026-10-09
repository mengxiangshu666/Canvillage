"""Small, dependency-free Tavily key pool used by the Canvas Agent.

Credentials stay out of logs and result payloads. Production keys are read only
from the protected project-local file ``项目资产/state/tavily-keys.txt``. Tests
may inject a temporary ``keys_file`` explicitly without changing production
path resolution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_KEY_RE = re.compile(r"(?:tvly-|tavily-)[A-Za-z0-9_-]{8,}", re.IGNORECASE)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_KEY_FILE = _PROJECT_ROOT / "项目资产" / "state" / "tavily-keys.txt"


class TavilyPoolError(RuntimeError):
    """All provider failures are normalized to this non-secret error."""


class TavilyKeyPool:
    def __init__(
        self,
        *,
        keys: list[str] | None = None,
        keys_file: str | os.PathLike[str] | None = None,
        opener: Any = urlopen,
        clock: Any = time.monotonic,
    ) -> None:
        self._keys = self._normalize_keys(keys if keys is not None else self._load_keys(keys_file))
        self._opener = opener
        self._clock = clock
        self._cursor = 0
        self._cooldown: dict[str, float] = {}
        self._lock = threading.RLock()
        self._request_gate = threading.Semaphore(self._env_int("TAVILY_MAX_CONCURRENCY", 4, 1, 16))
        self._last_request = 0.0
        self._min_interval = self._env_float("TAVILY_MIN_INTERVAL_SECONDS", 0.15, 0.0, 10.0)
        self._cooldown_seconds = self._env_float("TAVILY_FAILURE_COOLDOWN_SECONDS", 10.0, 0.1, 3600.0)
        self._timeout = self._env_float("TAVILY_TIMEOUT_SECONDS", 20.0, 1.0, 120.0)
        self._total_timeout = self._env_float("TAVILY_TOTAL_TIMEOUT_SECONDS", 30.0, 2.0, 120.0)
        self._max_attempts = self._env_int("TAVILY_MAX_ATTEMPTS", 3, 1, 16)
        self._cache_ttl = self._env_float("TAVILY_CACHE_TTL_SECONDS", 300.0, 0.0, 86400.0)
        self._cache_size = self._env_int("TAVILY_CACHE_SIZE", 128, 8, 2048)
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    @staticmethod
    def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _normalize_keys(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            match = _KEY_RE.search(str(value).strip())
            if not match:
                continue
            key = match.group(0)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result

    @classmethod
    def _load_keys(cls, keys_file: str | os.PathLike[str] | None) -> list[str]:
        values: list[str] = []
        if keys_file is not None:
            paths = [Path(keys_file)]
        else:
            configured = os.environ.get("TAVILY_KEYS_FILE", "").strip()
            candidate = Path(configured) if configured else _DEFAULT_KEY_FILE
            if not candidate.is_absolute():
                candidate = _PROJECT_ROOT / candidate
            try:
                candidate = candidate.resolve(strict=False)
                candidate.relative_to(_PROJECT_ROOT.resolve())
            except (OSError, ValueError):
                candidate = _DEFAULT_KEY_FILE
            paths = [candidate]
        for path in paths:
            try:
                if path.exists():
                    values.extend(
                        line for line in path.read_text(encoding="utf-8").splitlines()
                        if not line.lstrip().startswith("#")
                    )
                    break
            except OSError:
                continue
        return values

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @staticmethod
    def _cache_key(
        query: str,
        *,
        project_id: str,
        canvas_id: str,
        max_results: int,
        topic: str,
        search_depth: str,
        include_answer: bool,
        include_raw_content: bool = False,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
        time_range: str = "",
    ) -> str:
        payload = json.dumps(
            {
                "q": query,
                "project": project_id,
                "canvas": canvas_id,
                "n": max_results,
                "topic": topic,
                "depth": search_depth,
                "answer": include_answer,
                "raw": include_raw_content,
                "include_domains": include_domains,
                "exclude_domains": exclude_domains,
                "time_range": time_range,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_domains(values: object) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple, set)):
            return ()
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            raw = str(value or "").strip().lower()
            if not raw:
                continue
            if "://" in raw:
                raw = raw.split("://", 1)[1]
            raw = raw.split("/", 1)[0].strip().strip(".")
            if not raw or any(char.isspace() for char in raw) or len(raw) > 253:
                continue
            if raw not in seen:
                seen.add(raw)
                result.append(raw)
        return tuple(result[:50])

    def _pick_key(self, tried: set[str]) -> str | None:
        with self._lock:
            now = self._clock()
            for _ in range(len(self._keys)):
                key = self._keys[self._cursor % len(self._keys)]
                self._cursor = (self._cursor + 1) % len(self._keys)
                if key not in tried and self._cooldown.get(key, 0.0) <= now:
                    return key
        return None

    def _mark_failed(self, key: str) -> None:
        with self._lock:
            self._cooldown[key] = self._clock() + self._cooldown_seconds

    def _throttle(self) -> None:
        with self._lock:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = self._clock()

    def search(
        self,
        query: str,
        *,
        project_id: str = "",
        canvas_id: str = "",
        max_results: int = 5,
        topic: str = "general",
        search_depth: str = "basic",
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_domains: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        time_range: str | None = None,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise TavilyPoolError("query is required")
        if len(query) > 4000:
            raise TavilyPoolError("query is too long")
        max_results = max(1, min(int(max_results), 20))
        topic = topic if topic in {"general", "news", "finance"} else "general"
        search_depth = search_depth if search_depth in {"basic", "advanced"} else "basic"
        include_domains_tuple = self._normalize_domains(include_domains)
        exclude_domains_tuple = self._normalize_domains(exclude_domains)
        time_range = str(time_range or "").strip().lower()
        if time_range not in {"day", "week", "month", "year"}:
            time_range = ""
        cache_key = self._cache_key(
            query,
            project_id=project_id,
            canvas_id=canvas_id,
            max_results=max_results,
            topic=topic,
            search_depth=search_depth,
            include_answer=include_answer,
            include_raw_content=bool(include_raw_content),
            include_domains=include_domains_tuple,
            exclude_domains=exclude_domains_tuple,
            time_range=time_range,
        )
        now = self._clock()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and (self._cache_ttl <= 0 or now - cached[0] <= self._cache_ttl):
                self._cache.move_to_end(cache_key)
                return {**cached[1], "cached": True}
            if cached:
                self._cache.pop(cache_key, None)
        if not self._keys:
            raise TavilyPoolError("no Tavily API key configured")
        request_payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "search_depth": search_depth,
            "include_answer": bool(include_answer),
        }
        if include_raw_content:
            request_payload["include_raw_content"] = True
        if include_domains_tuple:
            request_payload["include_domains"] = list(include_domains_tuple)
        if exclude_domains_tuple:
            request_payload["exclude_domains"] = list(exclude_domains_tuple)
        if time_range:
            request_payload["time_range"] = time_range
        payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        tried: set[str] = set()
        last_error = "provider request failed"
        deadline = self._clock() + self._total_timeout
        if not self._request_gate.acquire(timeout=self._total_timeout):
            raise TavilyPoolError("search capacity timeout")
        try:
            for _ in range(min(len(self._keys), self._max_attempts)):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    last_error = "provider request timed out"
                    break
                key = self._pick_key(tried)
                if not key:
                    break
                tried.add(key)
                self._throttle()
                remaining = deadline - self._clock()
                if remaining <= 0:
                    last_error = "provider request timed out"
                    break
                request = Request(
                    os.environ.get("TAVILY_API_URL", "https://api.tavily.com/search"),
                    data=payload,
                    method="POST",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "village-canvas-canvas-agent/1.0"},
                )
                try:
                    with self._opener(
                        request,
                        timeout=max(1.0, min(self._timeout, remaining)),
                    ) as response:
                        raw = json.loads(response.read().decode("utf-8", errors="replace"))
                    result = self._normalize_result(
                        raw,
                        project_id=project_id,
                        canvas_id=canvas_id,
                        query=query,
                        topic=topic,
                        search_depth=search_depth,
                    )
                    with self._lock:
                        self._cache[cache_key] = (self._clock(), result)
                        self._cache.move_to_end(cache_key)
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)
                    return result
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                    last_error = f"provider request failed ({type(exc).__name__})"
                    self._mark_failed(key)
        finally:
            self._request_gate.release()
        raise TavilyPoolError(last_error)

    @staticmethod
    def _normalize_result(
        raw: Any,
        *,
        project_id: str,
        canvas_id: str,
        query: str,
        topic: str = "general",
        search_depth: str = "basic",
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TavilyPoolError("invalid Tavily response")
        results: list[dict[str, Any]] = []
        for item in raw.get("results") or []:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    key: item.get(key)
                    for key in (
                        "title",
                        "url",
                        "content",
                        "raw_content",
                        "published_date",
                        "score",
                    )
                    if item.get(key) is not None
                }
            )
        return {
            "ok": True,
            "query": query,
            "answer": raw.get("answer"),
            "results": results,
            "result_count": len(results),
            "cached": False,
            "topic": topic,
            "search_depth": search_depth,
            "scope": {
                "project_id": project_id or None,
                "canvas_id": canvas_id or None,
            },
        }


_POOL: TavilyKeyPool | None = None


def get_tavily_pool() -> TavilyKeyPool:
    global _POOL
    if _POOL is None:
        _POOL = TavilyKeyPool()
    return _POOL
