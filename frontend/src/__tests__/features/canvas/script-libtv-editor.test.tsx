// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { FreezoneStoryScriptRow } from '@/api/ops';
import { ScriptLibtvEditor } from '@/features/canvas/nodes/script/ScriptLibtvEditor';
import type { ScriptReadiness } from '@/features/canvas/nodes/script/scriptReadiness';

function row(shot: number, patch: Partial<FreezoneStoryScriptRow> = {}) {
  return {
    shot_id: `shot:${shot}`,
    shot_order: shot,
    display_shot_no: String(shot),
    shot_no: shot,
    duration: 5,
    visual_description: `第 ${shot} 镜画面`,
    shot: '全景',
    lighting_mood: '冷蓝夜光',
    dialogue: '',
    sound: '雨声',
    video_motion_prompt: '缓慢推近',
    shot_prompt: '',
    ...patch,
  } as unknown as FreezoneStoryScriptRow;
}

function readiness(shotCount: number, blocking = 0): ScriptReadiness {
  return {
    status: blocking > 0 ? 'blocked' : 'ready',
    title: '测试',
    summary: '测试',
    canProceed: blocking === 0,
    canShoot: false,
    blockers: [],
    degradations: [],
    work: [],
    notices: [],
    issues: [],
    counts: {
      shotCount,
      syncedShotCount: 0,
      blockedShotCount: 0,
      staleShotCount: 0,
      missingStoryboardCount: 0,
      pendingStoryboardCount: 0,
      missingAssetCount: 0,
      degradedShotCount: 0,
      referenceOverflowShotCount: 0,
      contractBlockingCount: blocking,
      contractAdvisoryCount: 0,
    },
  };
}

function baseProps() {
  return {
    open: true,
    title: '雨夜码头',
    rows: [row(1), row(2)],
    readiness: readiness(2),
    assetTotal: 3,
    assetReady: 1,
    nextLabel: '下一步：准备资产',
    onClose: vi.fn(),
    onNext: vi.fn(),
    onCellCommit: vi.fn(),
    rowOperations: {
      onInsertAfter: vi.fn(),
      onDuplicate: vi.fn(),
      onMove: vi.fn(),
      onDelete: vi.fn(),
      onRewrite: vi.fn(),
      canDelete: true,
    },
  };
}

describe('LibTV 脚本编辑器外观', () => {
  it('按 LibTV 口径显示三阶段和右侧完成度', () => {
    render(<ScriptLibtvEditor {...baseProps()} />);
    expect(screen.getByText('确认镜头')).toBeTruthy();
    expect(screen.getByText('2个镜头已就绪')).toBeTruthy();
    expect(screen.getByText('准备资产')).toBeTruthy();
    expect(screen.getByText('1/3 已生成、还差 2 个')).toBeTruthy();
    expect(screen.getByText('合成提示词')).toBeTruthy();
    expect(screen.getByText('0/2 已合成')).toBeTruthy();
    expect(screen.getByText('1/3 完成后可批量生视频')).toBeTruthy();
  });

  it('最终提示词一列用 LibTV 的表头与空态占位', () => {
    render(<ScriptLibtvEditor {...baseProps()} />);
    expect(screen.getByText('对白·旁白')).toBeTruthy();
    expect(screen.getByText('最终提示词')).toBeTruthy();
    const empties = document.querySelectorAll('[data-empty-placeholder="待生成提示词"]');
    expect(empties.length).toBe(2);
  });

  it('底部添加镜头和下一步走既有回调', () => {
    const props = baseProps();
    render(<ScriptLibtvEditor {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /添加镜头/ }));
    expect(props.rowOperations.onInsertAfter).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByRole('button', { name: /下一步：准备资产/ }));
    expect(props.onNext).toHaveBeenCalledTimes(1);
  });

  it('行操作菜单仍能删除这一镜', () => {
    const props = baseProps();
    render(<ScriptLibtvEditor {...props} />);
    fireEvent.click(screen.getByRole('button', { name: '第 1 镜操作' }));
    fireEvent.click(screen.getByRole('button', { name: '删除这一镜' }));
    expect(props.rowOperations.onDelete).toHaveBeenCalledWith(0);
  });
});
