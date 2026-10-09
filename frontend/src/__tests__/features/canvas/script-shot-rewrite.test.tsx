// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from '@/api/client';
import { submitFreezoneStoryScript } from '@/api/ops';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ScriptContractBanner } from '@/features/canvas/nodes/script/ScriptContractBanner';

vi.mock('@/api/client', () => ({
  apiCall: vi.fn(),
}));

/**
 * 单镜重写这条路的**报文口径**，以及合同报告在界面上的三档分桶。
 *
 * 报文这一侧钉的是「重写模式真的把整表、目标镜身份、修改要求都发出去了」——
 * 少发任何一项，后端要么拒绝定位、要么把整表当成新剧本重写一遍，两种都不会报错，
 * 只会悄悄毁掉用户已经满意的那几镜。
 */

const CARD = '[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫]';

it('shows live duplicate keyframe refusal without waiting for a saved report', () => {
  render(<ScriptContractBanner report={null} rows={[{
    shot_no: 1, start_state: '站在门边', keyframe_plan: [
      { role: 'action_state', state: '站在门边', purpose: '' },
      { role: 'ending_state', state: '转身离开', purpose: '锁出口' },
      { role: 'ending_state', state: '转身离开', purpose: '锁出口' },
    ],
  }]} />);
  fireEvent.click(screen.getByRole('button', { name: /合同检查/ }));
  expect(screen.getByText(/与首帧文字状态相同/)).toBeInTheDocument();
  expect(screen.getByText(/与状态画面 2 相同/)).toBeInTheDocument();
});

it('allows dismissing live keyframe reasons and shows a changed rejection again', () => {
  const rows = [{ start_state: '站在门边', keyframe_plan: [{ role: 'action_state', state: '站在门边', purpose: '' }] }];
  const dismiss = vi.fn();
  const view = render(<ScriptContractBanner report={null} rows={rows} onDismiss={dismiss} />);
  fireEvent.click(screen.getByTitle('关掉这条横幅'));
  expect(dismiss).toHaveBeenCalledOnce();
  expect(screen.queryByRole('button', { name: /合同检查/ })).not.toBeInTheDocument();
  view.rerender(<ScriptContractBanner report={null} rows={[{ ...rows[0], shot_no: 2 }]} onDismiss={dismiss} />);
  expect(screen.getByRole('button', { name: /合同检查/ })).toBeInTheDocument();
});

function shotPrompt(): string {
  return [
    '[画面构图：近景特写，平视机位]',
    `[角色卡/主体描述：${CARD}]`,
    '[主体/人物空间与互动关系：她独坐在办公桌前]',
    '[极具体的微表情：眼下发青]',
    '[明确的场景环境元素：深夜办公室]',
    '[光影几何与大气效果：冷蓝主调]',
    '[视觉风格/质感：都市悬疑写实电影感]',
    '[技术参数：85mm镜头，f/1.8]',
  ].join(' + ');
}

function row(shotNo: number, shotId: string, description: string) {
  return {
    shot_id: shotId,
    shot_no: shotNo,
    duration: 5,
    visual_description: description,
    shot_prompt: shotPrompt(),
    video_motion_prompt: '[时长：5s]',
  };
}

describe('单镜重写的请求报文', () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: 'job-1', task_key: 'task-1' });
  });

  function lastBody(): Record<string, unknown> {
    const calls = vi.mocked(apiCall).mock.calls;
    const call = calls[calls.length - 1];
    return (call?.[1]?.json ?? {}) as Record<string, unknown>;
  }

  it('带上整表、目标镜身份与修改要求', async () => {
    const rows = [row(1, 'SHOT-A', '她抬眼看屏'), row(2, 'SHOT-B', '她起身')];
    await submitFreezoneStoryScript('58', {
      currentRows: rows,
      rewriteShotId: 'SHOT-B',
      rewriteIndex: 1,
      prompt: '让她停在原地',
      title: '我在盛唐写天下',
    });

    const body = lastBody();
    expect(body.current_rows).toEqual(rows);
    expect(body.rewrite_shot_id).toBe('SHOT-B');
    expect(body.rewrite_index).toBe(1);
    expect(body.prompt).toBe('让她停在原地');
    expect(body.title).toBe('我在盛唐写天下');
  });

  it('普通生成不发重写字段（否则会被当成重写模式）', async () => {
    await submitFreezoneStoryScript('58', { sourceText: '她收到一封信。' });
    const body = lastBody();
    expect(body.current_rows).toBeUndefined();
    expect(body.rewrite_shot_id).toBeUndefined();
    expect(body.rewrite_index).toBeUndefined();
  });

  it('空表不发重写字段，退回普通生成', async () => {
    await submitFreezoneStoryScript('58', {
      sourceText: '她收到一封信。',
      currentRows: [],
      rewriteShotId: 'SHOT-B',
    });
    expect(lastBody().current_rows).toBeUndefined();
  });

  it('整表是拷贝发送，不把节点的行对象直接交给请求层', async () => {
    const rows = [row(1, 'SHOT-A', '她抬眼看屏')];
    await submitFreezoneStoryScript('58', { currentRows: rows, rewriteShotId: 'SHOT-A' });
    const sent = lastBody().current_rows as unknown[];
    expect(sent[0]).not.toBe(rows[0]);
    expect(sent[0]).toEqual(rows[0]);
  });

  it('一键优化带上修复模式与合同问题，不伪装成单镜重写', async () => {
    const rows = [row(1, 'SHOT-A', '她抬眼看屏'), row(2, 'SHOT-B', '她起身')];
    await submitFreezoneStoryScript('58', {
      currentRows: rows,
      repairMode: 'script-contract',
      repairIssues: [
        {
          rule_id: 'script.viewability.framing_mix.v1',
          severity: 'blocking',
          message: '贴身景别占比过高',
          row_index: -1,
          shot_no: '',
          field: 'shot',
          fixed: false,
          detail: { portraitShare: 0.82 },
        },
      ],
    });

    const body = lastBody();
    expect(body.current_rows).toEqual(rows);
    expect(body.repair_mode).toBe('script-contract');
    expect(body.repair_issues).toEqual([
      expect.objectContaining({
        rule_id: 'script.viewability.framing_mix.v1',
        detail: { portraitShare: 0.82 },
      }),
    ]);
    expect(body.rewrite_shot_id).toBeUndefined();
    expect(body.rewrite_index).toBeUndefined();
  });
});

describe('合同报告横幅', () => {
  it('问题清除后仍能查看联合修改的诊断和未采用原因', () => {
    render(<ScriptContractBanner report={{ issues: [], repair: {
      scope: 'sequence', applied: 1, rejected: 1, target_results: [{
        row_index: 1, row_indices: [1, 2], sequence_ids: ['S1'], rule_ids: [], pass: 1,
        outcome: 'rejected', diagnosis: '先听见报警，再作出选择。', reason: 'contract_regression',
      }],
    } }} />);
    const button = screen.getByRole('button', { name: /已优化 1 处/ });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/先听见报警/).textContent).toContain('已保留原版本');
  });
  it('三档分桶：要人处理的、已改正的、提醒的，各报各的数量', () => {
    render(
      <ScriptContractBanner
        report={{
          issue_count: 3,
          fixed_count: 1,
          blocking_count: 1,
          advisory_count: 1,
          issues: [
            {
              rule_id: 'script.camera.single.v1',
              severity: 'blocking',
              message: '一个镜头写了多个主运镜：镜头前推、环绕拍摄',
              row_index: 0,
              shot_no: '2',
              field: 'video_motion_prompt',
              fixed: false,
            },
            {
              rule_id: 'script.character_card.verbatim.v1',
              severity: 'blocking',
              message: '角色卡已按本篇首次出现改回逐字一致',
              row_index: 1,
              shot_no: '3',
              field: 'character_description_1',
              fixed: true,
            },
            {
              rule_id: 'script.shot_no.sequence.v1',
              severity: 'advisory',
              message: '第 4 行的镜号是 5；镜号应从 1 起连续',
              row_index: 3,
              shot_no: '5',
              field: 'shot_no',
              fixed: false,
            },
          ],
        }}
      />,
    );

    const summary = screen.getByRole('button', { name: /合同检查/ });
    expect(summary.textContent).toContain('1 处不符合合同');
    expect(summary.textContent).toContain('2');
    expect(summary.textContent).toContain('1 处已自动改正');
    expect(summary.textContent).toContain('1 处提醒');
  });

  it('没有报告或没有问题时不占地方', () => {
    const { container: empty } = render(<ScriptContractBanner report={null} />);
    expect(empty.firstChild).toBeNull();

    const { container: clean } = render(<ScriptContractBanner report={{ issues: [] }} />);
    expect(clean.firstChild).toBeNull();
  });
});
