// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  forEachPointerAuraDot,
  mixRgb,
  parseHexColor,
  resolveGridRgb,
  resolvePointerAuraGrid,
  rgba,
} from '@/features/canvas/ui/canvasPointerAuraGeometry';

describe('pointer aura grid', () => {
  it('matches the XYFlow dot geometry for a resting viewport', () => {
    const grid = resolvePointerAuraGrid({
      gap: 20,
      dotSize: 2.4,
      zoom: 1,
      translateX: 0,
      translateY: 0,
    });

    expect(grid.cell).toBe(20);
    expect(grid.dotRadius).toBeCloseTo(1.2, 5);
    // XYFlow 把图案整体平移 1 + 单元格 / 2，圆心再减去点半径。
    expect(grid.originX).toBeCloseTo(1.2 - 1 - 10, 5);
    expect(grid.originY).toBeCloseTo(1.2 - 1 - 10, 5);
  });

  it('scales the cell with zoom while keeping the dot diameter stable', () => {
    const grid = resolvePointerAuraGrid({
      gap: 20,
      dotSize: 2.4 / 2,
      zoom: 2,
      translateX: 0,
      translateY: 0,
    });

    expect(grid.cell).toBe(40);
    expect(grid.dotRadius).toBeCloseTo(1.2, 5);
  });

  it('treats a pan of exactly one cell as the same lattice', () => {
    const base = resolvePointerAuraGrid({
      gap: 24,
      dotSize: 2.4,
      zoom: 1,
      translateX: 5,
      translateY: -7,
    });
    const panned = resolvePointerAuraGrid({
      gap: 24,
      dotSize: 2.4,
      zoom: 1,
      translateX: 5 + 24,
      translateY: -7 + 24,
    });

    expect(panned.originX).toBeCloseTo(base.originX, 5);
    expect(panned.originY).toBeCloseTo(base.originY, 5);
  });

  it('keeps negative pans on the same lattice instead of drifting', () => {
    const positive = resolvePointerAuraGrid({
      gap: 20,
      dotSize: 2.4,
      zoom: 1,
      translateX: 3,
      translateY: 3,
    });
    const negative = resolvePointerAuraGrid({
      gap: 20,
      dotSize: 2.4,
      zoom: 1,
      translateX: 3 - 20 * 1000,
      translateY: 3 - 20 * 1000,
    });

    expect(negative.originX).toBeCloseTo(positive.originX, 5);
    expect(negative.originY).toBeCloseTo(positive.originY, 5);
  });

  it('never collapses to a zero-sized cell', () => {
    const grid = resolvePointerAuraGrid({
      gap: 0,
      dotSize: 0,
      zoom: 0,
      translateX: Number.NaN,
      translateY: Number.NaN,
    });

    expect(grid.cell).toBeGreaterThan(0);
    expect(grid.dotRadius).toBeGreaterThan(0);
    expect(Number.isFinite(grid.originX)).toBe(true);
    expect(Number.isFinite(grid.originY)).toBe(true);
  });
});

describe('pointer aura falloff', () => {
  const grid = resolvePointerAuraGrid({
    gap: 16,
    dotSize: 2.4,
    zoom: 1,
    translateX: 0,
    translateY: 0,
  });

  it('lights the dot under the pointer at full strength', () => {
    const hits: Array<{ distance: number; intensity: number }> = [];
    forEachPointerAuraDot(grid, grid.originX, grid.originY, 190, (x, y, intensity) => {
      hits.push({ distance: Math.hypot(x - grid.originX, y - grid.originY), intensity });
    });

    const center = hits.find((hit) => hit.distance === 0);
    expect(center?.intensity).toBeCloseTo(1, 5);
    expect(hits.every((hit) => hit.intensity <= 1)).toBe(true);
    expect(hits.every((hit) => hit.distance === 0 || hit.intensity < 1)).toBe(true);
  });

  it('keeps every emitted dot on the grid lattice and inside the reach', () => {
    const reach = 190;
    const centerX = 137;
    const centerY = 89;
    const columns = new Set<number>();

    forEachPointerAuraDot(grid, centerX, centerY, reach, (x, y, intensity) => {
      const column = (x - grid.originX) / grid.cell;
      const row = (y - grid.originY) / grid.cell;
      expect(Number.isInteger(Math.round(column * 1e6) / 1e6)).toBe(true);
      expect(Number.isInteger(Math.round(row * 1e6) / 1e6)).toBe(true);
      expect(Math.hypot(x - centerX, y - centerY)).toBeLessThanOrEqual(reach + 1e-6);
      expect(intensity).toBeGreaterThan(0);
      expect(intensity).toBeLessThanOrEqual(1);
      columns.add(Math.round(column));
    });

    // 16px 网格、190px 半径大约覆盖 π·190²/16² ≈ 443 个点，太多就说明
    // 遍历范围算错了，会拖慢每一帧。
    expect(columns.size).toBeGreaterThan(20);
    expect(columns.size).toBeLessThan(30);
  });

  it('fades with distance instead of stepping', () => {
    const samples: Array<{ distance: number; intensity: number }> = [];
    forEachPointerAuraDot(grid, 0, 0, 190, (x, y, intensity) => {
      samples.push({ distance: Math.hypot(x, y), intensity });
    });
    samples.sort((a, b) => a.distance - b.distance);

    expect(samples.length).toBeGreaterThan(10);
    for (let index = 1; index < samples.length; index += 1) {
      expect(samples[index].intensity).toBeLessThanOrEqual(
        samples[index - 1].intensity,
      );
    }
  });

  it('does nothing when the reach is not positive', () => {
    let calls = 0;
    forEachPointerAuraDot(grid, 0, 0, 0, () => {
      calls += 1;
    });
    forEachPointerAuraDot(grid, 0, 0, Number.NaN, () => {
      calls += 1;
    });

    expect(calls).toBe(0);
  });
});

describe('pointer aura colors', () => {
  it('reads hex colors and falls back for anything else', () => {
    expect(parseHexColor('#4b4b53')).toEqual([75, 75, 83]);
    expect(parseHexColor('4b4b53')).toEqual([75, 75, 83]);
    expect(parseHexColor('var(--primary)')).toBeNull();
    expect(resolveGridRgb('var(--primary)')).toEqual([176, 176, 183]);
  });

  it('mixes toward white and clamps the amount', () => {
    expect(mixRgb([0, 0, 0], [255, 255, 255], 0.5)).toEqual([128, 128, 128]);
    expect(mixRgb([10, 20, 30], [255, 255, 255], 5)).toEqual([255, 255, 255]);
    expect(mixRgb([10, 20, 30], [255, 255, 255], -1)).toEqual([10, 20, 30]);
  });

  it('formats rgba without floating point noise', () => {
    expect(rgba([12, 34, 56], 0.5)).toBe('rgba(12, 34, 56, 0.5)');
    expect(rgba([12, 34, 56], 2)).toBe('rgba(12, 34, 56, 1)');
  });
});
