// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { CHARACTER_STUDIO_PRESET_GROUPS } from "@/features/canvas/domain/characterStudioPresets";
import {
  HAIR_BY_PRESET,
  OUTFIT_BY_PRESET,
  DEFAULT_BODY_PROPORTIONS,
  activeHairFor,
  activeOutfitFor,
  bodyProportionsFromPresets,
  bodyProportionsFromSliders,
  hairSpecFromEnum,
  outfitSpecFromEnum,
} from "@/features/canvas/domain/characterStudio3dSpecs";

function groupPresetIds(groupId: string): string[] {
  const group = CHARACTER_STUDIO_PRESET_GROUPS.find((item) => item.id === groupId);
  return group?.presets.map((preset) => preset.id) ?? [];
}

describe("3D 白模试衣映射", () => {
  it("每条发型与服饰预设都有 3D 替身，新增预设不会静默掉队", () => {
    expect(groupPresetIds("hair").every((id) => Boolean(HAIR_BY_PRESET[id]))).toBe(true);
    expect(groupPresetIds("costume").every((id) => Boolean(OUTFIT_BY_PRESET[id]))).toBe(true);
  });

  it("发型与服饰默认值够用（颜色、长度、领型）", () => {
    for (const spec of Object.values(HAIR_BY_PRESET)) {
      expect(spec.color).toMatch(/^#[0-9a-f]{6}$/i);
      expect(spec.length).toBeGreaterThanOrEqual(0);
    }
    for (const spec of Object.values(OUTFIT_BY_PRESET)) {
      expect(spec.color).toMatch(/^#[0-9a-f]{6}$/i);
      expect(["none", "short", "long", "wide"]).toContain(spec.sleeve);
    }
  });

  it("身材预设按四条比例轴叠加，并夹在不会拉爆的范围内", () => {
    expect(bodyProportionsFromPresets([])).toEqual(DEFAULT_BODY_PROPORTIONS);

    const tall = bodyProportionsFromPresets(["body-taller"]);
    expect(tall.height).toBeCloseTo(1.05, 5);

    const stacked = bodyProportionsFromPresets([
      "body-taller",
      "body-long-legs",
      "body-small-head",
      "body-slim",
    ]);
    expect(stacked.height).toBeCloseTo(1.05, 5);
    expect(stacked.legs).toBeCloseTo(1.07, 5);
    expect(stacked.head).toBeCloseTo(0.93, 5);
    expect(stacked.build).toBeCloseTo(0.95, 5);

    const clamped = bodyProportionsFromPresets([
      "body-taller",
      "body-taller",
      "body-taller",
      "body-slim",
      "body-slim",
      "body-slim",
      "body-small-head",
      "body-small-head",
    ]);
    expect(clamped.height).toBeLessThanOrEqual(1.1);
    expect(clamped.build).toBeGreaterThanOrEqual(0.85);
    expect(clamped.head).toBeGreaterThanOrEqual(0.88);
  });

  it("同组只取第一条生效，未知 id 直接忽略", () => {
    expect(activeHairFor(["unknown", "hair-bob"])?.style).toBe("bob");
    expect(
      activeHairFor(["hair-bob", "hair-waves"])?.style,
    ).toBe("bob");
    expect(activeOutfitFor(["costume-hanfu", "costume-suit"])?.style).toBe(
      "long_robe",
    );
    expect(activeHairFor(["unknown"])).toBeNull();
    expect(activeOutfitFor([])).toBeNull();
  });

  it("身材滑杆（±100）驱动四条比例轴并夹在安全范围", () => {
    expect(bodyProportionsFromSliders({})).toEqual(DEFAULT_BODY_PROPORTIONS);

    const tall = bodyProportionsFromSliders({ "body-taller": 100 });
    expect(tall.height).toBeCloseTo(1.05, 5);

    const shortAndWide = bodyProportionsFromSliders({
      "body-taller": -100,
      "body-slim": -100,
    });
    expect(shortAndWide.height).toBeCloseTo(0.95, 5);
    expect(shortAndWide.build).toBeCloseTo(0.95, 5);

    const clamped = bodyProportionsFromSliders({
      "body-taller": 100,
      "body-small-head": -100,
      "body-long-legs": 100,
    });
    expect(clamped.height).toBeLessThanOrEqual(1.1);
    expect(clamped.head).toBeGreaterThanOrEqual(0.88);
    expect(clamped.legs).toBeLessThanOrEqual(1.1);
  });

  it("造型目录枚举名映射 3D 替身，映射不中退 null 不乱穿", () => {
    expect(hairSpecFromEnum("LongStraight")?.style).toBe("long_straight");
    expect(hairSpecFromEnum("BobCut")?.style).toBe("bob");
    expect(hairSpecFromEnum("TotallyUnknown")).toBeNull();

    expect(outfitSpecFromEnum("QipaoDress1")?.style).toBe("long_dress");
    expect(outfitSpecFromEnum("BusinessSuit1")?.style).toBe("suit");
    expect(outfitSpecFromEnum("Sportswear2")?.style).toBe("top_pants");
    expect(outfitSpecFromEnum("NothingKnown")).toBeNull();
  });
});
