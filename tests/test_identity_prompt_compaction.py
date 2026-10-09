"""Regression contract for model-bound character identity prompts.

The full local prompt may retain rich screenplay evidence for audit, but the
900-character NewAPI uplink must prioritize the requested character sheet over
unrelated screenplay events.
"""

from novelvideo.generators.newapi_image_uplink import compact_prompt_for_newapi_upstream


def _identity_sheet_prompt() -> str:
    unrelated_story = (
        "全项目统一剧本风格证据：类型是荒诞科幻、黑色喜剧、生物朋克。"
        "第一场里，阿诚坐在电脑屏幕前，死死盯着第108章：然后，他死了。"
        "阿诚的房间堆满泡面和废纸，镜头极速推近他的脸和电脑屏幕。"
    )
    # Repeat the narrative evidence to reproduce the production prompt shape:
    # style evidence currently appears before the character-specific contract.
    style_evidence = unrelated_story * 8
    return f"""Character identity reference sheet. Neutral studio setup.
PLAIN SOLID WHITE background; no environment, scenery, or props.
{style_evidence}

Using Image 1 as the FACE IDENTITY ANCHOR for [MDNL] (摩登女郎),
create a 4-panel character sheet: face close-up, front full body,
three-quarter full body, and back full body.

IDENTITY LOCKING (CRITICAL): preserve the exact same facial identity from Image 1.

CHARACTER DETAILS (CRITICAL):
米白色修身羊毛大衣，内搭黑色高领针织衫与铅笔裙，脚穿裸色尖头高跟鞋。

COSTUME REFERENCE IMAGE (CRITICAL):
Image 2 is the costume anchor. Combine the FACE from Image 1 with the CLOTHING
from Image 2. All four panels must show the same person and the same outfit.
"""


def _compact_identity_sheet() -> str:
    compacted, _meta = compact_prompt_for_newapi_upstream(
        _identity_sheet_prompt(),
        max_chars=900,
        reference_count=2,
    )
    return compacted


def test_newapi_identity_sheet_compaction_keeps_character_and_reference_contract() -> None:
    compacted = _compact_identity_sheet()
    lowered = compacted.casefold()
    required = {
        "character_name": "摩登女郎" in compacted,
        "appearance": "米白色修身羊毛大衣" in compacted,
        "four_panel_layout": "4-panel" in lowered,
        "image_1_face_anchor": "image 1" in lowered and "face" in lowered,
        "image_2_costume_anchor": (
            "image 2" in lowered
            and ("costume" in lowered or "clothing" in lowered)
        ),
    }
    missing = [name for name, present in required.items() if not present]

    assert not missing, (
        f"900-character identity uplink lost required fields: {missing}\n"
        f"uplink={compacted}"
    )


def test_newapi_identity_sheet_compaction_drops_unrelated_story_content() -> None:
    compacted = _compact_identity_sheet()
    forbidden = [token for token in ("阿诚", "第108章", "电脑屏幕") if token in compacted]

    assert not forbidden, (
        f"identity uplink retained unrelated screenplay content: {forbidden}\n"
        f"uplink={compacted}"
    )


def test_newapi_animated_identity_sheet_uses_identity_compaction() -> None:
    pollution = (
        "阿诚坐在电脑前，电脑屏幕显示第108章；镜头极速推近。" * 30
    )
    prompt = f"""Animated character turnaround / identity sheet. Neutral presentation setup.
PLAIN SOLID WHITE background ONLY. {pollution}

Using the reference image as IDENTITY ANCHOR for [MDNL] (摩登女郎),
create a 4-panel animated character reference sheet arranged LEFT to RIGHT:
- Panel 1: FACE CLOSEUP
- Panel 2: FRONT full body
- Panel 3: THREE-QUARTER VIEW full body
- Panel 4: BACK VIEW full body

IDENTITY LOCKING (CRITICAL): preserve the same character identity exactly.
CHARACTER DETAILS (CRITICAL - use this for clothing and appearance):
米白色羊毛大衣，黑色高领针织衫，裸色高跟鞋。
"""

    compacted, meta = compact_prompt_for_newapi_upstream(
        prompt,
        max_chars=900,
        reference_count=1,
    )

    assert meta["identity_sheet_mode"] is True
    assert "摩登女郎" in compacted
    assert "米白色羊毛大衣" in compacted
    assert "4-panel" in compacted.casefold()
    assert "identity anchor" in compacted.casefold()
    for forbidden in ("阿诚", "第108章", "电脑屏幕"):
        assert forbidden not in compacted
