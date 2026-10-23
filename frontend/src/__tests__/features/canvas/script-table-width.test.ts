// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { scriptFieldSequence } from '@/features/canvas/nodes/script/scriptFields';
import {
  fieldsTotalWidth,
  resolveVisibleScriptColumns,
} from '@/features/canvas/nodes/script/scriptColumns';
import { SCRIPT_ROW_ACTIONS_WIDTH_PX } from '@/features/canvas/nodes/script/ScriptResultTable';
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
