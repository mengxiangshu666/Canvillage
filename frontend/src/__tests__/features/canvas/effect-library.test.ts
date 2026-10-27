// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { existsSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  EFFECT_CATEGORIES,
  EFFECT_PRESETS,
  EFFECT_PREVIEW_TEMPLATE_IDS,
  effectPreviewTemplateId,
  filterEffectPresets,
} from "@/features/canvas/domain/effectPresets";
import {
  EFFECT_TEMPLATES,
  filterEffectTemplates,
} from "@/features/canvas/domain/effectTemplates";
import {
  clearEffectDirection,
  effectDirectionText,
  hasEffectDirection,
  setEffectDirection,
} from "@/features/canvas/domain/promptEffects";
import { splitPromptSegmentChunks } from "@/features/canvas/domain/promptSegments";

const FLAME = "赤红烈焰从主体边缘燃起并向上翻卷";
const RAIN = "密集雨丝斜向砸落，地面溅起水花";

const SHOT_PROMPT = [
  "[画面构图：全景]",
  "[角色卡：[阿波：红夹克]]",
  "[主体/人物空间：站在街口]",
  "[微表情：抬眼]",
  "[场景环境：雨夜街口]",
  "[光影几何：冷蓝窗光]",
  "[视觉风格：写实]",
  "[技术参数：35mm]",
].join(" + ");

const MOTION_PROMPT = [
  "[摄影机运镜：固定机位]",
  "[主体物理动作：抬拳]",
  "[环境物理动态：雨丝斜落]",
  "[音效与氛围：雨声]",
  "[对话台词：无]",
  "[时长：4s]",
].join(" + ");

describe("特效库预设", () => {
  it("id 唯一、正文非空、分类都有登记", () => {
    const ids = EFFECT_PRESETS.map((preset) => preset.id);
    expect(new Set(ids).size).toBe(ids.length);
    const categoryIds = new Set(EFFECT_CATEGORIES.map((category) => category.id));
    for (const preset of EFFECT_PRESETS) {
      expect(preset.promptFragment.trim().length).toBeGreaterThan(0);
      expect(preset.label.trim().length).toBeGreaterThan(0);
      expect(categoryIds.has(preset.category)).toBe(true);
      expect(preset.keywords.length).toBeGreaterThan(0);
    }
  });

  it("覆盖平台特效库的六个空壳标签", () => {
    const labels = EFFECT_PRESETS.map((preset) => preset.label);
    for (const label of ["火焰缠身", "金钱雨", "传送门", "粒子消散", "3D 旋转", "瞬间换装"]) {
      expect(labels).toContain(label);
    }
  });

  it("每条画面特效都有可用的示意预览", () => {
    const templateIds = new Set(EFFECT_TEMPLATES.map((template) => template.id));
    const presetIds = new Set(EFFECT_PRESETS.map((preset) => preset.id));
    for (const preset of EFFECT_PRESETS) {
      const templateId = effectPreviewTemplateId(preset.id);
      expect(templateId, preset.id).toBeTruthy();
      expect(templateIds.has(templateId as string), `${preset.id} -> ${templateId}`).toBe(
        true,
      );
    }
    for (const key of Object.keys(EFFECT_PREVIEW_TEMPLATE_IDS)) {
      expect(presetIds.has(key), key).toBe(true);
    }
  });

  it("搜索命中别名、分类名与多词组合", () => {
    expect(filterEffectPresets(EFFECT_PRESETS, "火焰").map((item) => item.id)).toContain(
      "flame-on",
    );
    expect(filterEffectPresets(EFFECT_PRESETS, "仙侠 火").map((item) => item.id)).toContain(
      "fire-lotus",
    );
    expect(filterEffectPresets(EFFECT_PRESETS, "电商").length).toBeGreaterThan(0);
    expect(filterEffectPresets(EFFECT_PRESETS, "不存在的词")).toHaveLength(0);
  });
});

describe("镜头模板", () => {
  it("49 条模板都有本地封面和动图", () => {
    expect(EFFECT_TEMPLATES).toHaveLength(49);
    const ids = EFFECT_TEMPLATES.map((template) => template.id);
    expect(new Set(ids).size).toBe(ids.length);
    // 预览素材摘自 libtv，作者保留权利，公开源码包按 .gitattributes 排除；
    // 素材不在时只校验目录契约，不把「没有素材」当成缺陷。
    const mediaRoot = path.resolve(process.cwd(), "public");
    const hasMedia = existsSync(path.join(mediaRoot, "effects", "templates"));
    for (const template of EFFECT_TEMPLATES) {
      if (hasMedia) {
        for (const url of [template.posterUrl, template.videoUrl]) {
          const file = path.resolve(process.cwd(), "public", url.replace(/^\//, ""));
          expect(existsSync(file), file).toBe(true);
          expect(statSync(file).size, file).toBeGreaterThan(500);
        }
      }
      expect(template.promptFragment.trim().length).toBeGreaterThan(0);
    }
  });

  it("搜索命中模板名与描述", () => {
    expect(filterEffectTemplates(EFFECT_TEMPLATES, "机甲").map((item) => item.id)).toContain(
      "23043917",
    );
    expect(filterEffectTemplates(EFFECT_TEMPLATES, "产品 扫光").length).toBeGreaterThan(0);
    expect(filterEffectTemplates(EFFECT_TEMPLATES, "不存在的模板")).toHaveLength(0);
  });
});

describe("特效写入提示词", () => {
  it("自由提示词新增 [特效] 段，重复点选不堆字", () => {
    const once = setEffectDirection("雨夜街头", FLAME);
    expect(once).toBe(`雨夜街头 + [特效] ${FLAME}`);
    expect(setEffectDirection(once, FLAME)).toBe(once);
    expect(effectDirectionText(once)).toBe(FLAME);
  });

  it("清除后回到原文", () => {
    const next = setEffectDirection("雨夜街头", FLAME);
    expect(clearEffectDirection(next)).toBe("雨夜街头");
    expect(hasEffectDirection(clearEffectDirection(next))).toBe(false);
  });

  it("八段分镜保持八段，特效折进光影几何段", () => {
    const next = setEffectDirection(SHOT_PROMPT, FLAME);
    const chunks = splitPromptSegmentChunks(next, true);

    expect(chunks).toHaveLength(8);
    expect(chunks[5]).toContain("冷蓝窗光");
    expect(chunks[5]).toContain(`特效：${FLAME}`);
    expect(next).not.toContain("[特效]");
  });

  it("换一条特效只替换标记正文，不叠加", () => {
    const first = setEffectDirection(SHOT_PROMPT, FLAME);
    const second = setEffectDirection(first, RAIN);

    expect(second).toContain(RAIN);
    expect(second).not.toContain(FLAME);
    expect(second.match(/特效：/g)).toHaveLength(1);
    expect(splitPromptSegmentChunks(second, true)).toHaveLength(8);
  });

  it("六段运动稿保持六段，特效折进环境物理动态段", () => {
    const next = setEffectDirection(MOTION_PROMPT, RAIN);
    const chunks = splitPromptSegmentChunks(next, true);

    expect(chunks).toHaveLength(6);
    expect(chunks[2]).toContain("雨丝斜落");
    expect(chunks[2]).toContain(`特效：${RAIN}`);
  });

  it("写入和清除都保留视频资产引用块", () => {
    const reference = "[视频资产引用：#1 主角]";
    const withReference = `${reference}\n${SHOT_PROMPT}`;
    const next = setEffectDirection(withReference, FLAME);
    expect(next.startsWith(reference)).toBe(true);
    expect(clearEffectDirection(next).startsWith(reference)).toBe(true);
    expect(clearEffectDirection(next)).toContain(SHOT_PROMPT);
  });
});
