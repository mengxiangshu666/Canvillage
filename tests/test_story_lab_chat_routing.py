from novelvideo.chat.service import _script_creation_model_reply_prompt


def test_no_attachment_script_request_reaches_story_lab_agent() -> None:
    assert _script_creation_model_reply_prompt("帮我把赛博朋克创意写成十集微短剧") is None


def test_story_creation_with_project_context_reaches_story_lab_agent() -> None:
    prompt = "[CURRENT_PROJECT]\ndemo\n[/CURRENT_PROJECT]\n生成一部长篇小说大纲"
    assert _script_creation_model_reply_prompt(prompt) is None
