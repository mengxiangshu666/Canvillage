// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const official = JSON.parse(
  readFileSync("public/data/character-studio/officialCharacters.json", "utf-8"),
);
const styling = JSON.parse(
  readFileSync("public/data/character-studio/stylingCatalog.json", "utf-8"),
);
const voice = JSON.parse(
  readFileSync("public/data/character-studio/voiceLibrary.json", "utf-8"),
);

function countBy(items: Array<Record<string, unknown>>, key: string) {
  const counts: Record<string, number> = {};
  for (const item of items) {
    const value = String(item[key]);
    counts[value] = (counts[value] ?? 0) + 1;
  }
  return counts;
}

describe("角色造型室静态数据", () => {
  it("官方角色库是 23 条，六类分布对齐 libtv r76 实测", () => {
    expect(official.items).toHaveLength(23);
    expect(countBy(official.items, "categoryCode")).toEqual({
      fashion_role: 8,
      supporting: 5,
      male_lead: 3,
      female_lead: 3,
      general_people: 2,
      children: 2,
    });
    expect(countBy(official.items, "gender")).toEqual({ 女: 13, 男: 10 });
    expect(countBy(official.items, "era")).toEqual({ 现代: 21, 古代: 2 });
    expect(countBy(official.items, "region")).toEqual({ 东亚: 20, 欧美: 3 });
    for (const item of official.items) {
      expect(item.shortDescription).toBeTruthy();
      expect(item.appearanceDescription).toBeTruthy();
      expect(item.personalityDescription).toBeTruthy();
      expect(item.sceneHint).toBeTruthy();
    }
  });

  it("造型目录 226 条来自 libtv r79 静态注册表，只含目录字段", () => {
    expect(styling.items).toHaveLength(226);
    expect(countBy(styling.items, "kind")).toEqual({ hair: 63, outfit: 112, makeup: 51 });
    for (const item of styling.items) {
      expect(item.name).toBeTruthy();
      expect(item.key).toBeTruthy();
      expect(item.enum).toBeTruthy();
      expect(JSON.stringify(item)).not.toContain("liblib");
      expect(JSON.stringify(item)).not.toContain("https://");
    }
  });

  it("音色库 327 条只保留 voiceId 与中文名，不带平台音频 URL", () => {
    expect(voice.items).toHaveLength(327);
    expect(countBy(voice.items, "gender")).toEqual({ female: 164, male: 163 });
    for (const item of voice.items) {
      expect(item.voiceId).toBeTruthy();
      expect(item.name).toBeTruthy();
      expect(JSON.stringify(item)).not.toContain("liblib");
      expect(JSON.stringify(item)).not.toContain("https://");
    }
  });
});
