"""MiniMax H3 must receive the official audiovisual prompt structure."""

from novelvideo.freezone.video_request_contract import (
    H3_AUDIO_TAIL,
    NATIVE_SPEECH_OFF_CONSTRAINT,
    build_minimax_h3_provider_prompt,
    derive_h3_soundscape,
    is_minimax_h3_model_identifier,
    lint_h3_provider_prompt,
)
from novelvideo.generators.video.direct_video_protocol_contracts import (
    build_minimax_video_v2_payload,
)


def test_h3_prompt_uses_official_sections_and_contains_each_turn_once() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "[Shot 1] 女孩看向门口，说话表演。",
        ["你终于来了。", "我们走吧。", "我们走吧。"],
        speaker="女孩",
    )

    assert prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert prompt.index("integrated_multimodal_description:") < prompt.index(
        "overall_soundscape:"
    ) < prompt.index("non_diegetic_music:")
    assert prompt.count('<d>[Chinese] 你终于来了。</d>') == 1
    assert prompt.count('<d>[Chinese] 我们走吧。</d>') == 2
    assert prompt.count("你终于来了。") == 1
    assert prompt.count("我们走吧。") == 2
    assert "</d>。" not in prompt
    assert "说话表演" not in prompt
    assert "对白：" not in prompt
    assert NATIVE_SPEECH_OFF_CONSTRAINT not in prompt


def test_h3_visual_only_prompt_has_no_speech_constraint_or_placeholder() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "人物开口说话，镜头缓慢推近。说话表演",
        [],
    )

    assert prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert "说话表演" not in prompt
    assert "不要朗读" not in prompt
    assert "没有必须说出的台词" not in prompt
    assert "<d>" not in prompt


def test_h3_singing_uses_sings_and_preserves_original_lyrics() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "主体跳起来开始演唱表演。",
        ["哎哟哎哟哎哟花姑娘！！！"],
        sung=True,
    )

    assert "sings: <d>[Chinese] 哎哟哎哟哎哟花姑娘！！！</d>" in prompt
    assert "从第 0 秒直接演唱" not in prompt
    assert "禁止开场说话" not in prompt


def test_h3_language_tags_match_the_original_dialogue() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "The speaker performs. 说话表演 说话表演",
        ["ありがとう。", "안녕하세요."],
    )

    assert "<d>[Japanese] ありがとう。</d>" in prompt
    assert "<d>[Korean] 안녕하세요.</d>" in prompt


def test_h3_builder_is_idempotent_for_an_already_structured_prompt() -> None:
    once = build_minimax_h3_provider_prompt(
        "女孩站在雨里，说话表演。",
        ["我回来了。"],
    )

    assert build_minimax_h3_provider_prompt(once, ["我回来了。"]) == once


def test_h3_prompt_ends_with_the_audio_tail_exactly_once() -> None:
    """用户 2026-10-04 拍板的规则「不要字幕、不要 BGM」必须留在收尾。

    规则没变，写法从中文换成英文：真机复验（2026-10-04 第四轮）证明中文尾注
    挡不住字幕，成片照样把台词烧进画面；现在的英文写法取自外部语料里成片产线
    实测在用的句子。
    """

    prompt = build_minimax_h3_provider_prompt(
        "女孩站在雨里，说话表演。",
        ["我回来了。"],
    )

    assert prompt.endswith(f"\n\n{H3_AUDIO_TAIL}")
    assert prompt.count(H3_AUDIO_TAIL) == 1
    assert "不要朗读" not in prompt
    # 同一条提示词过第二遍机器不能重复追加。
    assert build_minimax_h3_provider_prompt(prompt, ["我回来了。"]) == prompt
    # 无台词的静默镜头同样要带尾注。
    silent = build_minimax_h3_provider_prompt("黑屏，无字的纯黑画面。", [])
    assert silent.endswith(f"\n\n{H3_AUDIO_TAIL}")
    assert silent.count(H3_AUDIO_TAIL) == 1


def test_h3_pipeline_meta_never_reaches_the_visual_body() -> None:
    """优化器写给流程看的句子不能在成片提示词里复现。

    真机事故（2026-10-04，project 6925 beat 09）：画面里烧出的字幕
    「背向连续完成停步」逐字来自下面这段散文。
    """

    optimizer_prompt = (
        "从输入首帧的小臣转过身体背向供桌迈步开始，镜头沿主体方向平滑推近。"
        "说话者从首帧的面部状态开始保持连续表演，随后连续完成停步说话表演。"
        "运动在约4.0秒内沿同一方向延续，保持当前可见主体、空间和光线连续，"
        "不新增角色或道具，最后落在小臣嘴唇闭合目视前方地面，"
        "形成可直接剪辑的明确切点。口型、手势与台词同步，保持说话动作连续。\n\n"
        "PROJECT SCRIPT-DERIVED STYLE [a5f45f44a54a]:\n"
        "Preserve the visual medium, material language, palette, and art direction."
    )

    prompt = build_minimax_h3_provider_prompt(
        optimizer_prompt,
        ["我就知道。"],
        speaker="小臣_少年时期",
    )
    body = prompt.split("overall_soundscape:", 1)[0]

    assert "小臣转过身体背向供桌迈步" in body
    assert "随后停步" in body
    assert "末拍停在小臣嘴唇闭合目视前方地面" in body
    # 风格快照的英文正文是流程说明，不是画面内容：它进了画面段就会变成
    # 可念文本（H3 照读成乱语）。首帧已经承载风格，画面段必须干净。
    assert "Preserve the visual medium" not in body
    assert "PROJECT SCRIPT-DERIVED STYLE" not in body
    assert "<d>[Chinese] 我就知道。</d>" in body
    for meta in (
        "运动在约",
        "保持当前可见主体",
        "不新增角色或道具",
        "形成可直接剪辑的明确切点",
        "口型、手势与台词同步",
        "a5f45f44a54a",
        "首帧的面部状态",
    ):
        assert meta not in body


def test_h3_structured_prompt_is_still_sanitized() -> None:
    once = build_minimax_h3_provider_prompt(
        "女孩站在雨里，说话表演。",
        ["我回来了。"],
    )
    dirty = once.replace(
        "integrated_multimodal_description: [Shot 1]",
        (
            "integrated_multimodal_description: [Shot 1] "
            "女孩只负责倾听和反应，不开口。"
        ),
    )

    cleaned = build_minimax_h3_provider_prompt(dirty, ["我回来了。"])

    assert "只负责倾听和反应" not in cleaned
    assert "不开口" not in cleaned
    assert cleaned.count("<d>[Chinese] 我回来了。</d>") == 1


def test_h3_director_meta_never_becomes_spoken_copy() -> None:
    prompt = build_minimax_h3_provider_prompt(
        (
            "钟楼顶层，林默在画面右侧保持不动，只负责倾听和反应，不开口。"
            "林默面对林默说出这句话。"
            "苏岚抬起头望向铜钟说话，说话表演。口型同步。"
            "对白语气：压抑。台词：不要朗读。别说话。"
            "镜头缓慢上扬。"
        ),
        [
            "水位越过警戒线，七分钟后封港。",
            "如果钟声能让人记住。",
            "那就让全城听见。",
        ],
        speaker="苏岚",
    )

    body = prompt.split("overall_soundscape:", 1)[0]
    assert "林默在画面右侧保持不动" in body
    assert "只负责倾听和反应" not in body
    assert "不开口" not in body
    assert "说出这句话" not in body
    assert "口型同步" not in body
    assert "对白" not in body
    assert "台词" not in body
    assert "说话表演" not in body
    assert "抬起头望向铜钟" in body
    assert "镜头缓慢上扬" in body
    assert body.count('<d>[Chinese] 水位越过警戒线，七分钟后封港。</d>') == 1
    assert body.count('<d>[Chinese] 如果钟声能让人记住。</d>') == 1
    assert body.count('<d>[Chinese] 那就让全城听见。</d>') == 1
    assert body.index("水位越过警戒线") < body.index("如果钟声能让人记住") < body.index(
        "那就让全城听见"
    )


def test_h3_platform_instructions_do_not_reach_the_audio_script() -> None:
    prompt = build_minimax_h3_provider_prompt(
        (
            "根据分镜脚本和分镜图生成一段15秒的视频\n"
            "输出要求：动作自然，运动平滑。\n"
            "首帧约束：保持输入主体和场景。\n"
            "雨夜钟楼内部，女孩抬头望向铜钟，镜头缓慢推近。"
        ),
        ["我回来了。"],
    )

    body = prompt.split("overall_soundscape:", 1)[0]
    assert "根据分镜" not in body
    assert "输出要求" not in body
    assert "首帧约束" not in body
    assert "雨夜钟楼内部" in body
    assert "女孩抬头望向铜钟" in body


def test_h3_real_platform_slot_and_rejection_metadata_is_removed() -> None:
    prompt = build_minimax_h3_provider_prompt(
        (
            "[明确的摄影机运镜轨迹与速度：极慢速环绕半周] + "
            "[主体极其具体的物理动作细节或状态变化：手指轻触发丝] + "
            "[环境物理动态：白亚麻裙摆在轮椅踏板上轻微拂动] + "
            "[音效与氛围描述：低声啜泣与衣物摩擦] + "
            "[对话台词与语气：林建国（苍老哽咽）：说话表演] + "
            "[时长：6.0s]\n"
            "严格继承输入图片中的主体、构图、服装、光线和场景信息。\n"
            "综合文本、图像、视频和音频参考统一建模，保持主体身份和场景连续。\n"
            "拒绝：画面闪烁、人物变形、跳帧、文字字幕、水印。"
        ),
        ["对不起……让你等了三十年……"],
        speaker="林建国",
    )

    body = prompt.split("overall_soundscape:", 1)[0]
    assert "极慢速环绕半周" in body
    assert "手指轻触发丝" in body
    assert "白亚麻裙摆" in body
    assert "[音效与氛围描述" not in body
    assert "[对话台词与语气" not in body
    assert "[时长：" not in body
    assert "严格继承" not in body
    assert "综合文本" not in body
    assert "拒绝：" not in body
    assert body.count("<d>[Chinese] 对不起……让你等了三十年……</d>") == 1


def test_h3_literal_dialogue_tags_are_never_sanitized() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "女孩站在雨里，<d>[Chinese] 我不开口，也不说出这句话。</d>，镜头缓慢推近。",
        [],
    )

    assert "<d>[Chinese] 我不开口，也不说出这句话。</d>" in prompt


def test_h3_final_v2_payload_has_one_text_slot_with_the_structured_prompt() -> None:
    prompt = build_minimax_h3_provider_prompt(
        "女孩站在雨里，说话表演。",
        ["我回来了。"],
    )
    payload = build_minimax_video_v2_payload(
        model="minimax-h3",
        prompt=prompt,
        resolution="768p",
        duration=6,
        ratio="16:9",
    )

    assert payload["content"] == [{"type": "text", "text": prompt}]
    assert payload["ratio"] == "16:9"


def test_minimax_h3_identifier_detection_does_not_match_unrelated_h3() -> None:
    assert is_minimax_h3_model_identifier("direct_minimax-h3")
    assert is_minimax_h3_model_identifier("MiniMax_H3")
    assert not is_minimax_h3_model_identifier("h3")
    assert not is_minimax_h3_model_identifier("seedance-2")


def test_h3_style_snapshot_never_reaches_the_speakable_body() -> None:
    """风格锁只服务流程；进画面段就会被念出来。

    真机提示词的持久化会把换行压成空格，锁头和正文常常挤在同一行——
    只截断锁头之后的文字，前面的画面内容一个字都不能丢。
    """

    body = (
        "小臣背向供桌迈步，镜头缓慢推近。末拍停在他停步的近景。 "
        "PROJECT SCRIPT-DERIVED STYLE [a5f45f44a54a]:\n"
        "Preserve the visual medium, material language, palette, and art direction "
        "established by the screenplay and start frame; derive motion from that "
        "medium and do not switch to a generic live-action or animation default.\n"
        "AVOID: generic live-action"
    )

    prompt = build_minimax_h3_provider_prompt(body, [], beat={"visual_description": "小臣迈步"})
    head = prompt.split("overall_soundscape:", 1)[0]

    assert "小臣背向供桌迈步" in head
    assert "末拍停在他停步的近景" in head
    assert "PROJECT SCRIPT-DERIVED STYLE" not in head
    assert "Preserve the visual medium" not in head
    assert "AVOID" not in head
    assert "do not switch" not in head


def test_h3_soundscape_is_concrete_chinese_without_voice_words() -> None:
    """声音层只写现场有什么声音，不写谁在说话。"""

    beat = {
        "visual_description": "深夜，雨点落在木屋外的屋檐上，父亲站在门前",
        "time_of_day": "夜",
        "scene_ref_json": '{"scene_id": "木屋"}',
    }

    soundscape = derive_h3_soundscape(beat)
    prompt = build_minimax_h3_provider_prompt("父亲站在门前，镜头缓慢推近。", [], beat=beat)
    section = prompt.split("overall_soundscape:", 1)[1].split("non_diegetic_music:", 1)[0]

    assert "雨" in soundscape
    assert soundscape in section
    # 老模板是整段英文，进模型就是可念文本。
    assert "Natural ambient sound" not in prompt
    for word in ("人声", "台词", "对白", "说话", "旁白"):
        assert word not in section


def test_h3_framework_lint_flags_the_two_real_failure_modes() -> None:
    """静态检查要能抓住我在真实项目里遇到的两种坏提示词。"""

    on_screen_text = (
        "黑屏中央空无一物，镜头沿主体方向平滑推近。末拍停在正中呈现第三场字样。"
    )
    prompt = build_minimax_h3_provider_prompt(on_screen_text, [])
    codes = {issue.code for issue in lint_h3_provider_prompt(prompt, audio_type="silence")}
    assert "on_screen_text_requested" in codes

    # 画布节点允许用户手写提示词，这类文字绕过优化器直接进模型；
    # 检查必须能在送出去之前把它照出来。
    silent_with_speech = (
        "integrated_multimodal_description: [Shot 1] 人物开口说话，镜头缓慢推近。\n\n"
        "overall_soundscape: 环境底噪与物理拟音。\n\n"
        "non_diegetic_music: N/A\n\nNo subtitles, no captions, no text on screen, no dialogue text, no Chinese text overlay appear at any moment in this shot. Dialogue exists only through speech and lip movement. No background music."
    )
    codes = {
        issue.code
        for issue in lint_h3_provider_prompt(silent_with_speech, audio_type="silence")
    }
    assert "speech_words_in_silent_shot" in codes

    with_time_code = (
        "integrated_multimodal_description: [Shot 1] 0-3秒：人物走向门口；"
        "3-5秒：推门离开。\n\n"
        "overall_soundscape: 环境底噪与物理拟音。\n\n"
        "non_diegetic_music: N/A\n\nNo subtitles, no captions, no text on screen, no dialogue text, no Chinese text overlay appear at any moment in this shot. Dialogue exists only through speech and lip movement. No background music."
    )
    codes = {
        issue.code
        for issue in lint_h3_provider_prompt(with_time_code, audio_type="silence")
    }
    assert "time_code_in_body" in codes


def test_h3_framework_lint_is_clean_on_a_framework_conformant_prompt() -> None:
    body = (
        "画面中人物背向门口迈步，镜头沿行进方向缓慢推近。"
        "随后右脚落定、重心前移，衣摆随惯性向前摆出；"
        "接着上半身转向供桌，手掌撑住桌沿。末拍停在手掌与桌沿接触的近景。"
    )
    prompt = build_minimax_h3_provider_prompt(
        body,
        ["我就知道。"],
        speaker="人物",
        beat={"visual_description": "屋内，人物走向供桌"},
    )

    assert lint_h3_provider_prompt(
        prompt,
        audio_type="dialogue",
        dialogue_text="我就知道。",
    ) == ()


def test_h3_workflow_six_segment_motion_prompt_loses_its_slot_names() -> None:
    """工作流那条链路交上来的是脚本合同的六段式运动稿，不是 H3 正文。

    真机证据：段名和流程说明留在画面段里，H3 会把它当剧本念出来或画成字幕。
    """

    raw = (
        "[明确的摄影机运镜轨迹与速度：跟随拍摄，镜头随门开合的节奏向后微退半步，速度缓慢稳定] + "
        "[主体极其具体的物理动作细节或状态变化：林晚右手发力拉开门板，邮差随之抬起右臂把信封递到两人之间] + "
        "[环境物理动态：走廊灯泡轻微频闪，邮差制服下摆持续滴水] + "
        "[音效与氛围描述：门轴吱呀声、走廊滴水声、持续雨声] + "
        "[对话台词与语气：无] + [时长：4.0s]"
    )
    prompt = build_minimax_h3_provider_prompt(raw, [])
    body = prompt.split("overall_soundscape:", 1)[0]

    for label in ("[明确的摄影机", "[主体极其具体", "[环境物理动态", "[音效", "[对话台词", "[时长"):
        assert label not in body
    assert "跟随拍摄" in body
    assert "林晚右手发力拉开门板" in body
    assert "走廊灯泡轻微频闪" in body
    assert lint_h3_provider_prompt(prompt) == ()


def test_h3_workflow_sound_design_reaches_the_soundscape_section() -> None:
    """工作流写的现场声不该被丢掉：它属于 ``overall_soundscape``，不属于画面段。"""

    raw = (
        "[明确的摄影机运镜轨迹与速度：镜头前推] + "
        "[主体极其具体的物理动作细节或状态变化：她握笔在纸面划出一道短横] + "
        "[环境物理动态：窗玻璃上雨水持续下淌] + "
        "[音效与氛围描述：雨打玻璃的闷响、笔尖划纸的沙沙声、无音乐] + "
        "[对话台词与语气：无] + [时长：4.0s]"
    )
    prompt = build_minimax_h3_provider_prompt(raw, [])
    section = prompt.split("overall_soundscape:", 1)[1].split("non_diegetic_music:", 1)[0]

    assert "雨打玻璃的闷响" in section
    assert "笔尖划纸的沙沙声" in section
    assert "无音乐" not in section
    assert "无音乐" not in prompt.split("overall_soundscape:", 1)[0]


def test_h3_workflow_dialogue_slot_is_tagged_once_and_not_left_in_the_body() -> None:
    raw = (
        "[明确的摄影机运镜轨迹与速度：跟随拍摄] + "
        "[主体极其具体的物理动作细节或状态变化：邮差抬起右臂把信封递出] + "
        "[环境物理动态：门缝吹入的穿堂风带起碎发] + "
        "[音效与氛围描述：门轴吱呀声、持续雨声] + "
        "[对话台词与语气：邮差（沙哑低沉、公事公办）：「签收一下。」] + [时长：4.0s]"
    )
    prompt = build_minimax_h3_provider_prompt(raw, ["签收一下。"], speaker="邮差")
    body = prompt.split("overall_soundscape:", 1)[0]

    assert body.count("<d>[Chinese] 签收一下。</d>") == 1
    assert "[对话台词与语气" not in body
    assert "沙哑低沉" not in body
    assert lint_h3_provider_prompt(
        prompt,
        audio_type="dialogue",
        dialogue_text="签收一下。",
    ) == ()


def test_h3_framework_lint_flags_leftover_workflow_slot_names() -> None:
    """编译层没拆掉的段名必须在送模型之前被照出来。"""

    prompt = (
        "integrated_multimodal_description: [Shot 1] "
        "[明确的摄影机运镜轨迹与速度：镜头前推]，人物抬头。\n\n"
        "overall_soundscape: 环境底噪与物理拟音。\n\n"
        "non_diegetic_music: N/A\n\nNo subtitles, no captions, no text on screen, no dialogue text, no Chinese text overlay appear at any moment in this shot. Dialogue exists only through speech and lip movement. No background music."
    )
    codes = {
        issue.code
        for issue in lint_h3_provider_prompt(prompt, audio_type="silence")
    }
    assert "workflow_label_in_body" in codes


def test_h3_audio_off_submission_still_sanitizes_the_picture_body() -> None:
    """关掉原生音频不等于跳过净化；外部配音的镜头仍要保留口型提示。"""

    from novelvideo.freezone.video_request_contract import sanitize_h3_visual_prompt
    from novelvideo.services.video_request_contract import (
        build_native_video_provider_prompt,
    )

    raw = (
        "[明确的摄影机运镜轨迹与速度：镜头前推] + "
        "[主体极其具体的物理动作细节或状态变化：她低头沉默，双唇抿紧] + "
        "[环境物理动态：雨丝斜落] + "
        "[音效与氛围描述：雨声] + [对话台词与语气：无] + [时长：4.0s]"
    )
    cleaned = sanitize_h3_visual_prompt(raw, speech_authorized=False)

    assert cleaned == "镜头前推。她低头沉默，双唇抿紧。雨丝斜落。"
    assert "[明确的摄影机" not in cleaned
    assert "overall_soundscape" not in cleaned

    class _Normalization:
        spoken_dialogue = ()

    assert build_native_video_provider_prompt(
        "minimax-h3",
        raw,
        _Normalization(),
        generate_audio=False,
    ) == cleaned

    dubbing = (
        "[明确的摄影机运镜轨迹与速度：缓慢推近] + "
        "[主体极其具体的物理动作细节或状态变化：他抬头，说话表演] + "
        "[环境物理动态：窗帘轻晃] + "
        "[音效与氛围描述：室内底噪] + [对话台词与语气：无] + [时长：5.0s]"
    )

    class _DubbingNormalization:
        spoken_dialogue = ("我就知道。",)

    with_dubbing = build_native_video_provider_prompt(
        "minimax-h3",
        dubbing,
        _DubbingNormalization(),
        generate_audio=False,
    )
    assert "说话表演" in with_dubbing
    assert "[明确的摄影机" not in with_dubbing


def test_h3_blocking_filter_and_facade_lint_paths_are_explicit():
    from novelvideo.freezone.video_request_contract import (
        blocking_h3_prompt_issues,
        lint_h3_provider_prompt,
    )
    from novelvideo.services.video_request_contract import (
        blocking_native_video_provider_prompt_issues,
        enforce_native_video_provider_prompt_contract,
        lint_native_video_provider_prompt,
    )

    issues = lint_h3_provider_prompt("")
    assert [issue.code for issue in blocking_h3_prompt_issues(issues)] == ["empty_prompt"]
    assert lint_native_video_provider_prompt("seedance", "plain prompt") == ()
    assert blocking_native_video_provider_prompt_issues("seedance", "plain prompt") == ()
    logs: list[str] = []
    enforce_native_video_provider_prompt_contract("seedance", "plain prompt", on_log=logs.append)
    assert logs == []


def test_enforcement_scans_once_and_preserves_blocking_error_and_logs(monkeypatch):
    import pytest
    from novelvideo.generators.video import direct_models
    from novelvideo.services import _video_request_contract as contract
    from novelvideo.services.video_request_contract import enforce_native_video_provider_prompt_contract

    counts = {"resolve": 0, "lint": 0}
    original_lint = contract.lint_h3_provider_prompt

    def resolve(_backend):
        counts["resolve"] += 1
        return None

    def lint(*args, **kwargs):
        counts["lint"] += 1
        return original_lint(*args, **kwargs)

    monkeypatch.setattr(direct_models, "resolve_direct_video_model", resolve)
    monkeypatch.setattr(contract, "lint_h3_provider_prompt", lint)
    logs = []
    with pytest.raises(ValueError, match="H3 提示词合同阻止派发：empty_prompt"):
        enforce_native_video_provider_prompt_contract("minimax-h3", "", on_log=logs.append)
    assert counts == {"resolve": 1, "lint": 1}
    assert logs == ["提示词框架检查 [empty_prompt] 最终提示词为空。"]
