// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { describeScriptRepairOutcome } from '@/features/canvas/nodes/script/scriptRepair';

describe('describeScriptRepairOutcome', () => {
  it('联合修改按范围计数，不冒充单镜数量', () => {
    expect(describeScriptRepairOutcome({ scope: 'sequence', applied: 1, failed: 1 }))
      .toBe('已优化 1 处，1 处改写失败；失败范围见合同报告');
  });
  it('explains why automatic repair stopped without suggesting repeated clicks', () => {
    expect(describeScriptRepairOutcome({ applied: 2, remaining_issue_count: 3, stop_reason: 'review_required' })).toContain('不建议反复点击');
    expect(describeScriptRepairOutcome({ applied: 2, remaining_issue_count: 3, stop_reason: 'budget_exhausted' })).toContain('额度已用完');
  });
  it('说明改了几镜', () => {
    expect(describeScriptRepairOutcome({ targets: 3, applied: 3, failed: 0 })).toBe(
      '已优化 3 镜',
    );
  });

  it('部分失败时如实报出失败镜数，不谎报全绿', () => {
    expect(
      describeScriptRepairOutcome({ targets: 3, applied: 2, failed: 1 }),
    ).toBe('已优化 2 镜，1 镜改写失败；失败镜头见合同报告');
  });

  it('被挤下的全片问题要告诉用户再点一次', () => {
    expect(
      describeScriptRepairOutcome({
        targets: 10,
        applied: 10,
        failed: 0,
        deferred_rule_ids: ['script.viewability.framing_mix.v1'],
      }),
    ).toBe('已优化 10 镜；另有 1 条问题本轮未处理，继续点击可再修');
  });

  it('缺少执行明细时不能宣称全部完成', () => {
    expect(describeScriptRepairOutcome(undefined)).toBe('优化结果缺少执行明细，请核对当前问题报告，暂不能确认全部处理完成');
  });

  it('退回变差的改写时不能说没有镜头需要改写', () => {
    expect(describeScriptRepairOutcome({ applied: 0, rejected: 2, remaining_issue_count: 1 }))
      .toBe('本次未找到更好的改写；2 次候选改写没有减少合同问题，已保留原版本；还剩 1 条问题，继续点击可再修');
  });

  it('保留成功修改并报出退回数量', () => {
    expect(describeScriptRepairOutcome({ applied: 1, rejected: 1 }))
      .toBe('已优化 1 镜；1 次候选改写没有减少合同问题，已保留原版本');
  });

  it('模型原地踏步时不能宣称没有问题', () => {
    expect(describeScriptRepairOutcome({ applied: 0, remaining_issue_count: 2 }))
      .toBe('本次未找到更好的改写：候选稿没有让合同问题变少；还剩 2 条问题，继续点击可再修');
  });
});
