from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import ingest


def test_project_gateway_health_runs_project_scoped_live_probe(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(ingest.router)
    app.dependency_overrides[ingest.get_api_user] = lambda: {"username": "alice"}

    async def resolve_project_scope(project, user, required_role):
        assert project == "project-1"
        assert user == {"username": "alice"}
        assert required_role == "viewer"
        return SimpleNamespace(state_dir=tmp_path / "project-state")

    calls = []

    async def inspect_cognee_gateway(*, state_dir, force):
        calls.append((state_dir, force))
        return {
            "ok": True,
            "status": "ready",
            "chat": {"model": "DC-cognee-LLM", "baseUrl": "https://relay.example/v1"},
            "embedding": {
                "model": "DC-cognee-embedding-v2",
                "baseUrl": "https://relay.example/v1",
                "dimensions": 1024,
            },
        }

    monkeypatch.setattr(ingest, "resolve_project_scope", resolve_project_scope)
    monkeypatch.setattr(ingest, "inspect_cognee_gateway", inspect_cognee_gateway)

    response = TestClient(app).get("/projects/project-1/ingest/gateway-health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"
    assert calls == [(tmp_path / "project-state", True)]
