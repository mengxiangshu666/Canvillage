from novelvideo.freezone.clip_contract import (
    ClipContract,
    ClipReference,
    ClipSegment,
    attach_clip_contract_to_node_data,
    compile_clip_prompt,
    validate_clip_contract,
)


def test_compile_clip_prompt_timeline_and_roles():
    contract = ClipContract(
        subject="女主小雨",
        scene="宿舍走廊",
        lighting="暖黄窗光",
        camera="中景平稳跟拍",
        style="电影纪实风",
        duration_sec=15,
        segments=[
            ClipSegment(
                start_sec=0,
                end_sec=5,
                who="小雨",
                where="宿舍门口",
                action="脚步轻快走近并停顿深呼吸",
                camera="中景平稳跟拍",
            ),
            ClipSegment(
                start_sec=5,
                end_sec=10,
                who="小雨与舍友",
                where="宿舍内",
                action="推门进入，舍友抬头询问",
                camera="室内中景",
            ),
            ClipSegment(
                start_sec=10,
                end_sec=15,
                who="小雨",
                where="宿舍内",
                action="先落寞后大笑说骗你们的",
                camera="缓慢拉远全景",
            ),
        ],
        references=[
            ClipReference(label="图片1", role="identity", character_name="小雨"),
            ClipReference(label="图片2", role="scene", note="宿舍场景"),
            ClipReference(label="音频1", role="audio", note="室内环境声"),
        ],
        endpoint="定格在欢声笑语全景",
    )
    prompt = compile_clip_prompt(contract)
    assert "图片1 作为角色外观锚定（小雨）" in prompt
    assert "0–5 秒" in prompt
    assert "5–10 秒" in prompt
    assert "10–15 秒" in prompt
    assert "面部稳定不变形" in prompt
    assert "电影纪实风" in prompt
    warnings = validate_clip_contract(contract)
    assert warnings == []


def test_attach_clip_contract_writes_node_fields():
    contract = ClipContract(
        subject="红衣女主",
        scene="悬崖竹林",
        duration_sec=10,
        segments=[
            ClipSegment(start_sec=0, end_sec=5, action="侧面举壶饮酒", camera="缓慢推进"),
            ClipSegment(start_sec=5, end_sec=10, action="与黑衣人对峙拔剑", camera="平稳跟随"),
        ],
        references=[
            ClipReference(label="图片1", role="identity", character_name="女主"),
            ClipReference(label="图片2", role="identity", character_name="刺客"),
            ClipReference(label="图片3", role="scene"),
        ],
    )
    data = attach_clip_contract_to_node_data({}, contract)
    assert data["clipContract"]["schema_version"] == "clip_contract.v1"
    assert "compiledPrompt" in data
    assert data["prompt"] == data["compiledPrompt"]
    assert "女主" in data["compiledPrompt"] or "红衣女主" in data["compiledPrompt"]
