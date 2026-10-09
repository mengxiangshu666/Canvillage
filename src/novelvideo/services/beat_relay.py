"""真尾帧接力的顺序派发守卫。

接力帧来自上一镜的成片，所以连续接缝的下一镜必须在上一镜出片之后才能开工；
这里只负责给出「该等一等」的判断与话术，任务状态与文件事实由调用方提供。
"""

from __future__ import annotations

from novelvideo.production.shot_contract import SEAM_CONTINUOUS, shot_handoff_seam


def relay_pending_reason(
    *,
    previous_beat: object,
    previous_beat_num: int,
    previous_video_exists: bool,
    previous_task_active: bool,
) -> str:
    """上一镜还在跑且成片未出时，返回阻止本镜开工的原因，否则返回空串。

    只拦连续接缝：硬切镜本来就要用自己的规划首帧，没必要等上一镜。
    上一镜既没有成片、也没有在跑的任务时同样放行——那种情况接力帧不可能出现，
    本镜会自然回退到规划首帧，而不是被一个永远等不到的依赖卡住。
    """

    if shot_handoff_seam(previous_beat) != SEAM_CONTINUOUS:
        return ""
    if previous_video_exists or not previous_task_active:
        return ""
    return (
        f"上一镜（Beat {previous_beat_num}）视频仍在生成中；"
        "真尾帧接力要等它出片后才能拿到真实尾帧，请稍后重试"
    )


__all__ = ["relay_pending_reason"]
