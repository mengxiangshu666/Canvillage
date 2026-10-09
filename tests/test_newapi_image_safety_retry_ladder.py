from __future__ import annotations

import asyncio
import base64


def test_generic_safety_retry_ladder_reduces_five_references_without_loop(monkeypatch) -> None:
    import httpx

    from novelvideo.generators import nanobanana_grid

    attempts: list[dict] = []

    class GenericSafetyResponse:
        status_code = 400
        text = "您的请求无法用于生成图像。该请求可能因安全政策被拦截，或不适合进行图像生成。"
        headers = {"x-oneapi-request-id": "generic-safety"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class SuccessResponse:
        status_code = 200
        headers = {"x-oneapi-request-id": "generic-safety-recovered"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "recovered",
                "data": [{"b64_json": base64.b64encode(b"recovered").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            attempts.append(json)
            return GenericSafetyResponse() if len(attempts) < 4 else SuccessResponse()

    class FakeUsageMeter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            return "reservation"

        async def refund_model_call_credit_reservation(self, *_args, **_kwargs):
            return None

        async def bump_model_call(self, **_kwargs):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        nanobanana_grid,
        "upload_image_bytes",
        lambda data, **_kwargs: f"https://relay.test/{len(data)}.jpg",
    )
    monkeypatch.setattr(nanobanana_grid, "get_usage_meter", lambda: FakeUsageMeter())

    prompt = "REFERENCE PRIORITY — Image 1 is authoritative.\n" + (
        "cinematic rain-soaked alley, paper lantern reflections, quiet blue-hour atmosphere. "
        * 80
    )
    image, _text, error = asyncio.run(
        nanobanana_grid._call_newapi_image_api(
            api_key="token",
            model="LingShan-G2",
            prompt=prompt,
            reference_images=[b"one", b"two", b"three", b"four", b"five"],
            image_config={"aspect_ratio": "9:16", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image == b"recovered"
    assert error == ""
    assert [len(item.get("images") or []) for item in attempts] == [5, 5, 4, 3]
    assert len(attempts[2]["prompt"]) <= 220
    assert len(attempts[3]["prompt"]) <= 220
