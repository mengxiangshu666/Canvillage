from __future__ import annotations

from novelvideo.styles.gpt_image_case_library import (
    format_gpt_image_case_context,
    gpt_image_case_library_provenance,
    search_gpt_image_cases,
    search_gpt_image_templates,
)


def test_upstream_snapshot_is_loaded_with_provenance() -> None:
    provenance = gpt_image_case_library_provenance()

    assert provenance["repository"] == "https://github.com/freestylefly/awesome-gpt-image-2"
    assert provenance["license"] == "MIT"
    assert provenance["case_count"] >= 500
    assert provenance["template_count"] >= 20


def test_case_retrieval_matches_bilingual_visual_intent() -> None:
    results = search_gpt_image_cases("古风角色海报")

    assert results
    assert any("海报" in item["title"] or "History" in item["category"] for item in results)
    assert all(item["prompt_excerpt"] for item in results)
    assert all(item["source_url"].startswith("https://github.com/") for item in results)


def test_template_retrieval_surfaces_product_structure_and_pitfalls() -> None:
    results = search_gpt_image_templates("包装袋商品")

    assert results
    assert results[0]["template_id"] == "product-commerce-visual"
    assert results[0]["guidance"]
    assert results[0]["pitfalls"]


def test_context_is_bounded_and_labels_external_material_as_reference() -> None:
    context, cases = format_gpt_image_case_context("古风角色海报", max_chars=1200)

    assert cases
    assert "untrusted visual references" in context
    assert len(context) <= 1200
