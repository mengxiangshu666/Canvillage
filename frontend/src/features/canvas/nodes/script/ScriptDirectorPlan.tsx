// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryDirectorPlan, FreezoneStoryScriptRow } from '@/api/scriptContract';
import { directorSequenceIssues } from './directorSequenceCoverage';
import { scriptCutReviews } from './scriptCutReview';
import { RefreshCw } from 'lucide-react';

const PLAN_FIELDS = [
  ['story_promise', '故事承诺'], ['protagonist_goal', '主体目标'], ['core_conflict', '核心关系与冲突'],
  ['ending_change', '结尾变化'], ['rhythm_curve', '节奏曲线'], ['sound_plan', '声音计划'],
] as const;
const LOOK_FIELDS = [
  ['visual_style', '视觉风格'], ['texture', '材质质感'], ['color_progression', '色彩发展'],
  ['lighting', '光线规则'], ['camera_language', '摄影语言'],
] as const;

export function ScriptDirectorPlan({ plan, rows, disabled, pending, onCommit, onRewriteSequence }: {
  plan?: FreezoneStoryDirectorPlan | null;
  rows: FreezoneStoryScriptRow[];
  disabled?: boolean;
  pending?: boolean;
  onCommit: (plan: FreezoneStoryDirectorPlan) => void;
  onRewriteSequence?: (sequenceId: string) => void;
}) {
  const issues = directorSequenceIssues(plan, rows);
  const field = (key: string, label: string, value: string | undefined, commit: (value: string) => void, maxLength: number) => (
    <label key={`${key}:${value ?? ''}`} className="block min-w-0 text-xs text-text-muted">
      <span>{label}</span>
      <textarea aria-label={label} defaultValue={value ?? ''} disabled={disabled} maxLength={maxLength} rows={2}
        onBlur={event => { if (event.target.value !== (value ?? '')) commit(event.target.value); }}
        className="mt-1 w-full resize-y rounded border border-white/10 bg-black/10 p-2 text-text-dark disabled:opacity-50" />
    </label>
  );
  return (
    <details className="nodrag nowheel min-w-0 border-b border-white/10 px-3 py-2 text-xs">
      <summary className="cursor-pointer text-text-dark">导演规划 · {plan?.sequences?.length ?? 0} 段{issues.length ? ` · ${issues.length} 项待核对` : ''}</summary>
      <div className="mt-2 max-h-80 space-y-3 overflow-auto">
        {pending && <p className="text-amber-700 dark:text-amber-200" role="status">导演规划已修改，已有镜头尚未同步</p>}
        <div className="grid gap-2 md:grid-cols-2">
          {PLAN_FIELDS.map(([key, label]) => field(key, label, plan?.[key], value => onCommit({ ...plan, [key]: value }), key === 'rhythm_curve' || key === 'sound_plan' ? 2000 : 1200))}
          {LOOK_FIELDS.map(([key, label]) => field(key, label, plan?.visual_bible?.[key], value => onCommit({ ...plan, visual_bible: { ...plan?.visual_bible, [key]: value } }), 1200))}
        </div>
        {!!plan?.assumptions?.length && <p className="break-words text-text-muted">创作假设：{plan.assumptions.join('；')}</p>}
        {issues.length > 0 && <ul className="space-y-1 text-amber-700 dark:text-amber-200" aria-label="序列待核对项">{issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}
        {plan?.sequences?.map((sequence, index) => <div key={`${sequence.sequence_id}:${index}`} className="space-y-2 border-t border-white/10 pt-2 text-text-muted">
          <div className="flex items-center justify-between gap-2"><p className="break-words text-text-dark">{sequence.title || `序列 ${index + 1}`} · 镜 {sequence.shot_nos?.join('、') || '未指定'}</p>
            {onRewriteSequence && sequence.sequence_id && <button type="button" className="inline-flex shrink-0 items-center gap-1 rounded border border-white/10 px-2 py-1 text-[11px] text-text-dark hover:bg-white/10 disabled:opacity-50" disabled={disabled} onClick={() => { const id = sequence.sequence_id; if (id) onRewriteSequence(id); }}><RefreshCw className="h-3 w-3" />联合返工</button>}
          </div>
          {(['dramatic_goal', 'resistance', 'escalation', 'turn', 'release', 'staging_plan', 'performance_plan'] as const).map((key, i) => field(
            `${sequence.sequence_id || index}:${key}`,
            `第 ${index + 1} 段 · ${['期待', '阻力', '发展', '转折', '余波', '空间调度', '表演推进'][i]}`,
            sequence[key],
            value => onCommit({
              ...plan,
              sequences: plan.sequences?.map((candidate, candidateIndex) =>
                candidateIndex === index ? { ...candidate, [key]: value } : candidate,
              ),
            }),
            1200,
          ))}
        </div>)}
        <details className="border-t border-white/10 pt-2 text-text-muted">
          <summary className="cursor-pointer text-text-dark">相邻镜头切点复核 · {Math.max(0, rows.length - 1)} 处</summary>
          {scriptCutReviews(rows).map((cut, index) => <div key={index} className="space-y-1 border-t border-white/10 py-2 break-words">
            <p className="text-text-dark">镜 {cut.from} → {cut.to}</p>
            <p>上一镜切点：{cut.endState || '未填写'}</p>
            <p>下一镜首帧：{cut.startState || '未填写'}</p>
            <p>切镜理由：{cut.reason || '未填写'}</p>
            <p>{cut.note}</p>
          </div>)}
        </details>
      </div>
    </details>
  );
}
