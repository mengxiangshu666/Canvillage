from __future__ import annotations

import json

import pytest

from novelvideo.chat import tavily_pool
from novelvideo.chat.tavily_pool import TavilyKeyPool, TavilyPoolError


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


def test_search_forwards_evidence_filters_and_raw_content():
    captured = {}

    def opener(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "results": [
                    {
                        "title": "官方资料",
                        "url": "https://docs.example.test/a",
                        "content": "摘要",
                        "raw_content": "完整正文",
                        "score": 0.9,
                    }
                ]
            }
        )

    pool = TavilyKeyPool(keys=["tvly-only-12345678"], opener=opener)
    result = pool.search(
        "agent evidence",
        include_raw_content=True,
        include_domains=["https://docs.example.test/path"],
        exclude_domains=["ads.example.test"],
        time_range="week",
    )

    assert captured["payload"]["include_raw_content"] is True
    assert captured["payload"]["include_domains"] == ["docs.example.test"]
    assert captured["payload"]["exclude_domains"] == ["ads.example.test"]
    assert captured["payload"]["time_range"] == "week"
    assert result["results"][0]["raw_content"] == "完整正文"
    assert result["results"][0]["score"] == 0.9


def test_rotation_failure_fallback_and_secret_free_result(monkeypatch):
    calls = []
    def opener(request, timeout):
        calls.append(request.headers.get("Authorization"))
        if len(calls) == 1:
            raise OSError("upstream")
        return _Response({"results": [{"title": "T", "url": "https://example.test", "content": "C", "ignored": "x"}]})
    pool = TavilyKeyPool(keys=["tvly-first-12345678", "tvly-second-12345678"], opener=opener)
    result = pool.search("query", project_id="p", canvas_id="c")
    assert result["result_count"] == 1
    assert result["scope"] == {"project_id": "p", "canvas_id": "c"}
    assert result["results"][0].keys() == {"title", "url", "content"}
    assert calls == ["Bearer tvly-first-12345678", "Bearer tvly-second-12345678"]


def test_cache_is_scoped_and_avoids_second_request():
    count = 0
    def opener(request, timeout):
        nonlocal count
        count += 1
        return _Response({"results": []})
    pool = TavilyKeyPool(keys=["tvly-only-12345678"], opener=opener)
    first = pool.search("same", project_id="p", canvas_id="c")
    second = pool.search("same", project_id="p", canvas_id="c")
    other_scope = pool.search("same", project_id="p", canvas_id="other")
    assert count == 2
    assert first["cached"] is False
    assert second["cached"] is True
    assert other_scope["cached"] is False


def test_keys_file_parser_masks_wrapped_lines(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text("header\n1. 卡密：tvly-a123456789\n# tvly-comment12345678\n", encoding="utf-8")
    pool = TavilyKeyPool(keys_file=path, opener=lambda *_args, **_kwargs: _Response({"results": []}))
    assert pool.key_count == 1


def test_default_key_file_stays_inside_project(monkeypatch, tmp_path):
    external = tmp_path / "outside-keys.txt"
    external.write_text("tvly-external-12345678\n", encoding="utf-8")
    missing_default = tavily_pool._PROJECT_ROOT / "项目资产" / "state" / "test-missing-key.txt"
    monkeypatch.setattr(tavily_pool, "_DEFAULT_KEY_FILE", missing_default)
    monkeypatch.setenv("TAVILY_KEYS_FILE", str(external))

    pool = TavilyKeyPool()

    assert pool.key_count == 0
    assert tavily_pool._DEFAULT_KEY_FILE.resolve().is_relative_to(
        tavily_pool._PROJECT_ROOT.resolve()
    )


def test_missing_key_returns_normalized_secret_free_error():
    pool = TavilyKeyPool(keys=[])

    with pytest.raises(TavilyPoolError) as captured:
        pool.search("query")

    assert str(captured.value) == "no Tavily API key configured"
    assert "tvly-" not in str(captured.value)


def test_search_caps_rotation_and_each_request_to_the_total_budget(monkeypatch):
    monkeypatch.setenv("TAVILY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("TAVILY_TOTAL_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("TAVILY_TIMEOUT_SECONDS", "20")
    timeouts = []

    def opener(_request, timeout):
        timeouts.append(timeout)
        raise OSError("upstream")

    pool = TavilyKeyPool(
        keys=[
            "tvly-first-12345678",
            "tvly-second-12345678",
            "tvly-third-12345678",
        ],
        opener=opener,
    )

    with pytest.raises(TavilyPoolError):
        pool.search("query")

    assert len(timeouts) == 2
    assert all(1.0 <= timeout <= 7.0 for timeout in timeouts)


def test_search_fails_fast_when_the_concurrency_gate_is_saturated(monkeypatch):
    monkeypatch.setenv("TAVILY_TOTAL_TIMEOUT_SECONDS", "2")
    pool = TavilyKeyPool(keys=["tvly-only-12345678"])

    class SaturatedGate:
        def __init__(self):
            self.timeout = None

        def acquire(self, *, timeout):
            self.timeout = timeout
            return False

        def release(self):
            raise AssertionError("an unacquired gate must not be released")

    gate = SaturatedGate()
    pool._request_gate = gate

    with pytest.raises(TavilyPoolError, match="search capacity timeout"):
        pool.search("query")

    assert gate.timeout == 2.0
