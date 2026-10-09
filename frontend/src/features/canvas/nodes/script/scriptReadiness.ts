// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { ScriptContractIssue, ScriptContractMetrics, ScriptContractReport } from '@/api/scriptContract';
import { formatShotNumbers, type ScriptAssetPreflight, type ScriptPreflight } from './scriptPreflight';
import { scriptContractRuleTitle } from './scriptContractCopy';

const CANVAS_NON_BLOCKING_CONTRACT_RULES = new Set([
  'script.shot_prompt.segments.v1',
  'script.shot_prompt.order.v1',
  'script.character_card.verbatim.v1',
  'script.style.singleton.v1',
  'script.technical.singleton.v1',
  'script.motion.segments.v1',
  'script.camera.single.v1',
]);

/**
 * 脚本节点的「开拍雷达」。
 *
 * 它不新建任务状态，也不复制合同规则；只把四份已经存在的真相合成一句当前结论：
 * 合同报告、逐镜预检、资产预检、当前脚本行。面板回答的是「现在能不能继续花钱」，
 * 而不是再生成一份建议稿。
 *
 * 判定边界刻意分成四层：
 * - `blocker`：现在做下去会做错或做不出来，例如合同不合规、缺图片提示词、分镜图已过期；
 * - `degradation`：能继续，但人物一致性或参考图会降级；
 * - `work`：不是错误，只是还没做，例如分镜图待出；
 * - `notice`：合同提醒，不拦停，但值得在开拍前做创作判断。
 */

export type ScriptReadinessStatus = 'empty' | 'blocked' | 'degraded' | 'ready';
export type ScriptReadinessIssueKind = 'blocker' | 'degradation' | 'work' | 'notice';
export type ScriptReadinessAction = 'generate-assets' | 'generate-storyboard';

export interface ScriptReadinessIssue {
  /** 稳定 ID，便于测试、埋点与后续直接定位规则；不要用展示文案当 ID。 */
  id: string;
  kind: ScriptReadinessIssueKind;
  title: string;
  detail: string;
  count: number;
  shotNumbers: string[];
  action?: ScriptReadinessAction;
}

export interface ScriptReadiness {
  status: ScriptReadinessStatus;
  title: string;
  summary: string;
  /** 没有硬阻塞：可以继续推进分镜 / 出图；不代表最终画面零风险。 */
  canProceed: boolean;
  /** 合同、资产、分镜图全部对齐，且没有提醒和待办。 */
  canShoot: boolean;
  blockers: ScriptReadinessIssue[];
  degradations: ScriptReadinessIssue[];
  work: ScriptReadinessIssue[];
  notices: ScriptReadinessIssue[];
  issues: ScriptReadinessIssue[];
  counts: {
    shotCount: number;
    syncedShotCount: number;
    blockedShotCount: number;
    staleShotCount: number;
    missingStoryboardCount: number;
    pendingStoryboardCount: number;
    missingAssetCount: number;
    degradedShotCount: number;
    referenceOverflowShotCount: number;
    contractBlockingCount: number;
    contractAdvisoryCount: number;
  };
  metrics?: ScriptContractMetrics;
}

function issue(params: {
  id: string;
  kind: ScriptReadinessIssueKind;
  title: string;
  detail: string;
  shotNumbers?: readonly string[];
  count?: number;
  action?: ScriptReadinessAction;
}): ScriptReadinessIssue {
  return {
    id: params.id,
    kind: params.kind,
    title: params.title,
    detail: params.detail,
    count: params.count ?? params.shotNumbers?.length ?? 1,
    shotNumbers: [...(params.shotNumbers ?? [])],
    action: params.action,
  };
}

function uniqueShotNumbers(issues: readonly ScriptContractIssue[]): string[] {
  return [...new Set(issues.map((entry) => entry.shot_no.trim()).filter(Boolean))];
}

function contractIssuesByRule(
  issues: readonly ScriptContractIssue[],
): Array<{ ruleId: string; issues: ScriptContractIssue[] }> {
  const grouped = new Map<string, ScriptContractIssue[]>();
  for (const entry of issues) {
    const list = grouped.get(entry.rule_id) ?? [];
    list.push(entry);
    grouped.set(entry.rule_id, list);
  }
  return [...grouped.entries()].map(([ruleId, groupedIssues]) => ({
    ruleId,
    issues: groupedIssues,
  }));
}

function contractIssueDetail(issues: readonly ScriptContractIssue[]): string {
  const first = issues[0]?.message?.trim() ?? '';
  return issues.length > 1 ? `${issues.length} 处同规则问题；${first}` : first;
}

/** 可看性指标压成一行；数据不足时返回空串。 */
export function describeScriptReadinessMetrics(
  metrics: ScriptContractMetrics | undefined,
): string {
  if (!metrics) return '';
  const percent = (value: number | undefined): string =>
    typeof value === 'number' ? `${Math.round(value * 100)}%` : '';
  const parts = [
    typeof metrics.total_seconds === 'number' ? `全片 ${metrics.total_seconds.toFixed(0)}s` : '',
    percent(metrics.dialogue_seconds_share)
      ? `台词时长 ${percent(metrics.dialogue_seconds_share)}`
      : '',
    percent(metrics.portrait_share) ? `贴身景别 ${percent(metrics.portrait_share)}` : '',
    percent(metrics.action_shot_share) ? `动作镜 ${percent(metrics.action_shot_share)}` : '',
    typeof metrics.longest_standoff_seconds === 'number' && metrics.longest_standoff_seconds > 0
      ? `最长静戏 ${metrics.longest_standoff_seconds.toFixed(0)}s`
      : '',
  ];
  return parts.filter(Boolean).join(' · ');
}

/**
 * 纯函数：从既有读数算出当前开拍结论。
 *
 * 这里不读 store、不发请求、不写任何字段。组件只负责展示，因此所有「为什么判 blocked /
 * degraded / ready」都能用单测钉住。
 */
export function computeScriptReadiness(params: {
  rows: readonly FreezoneStoryScriptRow[];
  preflight: ScriptPreflight;
  assetPreflight: ScriptAssetPreflight;
  contractReport: ScriptContractReport | null | undefined;
  directorPlanNeedsSync?: boolean;
}): ScriptReadiness {
  const { rows, preflight, assetPreflight, contractReport } = params;
  const blockers: ScriptReadinessIssue[] = [];
  const degradations: ScriptReadinessIssue[] = [];
  const work: ScriptReadinessIssue[] = [];
  const notices: ScriptReadinessIssue[] = [];
  if (params.directorPlanNeedsSync) blockers.push(issue({
    id: 'director-plan-pending', kind: 'blocker', title: '导演规划尚未同步',
    detail: DIRECTOR_PLAN_PENDING_REASON,
  }));

  const allContractIssues = contractReport?.issues ?? [];
  const contractBlocking = allContractIssues.filter(
    (entry) =>
      !entry.fixed &&
      entry.severity === 'blocking' &&
      !CANVAS_NON_BLOCKING_CONTRACT_RULES.has(entry.rule_id),
  );
  const contractAdvisory = allContractIssues.filter(
    (entry) =>
      !entry.fixed &&
      (entry.severity !== 'blocking' || CANVAS_NON_BLOCKING_CONTRACT_RULES.has(entry.rule_id)),
  );

  if (rows.length > 0 && !contractReport) {
    degradations.push(
      issue({
        id: 'contract-report-missing',
        kind: 'degradation',
        title: '合同报告需要刷新',
        detail:
          '脚本行改过或来自旧数据，当前没有可验证的合同读数。保存画布后会自动复核；也可以重新生成脚本。',
      }),
    );
  }

  for (const group of contractIssuesByRule(contractBlocking)) {
    blockers.push(
      issue({
        id: `contract:${group.ruleId}`,
        kind: 'blocker',
        title: scriptContractRuleTitle(group.ruleId),
        detail: contractIssueDetail(group.issues),
        shotNumbers: uniqueShotNumbers(group.issues),
        count: group.issues.length,
      }),
    );
  }

  if (preflight.blockedShotNumbers.length > 0) {
    blockers.push(
      issue({
        id: 'shot-prompt-missing',
        kind: 'blocker',
        title: '有镜头没有可提交的图片提示词',
        detail: `${formatShotNumbers(preflight.blockedShotNumbers)} 的分镜提示词与画面描述都为空，生成分镜图时不会出图。`,
        shotNumbers: preflight.blockedShotNumbers,
      }),
    );
  }

  if (preflight.staleShotNumbers.length > 0) {
    blockers.push(
      issue({
        id: 'storyboard-stale',
        kind: 'blocker',
        title: '分镜图已与当前脚本脱节',
        detail: `${formatShotNumbers(preflight.staleShotNumbers)} 的分镜图来自旧脚本；先重出这些图，否则后续视频拍的是旧内容。`,
        shotNumbers: preflight.staleShotNumbers,
        action: 'generate-storyboard',
      }),
    );
  }

  if (assetPreflight.referenceMissing.length > 0) {
    degradations.push(
      issue({
        id: 'asset-reference-missing',
        kind: 'degradation',
        title: '资产参考图不齐',
        detail: `${assetPreflight.missingSummary || `${assetPreflight.referenceMissing.length} 项资产`}没有可用于分镜参考的资产图；能继续，但人物、场景或道具的一致性会降级。`,
        count: assetPreflight.referenceMissing.length,
        action: 'generate-assets',
      }),
    );
  }

  if (preflight.degradedShotNumbers.length > 0) {
    degradations.push(
      issue({
        id: 'character-image-missing',
        kind: 'degradation',
        title: '角色镜头缺少角色图',
        detail: `${formatShotNumbers(preflight.degradedShotNumbers)} 写了角色名，但当前没有对应角色图；出图能继续，角色长相容易漂移。`,
        shotNumbers: preflight.degradedShotNumbers,
        action: 'generate-assets',
      }),
    );
  }

  if (preflight.overflowShotNumbers.length > 0) {
    degradations.push(
      issue({
        id: 'reference-overflow',
        kind: 'degradation',
        title: '参考图超限，多出来的会被静默丢掉',
        detail: `${formatShotNumbers(preflight.overflowShotNumbers)} 的参考图超过后端上限，提示词里仍可能写着被丢掉的图片编号。`,
        shotNumbers: preflight.overflowShotNumbers,
      }),
    );
  }

  if (preflight.tokenMissingShotNumbers.length > 0) {
    degradations.push(
      issue({
        id: 'reference-token-missing',
        kind: 'degradation',
        title: '提示词引用了不存在的图片编号',
        detail: `${formatShotNumbers(preflight.tokenMissingShotNumbers)} 引用了当前参考图数组里不存在的图片；模型会收到悬空锚点。`,
        shotNumbers: preflight.tokenMissingShotNumbers,
      }),
    );
  }

  const missingStoryboard = preflight.entries
    .filter(
      (entry) =>
        entry.state === 'missing' && !preflight.blockedShotNumbers.includes(entry.shotNumber),
    )
    .map((entry) => entry.shotNumber);
  if (missingStoryboard.length > 0) {
    work.push(
      issue({
        id: 'storyboard-missing',
        kind: 'work',
        title: '分镜图还没有开始',
        detail: `${formatShotNumbers(missingStoryboard)} 还没有派生分镜图；合同与提示词允许后，先从这一步开始出图。`,
        shotNumbers: missingStoryboard,
        action: 'generate-storyboard',
      }),
    );
  }

  if (preflight.pendingShotNumbers.length > 0) {
    work.push(
      issue({
        id: 'storyboard-pending',
        kind: 'work',
        title: '分镜图还没出齐',
        detail: `${formatShotNumbers(preflight.pendingShotNumbers)} 还没出图或上次失败；补齐后再进入逐镜视频。`,
        shotNumbers: preflight.pendingShotNumbers,
        action: 'generate-storyboard',
      }),
    );
  }

  for (const group of contractIssuesByRule(contractAdvisory)) {
    notices.push(
      issue({
        id: `advisory:${group.ruleId}`,
        kind: 'notice',
        title: scriptContractRuleTitle(group.ruleId),
        detail: contractIssueDetail(group.issues),
        shotNumbers: uniqueShotNumbers(group.issues),
        count: group.issues.length,
      }),
    );
  }

  const issues = [...blockers, ...degradations, ...work, ...notices];
  const status: ScriptReadinessStatus =
    rows.length === 0
      ? 'empty'
      : blockers.length > 0
        ? 'blocked'
        : degradations.length > 0 || notices.length > 0
          ? 'degraded'
          : 'ready';
  const canProceed = status !== 'empty' && blockers.length === 0;
  const canShoot = status === 'ready' && work.length === 0;
  const title =
    status === 'empty'
      ? '还没有分镜表'
      : status === 'blocked'
        ? '暂不能开拍'
        : status === 'degraded'
          ? '可以继续，先看降级项'
          : canShoot
            ? '可开拍'
            : '可以开始出图';
  const firstIssue = issues[0];
  const summary =
    status === 'empty'
      ? '先写出一张带镜号、画面与提示词的分镜表。'
      : status === 'blocked'
        ? `${blockers.length} 项硬阻塞${firstIssue ? `：${firstIssue.title}` : ''}`
        : status === 'degraded'
          ? `${degradations.length + notices.length} 项质量提醒；没有硬阻塞，可以带着明确取舍继续`
          : canShoot
            ? '合同、资产参考与分镜图都已对齐，可以进入开拍。'
            : `${work.length} 类尚未完成的出图工作；其余开拍条件已齐。`;

  return {
    status,
    title,
    summary,
    canProceed,
    canShoot,
    blockers,
    degradations,
    work,
    notices,
    issues,
    counts: {
      shotCount: rows.length,
      syncedShotCount: preflight.counts.synced,
      blockedShotCount: preflight.blockedShotNumbers.length,
      staleShotCount: preflight.staleShotNumbers.length,
      missingStoryboardCount: preflight.counts.missing,
      pendingStoryboardCount: preflight.counts.pending,
      missingAssetCount: assetPreflight.referenceMissing.length,
      degradedShotCount: preflight.degradedShotNumbers.length,
      referenceOverflowShotCount: preflight.overflowShotNumbers.length,
      contractBlockingCount: contractBlocking.length,
      contractAdvisoryCount: contractAdvisory.length,
    },
    metrics: contractReport?.metrics,
  };
}
import { DIRECTOR_PLAN_PENDING_REASON } from './directorSequenceCoverage';
