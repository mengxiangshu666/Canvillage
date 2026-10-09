import asyncio
import base64
import re

import httpx
import pytest


pytestmark = pytest.mark.m04


_GENERIC_SAFETY_BODY = (
    "您的请求无法用于生成图像。该请求可能因安全政策被拦截，"
    "或不适合进行图像生成。"
)


def _render_prompt() -> str:
    return """Colorize this 1×1 storyboard SKETCH (first attached image / Image 1) into a full-color continuous image. Each panel MUST be 2:3 PORTRAIT.
STYLE: Refined Chinese folk paper-cut graphic style, layered silhouettes, 剪纸风格，宣纸纤维，手工刀刻边缘。
Image 1 / SKETCH IS the base drawing — preserve ALL composition, crop, poses, and camera angles exactly.
REFERENCE IMAGES:
  Image 1 = SKETCH TO COLORIZE
  Image 2 = [DSZ_aa0e]: multi-view character reference sheet.
  Image 3 = [SW_0667]: multi-view character reference sheet.
  Image 4 = Prop "协议": prop identity reference.
  Image 5 = Scene "会议室": environment reference asset.
THIS IS A COLORIZATION TASK — the sketch is the BASE DRAWING, NOT just a reference.
- Visual description: 俯拍会议桌，[DSZ_aa0e]把协议[XY_faa1]推向[SW_0667]。
""" + ("verbose duplicated render contract " * 420)


def _short_render_prompt() -> str:
    prompt = """COLORIZE THIS STORYBOARD SKETCH.
STYLE: 剪纸风格，宣纸纤维。
Image 1 / SKETCH is the only layout authority.
Image 2 = [A_01]: person identity.
Image 3 = [B_02]: person identity.
Image 4 = Prop "协议": prop identity reference.
Image 5 = Scene "会议室": environment reference asset.
THIS IS A COLORIZATION TASK.
- Visual description: 俯拍会议桌，[A_01]把协议推向[B_02]。
"""
    assert len(prompt) <= 480
    return prompt


class _UsageMeter:
    def __init__(self) -> None:
        self.reservations = 0
        self.refunds = 0
        self.confirmations = 0

    async def reserve_current_model_call_credit(self, **_kwargs):
        self.reservations += 1
        return f"reservation-{self.reservations}"

    async def refund_model_call_credit_reservation(
        self,
        _reservation_id,
        *,
        metadata=None,
    ):
        self.refunds += 1

    async def bump_model_call(self, **_kwargs):
        self.confirmations += 1


def _assert_prompt_indices_are_in_range(payload: dict[str, object]) -> None:
    prompt = str(payload["prompt"])
    image_count = len(payload.get("images") or [])
    referenced = {
        int(number)
        for number in re.findall(r"(?:\bImage\s*|\bI)(\d+)\b", prompt, re.IGNORECASE)
    }
    assert referenced
    assert min(referenced) == 1
    assert max(referenced) <= image_count


def test_reference_budget_drops_one_reference_at_a_time_and_stops_at_three():
    from novelvideo.generators.newapi_image_uplink import next_newapi_reference_budget

    assert [next_newapi_reference_budget(count) for count in (6, 5, 4, 3, 2)] == [
        5,
        4,
        3,
        None,
        None,
    ]


def test_generic_400_compacts_then_reduces_reference_budget(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    posted: list[dict[str, object]] = []
    usage = _UsageMeter()

    class FakeResponse:
        def __init__(self, attempt: int) -> None:
            self.status_code = 200 if attempt == 3 else 400
            self.text = "" if self.status_code == 200 else _GENERIC_SAFETY_BODY
            self.headers = {"x-oneapi-request-id": f"req-{attempt}"}

        def raise_for_status(self):
            if self.status_code == 400:
                raise httpx.HTTPStatusError(
                    "bad request",
                    request=httpx.Request(
                        "POST",
                        "http://newapi.test/v1/images/generations",
                    ),
                    response=self,
                )

        def json(self):
            return {
                "id": "resp-reference-budget",
                "data": [{"b64_json": base64.b64encode(b"recovered").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def post(self, _url, *, headers, json):
            posted.append(json)
            return FakeResponse(len(posted))

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/{bytes(data).decode()}.{ext}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: usage)
    diagnostics: dict[str, object] = {}

    image_bytes, _text, error = asyncio.run(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=_render_prompt(),
            reference_images=[
                b"sketch",
                b"person-a",
                b"person-b",
                b"prop",
                b"scene",
            ],
            image_config={
                "aspect_ratio": "2:3",
                "image_size": "1K",
                "quality": "medium",
                "_submission_diagnostics": diagnostics,
                "_reference_role_labels": [
                    "previous_grid:sketch",
                    "identity:person-a",
                    "identity:person-b",
                    "prop:协议",
                    "scene:会议室",
                ],
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"recovered"
    assert error == ""
    assert [len(payload["images"]) for payload in posted] == [5, 5, 4]
    assert len(str(posted[0]["prompt"])) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_SOFT_LIMIT
    assert len(str(posted[1]["prompt"])) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT
    assert len(str(posted[2]["prompt"])) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT

    for payload in posted:
        _assert_prompt_indices_are_in_range(payload)

    # The editable sketch, current-shot identities and explicit prop are retained;
    # the scene plate is the first removable reference because layout comes from Image1.
    assert posted[2]["images"] == [
        "https://relay.test/sketch.png",
        "https://relay.test/person-a.png",
        "https://relay.test/person-b.png",
        "https://relay.test/prop.png",
    ]
    assert "I5" not in str(posted[2]["prompt"])
    assert "Image 5" not in str(posted[2]["prompt"])
    assert usage.reservations == 3
    assert usage.refunds == 2
    assert usage.confirmations == 1
    attempts = diagnostics["attempts"]
    assert [item["reference_count_effective"] for item in attempts] == [5, 5, 4]
    assert [item["outcome"] for item in attempts] == ["http_400", "http_400", "completed"]
    assert attempts[-1]["prompt_stage"] == "render"
    assert attempts[-1]["model_family"] == "lingshan_g2"
    assert attempts[-1]["reference_roles_kept"][-1] == "prop:协议"
    assert attempts[-1]["reference_roles_dropped"] == ["scene:会议室"]
    assert diagnostics["language_policy"] == "english_contract+source_language_semantics"


def test_short_render_prompt_still_reduces_reference_budget(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    posted: list[dict[str, object]] = []
    usage = _UsageMeter()

    class FakeResponse:
        def __init__(self, attempt: int) -> None:
            self.status_code = 200 if attempt == 2 else 400
            self.text = "" if self.status_code == 200 else _GENERIC_SAFETY_BODY
            self.headers = {"x-oneapi-request-id": f"short-{attempt}"}

        def raise_for_status(self):
            if self.status_code == 400:
                raise httpx.HTTPStatusError(
                    "bad request",
                    request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                    response=self,
                )

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"short-ok").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def post(self, _url, *, headers, json):
            posted.append(json)
            return FakeResponse(len(posted))

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        nanobanana_grid,
        "upload_image_bytes",
        lambda data, **_kwargs: f"https://relay.test/{bytes(data).decode()}.png",
    )
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: usage)

    image_bytes, _text, error = asyncio.run(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=_short_render_prompt(),
            reference_images=[b"sketch", b"person-a", b"person-b", b"prop", b"scene"],
            image_config={"aspect_ratio": "2:3", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"short-ok"
    assert error == ""
    assert [len(payload["images"]) for payload in posted] == [5, 4]
    assert "Image 5" not in str(posted[1]["prompt"])
    for payload in posted:
        _assert_prompt_indices_are_in_range(payload)


def test_generic_400_reference_budget_retries_are_bounded(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    posted: list[dict[str, object]] = []
    usage = _UsageMeter()

    class SafetyResponse:
        status_code = 400
        text = _GENERIC_SAFETY_BODY
        headers = {"x-oneapi-request-id": "req-always-rejected"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request(
                    "POST",
                    "http://newapi.test/v1/images/generations",
                ),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def post(self, _url, *, headers, json):
            posted.append(json)
            return SafetyResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/{bytes(data).decode()}.{ext}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(nanobanana_grid, "upload_image_bytes", fake_upload_image_bytes)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: usage)
    diagnostics: dict[str, object] = {}
    trace: dict[str, str] = {}

    image_bytes, _text, error = asyncio.run(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt=_render_prompt(),
            reference_images=[
                b"sketch",
                b"person-a",
                b"person-b",
                b"prop",
                b"scene",
            ],
            image_config={
                "aspect_ratio": "2:3",
                "image_size": "1K",
                "quality": "medium",
                "_submission_diagnostics": diagnostics,
            },
            base_url="http://newapi.test/v1",
            trace=trace,
        )
    )

    assert image_bytes is None
    assert "HTTP 400" in error
    assert [len(payload["images"]) for payload in posted] == [5, 5, 4, 3]
    for payload in posted:
        _assert_prompt_indices_are_in_range(payload)
    assert all(payload["images"][0].endswith("/sketch.png") for payload in posted)
    assert usage.reservations == 4
    assert usage.refunds == 4
    assert usage.confirmations == 0
    assert [item["outcome"] for item in diagnostics["attempts"]] == [
        "http_400",
        "http_400",
        "http_400",
        "http_400",
    ]
    assert trace["request_id"] == "req-always-rejected"


def test_missing_image_data_attempts_are_audited_not_left_pending(monkeypatch):
    from novelvideo.generators import nanobanana_grid

    usage = _UsageMeter()
    diagnostics: dict[str, object] = {}

    class EmptyResponse:
        status_code = 200
        text = ""
        headers = {"x-oneapi-request-id": "missing-data"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def post(self, _url, *, headers, json):
            return EmptyResponse()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: usage)

    image_bytes, _text, error = asyncio.run(
        nanobanana_grid._call_newapi_image_api(
            api_key="newapi-token",
            model="LingShan-G2",
            prompt="Create a simple image.",
            image_config={
                "aspect_ratio": "1:1",
                "image_size": "1K",
                "_submission_diagnostics": diagnostics,
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "missing data" in error
    assert [item["outcome"] for item in diagnostics["attempts"]] == [
        "missing_data",
        "missing_data",
        "missing_data",
    ]


def test_render_compactor_uses_minimal_bilingual_model_contract():
    from novelvideo.generators import nanobanana_grid

    compacted, meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        _render_prompt(),
        max_chars=nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT,
        reference_count=4,
        model="LingShan-G2",
    )

    assert meta["render_colorization_mode"] is True
    assert meta["prompt_stage"] == "render"
    assert meta["model_family"] == "lingshan_g2"
    assert meta["language_policy"] == "english_contract+source_language_semantics"
    assert len(compacted) <= nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT
    # Stable model instructions stay concise English; story/style semantics stay Chinese.
    assert compacted.startswith("COLORIZE Image1 SKETCH")
    assert "NEVER layout" in compacted
    assert "协议" in compacted
    assert "剪纸" in compacted
    assert "俯拍会议桌" in compacted
    assert "[XY_faa1]" not in compacted
    assert "verbose duplicated render contract" not in compacted
    assert "I5" not in compacted
    assert "Image 5" not in compacted


def test_render_prompt_profile_changes_with_model_family():
    from novelvideo.generators import nanobanana_grid

    ling_shan, ling_meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        _render_prompt(),
        max_chars=nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT,
        reference_count=4,
        model="LingShan-G2",
    )
    gpt_image, gpt_meta = nanobanana_grid.compact_prompt_for_newapi_upstream(
        _render_prompt(),
        max_chars=nanobanana_grid.NEWAPI_UPSTREAM_PROMPT_HARD_LIMIT,
        reference_count=4,
        model="gpt-image-1",
    )

    assert ling_shan.startswith("COLORIZE Image1 SKETCH")
    assert gpt_image.startswith("EDIT INPUT IMAGE 1")
    assert ling_shan != gpt_image
    assert ling_meta["model_family"] == "lingshan_g2"
    assert gpt_meta["model_family"] == "gpt_image"
    assert ling_meta["language_policy"] == (
        "english_contract+source_language_semantics"
    )
