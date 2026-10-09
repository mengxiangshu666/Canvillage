from pathlib import Path

import pytest

from novelvideo.workflow_runtime import compose_authorization


SIGNATURE_A = "a" * 64
SIGNATURE_B = "b" * 64


def _scope(
    *,
    signature: str = SIGNATURE_A,
    run_id: str = "wfr-compose-auth",
) -> dict[str, str]:
    return {
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "run_id": run_id,
        "step_id": "final_film",
        "source_result_signature": signature,
    }


def test_compose_authorization_is_exact_once_and_replayable(tmp_path: Path) -> None:
    ticket = compose_authorization.issue_compose_authorization(
        tmp_path,
        **_scope(),
    )

    allowed, reason, consumed = compose_authorization.consume_compose_authorization(
        tmp_path,
        authorization_id=ticket["id"],
        consume_key="consume-1",
        **_scope(),
    )
    assert allowed is True
    assert reason == "compose_authorization"
    assert consumed is not None
    assert consumed["consumed_at_ms"] > 0

    replay_allowed, replay_reason, _ = (
        compose_authorization.consume_compose_authorization(
            tmp_path,
            authorization_id=ticket["id"],
            consume_key="consume-1",
            **_scope(),
        )
    )
    assert replay_allowed is True
    assert replay_reason == "compose_authorization_replay"

    reused_allowed, reused_reason, _ = (
        compose_authorization.consume_compose_authorization(
            tmp_path,
            authorization_id=ticket["id"],
            consume_key="consume-2",
            **_scope(),
        )
    )
    assert reused_allowed is False
    assert reused_reason == "compose_authorization_already_consumed"


def test_compose_authorization_rejects_forgery_scope_and_stale_signature(
    tmp_path: Path,
) -> None:
    ticket = compose_authorization.issue_compose_authorization(
        tmp_path,
        **_scope(),
    )

    allowed, reason, _ = compose_authorization.consume_compose_authorization(
        tmp_path,
        authorization_id="wca_forged",
        consume_key="consume-1",
        **_scope(),
    )
    assert allowed is False
    assert reason == "compose_authorization_not_found"

    allowed, reason, _ = compose_authorization.consume_compose_authorization(
        tmp_path,
        authorization_id=ticket["id"],
        consume_key="consume-1",
        **_scope(run_id="wfr-other"),
    )
    assert allowed is False
    assert reason == "compose_authorization_scope_mismatch"

    allowed, reason, _ = compose_authorization.consume_compose_authorization(
        tmp_path,
        authorization_id=ticket["id"],
        consume_key="consume-1",
        **_scope(signature=SIGNATURE_B),
    )
    assert allowed is False
    assert reason == "compose_authorization_source_stale"


def test_compose_authorization_rotates_with_source_signature_and_expires(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(compose_authorization.time, "time", lambda: 1000.0)
    first = compose_authorization.issue_compose_authorization(
        tmp_path,
        **_scope(signature=SIGNATURE_A),
        ttl_seconds=60,
    )
    second = compose_authorization.issue_compose_authorization(
        tmp_path,
        **_scope(signature=SIGNATURE_B),
        ttl_seconds=60,
    )

    old = compose_authorization.get_compose_authorization(
        tmp_path,
        first["id"],
    )
    assert old is not None
    assert old["expires_at_ms"] <= second["created_at_ms"]

    monkeypatch.setattr(compose_authorization.time, "time", lambda: 1061.0)
    allowed, reason, _ = compose_authorization.consume_compose_authorization(
        tmp_path,
        authorization_id=second["id"],
        consume_key="consume-expired",
        **_scope(signature=SIGNATURE_B),
    )
    assert allowed is False
    assert reason == "compose_authorization_expired"
