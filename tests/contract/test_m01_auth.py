from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novelvideo.ports.auth_contract import AuthError, AuthFailureReason

pytestmark = pytest.mark.m01


def _reset_port_modules():
    import novelvideo.ports as ports
    import novelvideo.ports.local as local_ports
    import novelvideo.ports.registry as registry

    registry = importlib.reload(registry)
    ports = importlib.reload(ports)
    local_ports = importlib.reload(local_ports)
    return registry, ports, local_ports


def _patch_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"

    import novelvideo.api.deps as deps
    import novelvideo.api.routes.projects as project_routes
    import novelvideo.config as config
    import novelvideo.project_config as project_config
    import novelvideo.project_context as project_context
    import novelvideo.utils.project_paths as project_paths

    monkeypatch.setenv("NOVELVIDEO_DATA_ROOT", str(data_root))
    monkeypatch.setattr(config, "DATA_ROOT", str(data_root), raising=False)
    for module in (config, deps, project_paths):
        monkeypatch.setattr(module, "OUTPUT_DIR", str(output), raising=False)
        monkeypatch.setattr(module, "STATE_DIR", str(state), raising=False)
        monkeypatch.setattr(module, "RUNTIME_DIR", str(runtime), raising=False)
    monkeypatch.setattr(project_config, "OUTPUT_DIR", str(state), raising=False)
    monkeypatch.setattr(project_config, "STATE_DIR", str(state), raising=False)
    monkeypatch.setattr(project_routes, "resolve_worker_id", lambda: "node_local", raising=False)
    monkeypatch.setattr(project_context, "resolve_worker_id", lambda: "node_local")


class _RejectingAuthPort:
    async def verify_session(self, raw_cookie: str | None) -> dict:
        if raw_cookie is None:
            raise AuthError(AuthFailureReason.MISSING, "Missing session or agent token")
        raise AuthError(AuthFailureReason.INVALID, "Invalid session")

    async def revoke_session(self, raw_cookie: str) -> None:  # noqa: ARG002
        return None


def _ce_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    registry, _, _ = _reset_port_modules()
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")
    for module_name in list(sys.modules):
        if module_name == "novelvideo.api" or module_name.startswith("novelvideo.api."):
            sys.modules.pop(module_name)
    _patch_roots(monkeypatch, tmp_path)

    from novelvideo.model_gateway_settings import save_direct_models
    from novelvideo.generators.direct_model_capability_cache import (
        record_direct_model_capability,
    )

    save_direct_models(
        "embedding",
        [
            {
                "label": "合同测试向量模型",
                "modelId": "bge-m3",
                "baseUrl": "https://embedding.example/v1",
                "apiKey": "sk-contract-placeholder",
                "enabled": True,
                "isDefault": True,
            }
        ],
    )
    record_direct_model_capability(
        base_url="https://embedding.example/v1",
        kind="embedding",
        upstream_model="bge-m3",
        protocol="openai-embeddings",
        capability={
            "probeContractVersion": 1,
            "verificationStatus": "catalog-confirmed",
            "modelFound": True,
            "discoveredModelCount": 1,
            "embeddingProbeStatus": "passed",
            "embeddingResponseUsable": True,
            "embeddingDimensions": 1024,
        },
    )

    from novelvideo.ports.local import project as local_project

    monkeypatch.setattr(local_project, "resolve_worker_id", lambda: "node_local", raising=False)
    registry.ensure_bootstrap()

    from novelvideo.api.app import create_app

    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    return TestClient(app)


def test_ce_auth_me_logout_and_project_crud_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _ce_client(monkeypatch, tmp_path) as client:
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == {
            "ok": True,
            "data": {
                "username": "local",
                "role": "owner",
                "credit_balance": 0,
                "credential_kind": "user",
                "current_scope_kind": None,
                "current_project_id": None,
                "scopes": None,
            },
        }

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"ok": True}
        assert "st_session=" in logout.headers["set-cookie"]
        assert "Max-Age=0" in logout.headers["set-cookie"]

        login = client.post("/api/v1/auth/login", json={"username": "local", "password": "x"})
        assert login.status_code == 404

        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        assert "/api/v1/auth/login" not in openapi.json()["paths"]

        created = client.post("/api/v1/projects", json={"name": "demo"})
        assert created.status_code == 200
        project_id = created.json()["data"]["project_id"]
        assert (tmp_path / "data" / "项目" / "demo").is_dir()

        listed = client.get("/api/v1/projects")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["id"] == project_id

        detail = client.get(f"/api/v1/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["project_id"] == project_id
        assert detail.json()["data"]["cognee_embedding_model"].startswith("direct/")
        assert detail.json()["data"]["cognee_embedding_dimension"] == 1024


@pytest.mark.ee
def test_ee_auth_missing_and_bad_cookie_contract() -> None:
    registry, _, _ = _reset_port_modules()
    registry.register_port("auth", _RejectingAuthPort())

    from novelvideo.api.app import create_app

    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    with TestClient(app) as client:
        missing = client.get("/api/v1/auth/me")
        assert missing.status_code == 401
        assert missing.json()["detail"] == "Missing session or agent token"

        bad = client.get("/api/v1/auth/me", cookies={"st_session": "bad-cookie"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid session"

        missing_logout = client.post("/api/v1/auth/logout")
        assert missing_logout.status_code == 200
        assert missing_logout.json() == {"ok": True}
        assert "st_session=" in missing_logout.headers["set-cookie"]
        assert "Max-Age=0" in missing_logout.headers["set-cookie"]

        bad_logout = client.post(
            "/api/v1/auth/logout",
            cookies={"st_session": "bad-cookie"},
        )
        assert bad_logout.status_code == 200
        assert bad_logout.json() == {"ok": True}
        assert "st_session=" in bad_logout.headers["set-cookie"]
        assert "Max-Age=0" in bad_logout.headers["set-cookie"]
