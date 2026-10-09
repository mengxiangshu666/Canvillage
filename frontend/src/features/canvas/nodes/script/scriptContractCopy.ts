// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

const RULE_TITLES: Record<string, string> = {
  'script.shot_prompt.segments.v1': '分镜提示词段数不对',
  'script.shot_prompt.order.v1': '分镜提示词段序错乱',
  'script.character_card.verbatim.v1': '角色卡被改写',
  'script.style.singleton.v1': '视觉风格段逐镜改变',
  'script.assets.definition_consistency.v1': '同名资产的基准说明不同',
  'script.technical.singleton.v1': '技术参数段逐镜改变',
  'script.motion.segments.v1': '视频运动提示词段数不对',
  'script.camera.single.v1': '一个镜头写了多个主运镜',
  'script.motion.duration_match.v1': '运动稿时长与时长列不一致',
  'script.reference.budget.v1': '参考图数量或编号有问题',
  'script.shot_no.sequence.v1': '镜号不连续',
  'script.viewability.dialogue_ratio.v1': '台词太密：像配了插图的广播剧',
  'script.viewability.framing_mix.v1': '景别单一：整片都是一种距离',
  'script.continuity.adjacent_framing.v1': '相邻镜头观看重点需复核',
  'script.continuity.missing_states.v1': '连续动作缺少交接状态',
  'script.continuity.character_state.v1': '服装装备没有接上',
  'script.duration.total_budget.v1': '整片时长与目标有差异（仅提醒）',
  'script.plan.sequence_membership.v1': '镜头段落归属不一致',
  'script.keyframe.duplicate_plan.v1': '重复补图计划已拒绝',
  'script.viewability.static_standoff.v1': '静态对峙过长：画面没有事件',
  'script.viewability.action_share.v1': '缺少动作镜：打戏只剩过场',
  'script.viewability.pacing.v1': '节奏没有长短对比',
};

/** 合同规则 ID 翻成人话；未知规则原样返回，便于诊断新规则。 */
export function scriptContractRuleTitle(ruleId: string): string {
  return RULE_TITLES[ruleId] ?? ruleId;
}
