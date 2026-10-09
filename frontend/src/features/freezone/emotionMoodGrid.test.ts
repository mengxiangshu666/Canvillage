// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { MOOD_SHAPE_OVERRIDES } from "./ExpressionHeadPreview";
import {
  MOOD_CENTER_ROW,
  MOOD_CENTER_SNAP,
  MOOD_GRID,
  MOOD_GRID_CELL_COUNT,
  MOOD_GRID_COLUMNS,
  MOOD_GRID_ROWS,
  affectFromMoodCell,
  moodCellFromAffect,
  moodDirectionModifier,
} from "./emotionMoodGrid";

describe("emotionMoodGrid", () => {
  it("contains one unique, model-ready semantic anchor for every 10x5 cell", () => {
    expect(MOOD_GRID_ROWS).toBe(10);
    expect(MOOD_GRID_COLUMNS).toBe(5);
    expect(MOOD_GRID_CELL_COUNT).toBe(50);
    expect(MOOD_GRID).toHaveLength(50);
    expect(new Set(MOOD_GRID.map((item) => item.key)).size).toBe(50);
    expect(new Set(MOOD_GRID.map((item) => item.label)).size).toBe(50);
    expect(new Set(MOOD_GRID.map((item) => item.expressionPrompt)).size).toBe(50);

    for (const item of MOOD_GRID) {
      // Short visual cues only — long FACS essays hurt dual-layer image edit.
      expect(item.expressionPrompt.length).toBeGreaterThan(12);
      expect(item.expressionPrompt.length).toBeLessThan(120);
      expect(item.expressionPrompt).not.toMatch(/Eyebrows:/);
      expect(item.expressionPrompt).not.toContain("Keep this emotion isolated");
    }
  });

  it("maps every cell center back to the same cell", () => {
    for (let row = 0; row < MOOD_GRID_ROWS; row += 1) {
      for (let col = 0; col < MOOD_GRID_COLUMNS; col += 1) {
        const affect = affectFromMoodCell(row, col);
        expect(moodCellFromAffect(affect.x, affect.y)).toMatchObject({ row, col });
      }
    }
  });

  it("clamps pointer and requested cell values at all mood-space boundaries", () => {
    expect(moodCellFromAffect(-5, 5)).toMatchObject({ row: 0, col: 0 });
    expect(moodCellFromAffect(5, 5)).toMatchObject({ row: 0, col: 4 });
    expect(moodCellFromAffect(-5, -5)).toMatchObject({ row: 9, col: 0 });
    expect(moodCellFromAffect(5, -5)).toMatchObject({ row: 9, col: 4 });
    expect(affectFromMoodCell(-99, -99)).toEqual(affectFromMoodCell(0, 0));
    expect(affectFromMoodCell(99, 99)).toEqual(affectFromMoodCell(9, 4));
  });

  it("treats the pad center as a neutral point instead of the nearest anchor", () => {
    // 10 行网格没有 y=0 的行：按最近行取值会得到「难以置信」，与「中性 100%」矛盾。
    expect(moodCellFromAffect(0, 0)).toMatchObject({
      row: MOOD_CENTER_ROW,
      col: MOOD_CENTER_ROW,
      item: { key: "neutral-center", label: "中性", primaryExpression: "neutral" },
    });
    expect(moodCellFromAffect(0.04, -0.04).item.label).toBe("中性");
    expect(affectFromMoodCell(MOOD_CENTER_ROW, MOOD_CENTER_ROW)).toEqual({ x: 0, y: 0 });
    // 吸附半径必须小于半格高（格心 y = ±0.1），否则会吞掉中间两行。
    expect(MOOD_CENTER_SNAP).toBeLessThan(0.1);
    expect(moodCellFromAffect(0.06, 0)).toMatchObject({ row: 5, col: 2 });
  });

  it("gives every semantic key a distinct morph override", () => {
    expect(Object.keys(MOOD_SHAPE_OVERRIDES).sort()).toEqual(MOOD_GRID.map((item) => item.key).sort());
    const signatures = MOOD_GRID.map((item) => JSON.stringify(
      Object.entries(MOOD_SHAPE_OVERRIDES[item.key]).sort(([a], [b]) => a.localeCompare(b)),
    ));
    expect(new Set(signatures).size).toBe(50);
    for (const item of MOOD_GRID) {
      expect(Object.keys(MOOD_SHAPE_OVERRIDES[item.key]).length).toBeGreaterThanOrEqual(6);
    }
  });
});

describe("moodDirectionModifier", () => {
  it("names the dominant axis like the reference 「偏{label}」 wording", () => {
    expect(moodDirectionModifier(0, 0.8)).toBe("偏激动");
    expect(moodDirectionModifier(0, -0.8)).toBe("偏平静");
    expect(moodDirectionModifier(-0.8, 0)).toBe("偏疏离");
    expect(moodDirectionModifier(0.8, 0)).toBe("偏亲近");
    // 两轴同时偏离时取偏离更大的那一轴。
    expect(moodDirectionModifier(0.9, -0.3)).toBe("偏亲近");
    expect(moodDirectionModifier(0.3, -0.9)).toBe("偏平静");
  });

  it("stays silent inside the neutral dead zone and on bad input", () => {
    expect(moodDirectionModifier(0, 0)).toBe("");
    expect(moodDirectionModifier(0.18, -0.18)).toBe("");
    expect(moodDirectionModifier(0.19, 0)).toBe("偏亲近");
    expect(moodDirectionModifier(Number.NaN, 0.5)).toBe("");
    expect(moodDirectionModifier(0, Number.POSITIVE_INFINITY)).toBe("");
  });
});
