// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Check } from 'lucide-react';
import type { ScriptReadiness } from './scriptReadiness';

/**
 * LibTV 脚本 V2 的“确认镜头 → 准备资产 → 合成提示词”阶段条。
 * 只读 readiness，不写任何节点状态；用于在长表上方给出一眼可读的位置。
 */
export function ScriptWorkflowStrip({ readiness }: { readiness: ScriptReadiness }) {
  if (readiness.counts.shotCount === 0) return null;
  const steps = [
    {
      label: '确认镜头',
      done: !readiness.issues.some((item) => item.id === 'director-plan-pending')
        && readiness.counts.contractBlockingCount === 0,
    },
    {
      label: '准备资产',
      done: readiness.counts.missingAssetCount === 0 && readiness.counts.staleShotCount === 0,
    },
    {
      label: '提示词/分镜',
      done: readiness.counts.missingStoryboardCount === 0
        && readiness.counts.pendingStoryboardCount === 0
        && readiness.counts.contractBlockingCount === 0,
    },
  ];
  const currentIndex = steps.findIndex((step) => !step.done);
  return (
    <div className="flex min-h-10 items-center gap-3 border-b border-white/[0.06] bg-[#161618]/90 px-3 py-1.5">
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        {steps.map((step, index) => {
          const done = step.done;
          const current = !done && index === currentIndex;
          return (
            <div key={step.label} className="flex min-w-0 items-center gap-1.5">
              {index > 0 && (
                <span className={`h-px w-4 shrink-0 ${done || index <= currentIndex ? 'bg-[#14C9C9]/45' : 'bg-white/10'}`} />
              )}
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] ${
                  done
                    ? 'border-[#14C9C9]/50 bg-[#14C9C9]/15 text-[#7ee7e7]'
                    : current
                      ? 'border-amber-300/55 bg-amber-400/10 text-amber-200'
                      : 'border-white/15 text-text-muted/70'
                }`}
              >
                {done ? <Check className="h-3 w-3" /> : index + 1}
              </span>
              <span className={`whitespace-nowrap text-[10.5px] ${current ? 'text-amber-100/90' : done ? 'text-text-dark/85' : 'text-text-muted/70'}`}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>
      <span className="max-w-[46%] truncate text-right text-[10.5px] text-text-muted/80" title={readiness.summary}>
        {readiness.title}
      </span>
    </div>
  );
}
