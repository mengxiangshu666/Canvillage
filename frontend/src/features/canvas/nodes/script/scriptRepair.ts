// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ScriptContractIssue, ScriptContractRepairSummary } from '@/api/scriptContract';

/**
 * Rules that the row-rewrite contract cannot materially repair. They stay visible in
 * the contract report, but must not make the "一键优化" action look effective when
 * the model cannot change the measured field.
 */
const NON_MODEL_REPAIR_RULES = new Set([
  'script.character_card.verbatim.v1',
  'script.style.singleton.v1',
  'script.technical.singleton.v1',
  'script.motion.duration_match.v1',
  'script.reference.budget.v1',
  'script.shot_no.sequence.v1',
  'script.duration.total_budget.v1',
  'script.plan.sequence_membership.v1',
  'script.keyframe.duplicate_plan.v1',
]);

const CREATIVE_REVIEW_RULES = new Set([
  'script.viewability.framing_mix.v1', 'script.viewability.dialogue_ratio.v1',
  'script.viewability.static_standoff.v1', 'script.viewability.action_share.v1',
  'script.viewability.pacing.v1',
]);

export function optimizableScriptIssues(
  issues: readonly ScriptContractIssue[] | null | undefined,
): ScriptContractIssue[] {
  return (issues ?? []).filter(
    (issue) => !issue.fixed && !NON_MODEL_REPAIR_RULES.has(issue.rule_id)
      && !(issue.severity === 'advisory' && CREATIVE_REVIEW_RULES.has(issue.rule_id)),
  );
}

/**
 * 把一次一键优化的执行读数翻成给用户的那一句话。
 *
 * 后端逐镜串行重写，可能出现「改了 4 镜、1 镜失败」。此前界面只会说「已完成」，
 * 用户看不出这次点击有没有真的改动脚本，所以这里显式区分三种结果。
 */
export function describeScriptRepairOutcome(
  repair: ScriptContractRepairSummary | null | undefined,
): string {
  if (!repair || typeof repair.applied !== 'number') {
    return '优化结果缺少执行明细，请核对当前问题报告，暂不能确认全部处理完成';
  }
  const applied = repair.applied;
  const unit = repair.scope === 'sequence' ? '处' : '镜';
  const failed = repair.failed ?? 0;
  const deferred = repair.deferred_rule_ids?.length ?? 0;
  const remaining = repair.remaining_issue_count ?? 0;
  const rejected = repair.rejected ?? 0;
  const retained = rejected > 0
    ? `；${rejected} 次候选改写没有减少合同问题，已保留原版本`
    : '';
  const more = remaining > 0
    ? repair.stop_reason === 'review_required'
      ? `；剩余 ${remaining} 条需核对，已处理目标没有进一步改善，不建议反复点击`
      : repair.stop_reason === 'budget_exhausted'
        ? `；本次修复额度已用完，剩余 ${remaining} 条尚需处理`
        : `；还剩 ${remaining} 条问题，继续点击可再修`
    : deferred > 0
      ? `；另有 ${deferred} 条问题本轮未处理，继续点击可再修`
      : '';
  if (failed > 0) {
    return `已优化 ${applied} ${unit}，${failed} ${unit}改写失败；失败${unit === '镜' ? '镜头' : '范围'}见合同报告${retained}${more}`;
  }
  if (applied === 0) {
    return rejected > 0 || remaining > 0
      ? `本次未找到更好的改写${rejected > 0 ? '' : '：候选稿没有让合同问题变少'}${retained}${more}`
      : `没有镜头需要改写${more}`;
  }
  return `已优化 ${applied} ${unit}${retained}${more}`;
}
