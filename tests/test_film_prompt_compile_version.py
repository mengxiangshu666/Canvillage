import pytest

from novelvideo.freezone.film_prompt_contract import (
    FILM_PROMPT_COMPILER_VERSION,
    build_film_prompt_bundle,
    validate_film_prompt_bundle,
)


def _rows():
    return [
        {
            "shot_no": 1,
            "duration": 4,
            "shot_prompt": "[画面构图：中景] + [角色卡/主体描述：阿雀] + [空间关系：屋内] + [状态：抬眼] + [环境：祠堂] + [光影：门口天光] + [风格：写实] + [技术参数：35mm]",
            "video_motion_prompt": "[主体动作：抬眼] + [物理动作：呼吸] + [状态变化：注意] + [摄影：固定] + [声音：雨声] + [时长：4.0s]",
        }
    ]


def test_compiler_version_and_input_result_hashes_are_deterministic():
    first = build_film_prompt_bundle(
        _rows(), input_context={"source_hash": "abc", "template": "v1"}
    )
    second = build_film_prompt_bundle(
        _rows(), input_context={"source_hash": "abc", "template": "v1"}
    )
    assert first["compiler"] == second["compiler"]
    assert first["compiler"]["version"] == FILM_PROMPT_COMPILER_VERSION
    assert first["compiler"]["input_hash"].startswith("film-input.v1:")
    assert first["compiler"]["result_hash"].startswith("film-result.v1:")
    assert validate_film_prompt_bundle(first)["compiler"] == first["compiler"]


def test_compiler_hash_changes_when_public_inputs_or_context_change():
    base = build_film_prompt_bundle(_rows(), input_context={"source_hash": "abc"})
    changed_source = build_film_prompt_bundle(
        [{**_rows()[0], "duration": 5}],
        input_context={"source_hash": "abc"},
    )
    changed_context = build_film_prompt_bundle(
        _rows(), input_context={"source_hash": "def"}
    )
    assert changed_source["compiler"]["input_hash"] != base["compiler"]["input_hash"]
    assert changed_context["compiler"]["input_hash"] != base["compiler"]["input_hash"]


def test_tampered_compiler_receipt_is_rejected_but_legacy_bundle_still_valid():
    bundle = build_film_prompt_bundle(_rows())
    tampered = {
        **bundle,
        "compiler": {
            **bundle["compiler"],
            "result_hash": "film-result.v1:000000000000000000000000",
        },
    }
    with pytest.raises(ValueError, match="compiler receipt"):
        validate_film_prompt_bundle(tampered)

    legacy = {key: value for key, value in bundle.items() if key != "compiler"}
    assert (
        validate_film_prompt_bundle(legacy)["contract_revision"]
        == bundle["contract_revision"]
    )
