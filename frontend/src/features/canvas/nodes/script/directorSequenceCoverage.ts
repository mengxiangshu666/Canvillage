// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryDirectorPlan, FreezoneStoryScriptRow } from '@/api/scriptContract';

export function directorPlanGenerationInstruction(plan: FreezoneStoryDirectorPlan | null | undefined): string {
  return plan ? `用户已调整的导演规划：以此为创作方向更新镜头表；若镜头有变，重新核对序列镜号，不得忽略用户调整。\n${JSON.stringify(plan)}` : '';
}

/** Check references, not artistic choices: sequences may deliberately overlap. */
export function directorSequenceIssues(plan: FreezoneStoryDirectorPlan | null | undefined, rows: readonly FreezoneStoryScriptRow[]): string[] {
  if (!plan?.sequences?.length) return ['尚未规划序列'];
  const shots = rows.map(row => Number(row.shot_no));
  const issues: string[] = [];
  if (shots.some(shot => !Number.isInteger(shot) || shot <= 0)) issues.push('镜号包含非正整数，无法核对序列');
  if (new Set(shots).size !== shots.length) issues.push('镜号重复，无法明确定位序列');
  const available = new Set(shots.filter(shot => Number.isInteger(shot) && shot > 0));
  const covered = new Set<number>();
  const sequenceIds = new Set<string>();
  plan.sequences.forEach((sequence, index) => {
    const label = sequence.title || `序列 ${index + 1}`;
    if (!sequence.sequence_id) issues.push(`${label}缺少编号`);
    else if (sequenceIds.has(sequence.sequence_id)) issues.push(`${label}编号重复`);
    else sequenceIds.add(sequence.sequence_id);
    const refs = sequence.shot_nos ?? [];
    if (!refs.length) issues.push(`${label}尚未指定镜头`);
    if (new Set(refs).size !== refs.length) issues.push(`${label}重复引用同一镜`);
    refs.forEach(shot => {
      if (!available.has(shot)) issues.push(`${label}引用了不存在的镜 ${shot}`);
      else covered.add(shot);
    });
  });
  const missing = [...available].filter(shot => !covered.has(shot));
  if (missing.length) issues.push(`镜 ${missing.join('、')} 尚未归入序列`);
  return issues;
}
export const DIRECTOR_PLAN_PENDING_REASON = '导演规划已修改，已有镜头尚未同步，请先重新生成脚本';
