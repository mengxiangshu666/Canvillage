// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import { SCRIPT_NODE_SIZE } from '@/features/canvas/domain/canvasNodes';
import {
  PANEL_OVERHANG_MAX_PX,
  SCRIPT_NODE_Z,
  resolveScriptPanelOverhang,
} from '@/features/canvas/nodes/script/scriptNodeLayout';

/**
 * 脚本节点浮层层级 + 输入面板外溢。
 *
 * 两条都是实测踩过的排版缺陷：
 * - 参考图贴片的悬浮预览写在 `z-[400]`，而全屏是 `z-[220]`、分镜弹层是 `z-[230]`
 *   —— 打开全屏后节点内那张预览会画在蒙层**之上**。三个数字此前散在三处，改一个不会
 *   带动另外两个，所以拉成一张表并在这里钉住相对次序。
 * - 输入面板外溢是定值 60，最小宽度 360 时面板比节点宽 120px、两侧悬空。
 */
describe('脚本浮层层级', () => {
  it('弹层 > 全屏 > 贴片预览（越小越靠下）', () => {
    expect(SCRIPT_NODE_Z.chipPreview).toBeLessThan(SCRIPT_NODE_Z.fullscreen);
    expect(SCRIPT_NODE_Z.fullscreen).toBeLessThan(SCRIPT_NODE_Z.storyboardDialog);
  });

  it('贴片预览不再是此前那个 400 —— 那个值会压在全屏蒙层之上', () => {
    // 回归钉：修之前这里是 400，全屏是 220 / 弹层是 230，于是「打开全屏后节点内的
    // 预览画在蒙层上面」。只要有人把它改回高位，用例立刻红。
    expect(SCRIPT_NODE_Z.chipPreview).toBeLessThanOrEqual(200);
  });
});

describe('输入面板外溢', () => {
  it('最小宽度下不外溢 —— 面板与节点等宽，不两侧悬空', () => {
    expect(resolveScriptPanelOverhang(SCRIPT_NODE_SIZE.min.width)).toBe(0);
    // 比下限还窄时也按 0，不出现负外溢（负值会让面板比节点窄，露出背景）。
    expect(resolveScriptPanelOverhang(200)).toBe(0);
  });

  it('宽度增加时外溢渐增，到上限封顶', () => {
    const mid = resolveScriptPanelOverhang(420);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(PANEL_OVERHANG_MAX_PX);
    expect(resolveScriptPanelOverhang(SCRIPT_NODE_SIZE.withResult.width)).toBe(
      PANEL_OVERHANG_MAX_PX,
    );
    expect(resolveScriptPanelOverhang(4000)).toBe(PANEL_OVERHANG_MAX_PX);
  });

  it('非有限值回落最小宽度那一档，不返回 NaN（NaN 会让 left/right 失效）', () => {
    // NaN 直接透传会让 `left: NaNpx` 变成无效声明、面板退回默认定位 —— 所以
    // 非有限值一律当「没量到宽度」处理，取最小档（0 外溢），而不是取上限。
    expect(resolveScriptPanelOverhang(Number.NaN)).toBe(0);
    expect(resolveScriptPanelOverhang(Number.POSITIVE_INFINITY)).toBe(0);
  });
});
