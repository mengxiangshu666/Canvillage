import json
from pathlib import Path

import pytest

from novelvideo.freezone.starter_workflow_contract import (
    StarterWorkflowContractError,
    select_starter_workflow_id,
    validate_starter_workflow,
    validate_starter_workflow_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "novelvideo" / "assets" / "canvas_starter_workflows.json"


def _catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_has_contract_and_valid_graph_for_every_starter_workflow():
    catalog = validate_starter_workflow_catalog(_catalog())

    assert len(catalog) == 16
    assert set(catalog) == {
        "story-continuity-film",
        "single-reference-video",
        "multi-reference-video",
        "refine-then-video",
        "storyboard-to-video",
        "video-composition",
        "panorama-shot-planning",
        "product-showcase-video",
        "first-last-frame-transition",
        "motion-reference-redraw",
        "script-voice-video",
        "music-driven-video",
        "video-analysis-recut",
        "image-to-3d-shot",
        "original-vertical-short-film",
        "product-advertisement",
    }


def test_catalog_rejects_a_template_that_claims_compose_without_compose_node():
    template = next(item for item in _catalog() if item["id"] == "single-reference-video")
    invalid = {**template, "required_roles": ["video_generation", "final_compose"]}

    with pytest.raises(StarterWorkflowContractError, match="final_compose"):
        validate_starter_workflow(invalid)


def test_catalog_rejects_edges_to_unknown_node_keys():
    template = next(item for item in _catalog() if item["id"] == "single-reference-video")
    invalid = {**template, "edges": [{"source": "reference", "target": "missing"}]}

    with pytest.raises(StarterWorkflowContractError, match="invalid edge"):
        validate_starter_workflow(invalid)


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        ({"delivery_level": "shot_draft", "shot_count": 1}, "storyboard-to-video"),
        ({"delivery_level": "shot_draft", "shot_count": 8}, "story-continuity-film"),
        (
            {
                "delivery_level": "media_draft",
                "graph_contract": {"required_node_roles": ["character_asset"]},
            },
            "story-continuity-film",
        ),
        ({"delivery_level": "final_film"}, "script-voice-video"),
    ],
)
def test_starter_selection_follows_director_intent(contract: dict, expected: str):
    assert select_starter_workflow_id(contract, fallback="storyboard-to-video") == expected
