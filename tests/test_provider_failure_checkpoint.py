from novelvideo.chat.context_checkpoint import build_checkpoint
from novelvideo.chat import service as chat_service


def test_provider_error_classification_covers_balance_and_transient_failures() -> None:
    classify = chat_service._classify_provider_error

    assert (
        classify(
            "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"
        )
        == "provider_balance_insufficient"
    )
    assert classify("401 Unauthorized invalid_api_key") == "provider_auth_failed"
    assert classify("429 rate limit exceeded") == "provider_rate_limited"
    assert classify("503 service unavailable") == "provider_unavailable"
    assert classify("ordinary tool failure") == ""


def test_structured_provider_error_survives_checkpoint_bounding(tmp_path) -> None:
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="完成一个样片",
        pending_steps=[{"step": "recover_provider_failure"}],
        active_errors=[
            {
                "type": "provider_balance_insufficient",
                "message": "Error code: 402 - Insufficient Balance",
            }
        ],
        next_action="blocked:provider_balance_insufficient；等待上游余额恢复。",
    )

    assert checkpoint["active_errors"] == [
        {
            "type": "provider_balance_insufficient",
            "message": "Error code: 402 - Insufficient Balance",
        }
    ]
    assert checkpoint["next_action"].startswith("blocked:provider_balance_insufficient")
