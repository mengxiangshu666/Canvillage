"""The durable project creative contract behind the Agent's one-shot alignment."""

from __future__ import annotations

import json

from novelvideo.creative_execution.creative_contract import (
    CONTRACT_FIELDS,
    CREATIVE_CONTRACT_CONFIG_KEY,
    contract_answer_map,
    derive_project_contract_defaults,
    load_creative_contract,
    merge_project_contract_answers,
    render_creative_contract_prompt,
    resolve_creative_contract,
    save_creative_contract,
)


def _state_dir(tmp_path, config: dict | None = None):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project_config.json").write_text(
        json.dumps(config or {}),
        encoding="utf-8",
    )
    return state_dir


def test_answers_survive_into_a_new_conversation(tmp_path):
    state_dir = _state_dir(tmp_path)

    save_creative_contract(
        state_dir,
        {"creative_subject": "雨夜车站里女孩撑开黑伞", "audio": "无对白，仅环境音"},
    )

    # A later conversation supplies nothing of its own and still gets them back.
    merged = merge_project_contract_answers({}, state_dir=state_dir)

    assert merged["creative_subject"] == "雨夜车站里女孩撑开黑伞"
    assert merged["audio"] == "无对白，仅环境音"
    stored = json.loads((state_dir / "project_config.json").read_text(encoding="utf-8"))
    assert stored[CREATIVE_CONTRACT_CONFIG_KEY]["schema"] == "creative_contract.v1"
    assert stored[CREATIVE_CONTRACT_CONFIG_KEY]["fields"]["audio"]["source"] == "user"


def test_operator_answers_win_over_the_stored_contract(tmp_path):
    state_dir = _state_dir(tmp_path)
    save_creative_contract(state_dir, {"visual_style": "anime"})

    merged = merge_project_contract_answers(
        {"visual_style": "港式霓虹"},
        state_dir=state_dir,
    )

    assert merged["visual_style"] == "港式霓虹"


def test_locked_project_style_and_aspect_ratio_satisfy_the_contract(tmp_path):
    state_dir = _state_dir(
        tmp_path,
        {"visual_style": "anime", "aspect_ratio": "9:16"},
    )

    known, missing = resolve_creative_contract(state_dir)

    assert known["visual_style"] == "anime"
    assert known["aspect_ratio"] == "9:16"
    assert "visual_style" not in missing
    assert "aspect_ratio" not in missing
    # Project-owned values are derived, never frozen into the stored contract.
    stored = json.loads((state_dir / "project_config.json").read_text(encoding="utf-8"))
    assert CREATIVE_CONTRACT_CONFIG_KEY not in stored


def test_a_style_change_takes_effect_without_touching_the_contract(tmp_path):
    state_dir = _state_dir(tmp_path, {"visual_style": "chinese_period_drama"})
    assert merge_project_contract_answers({}, state_dir=state_dir)["visual_style"] == "chinese_period_drama"

    (state_dir / "project_config.json").write_text(
        json.dumps({"visual_style": "chinese_shadow_puppet"}),
        encoding="utf-8",
    )

    assert merge_project_contract_answers({}, state_dir=state_dir)["visual_style"] == "chinese_shadow_puppet"


def test_scene_anchor_can_be_derived_from_canvas_nodes(tmp_path):
    defaults = derive_project_contract_defaults(
        project_config={},
        canvas_nodes=[
            {"id": "n1", "type": "imageGenNode", "data": {"prompt": "立绘"}},
            {"id": "n2", "type": "imageGenNode", "data": {"scene": "老工业城市开阔空地"}},
        ],
    )

    assert defaults["scene_or_environment"] == "老工业城市开阔空地"


def test_render_is_empty_for_a_project_with_nothing_settled(tmp_path):
    state_dir = _state_dir(tmp_path)

    assert render_creative_contract_prompt(state_dir) == ""
    assert render_creative_contract_prompt(None) == ""


def test_render_lists_settled_fields_and_the_remaining_gap(tmp_path):
    state_dir = _state_dir(tmp_path, {"visual_style": "anime", "aspect_ratio": "16:9"})
    save_creative_contract(state_dir, {"creative_subject": "雨夜车站的追逐"})

    block = render_creative_contract_prompt(state_dir)

    assert block.startswith("[CREATIVE_CONTRACT]")
    assert block.endswith("[/CREATIVE_CONTRACT]")
    assert "创作主体/事件: 雨夜车站的追逐" in block
    assert "视觉风格: anime" in block
    assert "画幅: 16:9" in block
    assert "尚未对齐" in block
    assert "声音" in block
    assert "视觉风格: anime" in block


def test_render_is_bounded(tmp_path):
    state_dir = _state_dir(tmp_path)
    save_creative_contract(state_dir, {"creative_subject": "长" * 4_000})

    block = render_creative_contract_prompt(state_dir, max_chars=400)

    assert len(block) <= 400
    assert block.endswith("[/CREATIVE_CONTRACT]")


def test_load_never_raises_on_malformed_or_missing_config(tmp_path):
    state_dir = tmp_path / "missing"
    assert load_creative_contract(state_dir)["fields"] == {}

    state_dir = _state_dir(tmp_path)
    (state_dir / "project_config.json").write_text("{not json", encoding="utf-8")
    assert load_creative_contract(state_dir)["fields"] == {}

    state_dir = _state_dir(tmp_path, {"creative_contract": {"fields": "nope"}})
    assert load_creative_contract(state_dir)["fields"] == {}


def test_save_ignores_unknown_and_blank_fields(tmp_path):
    state_dir = _state_dir(tmp_path)

    save_creative_contract(
        state_dir,
        {"creative_subject": "  ", "unknown_field": "x", "audio": "配乐"},
    )

    fields = load_creative_contract(state_dir)["fields"]
    assert set(fields) == {"audio"}
    assert contract_answer_map({"fields": fields}) == {"audio": "配乐"}
    assert set(CONTRACT_FIELDS) >= set(fields)
