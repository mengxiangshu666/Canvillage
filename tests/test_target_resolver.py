from novelvideo.workflow_runtime.target_resolver import (
    resolve_existing_targets,
    should_block_creation,
)


def _node(node_id, label, node_type="storyboardNode", prompt="", **data):
    return {
        "id": node_id,
        "type": node_type,
        "data": {"displayName": label, "prompt": prompt, **data},
    }


def test_exact_label_resolves_existing_node_for_mutation():
    resolution = resolve_existing_targets(
        goal="优化第一镜头的提示词",
        operation="optimize_shot_prompt",
        nodes=[_node("shot-1", "第一镜头")],
    )

    assert resolution["recommended_strategy"] == "reuse_existing"
    assert resolution["suggested_target_node_ids"] == ["shot-1"]
    assert resolution["confidence"] == "high"
    assert should_block_creation(target_strategy="create_missing", resolution=resolution)


def test_explicit_creation_keeps_create_missing_available():
    resolution = resolve_existing_targets(
        goal="新增另一个第一镜头",
        operation="create_shot",
        nodes=[_node("shot-1", "第一镜头")],
    )

    assert resolution["explicit_creation_intent"] is True
    assert not should_block_creation(target_strategy="create_missing", resolution=resolution)


def test_command_node_id_is_high_confidence_even_without_label():
    resolution = resolve_existing_targets(
        goal="调整节点",
        operation="update_node_prompt",
        commands=[{"type": "update_node_prompt", "node_id": "node-a"}],
        nodes=[_node("node-a", "未命名")],
    )

    assert resolution["suggested_target_node_ids"] == ["node-a"]
    assert resolution["candidates"][0]["reasons"][0] == "explicit_node_id"


def test_asset_identity_resolves_existing_node_and_returns_stable_uri():
    resolution = resolve_existing_targets(
        goal="优化角色提示词",
        operation="update_node_prompt",
        target_node_ids=["character-1"],
        nodes=[
            _node(
                "node-1",
                "角色肖像",
                "imageGenNode",
                assetId="character-1",
                assetUri="hogi://assets/character-1",
                parentId="group-1",
                role="character",
            )
        ],
        canvas_id="canvas-a",
    )

    assert resolution["recommended_strategy"] == "reuse_existing"
    assert resolution["suggested_target_node_ids"] == ["node-1"]
    assert resolution["suggested_target_uris"] == ["hogi://assets/character-1"]
    assert resolution["candidates"][0] == {
        "node_id": "node-1",
        "node_type": "imageGenNode",
        "label": "角色肖像",
        "asset_id": "character-1",
        "asset_uri": "hogi://assets/character-1",
        "node_uri": "canvas://canvas-a/nodes/node-1",
        "role": "character",
        "parent_id": "group-1",
        "score": 1.0,
        "reasons": ["explicit_asset_identity", "mutation_intent"],
    }


def test_node_uri_command_binds_existing_node_even_when_display_name_differs():
    resolution = resolve_existing_targets(
        goal="继续处理这个对象",
        operation="update_node_prompt",
        commands=[
            {"type": "update_node_prompt", "node_id": "canvas://canvas-a/nodes/node-1"}
        ],
        nodes=[_node("node-1", "旧显示名", "imageGenNode", assetId="asset-1")],
        canvas_id="canvas-a",
    )

    assert resolution["suggested_target_node_ids"] == ["node-1"]
    assert resolution["identity_policy"]["label_is_display_only"] is True


def test_identity_embedded_in_goal_resolves_without_relying_on_display_name():
    resolution = resolve_existing_targets(
        goal="请继续优化 hogi://assets/character-1 的提示词",
        operation="update_node_prompt",
        nodes=[
            _node(
                "node-1",
                "已改名的节点",
                "imageGenNode",
                assetId="character-1",
                assetUri="hogi://assets/character-1",
            )
        ],
        canvas_id="canvas-a",
    )

    assert resolution["suggested_target_node_ids"] == ["node-1"]
    assert resolution["candidates"][0]["reasons"][:2] == [
        "identity_mentioned",
        "mutation_intent",
    ]


def test_role_or_parent_id_never_counts_as_explicit_target_identity():
    resolution = resolve_existing_targets(
        goal="调整角色",
        operation="update_node_prompt",
        target_node_ids=["character"],
        nodes=[
            _node(
                "node-1",
                "角色节点",
                "imageGenNode",
                role="character",
                parentId="character",
            )
        ],
    )

    assert resolution["suggested_target_node_ids"] == []
    assert resolution["confidence"] in {"low", "medium"}


def test_created_node_connection_treats_existing_endpoint_as_dependency():
    resolution = resolve_existing_targets(
        goal="完成咖啡包装概念设计并连接咖啡品牌主Logo",
        operation="canvas_command",
        target_node_ids=["asset-reference"],
        commands=[
            {
                "type": "create_image_prompt_node",
                "display_name": "咖啡包装概念设计",
            },
            {
                "type": "connect_nodes",
                "source": "asset-reference",
                "target": "$created:0",
            },
        ],
        nodes=[_node("asset-reference", "咖啡品牌主Logo", "imageGenNode")],
    )

    assert resolution["dependency_node_ids"] == ["asset-reference"]
    assert resolution["suggested_target_node_ids"] == []
    assert resolution["confidence"] == "low"
    assert not should_block_creation(
        target_strategy="create_missing", resolution=resolution
    )


def test_unrelated_query_does_not_produce_high_confidence_candidate():
    resolution = resolve_existing_targets(
        goal="查看当前项目状态",
        operation="inspect_project",
        nodes=[_node("shot-1", "第一镜头")],
    )

    assert resolution["suggested_target_node_ids"] == []
    assert resolution["confidence"] in {"low", "medium"}


def test_declared_create_missing_source_is_a_dependency_not_a_replacement():
    """Continuing downstream from a named upstream node must not be read as
    replacing that node, even though the goal says ``继续``."""

    resolution = resolve_existing_targets(
        goal="从当前脚本节点继续到最终成片",
        operation="脚本直达成片",
        target_node_ids=["script-a"],
        commands=[],
        nodes=[_node("script-a", "脚本生成器", "scriptNode")],
        canvas_id="canvas-a",
        declared_target_strategy="create_missing",
        creation_reason=(
            "画布只有 script-a 一个脚本节点，镜 1 的分镜图、视频与成片载体"
            "必须在脚本节点下游新建"
        ),
    )

    assert resolution["mutation_intent"] is True
    assert resolution["dependency_node_ids"] == ["script-a"]
    assert resolution["suggested_target_node_ids"] == []
    assert resolution["confidence"] == "low"
    assert not should_block_creation(
        target_strategy="create_missing", resolution=resolution
    )


def test_declared_create_missing_replacement_request_still_blocks():
    resolution = resolve_existing_targets(
        goal="替换第一镜头的提示词",
        operation="replace_shot_prompt",
        target_node_ids=["shot-1"],
        commands=[],
        nodes=[_node("shot-1", "第一镜头")],
        declared_target_strategy="create_missing",
        creation_reason="模型尝试创建替代节点",
    )

    assert resolution["dependency_node_ids"] == []
    assert resolution["suggested_target_node_ids"] == ["shot-1"]
    assert should_block_creation(
        target_strategy="create_missing", resolution=resolution
    )


def test_declared_create_missing_source_rewritten_in_batch_still_blocks():
    """A batch that rewrites the named node in place is a replacement, not a
    dependency, so the guard keeps firing."""

    resolution = resolve_existing_targets(
        goal="从当前脚本节点继续到最终成片",
        operation="脚本直达成片",
        target_node_ids=["script-a"],
        commands=[
            {"type": "update_node_prompt", "node_id": "script-a", "prompt": "改写"}
        ],
        nodes=[_node("script-a", "脚本生成器", "scriptNode")],
        declared_target_strategy="create_missing",
        creation_reason="画布只有脚本节点，下游载体必须新建",
    )

    assert resolution["suggested_target_node_ids"] == ["script-a"]
    assert should_block_creation(
        target_strategy="create_missing", resolution=resolution
    )
