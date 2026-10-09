from __future__ import annotations

import gzip
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PROXY_SCRIPT = ROOT / "scripts" / "acceptance" / "t113_real_provider_proxy.py"
CONTRACT_SCRIPT = ROOT / "scripts" / "acceptance" / "t113_single_shot_paid_l3.py"
RUNNER_SCRIPT = ROOT / "scripts" / "acceptance" / "t113_paid_sample_runner.py"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_proxy_classifies_only_billable_task_starts(tmp_path: Path) -> None:
    proxy = _load_module("t113_real_provider_proxy", PROXY_SCRIPT)
    server = proxy.ProviderProxyServer(
        role="test",
        base_url="https://provider.invalid",
        api_key="test",
        budget_journal=object(),
    )
    try:
        assert server.budget_kind_for_request("POST", "/v1/chat/completions") == (
            "textRequests"
        )
        assert server.budget_kind_for_request(
            "POST",
            "/v1/images/generations",
        ) == "imageTaskStarts"
        assert server.budget_kind_for_request(
            "POST",
            "/v1/images/edits",
        ) == "imageTaskStarts"
        assert server.budget_kind_for_request(
            "POST",
            "/v1/videos",
        ) == "videoTaskStarts"
        assert server.budget_kind_for_request(
            "POST",
            "/v2/video_generation/",
        ) == "videoTaskStarts"
        assert server.budget_kind_for_request(
            "GET",
            "/v2/query/video_generation/task-1",
        ) == ""
        assert server.budget_kind_for_request(
            "POST",
            "/v2/query/video_generation/task-1",
        ) == ""
        assert server.budget_kind_for_request("GET", "/v1/videos") == ""
    finally:
        server.server_close()


def test_minimax_v2_proxy_target_stays_on_the_station_root() -> None:
    proxy = _load_module("t113_real_provider_proxy_minimax_root", PROXY_SCRIPT)

    target = proxy.proxy_target_base_url(
        "https://dmc.cc/v1",
        protocol="minimax-video-v2",
    )

    assert target == "https://dmc.cc"
    assert proxy.merge_upstream_url(target, "/v2/video_generation") == (
        "https://dmc.cc/v2/video_generation"
    )
    assert proxy.merge_upstream_url(
        target,
        "/v2/query/video_generation/task-1",
    ) == "https://dmc.cc/v2/query/video_generation/task-1"
    assert proxy.proxy_target_base_url(
        "https://relay.example/v1",
        protocol="openai-video",
    ) == "https://relay.example/v1"


def test_proxy_preserves_encoded_upstream_json_without_forwarding_encoding_negotiation(
    tmp_path: Path,
) -> None:
    proxy = _load_module("t113_real_provider_proxy_encoding", PROXY_SCRIPT)

    class _GzipUpstream(BaseHTTPRequestHandler):
        seen_accept_encoding: str | None = None

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).seen_accept_encoding = self.headers.get("Accept-Encoding")
            body = gzip.compress(
                json.dumps({"data": [{"id": "gemini-3.8-flash"}]}).encode(
                    "utf-8"
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _GzipUpstream)
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        name="t113-gzip-upstream",
        daemon=True,
    )
    upstream_thread.start()
    server = proxy.ProviderProxyServer(
        role="text",
        base_url=f"http://127.0.0.1:{upstream.server_address[1]}",
        api_key="test",
    )
    proxy_thread = threading.Thread(
        target=server.serve_forever,
        name="t113-gzip-proxy",
        daemon=True,
    )
    proxy_thread.start()
    try:
        request = Request(
            f"{server.base_url}/v1/models",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        with urlopen(request, timeout=5) as response:
            raw = response.read()
            assert response.headers.get("Content-Encoding") == "gzip"
        assert _GzipUpstream.seen_accept_encoding in (None, "identity")
        assert json.loads(gzip.decompress(raw)) == {
            "data": [{"id": "gemini-3.8-flash"}]
        }
    finally:
        server.shutdown()
        server.server_close()
        proxy_thread.join(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)


def test_image_video_and_text_starts_have_independent_atomic_limits(
    tmp_path: Path,
) -> None:
    proxy = _load_module("t113_real_provider_proxy_budget", PROXY_SCRIPT)
    contract = _load_module("t113_single_shot_paid_contract", CONTRACT_SCRIPT)
    plan = contract.build_plan()
    plan["providerStartLimits"]["imageTaskStarts"] = 1
    plan["providerStartLimits"]["videoTaskStarts"] = 1
    journal = proxy.BudgetJournal(
        tmp_path / "provider-budget.json",
        plan=plan,
        reserve_start=contract.reserve_provider_start,
    )

    for kind in ("textRequests", "imageTaskStarts", "videoTaskStarts"):
        first = journal.reserve(kind, {"method": "POST", "path": f"/{kind}"})
        assert first["ok"] is True
        assert first["next"] == 1
        second = journal.reserve(kind, {"method": "POST", "path": f"/{kind}"})
        if kind == "textRequests":
            assert second["ok"] is True
            assert second["next"] == 2
        else:
            assert second["ok"] is False
            assert second["reason"] == "provider_start_limit_reached"

    snapshot = journal.snapshot()
    assert snapshot["providerCallsStarted"] is True
    assert snapshot["counters"]["textRequests"] == 2
    assert snapshot["counters"]["imageTaskStarts"] == 1
    assert snapshot["counters"]["videoTaskStarts"] == 1


def test_default_video_budget_persists_across_journal_instances(
    tmp_path: Path,
) -> None:
    proxy = _load_module("t113_real_provider_proxy_persistent_budget", PROXY_SCRIPT)
    contract = _load_module("t113_single_shot_paid_contract_persistent", CONTRACT_SCRIPT)
    budget_path = tmp_path / "provider-budget.json"
    budget_path.write_text(
        json.dumps(
            {
                "schema": "t113_provider_budget.v1",
                "counters": {"videoTaskStarts": 500},
                "events": [],
                "providerCallsStarted": True,
            }
        ),
        encoding="utf-8",
    )

    journal = proxy.BudgetJournal(
        budget_path,
        plan=contract.build_plan(),
        reserve_start=contract.reserve_provider_start,
    )
    denied = journal.reserve(
        "videoTaskStarts",
        {"method": "POST", "path": "/v2/video_generation"},
    )

    assert denied["ok"] is False
    assert denied["current"] == 500
    assert denied["limit"] == 500
    assert denied["reason"] == "provider_start_limit_reached"


def test_image_and_video_proxies_share_the_same_http_budget_journal(
    tmp_path: Path,
) -> None:
    proxy = _load_module("t113_real_provider_proxy_http_budget", PROXY_SCRIPT)
    contract = _load_module("t113_single_shot_paid_contract_http", CONTRACT_SCRIPT)

    class _Upstream(BaseHTTPRequestHandler):
        counts: dict[str, int] = {}

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            path = self.path.split("?", 1)[0]
            self.counts[path] = self.counts.get(path, 0) + 1
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        name="t113-budget-upstream",
        daemon=True,
    )
    upstream_thread.start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    plan = contract.build_plan()
    plan["providerStartLimits"]["imageTaskStarts"] = 1
    plan["providerStartLimits"]["videoTaskStarts"] = 1
    journal = proxy.BudgetJournal(
        tmp_path / "provider-budget.json",
        plan=plan,
        reserve_start=contract.reserve_provider_start,
    )
    cluster = proxy.ProviderProxyCluster(
        text_base_url=upstream_url,
        text_api_key="test",
        image_base_url=upstream_url,
        image_api_key="test",
        video_base_url=upstream_url,
        video_api_key="test",
        budget_journal=journal,
    )

    def post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return int(response.status), json.loads(response.read() or b"{}")
        except HTTPError as exc:
            return int(exc.code), json.loads(exc.read() or b"{}")

    try:
        image_url = f"{cluster.image.base_url}/v1/images/generations"
        image_payload = {
            "model": "gpt-image-2.5-sunburst",
            "prompt": "test",
            "size": "1024x576",
            "extra_fields": {"aspect_ratio": "16:9", "image_size": "1K"},
        }
        assert post(image_url, image_payload)[0] == 200
        image_denied, image_error = post(image_url, image_payload)
        assert image_denied == 429
        assert image_error["error"]["reason"] == "provider_start_limit_reached"

        video_url = f"{cluster.video.base_url}/v2/video_generation"
        video_payload = {
            "model": "MiniMax-H3",
            "resolution": "768P",
            "duration": 5,
            "ratio": "adaptive",
            "content": [
                {"type": "text", "text": "test"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.invalid/frame.png"},
                    "role": "first_frame",
                },
            ],
        }
        assert post(video_url, video_payload)[0] == 200
        video_denied, video_error = post(video_url, video_payload)
        assert video_denied == 429
        assert video_error["error"]["reason"] == "provider_start_limit_reached"

        assert _Upstream.counts == {
            "/v1/images/generations": 1,
            "/v2/video_generation": 1,
        }
        image_record = cluster.image.snapshot()[0]
        assert image_record["aspect_ratio"] == "16:9"
        assert image_record["image_size"] == "1K"
        video_record = cluster.video.snapshot()[0]
        assert video_record["resolution"] == "768P"
        assert video_record["ratio"] == "adaptive"
        assert video_record["duration"] == 5
        assert video_record["content_roles"] == ["first_frame"]
    finally:
        cluster.shutdown()
        cluster.server_close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)


def test_t113_fixture_has_no_asset_references_before_the_single_image_shot() -> None:
    from novelvideo.workflow_runtime.script_asset_ledger import (
        build_script_asset_ledger,
    )
    from novelvideo.freezone.script_contract import validate_script_rows

    runner = _load_module("t113_paid_sample_runner", RUNNER_SCRIPT)
    row = runner.build_t113_script_row(
        {
            "shot_no": 1,
            "duration": 2,
            "visual_description": "旧照相馆暗房里，阿木站在红灯下举起相机。",
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
            "scene_tags": "旧照相馆、暗房、红灯",
            "prop_tags": "相机、红灯",
            "shot_prompt": "[角色卡] [阿木] + [场景环境] 旧照相馆暗房",
            "video_motion_prompt": "[时长] [时长：2s]",
        }
    )

    ledger = build_script_asset_ledger([row])
    assert ledger["assets"] == []
    assert validate_script_rows([row]).blocking == []
    assert row["duration"] == 5
    assert "[时长：5s]" in row["video_motion_prompt"]
    assert "阿木" not in row["shot_prompt"]
    assert "旧照相馆" not in row["scene_tags"]
