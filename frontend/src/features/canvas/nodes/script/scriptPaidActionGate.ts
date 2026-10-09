// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ScriptReadiness, ScriptReadinessIssue } from './scriptReadiness';

/**
 * 真正会花钱的两个脚本动作。
 *
 * 它们共用雷达事实，但不能共用同一个 allowed：分镜过期对“重出分镜图”是待修复状态，
 * 对“逐镜出视频”却是硬阻塞。这里把动作差异收在一个纯函数里，按钮、执行入口和测试
 * 都读同一份结论。
 */
export type ScriptPaidAction = 'storyboard-images' | 'shot-videos';

export interface ScriptPaidActionGate {
  action: ScriptPaidAction;
  allowed: boolean;
  reason: string | null;
  blockers: ScriptReadinessIssue[];
  warnings: ScriptReadinessIssue[];
}

const STORYBOARD_IMAGE_REPAIRABLE_BLOCKER = 'storyboard-stale';

const ACTION_LABEL: Record<ScriptPaidAction, string> = {
  'storyboard-images': '出分镜图',
  'shot-videos': '出逐镜视频',
};

function blocksAction(action: ScriptPaidAction, issue: ScriptReadinessIssue): boolean {
  if (issue.kind !== 'blocker') return false;
  return !(action === 'storyboard-images' && issue.id === STORYBOARD_IMAGE_REPAIRABLE_BLOCKER);
}

function describeGateReason(
  action: ScriptPaidAction,
  readiness: ScriptReadiness,
  blockers: readonly ScriptReadinessIssue[],
): string | null {
  if (readiness.status === 'empty') {
    return `还没有分镜表，暂时不能${ACTION_LABEL[action]}。`;
  }
  const first = blockers[0];
  if (!first) return null;
  return `先处理「${first.title}」，再${ACTION_LABEL[action]}。`;
}

/**
 * 只派生，不写回。合同报告缺失是「需要刷新」提醒，不是硬阻塞；已有的
 * 硬阻塞与动作专属的过期状态仍按原规则拦截。
 */
export function computeScriptPaidActionGate(
  readiness: ScriptReadiness,
  action: ScriptPaidAction,
  textVideoShotNumbers: readonly string[] = [],
): ScriptPaidActionGate {
  const blockers = readiness.issues.flatMap((issue) => {
    if (!blocksAction(action, issue)) return [];
    if (action !== 'shot-videos' || issue.id !== 'storyboard-stale' || !issue.shotNumbers.length) return [issue];
    const shotNumbers = issue.shotNumbers.filter(number => !textVideoShotNumbers.includes(number));
    return shotNumbers.length ? [{ ...issue, shotNumbers, count: shotNumbers.length }] : [];
  });
  const warnings = [...readiness.degradations, ...readiness.notices];
  const allowed = readiness.status !== 'empty' && blockers.length === 0;
  return {
    action,
    allowed,
    reason: describeGateReason(action, readiness, blockers),
    blockers,
    warnings,
  };
}
