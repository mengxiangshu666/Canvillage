from novelvideo.research.aigc_director_recipes import (
    DIRECTOR_RESEARCH_PACK_VERSION,
    build_director_research_context,
    build_director_recipe_receipt,
    select_director_recipes,
)
from novelvideo.research.provenance import validate_knowledge_provenance


def test_recipe_selection_is_bounded_and_matches_video_context():
    recipes = select_director_recipes(
        mode="first_frame",
        beat={
            "visual_description": "雨夜巷口，角色抬头说话",
            "video_prompt": "镜头缓慢推近，保持首帧姿态向前动作",
            "audio_type": "dialogue",
            "dialogue": "别回头。",
        },
        limit=99,
    )

    assert 1 <= len(recipes) <= 4
    ids = {item["recipe_id"] for item in recipes}
    assert "director.first_frame_forward_chain.v1" in ids
    assert all(item["source"] and item["license"] for item in recipes)
    assert all(validate_knowledge_provenance(item["provenance"]) == [] for item in recipes)


def test_research_context_is_provenance_aware_and_does_not_dump_source_text():
    context = build_director_research_context(
        mode="multimodal_reference",
        beat={"visual_description": "两人对峙，参考图片1和图片2"},
        max_chars=3600,
    )

    assert context.startswith("[AIGC_DIRECTOR_RESEARCH]")
    assert context.endswith("[/AIGC_DIRECTOR_RESEARCH]")
    assert DIRECTOR_RESEARCH_PACK_VERSION not in context
    assert "来源：" in context
    assert "只引用当前镜头真正需要" in context or "参考素材" in context
    assert len(context) <= 3_650


def test_recipe_selection_does_not_inject_unrelated_recipe_for_unmatched_task():
    recipes = select_director_recipes(
        mode="text_to_video",
        beat={"visual_description": "一张静态产品海报，只有颜色和材质描述"},
        prompt_guidance="只保持品牌色和排版清晰",
    )

    assert recipes == []


def test_recipe_selection_respects_model_kind_and_creation_stage():
    recipes = select_director_recipes(
        mode="reference",
        model_kind="image",
        creation_stage="prompt",
        beat={"visual_description": "参考图中的角色保持身份，只修改背景"},
    )

    ids = {item["recipe_id"] for item in recipes}
    assert "director.image.reference_preservation.v1" in ids
    assert "director.first_frame_forward_chain.v1" not in ids
    assert all("image" in item["model_kinds"] for item in recipes)


def test_director_recipe_receipt_is_bounded_and_hashable():
    recipes = select_director_recipes(
        mode="reference",
        model_kind="image",
        creation_stage="prompt",
        beat={"visual_description": "参考图中的角色保持身份，只修改背景"},
    )
    receipt = build_director_recipe_receipt(
        recipes=recipes,
        model_kind="image",
        creation_stage="prompt",
        reference_semantics="explicit_roles",
        capability_revision="direct-model-contract.v2",
    )

    assert receipt["schema"] == "director.recipe-receipt.v1"
    assert receipt["recipe_ids"]
    assert receipt["selection_hash"]
    assert set(receipt["recipe_ids"]) == set(receipt["checks"])
    assert all("source" in item and "license" in item for item in receipt["sources"])
    assert all(len(item["content_sha256"]) == 64 for item in receipt["sources"])
