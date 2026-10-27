"""M03: single compiled canvas tasks must not be blocked by a long-flow profile."""

from __future__ import annotations

import pytest

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


def test_two_independent_prompt_nodes_route_canvas_without_workflow() -> None:
    from novelvideo.workflow_runtime.action_router import (
        atomic_prompt_node_creation_batch,
    )

    commands = [
        {"type": "create_image_prompt_node", "display_name": "male card"},
        {"type": "create_image_prompt_node", "display_name": "female card"},
    ]
    route = route_action(
        profile_from_mapping(
            {
                "operation": "create_character_identity_boards",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "step_count": 2,
                "item_count": 2,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": True,
            }
        ),
        contains_creation=True,
        atomic_prompt_batch=atomic_prompt_node_creation_batch(commands),
    )

    assert route.lane == "canvas"
    assert route.reason_code == "atomic_prompt_node_batch"
    assert route.requires_durable_run is False


def test_storyboard_shaped_prompt_batch_keeps_the_durable_lane() -> None:
    route = route_action(
        profile_from_mapping(
            {
                "operation": "storyboard_production",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "step_count": 4,
                "item_count": 4,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": True,
            }
        ),
        contains_creation=True,
        atomic_prompt_batch=True,
    )

    assert route.lane == "workflow"


def test_atomic_prompt_batch_rejects_structural_commands() -> None:
    from novelvideo.workflow_runtime.action_router import (
        atomic_prompt_node_creation_batch,
    )

    assert atomic_prompt_node_creation_batch(
        [{"type": "create_image_prompt_node"}]
    )
    assert not atomic_prompt_node_creation_batch(
        [{"type": "insert_starter_workflow"}]
    )


def test_simple_prompt_batch_helper_is_guarded_by_production_intent() -> None:
    from novelvideo.agent_tools.village_canvas.workflow_dispatch import (
        _requests_production_sequence,
        _simple_prompt_node_batch,
    )

    commands = [
        {"type": "create_image_prompt_node"},
        {"type": "create_image_prompt_node"},
    ]
    assert _simple_prompt_node_batch(commands)
    assert not _requests_production_sequence(
        "帮我做两个人物图片，爱死机风格的人物资产",
        "新建两张人物身份板",
    )
    assert _requests_production_sequence("继续到最终成片，出分镜图", "")


def test_single_node_create_with_copied_step_count_stays_canvas() -> None:
    route = route_action(
        profile_from_mapping(
            {
                "operation": "create_prop_identity_sheet",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "step_count": 2,
                "item_count": 1,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": True,
            }
        ),
        contains_creation=True,
        single_atomic_creation=True,
        atomic_prompt_batch=False,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "single_canvas_creation"


def test_generation_node_batch_uses_the_atomic_canvas_lane() -> None:
    from novelvideo.workflow_runtime.action_router import (
        atomic_prompt_node_creation_batch,
    )

    commands = [
        {"type": "create_canvas_node", "node_type": "imageGenNode"},
        {"type": "create_canvas_node", "node_type": "imageGenNode"},
    ]
    assert atomic_prompt_node_creation_batch(commands)
    route = route_action(
        profile_from_mapping(
            {
                "operation": "create_character_identity_boards",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "step_count": 2,
                "item_count": 2,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": True,
            }
        ),
        contains_creation=True,
        atomic_prompt_batch=atomic_prompt_node_creation_batch(commands),
    )

    assert route.lane == "canvas"
    assert route.reason_code == "atomic_prompt_node_batch"


@pytest.mark.parametrize(
    "commands,expected",
    [
        ([{"type": "create_image_prompt_node"}], True),
        ([{"type": "create_canvas_node", "node_type": "imageGenNode"}], True),
        (
            [
                {"type": "create_canvas_node", "node_type": "imageGenNode"},
                {"type": "create_canvas_node", "node_type": "videoGenNode"},
            ],
            True,
        ),
        (
            [
                {"type": "create_canvas_node", "node_type": "imageGenNode"},
                {"type": "connect_nodes", "source": "a", "target": "b"},
            ],
            True,
        ),
        ([{"type": "create_canvas_node", "node_type": "scriptNode"}], False),
        ([{"type": "insert_starter_workflow"}], False),
        ([{"type": "update_node_prompt"}], False),
        (
            [
                {"type": "create_canvas_node", "node_type": "imageGenNode"}
                for _ in range(9)
            ],
            False,
        ),
    ],
)
def test_atomic_command_shape_matrix(commands, expected) -> None:
    from novelvideo.workflow_runtime.action_router import (
        atomic_prompt_node_creation_batch,
    )

    assert atomic_prompt_node_creation_batch(commands) is expected


@pytest.mark.parametrize("step_count", [1, 2, 5])
@pytest.mark.parametrize("item_count", [1, 2, 5])
def test_atomic_generation_batch_ignores_model_step_hints(
    step_count: int, item_count: int
) -> None:
    route = route_action(
        profile_from_mapping(
            {
                "operation": "create_character_identity_boards",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "step_count": step_count,
                "item_count": item_count,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": True,
            }
        ),
        contains_creation=True,
        atomic_prompt_batch=True,
    )
    assert route.lane == "canvas"
    assert route.reason_code == "atomic_prompt_node_batch"


def test_client_and_server_share_one_shape_classifier() -> None:
    from novelvideo.agent_tools.village_canvas.workflow_dispatch import (
        _simple_prompt_node_batch,
    )
    from novelvideo.workflow_runtime.action_router import (
        atomic_prompt_node_creation_batch,
    )

    shapes = [
        [{"type": "create_canvas_node", "node_type": "imageGenNode"}],
        [{"type": "insert_starter_workflow"}],
        [
            {"type": "create_canvas_node", "node_type": "videoGenNode"},
            {"type": "connect_nodes"},
        ],
    ]
    for commands in shapes:
        assert _simple_prompt_node_batch(commands) == (
            atomic_prompt_node_creation_batch(commands)
        )
