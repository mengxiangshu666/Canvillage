// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from 'react';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';

import type { FreezoneStoryScriptRow, ScriptContractIssue, ScriptContractReport } from '@/api/scriptContract';
import { formatShotNumbers } from './scriptPreflight';
import { scriptContractRuleTitle } from './scriptContractCopy';
import { describeScriptRepairOutcome } from './scriptRepair';
import { scriptKeyframePlanIssues } from './scriptKeyframePlan';

/**
 * 合同报告横幅：把「服务端到底检查了什么、修了什么、还剩什么要人处理」摆在表上方。
 *
 * 角色卡逐字一致、第 7/8 段全篇唯一这两条一直只写在提示词里，从没被验证过；这份报告
 * 是它们第一次变成可读的结果。三件事必须分清，否则报告会变成噪声：
 *   · 服务端**已经修好**的（角色卡、风格段、技术段、时长）—— 只是告知，不需要动作；
 *   · 要**人处理**的阻塞项（段数不对、段序错乱、一镜多运镜）—— 这才是要红的那部分；
 *   · 提醒项（镜号不连续、参考图超配）—— 不阻断，但值得看一眼。
 */
export interface ScriptContractBannerProps {
  report: ScriptContractReport | null | undefined;
  rows?: readonly FreezoneStoryScriptRow[];
  /** 关闭这条横幅（重新生成会带来新的报告）。 */
  onDismiss?: () => void;
}

function ruleTitle(ruleId: string): string {
  return scriptContractRuleTitle(ruleId);
}

/** 把缺陷按「要人处理 / 已修好 / 提醒」分桶，横幅只报数量，明细展开看。 */
function bucket(issues: ScriptContractIssue[]) {
  return {
    blocking: issues.filter((issue) => !issue.fixed && issue.severity === 'blocking'),
    fixed: issues.filter((issue) => issue.fixed),
    advisory: issues.filter((issue) => !issue.fixed && issue.severity !== 'blocking'),
  };
}

export function ScriptContractBanner({ report, rows, onDismiss }: ScriptContractBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const [dismissedKeyframeIssues, setDismissedKeyframeIssues] = useState('');
  const keyframeIssues = rows ? scriptKeyframePlanIssues(rows) : [];
  const keyframeIssuesKey = JSON.stringify(keyframeIssues);
  const issues = rows ? [
    ...(report?.issues ?? []).filter(issue => issue.fixed || issue.rule_id !== 'script.keyframe.duplicate_plan.v1'),
    ...(keyframeIssuesKey === dismissedKeyframeIssues ? [] : keyframeIssues),
  ] : report?.issues ?? [];
  const repair = report?.repair;
  if (issues.length === 0 && !repair) return null;

  const { blocking, fixed, advisory } = bucket(issues);
  const tone = blocking.length > 0 || (repair?.failed ?? 0) > 0 ? 'warn' : 'quiet';
  const Icon = blocking.length > 0 ? AlertCircle : fixed.length > 0 ? CheckCircle2 : Info;

  const parts: string[] = [];
  if (blocking.length > 0) {
    const shots = blocking.map((issue) => issue.shot_no).filter(Boolean);
    parts.push(
      `${blocking.length} 处不符合合同${shots.length > 0 ? `（${formatShotNumbers([...new Set(shots)])}）` : ''}，需要你处理`,
    );
  }
  if (fixed.length > 0) parts.push(`${fixed.length} 处已自动改正`);
  if (advisory.length > 0) parts.push(`${advisory.length} 处提醒`);
  const summary = `合同检查：${[...parts, ...(repair ? [describeScriptRepairOutcome(repair)] : [])].join('，')}`;

  return (
    <div
      className={`flex items-start gap-2 border-b px-3 py-2 ${
        tone === 'warn'
          ? 'border-amber-400/25 bg-amber-400/10'
          : 'border-white/[0.08] bg-white/[0.03]'
      }`}
    >
      <Icon
        className={`mt-0.5 h-4 w-4 shrink-0 ${
          tone === 'warn' ? 'text-amber-300' : 'text-text-muted'
        }`}
      />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          className="block w-full text-left text-[12px] leading-5 text-text-dark/90 hover:text-text-dark"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          title="点开看是哪几镜、哪一条规则"
        >
          {summary}
        </button>
        {expanded && (
          <ul className="mt-1.5 flex flex-col gap-0.5 break-words">
            {[...blocking, ...fixed, ...advisory].map((issue, index) => (
              <li
                key={`${issue.rule_id}-${issue.row_index}-${index}`}
                className="text-[11px] leading-5 text-text-muted"
              >
                {issue.shot_no ? `第 ${issue.shot_no} 镜 · ` : ''}
                {ruleTitle(issue.rule_id)}：{issue.message}
                {issue.fixed ? '（已改正）' : ''}
              </li>
            ))}
            {repair?.target_results?.map((target, index) => (
              (target.diagnosis || target.reason) && <li key={`repair-${index}`} className="whitespace-pre-wrap text-[11px] leading-5 text-text-muted">
                {target.sequence_ids?.length ? `段落 ${target.sequence_ids.join('、')}` : `第 ${target.row_index + 1} 镜`}：
                {target.diagnosis}
                {target.reason === 'scope_changed' ? '修改超出目标范围，已保留原版本。' : target.reason === 'contract_regression' ? '候选稿增加了问题或丢失资产说明，已保留原版本。' : ''}
              </li>
            ))}
            {repair?.failures?.map((failure, index) => (
              <li key={`failure-${index}`} className="text-[11px] leading-5 text-text-muted">
                第 {failure.row_index + 1} 镜所在范围未完成改写：{failure.error}
              </li>
            ))}
          </ul>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          className="shrink-0 text-text-muted transition-colors hover:text-text-dark"
          onClick={() => { setDismissedKeyframeIssues(keyframeIssuesKey); onDismiss(); }}
          title="关掉这条横幅"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
