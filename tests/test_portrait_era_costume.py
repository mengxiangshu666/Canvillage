"""基准图在描述没写服装时，要按描述内容判断时代，不能默认现代装。

小江实锤：描述只有「修炼幻形诀的干女儿」，没写服装，基准图画成了卫衣牛仔裤，
后面所有镜头都照着穿。规则：描述里出现修仙元素就穿古装布袍。
"""

from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator


def _portrait_prompt(description: str) -> str:
    gen = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)
    return gen._build_character_prompt(
        character_tag="[XJ]",
        character_name="小江",
        character_prompt=description,
        style_name="script_auto",
        project_dir="",
        style_keywords="cinematic",
        negative_keywords="",
        ethnicity="chinese",
    )


def test_xianxia_description_without_costume_demands_period_robes():
    prompt = _portrait_prompt("十二三岁，黑色双马尾，修炼幻形诀的干女儿，圆脸")
    assert "period robes" in prompt
    assert "NEVER jeans" in prompt


def test_modern_description_is_not_forced_into_robes():
    prompt = _portrait_prompt("二十多岁的城市白领，黑色长发，鹅蛋脸")
    assert "modern-life description wears modern clothes" in prompt
