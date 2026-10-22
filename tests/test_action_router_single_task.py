"""M03: single compiled canvas tasks must not be blocked by a long-flow profile."""

from __future__ import annotations

from novelvideo.workflow_runtime.action_router import (
    profile_from_mapping,
    route_action,
    route_canvas_envelope,
)


def test_single_atomic_create_routes_canvas_without_director_contract() -> None:
    route = route_action(
        profile_from_mapping({}),
        contains_creation=True,
        single_atomic_creation=True,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "single_canvas_creation"
    assert route.requires_durable_run is False


def test_multi_command_creation_still_requires_director_contract() -> None:
    route = route_action(
        profile_from_mapping({}),
        contains_creation=True,
        single_atomic_creation=False,
    )

    assert route.lane == "blocked"
    assert route.reason_code == "director_contract_required"


def test_discussion_stays_blocked_even_for_a_single_create() -> None:
    route = route_action(
        profile_from_mapping({"interaction_mode": "discuss"}),
        contains_creation=True,
        single_atomic_creation=True,
    )

    assert route.lane == "blocked"
    assert route.reason_code == "execution_not_authorized"


def test_envelope_without_profile_uses_command_shape_for_one_node() -> None:
    route = route_canvas_envelope(
        {
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "imageGen",
                    "node": {"id": "node-1"},
                }
            ]
        }
    )

    # The legacy envelope path is a trusted internal call and keeps the
    # existing explicit-canvas reason; untrusted profiles use the shortcut.
    assert route.lane == "canvas"
    assert route.reason_code == "explicit_canvas"


def test_envelope_with_legacy_two_creates_stays_on_the_trusted_canvas_lane() -> None:
    route = route_canvas_envelope(
        {
            "commands": [
                {"type": "create_canvas_node", "node_type": "imageGen"},
                {"type": "create_canvas_node", "node_type": "imageGen"},
            ]
        }
    )

    # Direct/legacy envelopes are trusted internal calls; untrusted mappings
    # still go through profile_from_mapping and fail closed (test above).
    assert route.lane == "canvas"
    assert route.reason_code == "explicit_canvas"


def test_existing_node_mutation_stays_on_the_canvas_lane() -> None:
    route = route_action(
        profile_from_mapping({}),
        existing_node_mutation_only=True,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "existing_node_mutation_inferred"
