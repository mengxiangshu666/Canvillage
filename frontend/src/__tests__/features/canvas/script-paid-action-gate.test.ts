// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { FreezoneStoryScriptRow, ScriptContractIssue, ScriptContractReport } from '@/api/ops';
import type {
  ScriptAssetPreflight,
  ScriptPreflight,
  ScriptShotPreflightEntry,
  ScriptShotSyncState,
} from '@/features/canvas/nodes/script/scriptPreflight';
import { computeScriptPaidActionGate } from '@/features/canvas/nodes/script/scriptPaidActionGate';
import { computeScriptReadiness } from '@/features/canvas/nodes/script/scriptReadiness';

it('文生只免除本镜旧分镜图阻塞，保留其他镜头和导演阻塞', () => {
  const readiness = computeScriptReadiness({ rows: rows(2), preflight: preflight({ shotCount: 2, states: ['stale', 'stale'], staleShotNumbers: ['1', '2'] }), assetPreflight: assetPreflight(), contractReport: report() });
  const mixed = computeScriptPaidActionGate(readiness, 'shot-videos', ['1']);
  expect(mixed.allowed).toBe(false);
  expect(mixed.blockers.find(issue => issue.id === 'storyboard-stale')?.shotNumbers).toEqual(['2']);
  expect(computeScriptPaidActionGate(readiness, 'shot-videos', ['1', '2']).allowed).toBe(true);
  const blocked = { ...readiness, issues: [...readiness.issues, { id: 'director-plan', kind: 'blocker' as const, title: '导演规划待同步', detail: '', count: 1, shotNumbers: [] }] };
  expect(computeScriptPaidActionGate(blocked, 'shot-videos', ['1', '2']).allowed).toBe(false);
});

function rows(count: number): FreezoneStoryScriptRow[] {
  return Array.from({ length: count }, (_, index) => ({
    shot_no: String(index + 1),
    shot_prompt: '镜头推进',
    visual_description: '人物站在门口',
  }));
}

function preflight(params: {
  shotCount: number;
  states?: ScriptShotSyncState[];
  blockedShotNumbers?: string[];
  staleShotNumbers?: string[];
  degradedShotNumbers?: string[];
}): ScriptPreflight {
  const states =
    params.states ?? Array.from({ length: params.shotCount }, () => 'synced' as const);
  const entries: ScriptShotPreflightEntry[] = states.map((state, index) => ({
    rowKey: `shot:${index + 1}`,
    shotNumber: String(index + 1),
    state,
    defects: [],
    reasons: [],
    blocked: false,
  }));
  const counts: Record<ScriptShotSyncState, number> = {
    missing: 0,
    pending: 0,
    stale: 0,
    synced: 0,
  };
  for (const state of states) counts[state] += 1;
  return {
    entries,
    byRowKey: new Map(entries.map((entry) => [entry.rowKey, entry])),
    counts,
    generatableShotCount: states.filter((state) => state !== 'synced').length,
    blockedShotNumbers: params.blockedShotNumbers ?? [],
    degradedShotNumbers: params.degradedShotNumbers ?? [],
    staleShotNumbers: params.staleShotNumbers ?? [],
    pendingShotNumbers: [],
    overflowShotNumbers: [],
    tokenMissingShotNumbers: [],
    hasBlockers: (params.blockedShotNumbers ?? []).length > 0,
  };
}

function assetPreflight(missingCount = 0): ScriptAssetPreflight {
  return {
    referenceMissing: Array.from({ length: missingCount }, () => ({})) as never,
    pendingGeneration: [],
    contributionCount: 0,
    missingSummary: missingCount > 0 ? `${missingCount} 项资产` : '',
    hasGap: missingCount > 0,
  };
}

function blockingIssue(): ScriptContractIssue {
  return {
    rule_id: 'script.shot_prompt.order.v1',
    severity: 'blocking',
    message: '段序错乱',
    row_index: 0,
    shot_no: '1',
    field: 'shot_prompt',
    fixed: false,
  };
}

function report(issues: ScriptContractIssue[] = []): ScriptContractReport {
  return { schema: 'freezone.script-contract.v1', rows_fingerprint: 'fp', issues };
}

describe('脚本付费动作门禁', () => {
  it('导演修改未同步时两条动作都拦截，不把重出旧分镜当成修复', () => {
    const readiness = computeScriptReadiness({ rows: rows(2), preflight: preflight({ shotCount: 2 }), assetPreflight: assetPreflight(), contractReport: report(), directorPlanNeedsSync: true });
    for (const action of ['storyboard-images', 'shot-videos'] as const) {
      const gate = computeScriptPaidActionGate(readiness, action);
      expect(gate.allowed).toBe(false);
      expect(gate.blockers.map(issue => issue.id)).toContain('director-plan-pending');
    }
  });
  it('分镜过期不拦重出分镜，因为重出正是修复动作', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({
        shotCount: 2,
        states: ['stale', 'synced'],
        staleShotNumbers: ['1'],
      }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });

    const gate = computeScriptPaidActionGate(readiness, 'storyboard-images');
    expect(gate.allowed).toBe(true);
    expect(gate.blockers).toEqual([]);
  });

  it('同一份过期事实会拦住逐镜出视频', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({
        shotCount: 2,
        states: ['stale', 'synced'],
        staleShotNumbers: ['1'],
      }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });

    const gate = computeScriptPaidActionGate(readiness, 'shot-videos');
    expect(gate.allowed).toBe(false);
    expect(gate.blockers.map((issue) => issue.id)).toContain('storyboard-stale');
    expect(gate.reason).toContain('分镜图已与当前脚本脱节');
  });

  it('提示词格式问题只告警，不拦两条花钱动作', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({ shotCount: 2 }),
      assetPreflight: assetPreflight(),
      contractReport: report([blockingIssue()]),
    });

    const storyboardGate = computeScriptPaidActionGate(readiness, 'storyboard-images');
    const videoGate = computeScriptPaidActionGate(readiness, 'shot-videos');
    expect(storyboardGate.allowed).toBe(true);
    expect(videoGate.allowed).toBe(true);
    expect(storyboardGate.blockers).toEqual([]);
    expect(storyboardGate.warnings.map((item) => item.id)).toContain(
      'advisory:script.shot_prompt.order.v1',
    );
  });

  it('合同报告待刷新时给提醒但不锁住付费动作', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({ shotCount: 2 }),
      assetPreflight: assetPreflight(),
      contractReport: null,
    });

    const gate = computeScriptPaidActionGate(readiness, 'storyboard-images');
    expect(gate.allowed).toBe(true);
    expect(gate.blockers).toEqual([]);
    expect(gate.warnings.map((issue) => issue.id)).toContain('contract-report-missing');
  });

  it('缺图片提示词拦住出图；资产降级只提示不写进 blockers', () => {
    const readiness = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({
        shotCount: 2,
        blockedShotNumbers: ['2'],
        degradedShotNumbers: ['1'],
      }),
      assetPreflight: assetPreflight(1),
      contractReport: report(),
    });

    const gate = computeScriptPaidActionGate(readiness, 'storyboard-images');
    expect(gate.allowed).toBe(false);
    expect(gate.blockers.map((issue) => issue.id)).toContain('shot-prompt-missing');
    expect(gate.warnings.map((issue) => issue.id)).toContain('asset-reference-missing');
    expect(gate.blockers.map((issue) => issue.id)).not.toContain('asset-reference-missing');
  });

  it('空脚本两条动作都阻断；全绿时两条都放行', () => {
    const empty = computeScriptReadiness({
      rows: [],
      preflight: preflight({ shotCount: 0 }),
      assetPreflight: assetPreflight(),
      contractReport: null,
    });
    expect(computeScriptPaidActionGate(empty, 'storyboard-images').allowed).toBe(false);
    expect(computeScriptPaidActionGate(empty, 'shot-videos').allowed).toBe(false);

    const ready = computeScriptReadiness({
      rows: rows(2),
      preflight: preflight({ shotCount: 2 }),
      assetPreflight: assetPreflight(),
      contractReport: report(),
    });
    expect(computeScriptPaidActionGate(ready, 'storyboard-images').allowed).toBe(true);
    expect(computeScriptPaidActionGate(ready, 'shot-videos').allowed).toBe(true);
  });
});
