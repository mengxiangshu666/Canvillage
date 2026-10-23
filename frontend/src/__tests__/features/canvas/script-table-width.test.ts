// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { scriptFieldSequence } from '@/features/canvas/nodes/script/scriptFields';
import {
  fieldsTotalWidth,
  resolveVisibleScriptColumns,
} from '@/features/canvas/nodes/script/scriptColumns';
import {
  SCRIPT_ROW_ACTIONS_WIDTH_PX,
  fitColumnWidths,
} from '@/features/canvas/nodes/script/ScriptResultTable';
import { resolveScriptTableAvailableWidth } from '@/features/canvas/nodes/script/useScriptTableWidth';

describe('分镜表可用宽度', () => {
  it('节点内扣掉内边距、边框和行操作列', () => {
    expect(
      resolveScriptTableAvailableWidth({ width: 1280, isFullscreen: false, viewportWidth: 1600 }),
    ).toBe(1280 - 18 - SCRIPT_ROW_ACTIONS_WIDTH_PX);
  });

  it('全屏按视口宽扣弹层与行操作列', () => {
    expect(
      resolveScriptTableAvailableWidth({ width: 800, isFullscreen: true, viewportWidth: 1600 }),
    ).toBe(1600 - 66 - SCRIPT_ROW_ACTIONS_WIDTH_PX);
  });

  it('默认列集加上行操作列后不超过容器宽度', () => {
    const width = 1280;
    const available = resolveScriptTableAvailableWidth({
      width,
      isFullscreen: false,
      viewportWidth: 1600,
    });
    const visible = resolveVisibleScriptColumns({
      mode: 'default',
      availableWidthPx: available,
    });
    expect(fieldsTotalWidth(visible) + SCRIPT_ROW_ACTIONS_WIDTH_PX).toBeLessThanOrEqual(width);
    expect(visible.length).toBeLessThan(scriptFieldSequence().length);
  });
});

describe('列宽压缩', () => {
  it('800 宽节点的 5 个必留列会被压到容器内', () => {
    // 800 节点内容区 771px，扣掉 132px 行操作列后只剩 639px。
    const widths = [56, 68, 200, 200, 200];
    const fitted = fitColumnWidths(widths, 771 - SCRIPT_ROW_ACTIONS_WIDTH_PX);
    expect(fitted.reduce((sum, width) => sum + width, 0))
      .toBeLessThanOrEqual(771 - SCRIPT_ROW_ACTIONS_WIDTH_PX);
    expect(fitted.every((width) => width >= 36)).toBe(true);
    expect(fitted[0]).toBeLessThan(widths[0]);
  });

  it('空间够时不改动原宽', () => {
    const widths = [56, 68, 200, 200, 200];
    expect(fitColumnWidths(widths, 1000)).toEqual(widths);
  });

  it('压到 74% 仍放不下就保留原比例，交给横向滚动', () => {
    const widths = [56, 68, 200, 200, 200];
    const fitted = fitColumnWidths(widths, 300);
    expect(fitted.reduce((sum, width) => sum + width, 0)).toBeGreaterThan(300);
    expect(fitted[2]).toBe(148);
  });
});
