// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  BODY_SLIDERS,
  CHARACTER_STUDIO_PRESET_GROUPS,
  SKIN_SLIDERS,
  applyTaggedClause,
  composeSliderClause,
  hasStudioPreset,
  readTaggedClause,
  toggleStudioPreset,
} from "@/features/canvas/domain/characterStudioPresets";

function preset(groupId: string, presetId: string) {
  const group = CHARACTER_STUDIO_PRESET_GROUPS.find((item) => item.id === groupId);
  const found = group?.presets.find((item) => item.id === presetId);
  if (!found) throw new Error(`missing preset ${groupId}/${presetId}`);
  return found;
}

describe("角色造型室预设", () => {
  it("预设按语料分五组，组内条数与 r76 对齐（生成是第六个页签）", () => {
    expect(CHARACTER_STUDIO_PRESET_GROUPS.map((group) => group.id)).toEqual([
      "skin",
      "body",
      "makeup",
      "hair",
      "costume",
    ]);
    expect(
      Object.fromEntries(
        CHARACTER_STUDIO_PRESET_GROUPS.map((group) => [group.id, group.presets.length]),
      ),
    ).toEqual({ skin: 7, body: 10, makeup: 6, hair: 8, costume: 9 });
  });

  it("点选写入、再点移除，分隔符随之收敛", () => {
    const qipao = preset("costume", "costume-qipao");

    const once = toggleStudioPreset("深灰长风衣", qipao);
    expect(once).toBe("深灰长风衣，立领盘扣旗袍，剪裁贴身");
    expect(hasStudioPreset(once, qipao)).toBe(true);

    expect(toggleStudioPreset(once, qipao)).toBe("深灰长风衣");
    expect(hasStudioPreset("深灰长风衣", qipao)).toBe(false);
  });

  it("空字段与重复点选不会堆出多余标点", () => {
    const skin = preset("skin", "skin-smooth");

    expect(toggleStudioPreset("", skin)).toBe("皮肤细腻平滑，质感干净");
    expect(toggleStudioPreset("，  ", skin)).toBe("皮肤细腻平滑，质感干净");
    expect(toggleStudioPreset("皮肤细腻平滑，质感干净", skin)).toBe("");
  });

  it("手工文字原样保留，只删预设片段", () => {
    const taller = preset("body", "body-taller");

    expect(toggleStudioPreset("纤细，身手矫健", taller)).toBe("纤细，身手矫健，身形高挑");
    expect(toggleStudioPreset("纤细，身手矫健，身形高挑，爱笑", taller)).toBe(
      "纤细，身手矫健，爱笑",
    );
  });

  it("滑杆组条数与量程对齐 r79 DOM 实证：皮肤 7 条 0–100、身材 10 条 -100~100", () => {
    expect(SKIN_SLIDERS).toHaveLength(7);
    expect(SKIN_SLIDERS.map((slider) => [slider.min, slider.max])).toEqual(
      Array.from({ length: 7 }, () => [0, 100]),
    );
    expect(SKIN_SLIDERS.map((slider) => slider.label)).toEqual([
      "粗糙皮肤",
      "AI 美肌",
      "皮肤纹理",
      "皮肤透亮",
      "皮肤红润",
      "皮肤美白",
      "色斑祛斑",
    ]);
    expect(BODY_SLIDERS).toHaveLength(10);
    for (const slider of BODY_SLIDERS) {
      expect(slider.min).toBe(-100);
      expect(slider.max).toBe(100);
      expect(slider.negative).toBeTruthy();
    }
  });

  it("滑杆折成标签段：0 不写入、负值用 negative、同名段整体替换", () => {
    const skinClause = composeSliderClause("皮肤", SKIN_SLIDERS, {
      "skin-ai-smooth": 60,
      "skin-rough": 0,
    });
    expect(skinClause).toBe("皮肤：AI 美肌，皮肤细腻平滑60%");

    const bodyClause = composeSliderClause("身材", BODY_SLIDERS, {
      "body-taller": 80,
      "body-slim": -40,
    });
    expect(bodyClause).toBe("身材：身形高挑80%，身形丰润40%");

    const applied = applyTaggedClause("鹅蛋脸，柳叶眉", "皮肤", skinClause);
    expect(applied).toBe("鹅蛋脸，柳叶眉；皮肤：AI 美肌，皮肤细腻平滑60%");
    expect(readTaggedClause(applied, "皮肤")).toBe("AI 美肌，皮肤细腻平滑60%");
    expect(applyTaggedClause(applied, "皮肤", "")).toBe("鹅蛋脸，柳叶眉");
    expect(readTaggedClause("鹅蛋脸，柳叶眉", "皮肤")).toBe("");
  });
});
