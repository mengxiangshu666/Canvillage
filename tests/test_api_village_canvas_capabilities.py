"""HTTP contract for the village-canvas capability discovery route."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api import village_canvas_capabilities as route

REQUIRED_CARD_KEYS = {"id", "purpose", "domain"}
FORBIDDEN_KEY_HINTS = ("key", "token", "secret", "credential", "password")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[route.get_api_user] = lambda: {"id": "local"}
    return TestClient(app)


def test_capability_index_publishes_every_card_in_the_public_shape():
    with _client() as client:
        response = client.get("/api/v1/village-canvas/capabilities")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["count"] >= 90
    ids = [card["id"] for card in payload["capabilities"]]
    assert len(ids) == payload["count"] == len(set(ids))
    for card in payload["capabilities"]:
        assert REQUIRED_CARD_KEYS <= set(card)
        assert "search_terms" not in card
        assert not any(str(key).startswith("_") for key in card)
        assert not any(
            hint in str(key).casefold() for key in card for hint in FORBIDDEN_KEY_HINTS
        )


def test_capability_route_filters_by_exact_id_and_free_text():
    with _client() as client:
        everything = client.get("/api/v1/village-canvas/capabilities").json()["data"]
        sample_id = everything["capabilities"][0]["id"]
        one = client.get(
            "/api/v1/village-canvas/capabilities", params={"id": sample_id}
        ).json()["data"]
        miss = client.get(
            "/api/v1/village-canvas/capabilities", params={"id": "no-such-card"}
        ).json()["data"]
        searched = client.get(
            "/api/v1/village-canvas/capabilities", params={"query": "canvas"}
        ).json()["data"]

    assert one["count"] == 1 and one["capabilities"][0]["id"] == sample_id
    assert miss == {"count": 0, "capabilities": []}
    assert 0 < searched["count"] <= everything["count"]


def test_capability_route_rejects_unauthenticated_access():
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/village-canvas/capabilities")

    assert response.status_code >= 400
    assert response.status_code != 200
