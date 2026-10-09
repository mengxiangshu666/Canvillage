// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { cellText, scriptRowShotNumber } from './scriptViews';

export interface ScriptCutReview {
  from: string;
  to: string;
  endState: string;
  startState: string;
  reason: string;
  note: string;
}

/** Planning review only; different wording is not proof of a media discontinuity. */
export function scriptCutReviews(rows: readonly FreezoneStoryScriptRow[]): ScriptCutReview[] {
  return rows.slice(0, -1).map((row, index) => {
    const next = rows[index + 1];
    const endState = cellText(row, 'end_state');
    const startState = cellText(next, 'start_state');
    const reason = cellText(row, 'cut_reason');
    const continuous = /(continuous_action|continuous-action|动作衔接|连续动作)/i.test(cellText(row, 'transition_plan'));
    const hasState = (value: string) => !['', '无', '没有', 'none', 'n/a', 'null', '未知', '待定', '待补', '待补充'].includes(value.trim().toLowerCase());
    return {
      from: scriptRowShotNumber(row, index), to: scriptRowShotNumber(next, index + 1), endState, startState, reason,
      note: !hasState(endState) || !hasState(startState) ? '缺少切点或首帧状态，待补规划'
        : continuous && endState !== startState ? '已声明动作接续，需复核姿态、位置、方向和速度；文字差异不代表画面错误'
        : !reason ? '尚未说明为何在这里切镜'
        : '切镜规划已有，实际动作与声音仍未验',
    };
  });
}
