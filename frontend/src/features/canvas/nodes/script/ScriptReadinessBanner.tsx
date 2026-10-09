// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from 'react';
import {
  AlertOctagon,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  ImagePlus,
  Loader2,
  TriangleAlert,
  WandSparkles,
} from 'lucide-react';

import {
  describeScriptReadinessMetrics,
  type ScriptReadiness,
  type ScriptReadinessIssue,
} from './scriptReadiness';

export interface ScriptReadinessBannerProps {
  readiness: ScriptReadiness;
  onGenerateAssets?: () => void;
  onGenerateStoryboard?: () => void;
  onOptimize?: () => void;
  optimizeBusy?: boolean;
  optimizeDisabled?: boolean;
  optimizeError?: string | null;
}

const KIND_LABELS: Record<ScriptReadinessIssue['kind'], string> = {
  blocker: '硬阻塞',
  degradation: '降级',
  work: '待出图',
  notice: '提醒',
};

function issueCounts(readiness: ScriptReadiness): string[] {
  return [
    readiness.blockers.length > 0 ? `${readiness.blockers.length} 硬阻塞` : '',
    readiness.degradations.length > 0 ? `${readiness.degradations.length} 降级` : '',
    readiness.work.length > 0 ? `${readiness.work.length} 类待出图` : '',
    readiness.notices.length > 0 ? `${readiness.notices.length} 提醒` : '',
  ].filter(Boolean);
}

export function ScriptReadinessBanner({
  readiness,
  onGenerateAssets,
  onGenerateStoryboard,
  onOptimize,
  optimizeBusy = false,
  optimizeDisabled = false,
  optimizeError = null,
}: ScriptReadinessBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const tone =
    readiness.status === 'blocked'
      ? {
          wrapper: 'border-red-400/25 bg-red-500/10',
          icon: 'text-red-300',
          title: 'text-red-100',
          body: 'text-red-100/75',
        }
      : readiness.status === 'degraded'
        ? {
            wrapper: 'border-amber-300/25 bg-amber-400/10',
            icon: 'text-amber-200',
            title: 'text-amber-100',
            body: 'text-amber-100/75',
          }
        : readiness.canShoot
          ? {
              wrapper: 'border-emerald-400/20 bg-emerald-400/[0.07]',
              icon: 'text-emerald-300',
              title: 'text-emerald-100',
              body: 'text-emerald-100/70',
            }
          : {
              wrapper: 'border-sky-400/20 bg-sky-400/[0.07]',
              icon: 'text-sky-300',
              title: 'text-sky-100',
              body: 'text-sky-100/70',
            };
  const Icon =
    readiness.status === 'blocked'
      ? AlertOctagon
      : readiness.status === 'degraded'
        ? TriangleAlert
        : readiness.canShoot
          ? CheckCircle2
          : CircleDashed;
  const chips = issueCounts(readiness);
  const metrics = describeScriptReadinessMetrics(readiness.metrics);
  const actionKinds = new Set(readiness.issues.map((entry) => entry.action).filter(Boolean));
  const contractIssueCount = readiness.issues.filter(
    (entry) => entry.id.startsWith('contract:') || entry.id.startsWith('advisory:'),
  ).length;

  return (
    <div className={`border-b px-3 py-2 ${tone.wrapper}`}>
      <div className="flex items-start gap-2">
        <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${tone.icon}`} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className={`text-[12px] font-medium leading-5 ${tone.title}`}>
              {readiness.title}
            </span>
            {chips.map((chip) => (
              <span
                key={chip}
                className="text-[10px] leading-4 text-text-muted/80"
              >
                {chip}
              </span>
            ))}
          </div>
          <button
            type="button"
            className={`block w-full text-left text-[11px] leading-5 ${tone.body} hover:text-text-dark`}
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            title="点开看每一条判定"
          >
            {readiness.summary}
          </button>
          {metrics && (
            <div className="mt-0.5 truncate text-[10px] leading-4 text-text-muted/70" title={metrics}>
              {metrics}
            </div>
          )}
          {optimizeError && (
            <div
              className="mt-0.5 truncate text-[10px] leading-4 text-red-200/90"
              title={optimizeError}
            >
              一键优化失败：{optimizeError}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {contractIssueCount > 0 && onOptimize && (
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-amber-300/30 bg-amber-300/10 px-2 text-[10px] text-amber-100 transition-colors hover:border-amber-200/50 hover:bg-amber-300/15 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={optimizeBusy || optimizeDisabled}
              onClick={(event) => {
                event.stopPropagation();
                onOptimize();
              }}
              title="按合同报告优化问题镜头，不改已通过的内容"
            >
              {optimizeBusy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <WandSparkles className="h-3 w-3" />
              )}
              {optimizeBusy ? '优化中' : '一键优化'}
            </button>
          )}
          {actionKinds.has('generate-assets') && onGenerateAssets && (
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-white/[0.12] px-2 text-[10px] text-text-dark transition-colors hover:border-white/25 hover:bg-white/[0.06]"
              onClick={(event) => {
                event.stopPropagation();
                onGenerateAssets();
              }}
            >
              <ImagePlus className="h-3 w-3" />
              补资产图
            </button>
          )}
          {actionKinds.has('generate-storyboard') && onGenerateStoryboard && (
            <button
              type="button"
              className="inline-flex h-6 items-center gap-1 rounded border border-white/[0.12] px-2 text-[10px] text-text-dark transition-colors hover:border-white/25 hover:bg-white/[0.06]"
              onClick={(event) => {
                event.stopPropagation();
                onGenerateStoryboard();
              }}
            >
              <CircleDashed className="h-3 w-3" />
              出分镜图
            </button>
          )}
          <button
            type="button"
            className="text-text-muted transition-colors hover:text-text-dark"
            onClick={() => setExpanded((value) => !value)}
            title={expanded ? '收起判定明细' : '展开判定明细'}
          >
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
            />
          </button>
        </div>
      </div>

      {expanded && (
        <ul className="mt-1.5 flex flex-col gap-1 pl-6">
          {readiness.issues.map((entry, index) => (
            <li
              key={`${entry.id}-${index}`}
              className="text-[11px] leading-5 text-text-muted"
            >
              <span
                className={`mr-1.5 text-[10px] ${
                  entry.kind === 'blocker'
                    ? 'text-red-300'
                    : entry.kind === 'degradation'
                      ? 'text-amber-200'
                      : entry.kind === 'work'
                        ? 'text-sky-300'
                        : 'text-text-muted/75'
                }`}
              >
                {KIND_LABELS[entry.kind]}
              </span>
              <span className="text-text-dark/90">{entry.title}</span>
              {entry.detail ? `：${entry.detail}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
