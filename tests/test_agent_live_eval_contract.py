from __future__ import annotations

import json

from novelvideo.agent_tools.skills import list_agent_skills
from novelvideo.chat.agent_evals import evaluate_agent_trials
from novelvideo.chat.skill_routing_cases import SKILL_ROUTING_CASES
from scripts.evaluate_agent_live import (
    CASE_BY_ID,
    SKILL_ROUTING_LIVE_CASES,
    TARGET_NODE_ID,
    V2_CLOSE,
    V2_OPEN,
    _case_trace_contract,
    _parse_case_ids,
    _skill_load_receipts,
    _turn_delivery_receipt,
    _turn_intent_receipt,
    build_canvas_context,
    build_synthetic_canvas,
    build_v2_request,
    cleanup_project,
    create_project,
    get_canvas,
    harness_verification_frame,
    merge_trace_frames,
    project_exists,
    verify_case_state,
)


PROJECT_ID = "project-eval"
CANVAS_ID = "canvas-eval"


def _skill_load_frame(
    name: str,
    *,
    action: str | None = None,
    success: bool = True,
    sha256: str = "a" * 64,
    size_bytes: int = 123,
) -> dict:
    payload = {
        "schema": "village_agent_skill.v1",
        "ok": True,
        "name": name,
        "source": "bundled",
        "version": "1.0.0",
        "bytes": size_bytes,
        "sha256": sha256,
    }
    if action:
        payload["action"] = action
    return {
        "type": "tool.result",
        "name": "skill",
        "success": success,
        "result": {"text": json.dumps(payload)},
    }


def _turn_intent_frame(
    status: str,
    *,
    source: str = "",
    contract_hash: str = "",
    side_effect_tools: tuple[str, ...] = (),
) -> dict:
    return {
        "type": "thread_started",
        "turn_intent_receipt": {
            "schema": "village_turn_intent_receipt.v1",
            "status": status,
            "source": source,
            "contract_hash": contract_hash,
            "delivery_mode": "state_change" if status == "locked" else "",
            "requirement_count": 1 if status == "locked" else 0,
            "side_effect_tools": list(side_effect_tools),
        },
    }


def _turn_delivery_frame(
    status: str,
    *,
    reason_code: str = "terminal_tool_evidence_present",
    allow_finish: bool | None = None,
    verified: tuple[dict, ...] = (),
    pending: tuple[dict, ...] = (),
    blocked: tuple[dict, ...] = (),
) -> dict:
    return {
        "type": "complete",
        "turn_delivery_receipt": {
            "schema": "village_turn_delivery_receipt.v1",
            "status": status,
            "reason_code": reason_code,
            "allow_finish": (
                status in {"verified", "not_applicable"}
                if allow_finish is None
                else allow_finish
            ),
            "verified_evidence": list(verified),
            "pending_evidence": list(pending),
            "blocked_evidence": list(blocked),
        },
    }


def _snapshot(*, revision: int = 7) -> dict:
    canvas = build_synthetic_canvas(PROJECT_ID, CANVAS_ID)
    canvas["revision"] = revision
    return canvas


def test_synthetic_canvas_keeps_late_selected_target_and_spatial_anchors() -> None:
    canvas = _snapshot()

    assert len(canvas["nodes"]) == 156
    assert len(canvas["edges"]) == 3
    assert (
        canvas["nodes"].index(
            next(node for node in canvas["nodes"] if node["id"] == TARGET_NODE_ID)
        )
        > 80
    )
    target = next(node for node in canvas["nodes"] if node["id"] == TARGET_NODE_ID)
    assert target["selected"] is True
    assert target["position"] == {"x": 430, "y": 270}
    assert canvas["metadata"]["purpose"] == "synthetic_agent_live_eval"


def test_canvas_context_prioritizes_selected_then_viewport_before_storage_order() -> (
    None
):
    context = build_canvas_context(_snapshot(), selected_node_id=TARGET_NODE_ID)

    assert (
        context["canvas_outline_policy"] == "selected_then_viewport_then_canvas_order"
    )
    assert context["canvas_outline"][0]["id"] == TARGET_NODE_ID
    assert context["canvas_outline"][0]["selected"] is True
    assert context["canvas_outline"][0]["in_viewport"] is True
    assert context["visible_node_count"] == 4
    assert len(context["canvas_outline"]) == 80
    assert context["outline_truncated"]["nodes"] is True
    assert context["selected_node"]["display_name"] == "镜头-137"


def test_v2_request_matches_draft_no_media_execution_contract() -> None:
    request = build_v2_request(CASE_BY_ID["exact_reuse"], _snapshot())

    assert request.startswith(V2_OPEN)
    assert request.endswith(V2_CLOSE)
    payload = json.loads(request.removeprefix(V2_OPEN).removesuffix(V2_CLOSE))
    assert payload["v"] == 2
    assert payload["execution_lane"] == "canvas_execute"
    assert payload["run_mode"] == "draft"
    assert payload["task_authorization"] == {
        "scope": "current_turn",
        "run_mode": "draft",
        "allow_structure": True,
        "allow_paid_media": False,
        "max_paid_starts": 0,
        "require_video_confirmation": False,
    }
    assert payload["canvas"]["selected_node_id"] is None
    assert payload["canvas"]["canvas_outline"][0]["id"] == TARGET_NODE_ID
    assert payload["canvas"]["canvas_outline"][0]["selected"] is False
    assert payload["canvas"]["node_count"] == 156


def test_skill_load_case_requires_real_skill_tool_without_canvas_side_effects() -> None:
    request = build_v2_request(CASE_BY_ID["skill_load"], _snapshot())
    payload = json.loads(request.removeprefix(V2_OPEN).removesuffix(V2_CLOSE))

    assert payload["tool_policy"]["mode"] == "skill_reader"
    assert payload["tool_policy"]["no_skill_lookup"] is False
    assert payload["tool_policy"]["required_tools"] == ["skill"]
    assert payload["tool_policy"]["required_skills"] == [
        "village-canvas-music-score"
    ]
    assert payload["task_authorization"]["allow_paid_media"] is False
    assert payload["task_authorization"]["max_paid_starts"] == 0


def test_skill_routing_cases_cover_every_runtime_skill_without_leaking_ids() -> None:
    runtime_names = {skill.name for skill in list_agent_skills()}
    case_names = {case.skill_name for case in SKILL_ROUTING_CASES}

    assert len(SKILL_ROUTING_LIVE_CASES) == 45
    assert len(case_names) == len(SKILL_ROUTING_CASES)
    assert case_names == runtime_names
    assert all(case.skill_name not in case.prompt for case in SKILL_ROUTING_CASES)


def test_skill_routing_request_allows_route_without_injecting_answer() -> None:
    case = SKILL_ROUTING_LIVE_CASES[0]
    request = build_v2_request(case, _snapshot())
    payload = json.loads(request.removeprefix(V2_OPEN).removesuffix(V2_CLOSE))

    assert payload["tool_policy"]["mode"] == "skill_router"
    assert payload["tool_policy"]["no_skill_lookup"] is False
    assert payload["tool_policy"]["required_tools"] == []
    assert payload["tool_policy"]["required_skills"] == []
    assert payload["ACTIVE_SKILLS"] == []
    assert case.expected_skill not in request


def test_semantic_skill_grading_accepts_route_or_real_load() -> None:
    case = SKILL_ROUTING_LIVE_CASES[0]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    terminal = {"type": "chat.done"}
    correct = [
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame(case.expected_skill),
    ]
    wrong = [
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-music-score"),
    ]
    correct_plus_wrong = [
        *correct,
        _skill_load_frame("village-canvas-music-score", sha256="b" * 64),
    ]
    preactivated = [
        {
            "type": "thread_started",
            "route_receipt": {
                "schema": "village_agent_skill_route.v1",
                "pre_activated_skill": case.expected_skill,
                "load_receipt": False,
            },
        }
    ]
    live_shape = [
        {
            "type": "thread.started",
            "agent_event": {
                "payload": {
                    "skill_route": {
                        "schema": "village_agent_skill_route.v1",
                        "pre_activated_skill": case.expected_skill,
                        "load_receipt": False,
                    }
                }
            },
        }
    ]

    correct_contract = _case_trace_contract(case, metrics, terminal, correct)
    preactivated_contract = _case_trace_contract(
        case, metrics, terminal, preactivated
    )
    live_shape_contract = _case_trace_contract(case, metrics, terminal, live_shape)
    wrong_contract = _case_trace_contract(case, metrics, terminal, wrong)
    mixed_contract = _case_trace_contract(
        case, metrics, terminal, correct_plus_wrong
    )

    assert correct_contract["passed"] is True
    assert preactivated_contract["passed"] is True
    assert live_shape_contract["passed"] is True
    assert wrong_contract["passed"] is False
    assert mixed_contract["passed"] is False
    assert {
        item["name"]: item["passed"] for item in mixed_contract["checks"]
    }["exactly_one_active_skill"] is False


def test_skill_routing_group_expands_to_the_full_catalog() -> None:
    ids = _parse_case_ids("skill_routing")

    assert ids == [case.id for case in SKILL_ROUTING_LIVE_CASES]
    assert len(ids) == 45
    assert "read_only" not in ids


def test_activation_case_reads_the_binding_contract_through_the_real_skill_tool() -> None:
    case = CASE_BY_ID["skill_activation_contract"]
    request = build_v2_request(case, _snapshot())
    payload = json.loads(request.removeprefix(V2_OPEN).removesuffix(V2_CLOSE))

    assert payload["tool_policy"]["mode"] == "skill_reader"
    assert payload["tool_policy"]["required_tools"] == ["skill"]
    assert payload["tool_policy"]["required_skills"] == [
        "village-canvas-one-click-film"
    ]
    assert payload["task_authorization"]["allow_paid_media"] is False
    assert case.required_text == ("one-click-film", "village-canvas-storyboard")


def test_activation_case_fails_when_the_contract_never_reaches_the_model() -> None:
    case = CASE_BY_ID["skill_activation_contract"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    tool_frames = [
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-one-click-film"),
    ]

    paired = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            *tool_frames,
            {
                "type": "assistant.message",
                "text": "workflow 是 one-click-film，agents 含 village-canvas-storyboard。",
            },
            _turn_delivery_frame("not_applicable"),
        ],
    )
    silent = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            *tool_frames,
            {"type": "assistant.message", "text": "我已经知道一键成片怎么做了。"},
            _turn_delivery_frame("not_applicable"),
        ],
    )

    assert paired["passed"] is True
    assert silent["passed"] is False
    assert {item["name"] for item in silent["checks"] if not item["passed"]} == {
        "required_text_present"
    }


def test_skill_load_receipts_require_a_real_load_envelope() -> None:
    valid = _skill_load_frame("village-canvas-music-score")
    catalog = _skill_load_frame("village-canvas-music-score", action="list")
    failed = _skill_load_frame("village-canvas-music-score", success=False)
    malformed = {
        "type": "tool.result",
        "name": "skill",
        "success": True,
        "result": {"text": "not-json"},
    }

    receipts = _skill_load_receipts([valid, catalog, failed, malformed])

    assert receipts == [
        {
            "name": "village-canvas-music-score",
            "bytes": 123,
            "sha256": "a" * 64,
            "source": "bundled",
            "version": "1.0.0",
        }
    ]


def test_required_skill_loaded_uses_receipt_not_tool_success() -> None:
    case = CASE_BY_ID["skill_load"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    terminal = {"type": "chat.done"}
    bare_success = [
        {"type": "tool.call", "name": "skill"},
        {"type": "tool.result", "name": "skill", "success": True},
    ]
    loaded = [*bare_success, _skill_load_frame("village-canvas-music-score")]

    missing = _case_trace_contract(case, metrics, terminal, bare_success)
    present = _case_trace_contract(case, metrics, terminal, loaded)

    assert missing["passed"] is False
    assert next(
        item for item in missing["checks"] if item["name"] == "required_skill_loaded"
    )["passed"] is False
    assert present["passed"] is True
    assert next(
        item for item in present["checks"] if item["name"] == "required_skill_loaded"
    )["passed"] is True


def test_expected_tool_failure_is_not_an_unexpected_red_light() -> None:
    case = CASE_BY_ID["skill_paid_media_fence"]
    error_code = "skill_fence_paid_media_requires_task_authorization"
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": [error_code],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    trace = [
        _turn_intent_frame(
            "locked",
            source="runtime_side_effect_preflight",
            contract_hash="d" * 64,
            side_effect_tools=("village_canvas_capability",),
        ),
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-one-click-film"),
        {"type": "tool.call", "name": "village_canvas_capability"},
        {
            "type": "tool.result",
            "name": "village_canvas_capability",
            "success": False,
            "result": {
                "text": json.dumps(
                    {"ok": False, "error_code": error_code},
                    ensure_ascii=False,
                )
            },
        },
        _turn_delivery_frame(
            "blocked",
            reason_code="tool_delivery_failed_or_blocked",
            blocked=(
                {
                    "kind": "tool_receipt",
                    "tool": "village_canvas_capability",
                    "error_code": error_code,
                },
            ),
        ),
    ]

    contract = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        trace,
    )

    assert contract["passed"] is True
    assert {
        item["name"]: item["passed"] for item in contract["checks"]
    }["expected_tool_error_observed"] is True


def test_expected_and_tolerated_tool_failures_can_coexist() -> None:
    case = CASE_BY_ID["turn_intent_lock_rewrite"]
    expected = "village_turn_intent_locked"
    tolerated = "skill_fence_paid_media_requires_task_authorization"
    metrics = {
        "tool_failures": 2,
        "tool_failure_codes": [tolerated, expected],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    trace = [
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-one-click-film"),
        _turn_intent_frame(
            "locked",
            source="explicit",
            contract_hash="f" * 64,
            side_effect_tools=("village_canvas_capability",),
        ),
        {"type": "tool.call", "name": "village_canvas_freeze_turn_intent"},
        {
            "type": "tool.result",
            "name": "village_canvas_freeze_turn_intent",
            "success": False,
            "result": {
                "text": json.dumps(
                    {"ok": False, "error_code": expected},
                    ensure_ascii=False,
                )
            },
        },
        _turn_delivery_frame(
            "blocked",
            reason_code="tool_delivery_failed_or_blocked",
            blocked=(
                {
                    "kind": "tool_receipt",
                    "tool": "village_canvas_capability",
                    "error_code": tolerated,
                },
            ),
        ),
    ]

    contract = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        trace,
    )

    assert contract["passed"] is True
    assert contract["tolerated_tool_errors_observed"] == [tolerated]


def test_read_only_turn_accepts_absent_or_unfrozen_turn_intent() -> None:
    case = CASE_BY_ID["read_only"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    terminal = {"type": "chat.done"}
    not_frozen = [
        _turn_intent_frame("not_frozen"),
        _turn_delivery_frame("not_applicable"),
    ]
    unexpectedly_frozen = [
        _turn_intent_frame(
            "locked",
            source="runtime_side_effect_preflight",
            contract_hash="a" * 64,
            side_effect_tools=("village_canvas_dispatch_action",),
        ),
        _turn_delivery_frame("not_applicable"),
    ]

    passed = _case_trace_contract(case, metrics, terminal, not_frozen)
    absent = _case_trace_contract(case, metrics, terminal, [])
    failed = _case_trace_contract(case, metrics, terminal, unexpectedly_frozen)

    assert passed["passed"] is True
    assert absent["passed"] is True
    assert failed["passed"] is False
    assert next(
        item for item in failed["checks"] if item["name"] == "turn_intent_status"
    )["passed"] is False


def test_locked_turn_intent_requires_hash_and_real_side_effect_tool() -> None:
    case = CASE_BY_ID["exact_reuse"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
        "canvas_receipts": 1,
        "receipt_completeness_rate": 1.0,
    }
    locked = [
        _turn_intent_frame(
            "locked",
            source="runtime_side_effect_preflight",
            contract_hash="b" * 64,
            side_effect_tools=("village_canvas_dispatch_action",),
        )
    ]

    passed = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [*locked, _turn_delivery_frame("verified")],
    )
    missing_side_effect = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            _turn_intent_frame(
                "locked",
                source="runtime_side_effect_preflight",
                contract_hash="b" * 64,
            )
        ],
    )

    assert passed["passed"] is True
    assert missing_side_effect["passed"] is False
    assert next(
        item
        for item in missing_side_effect["checks"]
        if item["name"] == "turn_intent_side_effect_recorded"
    )["passed"] is False


def test_automatic_turn_intent_case_rejects_explicit_only_receipt() -> None:
    case = CASE_BY_ID["turn_intent_auto_freeze"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    explicit = [
        _turn_intent_frame(
            "locked",
            source="explicit",
            contract_hash="e" * 64,
            side_effect_tools=("village_canvas_capability",),
        )
    ]

    contract = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        explicit,
    )

    assert contract["passed"] is False
    assert next(
        item for item in contract["checks"] if item["name"] == "turn_intent_source"
    )["passed"] is False


def test_locked_rewrite_case_requires_the_real_locked_error() -> None:
    case = CASE_BY_ID["turn_intent_lock_rewrite"]
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": ["village_turn_intent_locked"],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    trace = [
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-one-click-film"),
        _turn_intent_frame(
            "locked",
            source="explicit",
            contract_hash="f" * 64,
            side_effect_tools=("village_canvas_capability",),
        ),
        {"type": "tool.call", "name": "village_canvas_freeze_turn_intent"},
        {
            "type": "tool.result",
            "name": "village_canvas_freeze_turn_intent",
            "success": False,
            "result": {
                "text": json.dumps(
                    {
                        "ok": False,
                        "error_code": "village_turn_intent_locked",
                    },
                    ensure_ascii=False,
                )
            },
        },
        _turn_delivery_frame(
            "blocked",
            reason_code="tool_delivery_failed_or_blocked",
            blocked=(
                {
                    "kind": "tool_receipt",
                    "tool": "village_canvas_capability",
                    "error_code": "skill_fence_paid_media_requires_task_authorization",
                },
            ),
        ),
    ]

    contract = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        trace,
    )

    assert contract["passed"] is True


def test_turn_intent_receipt_survives_json_tool_result_envelope() -> None:
    receipt = {
        "schema": "village_turn_intent_receipt.v1",
        "status": "locked",
        "source": "runtime_side_effect_preflight",
        "contract_hash": "c" * 64,
        "side_effect_tools": ["village_canvas_capability"],
    }
    frames = [
        {
            "type": "tool.result",
            "name": "village_canvas_capability",
            "success": False,
            "result": {
                "text": json.dumps(
                    {"ok": False, "turn_intent_receipt": receipt},
                    ensure_ascii=False,
                )
            },
        }
    ]

    assert _turn_intent_receipt(frames) == receipt


def test_turn_delivery_receipt_survives_event_and_json_envelopes() -> None:
    receipt = _turn_delivery_frame(
        "blocked",
        reason_code="tool_delivery_failed_or_blocked",
        blocked=({"kind": "tool_receipt", "source_ref": "wfr_1"},),
    )["turn_delivery_receipt"]
    frames = [
        {
            "type": "agent.event",
            "agent_event": {
                "payload": {"turn_delivery_receipt": receipt},
            },
        },
        {
            "type": "tool.result",
            "result": {
                "text": json.dumps(
                    {"ok": False, "turn_delivery_receipt": receipt},
                    ensure_ascii=False,
                )
            },
        },
    ]

    assert _turn_delivery_receipt(frames) == receipt


def test_live_delivery_status_is_graded_from_real_receipt() -> None:
    read_only = CASE_BY_ID["read_only"]
    metrics = {
        "tool_failures": 0,
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    terminal = {"type": "chat.done"}
    present = _case_trace_contract(
        read_only,
        metrics,
        terminal,
        [_turn_delivery_frame("not_applicable")],
    )
    missing = _case_trace_contract(read_only, metrics, terminal, [])

    assert present["passed"] is True
    assert missing["passed"] is True


def test_blocked_delivery_status_preserves_a_fenced_failure() -> None:
    case = CASE_BY_ID["skill_paid_media_fence"]
    terminal = {"type": "chat.done"}
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": [
            "skill_fence_paid_media_requires_task_authorization"
        ],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    trace = [
        _turn_intent_frame(
            "locked",
            source="runtime_side_effect_preflight",
            contract_hash="d" * 64,
            side_effect_tools=("village_canvas_capability",),
        ),
        {"type": "tool.call", "name": "skill"},
        _skill_load_frame("village-canvas-one-click-film"),
        {"type": "tool.call", "name": "village_canvas_capability"},
        {
            "type": "tool.result",
            "name": "village_canvas_capability",
            "success": False,
            "result": {
                "text": json.dumps(
                    {
                        "ok": False,
                        "error_code": (
                            "skill_fence_paid_media_requires_task_authorization"
                        ),
                    },
                    ensure_ascii=False,
                )
            },
        },
        _turn_delivery_frame(
            "blocked",
            reason_code="tool_delivery_failed_or_blocked",
            blocked=(
                {
                    "kind": "tool_receipt",
                    "tool": "village_canvas_capability",
                    "error_code": (
                        "skill_fence_paid_media_requires_task_authorization"
                    ),
                },
            ),
        ),
    ]

    contract = _case_trace_contract(case, metrics, terminal, trace)

    assert contract["passed"] is True
    assert next(
        item for item in contract["checks"] if item["name"] == "turn_delivery_status"
    )["passed"] is True


def test_unexpected_tool_failure_still_fails_an_expected_failure_case() -> None:
    case = CASE_BY_ID["skill_paid_media_fence"]
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": ["tool_arguments_invalid"],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }

    contract = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [],
    )

    checks = {item["name"]: item["passed"] for item in contract["checks"]}
    assert contract["passed"] is False
    assert checks["no_tool_failures"] is False
    assert checks["expected_tool_error_observed"] is False


def test_missing_canvas_accepts_http_200_with_null_data() -> None:
    class Api:
        def request(self, *_args, **_kwargs):
            return 200, {"ok": True, "data": None}

    assert get_canvas(Api(), PROJECT_ID, CANVAS_ID) is None


def test_missing_canvas_accepts_legacy_identity_less_empty_shell() -> None:
    class Api:
        def request(self, *_args, **_kwargs):
            return 200, {
                "ok": True,
                "data": {"nodes": [], "edges": [], "viewport": None},
            }

    assert get_canvas(Api(), PROJECT_ID, CANVAS_ID) is None


def test_isolated_project_creation_and_cleanup_are_canonical_and_verified() -> None:
    class Api:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, object]] = []
            self.purged = False

        def request(
            self,
            method: str,
            path: str,
            payload=None,
            *,
            allow_status=(),
        ):
            self.calls.append((method, path, payload))
            if method == "POST" and path == "/api/v1/projects":
                return 200, {"ok": True, "data": {"id": "project-temp"}}
            if method == "GET" and path == "/api/v1/projects/project-temp":
                if self.purged:
                    return 404, {"ok": False}
                return 200, {"ok": True, "data": {"id": "project-temp"}}
            if method == "GET" and "/freezone/canvases/" in path:
                return 200, {
                    "ok": True,
                    "data": {
                        "project_id": "project-temp",
                        "canvas_id": CANVAS_ID,
                        "metadata": {"purpose": "synthetic_agent_live_eval"},
                    },
                }
            if method == "DELETE" and "/freezone/canvases/" in path:
                return 200, {"ok": True}
            if method == "POST" and path.endswith("/purge"):
                self.purged = True
                return 200, {"ok": True}
            if method == "POST" and path.endswith("/delete"):
                return 200, {"ok": True}
            raise AssertionError((method, path, payload, allow_status))

    api = Api()

    assert create_project(api, "agent_eval_test") == "project-temp"
    assert project_exists(api, "project-temp") is True
    cleanup = cleanup_project(api, "project-temp", CANVAS_ID)

    assert cleanup["ok"] is True
    assert cleanup["remaining"] is False
    assert [step["name"] for step in cleanup["steps"]] == [
        "delete_canvas",
        "delete_project",
        "purge_project",
    ]
    assert api.calls[-1] == (
        "GET",
        "/api/v1/projects/project-temp",
        None,
    )


def test_read_only_verifier_rejects_any_canvas_mutation() -> None:
    before = _snapshot(revision=7)
    unchanged = _snapshot(revision=7)
    changed = _snapshot(revision=8)
    changed["nodes"][0]["data"]["prompt"] = "unexpected mutation"

    passed = verify_case_state(
        CASE_BY_ID["read_only"],
        before,
        unchanged,
        new_task_ids=[],
        new_workflow_run_ids=[],
    )
    failed = verify_case_state(
        CASE_BY_ID["read_only"],
        before,
        changed,
        new_task_ids=[],
        new_workflow_run_ids=[],
    )

    assert passed["passed"] is True
    assert harness_verification_frame(passed)["type"] == "verification_passed"
    assert failed["passed"] is False
    assert harness_verification_frame(failed)["type"] == "verification_failed"


def test_reuse_verifier_requires_target_prompt_change_without_creation() -> None:
    before = _snapshot(revision=7)
    after = _snapshot(revision=8)
    target = next(node for node in after["nodes"] if node["id"] == TARGET_NODE_ID)
    target["data"]["prompt"] = "雨幕压低空间，人物停顿后才看向镜外。"

    result = verify_case_state(
        CASE_BY_ID["exact_reuse"],
        before,
        after,
        new_task_ids=[],
        new_workflow_run_ids=[],
    )

    assert result["passed"] is True
    assert all(assertion["passed"] for assertion in result["assertions"])


def test_reuse_verifier_rejects_parallel_node_or_workflow() -> None:
    before = _snapshot(revision=7)
    after = _snapshot(revision=8)
    target = next(node for node in after["nodes"] if node["id"] == TARGET_NODE_ID)
    target["data"]["prompt"] = "changed"
    after["nodes"].append(
        {
            "id": "parallel-node",
            "type": "textAnnotationNode",
            "position": {"x": 0, "y": 0},
            "data": {"displayName": "parallel"},
        }
    )

    result = verify_case_state(
        CASE_BY_ID["exact_reuse"],
        before,
        after,
        new_task_ids=[],
        new_workflow_run_ids=["run-parallel"],
    )

    assert result["passed"] is False
    failed_names = {item["name"] for item in result["assertions"] if not item["passed"]}
    assert failed_names == {"no_new_workflow_runs", "node_ids_unchanged"}


def test_explicit_create_verifier_accepts_exactly_one_requested_node() -> None:
    before = _snapshot(revision=7)
    after = _snapshot(revision=8)
    after["nodes"].append(
        {
            "id": "shot-created-151",
            "type": "textAnnotationNode",
            "position": {"x": 600, "y": 300},
            "data": {"displayName": "镜头-151", "prompt": "雨停后的破晓收束镜头。"},
        }
    )

    result = verify_case_state(
        CASE_BY_ID["explicit_create"],
        before,
        after,
        new_task_ids=[],
        new_workflow_run_ids=[],
    )

    assert result["passed"] is True


def test_merge_trace_frames_filters_other_turns_but_preserves_replay_for_dedup() -> (
    None
):
    event = {
        "schema": "village_agent_event.v1",
        "event_id": "evt-1",
        "turn_id": "turn-a",
        "seq": 1,
        "type": "run.started",
        "payload": {},
    }
    merged = merge_trace_frames(
        [{"type": "thread.started", "turn_id": "turn-a", "agent_event": event}],
        [
            {"type": "agent.event", "turn_id": "turn-a", "agent_event": event},
            {
                "type": "agent.event",
                "turn_id": "turn-b",
                "agent_event": {**event, "turn_id": "turn-b"},
            },
        ],
        turn_id="turn-a",
    )

    assert len(merged) == 2
    assert all(item["turn_id"] == "turn-a" for item in merged)


def test_full_trace_reliability_cannot_hide_a_tool_failure_behind_harness_pass() -> (
    None
):
    verification = harness_verification_frame(
        {
            "passed": True,
            "assertions": [{"name": "state", "passed": True}],
        }
    )
    aggregate = evaluate_agent_trials(
        [[{"type": "tool.result", "success": False}, verification]],
        reliability_k=1,
    )

    assert aggregate["trial_passed"] == [False]
    assert aggregate["trials"][0]["tool_failures"] == 1


def test_shared_live_aggregate_requires_receipt_and_chat_done_for_mutation() -> None:
    verification = harness_verification_frame(
        {
            "passed": True,
            "assertions": [{"name": "state", "passed": True}],
        }
    )
    mutation = {
        "type": "tool.result",
        "success": True,
        "action_dispatch": {
            "route": {"reason_code": "existing_node_mutation"},
            "decision": {"target_strategy": "reuse_existing"},
        },
    }

    missing_receipt = evaluate_agent_trials(
        [[mutation, {"type": "chat.done"}, verification]],
        reliability_k=1,
        requires_canvas_receipt=True,
        require_terminal=True,
    )
    recoverable = evaluate_agent_trials(
        [[mutation, {"type": "chat.recoverable"}, verification]],
        reliability_k=1,
        requires_canvas_receipt=True,
        require_terminal=True,
    )

    assert missing_receipt["trial_passed"] == [False]
    assert recoverable["trial_passed"] == [False]

def _blocked_dispatch_frame(
    code: str,
    *,
    run_id: str = "",
    result: dict | None = None,
) -> dict:
    trace = {"turn_id": "turn-1"}
    if run_id:
        trace["workflow_run_id"] = run_id
    return {
        "type": "tool.result",
        "name": "village_canvas_dispatch_action",
        "success": False,
        "result": result
        or {
            "ok": False,
            "error_code": code,
            "error": "refused",
            "action_dispatch": {
                "route": {
                    "schema": "canvas_action_route.v1",
                    "lane": "blocked",
                    "reason_code": code,
                    "requires_durable_run": False,
                },
                "decision": {"target_strategy": "create_missing"},
            },
            "execution_trace": trace,
        },
    }


def test_skill_routing_cases_are_read_only_and_tolerate_blocked_lane_refusals() -> None:
    from scripts.evaluate_agent_live import (
        CASE_BY_ID,
        ROUTING_READ_ONLY_DIRECTIVE,
        _case_trace_contract,
    )

    case = CASE_BY_ID["skill_route__aigc-knowledge"]
    assert case.skill_routing is True
    assert case.tolerate_blocked_lane_refusals is True
    assert case.tolerated_tool_errors == ()
    for routing_case in SKILL_ROUTING_LIVE_CASES:
        assert routing_case.prompt.endswith(ROUTING_READ_ONLY_DIRECTIVE)
        assert routing_case.expected_skill not in routing_case.prompt

    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": ["existing_target_candidate_conflict"],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }

    tolerated = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            {"type": "tool.call", "name": "skill"},
            _skill_load_frame("village-canvas-aigc-knowledge"),
            _blocked_dispatch_frame("existing_target_candidate_conflict"),
        ],
    )
    assert tolerated["passed"] is True
    assert tolerated["tolerated_tool_errors_observed"] == [
        "existing_target_candidate_conflict"
    ]


def test_skill_routing_tolerance_never_covers_a_denial_that_touched_state() -> None:
    from scripts.evaluate_agent_live import CASE_BY_ID, _case_trace_contract

    case = CASE_BY_ID["skill_route__aigc-knowledge"]
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": ["execution_not_authorized"],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    terminal = {"type": "chat.done"}

    plain_failure = _case_trace_contract(
        case,
        metrics,
        terminal,
        [
            {
                "type": "tool.result",
                "name": "village_canvas_dispatch_action",
                "success": False,
                "result": {"ok": False, "error_code": "execution_not_authorized"},
            }
        ],
    )
    unlisted_code = _case_trace_contract(
        case,
        {**metrics, "tool_failure_codes": ["unexpected_upstream_failure"]},
        terminal,
        [_blocked_dispatch_frame("unexpected_upstream_failure")],
    )
    started_a_run = _case_trace_contract(
        case,
        metrics,
        terminal,
        [_blocked_dispatch_frame("execution_not_authorized", run_id="wfr_abc")],
    )

    assert plain_failure["passed"] is False
    assert unlisted_code["passed"] is False
    assert started_a_run["passed"] is False


def test_skill_routing_tolerates_failed_read_only_capability_calls() -> None:
    from scripts.evaluate_agent_live import CASE_BY_ID, _case_trace_contract

    case = CASE_BY_ID["skill_route__shotcraft"]
    metrics = {
        "tool_failures": 1,
        "tool_failure_codes": ["tool_failure"],
        "final_state_verified": True,
        "creation_on_reuse": 0,
    }
    tolerating = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            {"type": "tool.call", "name": "skill"},
            _skill_load_frame("village-canvas-shotcraft"),
            {
                "type": "tool.result",
                "name": "village_canvas_capability",
                "success": False,
                "result": {"ok": False, "error": "bad reference uri"},
            },
        ],
    )
    write_failure = _case_trace_contract(
        case,
        metrics,
        {"type": "chat.done"},
        [
            {"type": "tool.call", "name": "skill"},
            _skill_load_frame("village-canvas-shotcraft"),
            {
                "type": "tool.result",
                "name": "village_canvas_apply_commands",
                "success": False,
                "result": {"ok": False, "error": "write refused"},
            },
        ],
    )

    assert tolerating["passed"] is True
    assert write_failure["passed"] is False


def _transport_failure(**overrides) -> dict:
    result = {
        "passed": False,
        "terminal_type": "chat.recoverable",
        "error": "Request timed out.",
        "state_verification": {"passed": True},
        "_evaluation_frames": [
            {"type": "tool.call", "name": "skill"},
            _skill_load_frame("village-canvas-workflow-engineering"),
        ],
    }
    result.update(overrides)
    return result


def test_only_a_channel_death_is_eligible_for_a_transport_rerun() -> None:
    from scripts.evaluate_agent_live import _transport_only_failure

    assert _transport_only_failure(_transport_failure()) is True
    assert (
        _transport_only_failure(
            _transport_failure(
                error="",
                terminal_type="error",
                _evaluation_frames=[
                    {
                        "type": "error",
                        "message": (
                            "Server disconnected without sending a response."
                        ),
                    }
                ],
            )
        )
        is True
    )
    assert (
        _transport_only_failure(
            _transport_failure(
                error="",
                terminal_type="chat.recoverable",
                _evaluation_frames=[
                    {
                        "type": "chat.recoverable",
                        "retry_reason": "worker_lost",
                        "last_event": {"type": "agent_stream_transport"},
                    }
                ],
            )
        )
        is True
    )
    assert (
        _transport_only_failure(_transport_failure(passed=True)) is False
    )
    assert (
        _transport_only_failure(_transport_failure(terminal_type="chat.done"))
        is False
    )
    assert (
        _transport_only_failure(
            _transport_failure(state_verification={"passed": False})
        )
        is False
    )
    assert (
        _transport_only_failure(
            _transport_failure(
                _evaluation_frames=[
                    {
                        "type": "tool.result",
                        "name": "freezone_get_canvas_snapshot",
                        "success": False,
                        "result": {"ok": False},
                    }
                ]
            )
        )
        is False
    )


def test_a_harness_verdict_is_never_rerun_as_a_channel_death() -> None:
    from scripts.evaluate_agent_live import (
        _transport_evidence,
        _transport_only_failure,
    )

    budget = _transport_failure(
        error="",
        terminal_type="error",
        _evaluation_frames=[
            {
                "type": "error",
                "message": (
                    "The next tool call(s) would exceed the tool_calls_limit of "
                    "12 (tool_calls=13)."
                ),
            }
        ],
    )

    assert _transport_evidence(budget) == ""
    assert _transport_only_failure(budget) is False


def test_transport_rerun_keeps_every_attempt_on_disk(tmp_path) -> None:
    from scripts.evaluate_agent_live import _attempt_evidence, _write_raw_frames

    frames = [{"type": "chat.done", "turn_id": "turn-a"}]
    _write_raw_frames(tmp_path, "case-a", 1, frames, turn_id="turn-a")
    _write_raw_frames(tmp_path, "case-a", 1, frames, turn_id="turn-a", attempt=2)

    assert (tmp_path / "case-a-t1.jsonl").exists()
    assert (tmp_path / "case-a-t1.a2.jsonl").exists()

    evidence = _attempt_evidence(
        {
            "turn_id": "turn-a",
            "terminal_type": "chat.recoverable",
            "error": "Request timed out.",
            "duration_ms": 12,
            "passed": False,
            "live_frame_count": 3,
        },
        2,
    )

    assert evidence["attempt"] == 2
    assert evidence["terminal_type"] == "chat.recoverable"


def test_skill_routing_refusal_budget_is_counted_per_occurrence() -> None:
    from scripts.evaluate_agent_live import CASE_BY_ID, _case_trace_contract

    case = CASE_BY_ID["skill_route__aigc-knowledge"]
    contract = _case_trace_contract(
        case,
        {
            "tool_failures": 2,
            "tool_failure_codes": [
                "execution_not_authorized",
                "execution_not_authorized",
            ],
            "final_state_verified": True,
            "creation_on_reuse": 0,
        },
        {"type": "chat.done"},
        [_blocked_dispatch_frame("execution_not_authorized")],
    )

    assert contract["passed"] is False
    assert contract["tolerated_tool_errors_observed"] == ["execution_not_authorized"]
