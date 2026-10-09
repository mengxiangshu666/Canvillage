"""Validate observations against the exact submitted rows before model use."""

import json
from typing import Any

from pydantic import TypeAdapter

from novelvideo.freezone.script_contract import script_row_fingerprint
from novelvideo.freezone.sequence_rewrite import validate_sequence_rewrite_request
from novelvideo.ports.script_video_feedback import ScriptVideoFeedback


def prepare_script_video_feedback(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    notes = TypeAdapter(list[ScriptVideoFeedback]).validate_python(payload.get("video_feedback") or [])
    prompt = str(payload.get("prompt") or "")
    if not notes:
        return [], prompt
    if len(notes) > 50 or payload.get("repair_mode"):
        raise ValueError("审看意见最多50条，仅用于单镜或整段返工")
    rows = payload.get("current_rows") or []
    shot_id = str(payload.get("rewrite_shot_id") or "")
    index = int(payload.get("rewrite_index", -1))
    sequence_id = str(payload.get("rewrite_sequence_id") or "")
    if sequence_id:
        indices = validate_sequence_rewrite_request(
            rows=rows, director_plan=payload.get("director_plan"), sequence_id=sequence_id,
            instruction=prompt, shot_id=shot_id, rewrite_index=index,
        )
    else:
        indices = [i for i, row in enumerate(rows) if row.get("shot_id") == shot_id] if shot_id else [index]
    if not indices or any(i < 0 or i >= len(rows) for i in indices) or len(indices) != len(set(indices)):
        raise ValueError("审看意见缺少有效的目标镜头")
    seen = set()
    for note in notes:
        matches = [i for i in indices if rows[i].get("shot_id") == note.shot_id]
        if len(matches) != 1 or script_row_fingerprint(rows[matches[0]], matches[0]) != note.row_fingerprint:
            raise ValueError("审看意见不属于目标镜头或脚本已变更，请重新审看")
        if note.issue_id in seen or not note.description.strip():
            raise ValueError("审看意见编号重复或问题描述为空")
        seen.add(note.issue_id)
    normalized = [note.model_dump() for note in notes]
    requirements = [
        "逐条处理审看记录中的问题、观看影响和用户修复方向，按镜头和时间点定位，不得遗漏或合并成一句泛泛的提升质量。",
        "观察时间是已生成视频的时间点，不是必须在新脚本该秒安排动作的指令。保留故事目的、角色身份及共同美术基准。",
        "只修改有证据关联的目标镜头字段：画面设计进入分镜提示，动作、接触、遮挡与时间变化进入运动提示，声音问题进入声音设计。",
        "需要跨镜交接时核对相邻镜头作为上下文，不修改范围外镜头；单镜无法解决的跨镜问题说明限制，不擅自编造已解决。",
        "用户观察不等于已证实根因，未看见源图片或模型回执时不要断言；参考需求只能写成待检查或待补齐，不能宣称资产已锁定。",
        "不声称已经修复视频像素，不启动媒体生成。",
    ]
    if any(note.category == "artifact" for note in notes):
        requirements.append(
            "噪点与伪影返工：分别核对源图片中的随机噪点、压缩块、摩尔纹，以及视频中逐帧随机跳点、纹理爬动和轮廓闪烁。"
            "源图有缺陷时，在reference_requirements明确先检查/重做对应角色、场景、道具或分镜图，不能只靠运动提示补救。"
            "源图是否有问题尚无证据时标记待检查；视频提示明确纹理附着物体、静止区域稳定、曝光变化有现场原因。"
            "将要求写进对应分镜/运动提示，而不是只在说明中写去噪或高清。保留毛发、织物、磨损、笔触及自然运动模糊；"
            "不以磨平材质、冻结动作或删除剧情所需颗粒效果代替修复。"
        )
    if any(note.category in {"identity", "prop_state", "continuity"} for note in notes):
        requirements.append(
            "设计与状态返工：先对照既定角色、服饰和道具设计，以及相邻镜头的起止状态；"
            "分别检查五官与体型、服装裁片与配饰、道具数量与连接结构、持有者、接触位置和屏幕方向。"
            "区分剧本明确安排的换装、变形、转交或省略，与没有依据的设计漂移；不把设计变化伪装成运镜。"
            "未取得资产或首帧证据时，在reference_requirements写明待比对的资产和分镜，不能断言根因。"
            "若源图已经偏离设计，先要求修正对应源图；若源图正确而视频中发生漂移，"
            "在运动提示写具体的结构保持、持有与接触变化，并在起止状态写清道具状态。"
            "不堆叠静态外观重述，不凭空增加转场或变形动作；尊重本镜输入方式和既定故事意图。"
        )
    return normalized, prompt + "\n审看记录（用户观察，不是验收通过）：\n" + json.dumps(normalized, ensure_ascii=False) + "\n" + "\n".join(requirements)
