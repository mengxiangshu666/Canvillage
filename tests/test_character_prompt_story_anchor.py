from novelvideo.generators.image_generator import _direct_character_style_prompt


def test_character_prompt_does_not_upload_story_body_to_image_model(tmp_path):
    story = "雨夜废弃仓库，铁皮屋顶漏雨。两名青年在积水中徒手近身格斗。"
    (tmp_path / "novel.txt").write_text(story, encoding="utf-8")

    prompt = _direct_character_style_prompt(
        style="script_auto",
        project_dir=str(tmp_path),
        image_model="direct/test",
        subject_prompt="男性，青年，黑色短发",
        character_name="男主",
    )

    assert story not in prompt
    assert "雨夜废弃仓库" not in prompt
    assert "两名青年在积水中徒手近身格斗" not in prompt
    assert len(prompt) < 1200
    assert "不要根据角色名或动作刻板印象自行切换时代" in prompt
    assert "同项目角色保持同一视觉世界" in prompt
    assert "剧本动作、事件和场景状态由分镜与视频阶段处理" in prompt
    assert "只生成当前角色一人" in prompt
    assert "不出现其他人物、倒地角色" in prompt


def test_character_prompt_keeps_compact_style_metadata_only(tmp_path):
    story = (
        "题材：东方奇幻。\n"
        "风格：冷峻水墨与暗金材质。\n"
        "剧本正文：角色在爆炸和血战中受伤。"
    )
    (tmp_path / "novel.txt").write_text(story, encoding="utf-8")

    prompt = _direct_character_style_prompt(
        style="script_auto",
        project_dir=str(tmp_path),
        image_model="direct/test",
        subject_prompt="男性，青年，黑色短发",
        character_name="男主",
    )

    assert "题材：东方奇幻" in prompt
    assert "风格：冷峻水墨与暗金材质" in prompt
    assert "角色在爆炸和血战中受伤" not in prompt


def test_portrait_template_asks_for_a_full_body_front_shot(tmp_path):
    """默认口径是完整正面全身照，不能再是「半身或全身均可」。"""
    prompt = _direct_character_style_prompt(
        style="script_auto",
        project_dir=str(tmp_path),
        image_model="direct/test",
        subject_prompt="男性，青年，黑色短发",
        character_name="男主",
    )

    assert "正面全身角色基准图" in prompt
    assert "从头顶到脚底" in prompt
    assert "人物完整不裁切" in prompt
    assert "半身或全身均可" not in prompt
    assert "同项目角色保持同一视觉世界" in prompt


def test_four_view_template_describes_the_sheet_without_banning_panels(tmp_path):
    """四视图设定表是分格版式：要讲清布局，但不能沿用「禁拼贴 / 禁分屏」。"""
    prompt = _direct_character_style_prompt(
        style="script_auto",
        project_dir=str(tmp_path),
        image_model="direct/test",
        subject_prompt="男性，青年，黑色短发",
        character_name="男主",
        prompt_template="four_view",
    )

    assert "四视图设定表" in prompt
    assert "左侧 1/3" in prompt
    assert "右侧 2/3" in prompt
    assert "严格无头" in prompt
    assert "颈部残端" in prompt
    assert "同一张图" in prompt
    assert "浅灰 #E8E8E8" in prompt
    assert "拼贴、分屏" not in prompt


def test_unknown_template_falls_back_to_the_single_portrait_prompt(tmp_path):
    prompt = _direct_character_style_prompt(
        style="script_auto",
        project_dir=str(tmp_path),
        image_model="direct/test",
        subject_prompt="男性，青年，黑色短发",
        character_name="男主",
        prompt_template="not-a-template",
    )

    assert "正面全身角色基准图" in prompt
    assert "四视图设定表" not in prompt
