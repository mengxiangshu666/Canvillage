"""Paid-media budget ceilings shared by the control plane and the canvas agent."""

from __future__ import annotations

#: 单个画布回合最多能声明的付费媒体启动次数（分镜图 + 逐镜视频 + 其他付费任务）。
#: 这个上限是**一条契约**，turn grant、grant consume、WorkflowRun 创建和前端
#: 的自动授权文案都读同一个值；任何一处自己写死小数字，都会让一部多镜短片在
#: 中途被静默截断（12 镜短片就需要 24 次真实启动）。
MAX_PAID_MEDIA_STARTS_PER_TURN = 64

__all__ = ["MAX_PAID_MEDIA_STARTS_PER_TURN"]
