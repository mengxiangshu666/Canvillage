// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { FreezoneStoryScriptRow, ScriptContractIssue, ScriptContractReport } from '@/api/ops';
import { ScriptReadinessBanner } from '@/features/canvas/nodes/script/ScriptReadinessBanner';
import type {
  ScriptAssetPreflight,
  ScriptPreflight,
  ScriptShotPreflightEntry,
  ScriptShotSyncState,
} from '@/features/canvas/nodes/script/scriptPreflight';
import {
  computeScriptReadiness,
  describeScriptReadinessMetrics,
} from '@/features/canvas/nodes/script/scriptReadiness';
import { optimizableScriptIssues } from '@/features/canvas/nodes/script/scriptRepair';

function rows(count: number): FreezoneStoryScriptRow[] {
  return Array.from({ length: count }, (_, index) => ({
    shot_no: String(index + 1),
    shot_prompt: '镜头推进',
    visual_description: '人物站在门口',
  }));
}

function entry(shotNumber: string, state: ScriptShotSyncState): ScriptShotPreflightEntry {
  return {
    rowKey: `shot:${shotNumber}`,
    shotNumber,
    state,
    defects: state === 'missing' && shotNumber === '99' ? ['image-prompt-missing'] : [],
    reasons: [],
    blocked: false,
  };
}

function preflight(params: {
  shotCount: number;
  states?: ScriptShotSyncState[];
  blockedShotNumbers?: string[];
  degradedShotNumbers?: string[];
  staleShotNumbers?: string[];
  pendingShotNumbers?: string[];
  overflowShotNumbers?: string[];
  tokenMissingShotNumbers?: string[];
}): ScriptPreflight {
  const states = params.states ?? Array.from({ length: params.shotCount }, () => 'synced' as const);
  const entries = states.map((state, index) => entry(String(index + 1), state));
  const counts: Record<ScriptShotSyncState, number> = {
    missing: 0,
    pending: 0,
    stale: 0,
    synced: 0,
  };
  for (const state of states) counts[state] += 1;
  return {
    entries,
    byRowKey: new Map(entries.map((item) => [item.rowKey, item])),
    counts,
    generatableShotCount: states.filter((state) => state !== 'synced').length,
    blockedShotNumbers: params.blockedShotNumbers ?? [],
    degradedShotNumbers: params.degradedShotNumbers ?? [],
    staleShotNumbers: params.staleShotNumbers ?? [],
    pendingShotNumbers: params.pendingShotNumbers ?? [],
    overflowShotNumbers: params.overflowShotNumbers ?? [],
    tokenMissingShotNumbers: params.tokenMissingShotNumbers ?? [],
    hasBlockers: (params.blockedShotNumbers ?? []).length > 0,
  };
}

function assetPreflight(params: {
  missingCount?: number;
  missingSummary?: string;
  pendingCount?: number;
} = {}): ScriptAssetPreflight {
  const missingCount = params.missingCount ?? 0;
  return {
    referenceMissing: Array.from({ length: missingCount }, () => ({})) as never,
    pendingGeneration: Array.from({ length: params.pendingCount ?? 0 }, () => ({})) as never,
    contributionCount: 0,
    missingSummary: params.missingSummary ?? '',
    hasGap: missingCount > 0,
  };
}

function issue(params: {
  ruleId: string;
  severity?: 'blocking' | 'advisory';
  fixed?: boolean;
  shotNo?: string;
}): ScriptContractIssue {
  return {
    rule_id: params.ruleId,
    severity: params.severity ?? 'blocking',
    message: '规则说明',
    row_index: 0,
    shot_no: params.shotNo ?? '',
    field: 'shot_prompt',
    fixed: params.fixed ?? false,
  };
}

function report(issues: ScriptContractIssue[] = []): ScriptContractReport {
  return {
    schema: 'freezone.script-contract.v1',
    issues,
    rows_fingerprint: 'fp',
  };
}

describe('脚本节点开拍雷达 · 纯函数', () => {
  it('资产基准说明不同是可核对提醒，不把文字差异当成已证实的设计矛盾', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2), preflight: preflight({ shotCount: 2 }), assetPreflight: assetPreflight(),
      contractReport: report([issue({ ruleId: 'script.assets.definition_consistency.v1', severity: 'advisory', shotNo: '2' })]),
    });
    expect(readiness.canProceed).toBe(true);
    expect(readiness.notices[0].title).toBe('同名资产的基准说明不同');
    expect(readiness.notices[0].shotNumbers).toEqual(['2']);
    expect(optimizableScriptIssues(readiness.notices.length ? [issue({ ruleId: 'script.assets.definition_consistency.v1', severity: 'advisory', shotNo: '2' })] : [])).toHaveLength(1);
  });
  it('空脚本明确停在第一步，不伪造可开拍', () => {
    const readiness = computeScriptReadiness({
      rows: [],
      preflight: preflight({ shotCount: 0 }),
      assetPreflight: assetPreflight(),
      contractReport: null,
    });

    expect(readiness.status).toBe('empty');
    expect(readiness.canProceed).toBe(false);
    expect(readiness.canShoot).toBe(false);
  });

  it('提示词排版合同问题显示为提醒，不阻止 Canvas 付费动作', () => {
    const formatRules = [
      'script.shot_prompt.segments.v1',
      'script.shot_prompt.order.v1',
      'script.character_card.verbatim.v1',
      'script.style.singleton.v1',
      'script.technical.singleton.v1',
      'script.motion.segments.v1',
      'script.camera.single.v1',
    ];
    const readiness = computeScriptReadiness({
      rows: rows(8),
      preflight: preflight({ shotCount: 8 }),
      assetPreflight: assetPreflight(),
      contractReport: report(
        formatRules.map((ruleId) => issue({ ruleId, shotNo: '2' })),
      ),
    });

    expect(readiness.status).toBe('degraded');
    expect(readiness.canProceed).toBe(true);
    expect(readiness.blockers).toEqual([]);
    expect(readiness.notices.map((notice) => notice.id)).toEqual(
      formatRules.map((ruleId) => `advisory:${ruleId}`),
    );
    expect(readiness.counts.contractBlockingCount).toBe(0);
    expect(readiness.counts.contractAdvisoryCount).toBe(formatRules.length);
  });

  it('缺图片提示词与过期分镜都是硬阻塞，过期分镜给重出入口', () => {
    const readiness = computeScriptReadiness({
      rows: rows(8),
      preflight: preflight({
        shotCount: 8,
        states: ['synced', 'stale', 'missing', 'missing', 'missing', 'missing', 'missing', 'missing'],
        blockedShotNumbers: ['3'],
        staleShotNumbers: ['2'],
      }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });

    expect(readiness.status).toBe('blocked');
    expect(readiness.blockers.map((item) => item.id)).toEqual([
      'shot-prompt-missing',
      'storyboard-stale',
    ]);
    expect(readiness.blockers[1].action).toBe('generate-storyboard');
  });

  it('合同报告缺失只是需要复核，不足以宣告不可开拍', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({ shotCount: 2 }),
      assetPreflight: assetPreflight(),
      contractReport: null,
    });

    expect(readiness.status).toBe('degraded');
    expect(readiness.blockers).toEqual([]);
    expect(readiness.degradations[0].id).toBe('contract-report-missing');
    expect(readiness.canProceed).toBe(true);
    expect(readiness.canShoot).toBe(false);
  });

  it('资产缺口与角色图缺口是降级，能继续但要先做取舍', () => {
    const readiness = computeScriptReadiness({
      rows: rows(4),
      preflight: preflight({
        shotCount: 4,
        degradedShotNumbers: ['1', '3'],
      }),
      assetPreflight: assetPreflight({
        missingCount: 2,
        missingSummary: '1 个角色 / 1 个场景',
      }),
      contractReport: report(),
    });

    expect(readiness.status).toBe('degraded');
    expect(readiness.degradations.map((item) => item.id)).toEqual([
      'asset-reference-missing',
      'character-image-missing',
    ]);
    expect(readiness.degradations.every((item) => item.action === 'generate-assets')).toBe(true);
  });

  it('合同 advisory 不拦停，但会把状态留在 degraded，避免“可开拍 + 质量警告”并排', () => {
    const readiness = computeScriptReadiness({
      rows: rows(8),
      preflight: preflight({ shotCount: 8 }),
      assetPreflight: assetPreflight(),
      contractReport: report([
        issue({
          ruleId: 'script.viewability.dialogue_ratio.v1',
          severity: 'advisory',
        }),
      ]),
    });

    expect(readiness.status).toBe('degraded');
    expect(readiness.canProceed).toBe(true);
    expect(readiness.canShoot).toBe(false);
    expect(readiness.notices[0].id).toBe('advisory:script.viewability.dialogue_ratio.v1');
  });

  it('合同干净且分镜图已同步时才给“可开拍”', () => {
    const readiness = computeScriptReadiness({
      rows: rows(3),
      preflight: preflight({ shotCount: 3 }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });

    expect(readiness.status).toBe('ready');
    expect(readiness.canShoot).toBe(true);
    expect(readiness.title).toBe('可开拍');
  });

  it('没有硬阻塞但分镜图还没出时，报“可以开始出图”而不是可开拍', () => {
    const readiness = computeScriptReadiness({
      rows: rows(3),
      preflight: preflight({
        shotCount: 3,
        states: ['missing', 'missing', 'missing'],
      }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });

    expect(readiness.status).toBe('ready');
    expect(readiness.canProceed).toBe(true);
    expect(readiness.canShoot).toBe(false);
    expect(readiness.title).toBe('可以开始出图');
    expect(readiness.work[0].action).toBe('generate-storyboard');
  });

  it('可看性指标压成开拍前能直接看的一行', () => {
    expect(
      describeScriptReadinessMetrics({
        total_seconds: 120,
        dialogue_seconds_share: 0.9,
        portrait_share: 0.82,
        action_shot_share: 0.07,
        longest_standoff_seconds: 26,
      }),
    ).toBe('全片 120s · 台词时长 90% · 贴身景别 82% · 动作镜 7% · 最长静戏 26s');
  });
});

describe('脚本节点开拍雷达 · 界面', () => {
  it('缺少可提交提示词仍显示为硬阻塞', () => {
    const readiness = computeScriptReadiness({
      rows: rows(8),
      preflight: preflight({ shotCount: 8, blockedShotNumbers: ['2'] }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });
    render(<ScriptReadinessBanner readiness={readiness} />);

    expect(screen.getByText('暂不能开拍')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('点开看每一条判定'));
    expect(screen.getByText('有镜头没有可提交的图片提示词')).toBeInTheDocument();
  });

  it('全绿时给出唯一一处明确“可开拍”', () => {
    const readiness = computeScriptReadiness({
      rows: rows(3),
      preflight: preflight({ shotCount: 3 }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });
    render(<ScriptReadinessBanner readiness={readiness} />);

    expect(screen.getByText('可开拍')).toBeInTheDocument();
  });

  it('合同有可修复问题时提供一键优化入口', () => {
    const onOptimize = vi.fn();
    const readiness = computeScriptReadiness({
      rows: rows(8),
      preflight: preflight({ shotCount: 8 }),
      assetPreflight: assetPreflight(),
      contractReport: report([
        issue({
          ruleId: 'script.viewability.framing_mix.v1',
          severity: 'blocking',
          shotNo: '',
        }),
      ]),
    });

    render(<ScriptReadinessBanner readiness={readiness} onOptimize={onOptimize} />);
    fireEvent.click(screen.getByRole('button', { name: '一键优化' }));

    expect(onOptimize).toHaveBeenCalledOnce();
  });

  it('已修复与机械规则不会进入一键优化', () => {
    expect(
      optimizableScriptIssues([
        issue({ ruleId: 'script.viewability.framing_mix.v1', severity: 'blocking' }),
        issue({ ruleId: 'script.viewability.framing_mix.v1', severity: 'advisory' }),
        issue({ ruleId: 'script.camera.single.v1', fixed: true }),
        issue({ ruleId: 'script.shot_no.sequence.v1', severity: 'advisory' }),
      ]).map((entry) => entry.rule_id),
    ).toEqual(['script.viewability.framing_mix.v1']);
  });
});
