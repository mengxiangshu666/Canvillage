// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 3D 白模试衣的映射表：造型室里的发型 / 服饰 / 身材预设 → 3D 替身参数。
 *
 * 参考语料 libtv §20_语料/character_studio/00_INDEX.md（r76）里的造型室是
 * `midwam-human.glb` + Draco 的 3D 白模实时换装。我们没有那份模型的所有权，
 * 改用仓库已随包分发的 Quaternius UAL2 白模（CC0，导演台同一套替身），
 * 衣服与发型用低模图元做**替身**，颜色与形状即时可见，但不是生产服装资产。
 *
 * 纯数据与纯函数，无副作用；引擎在 studioApp.ts。
 */

export type StudioHairStyle =
  | "long_straight"
  | "sleek_shoulder"
  | "twin_tail"
  | "bob"
  | "bun"
  | "high_pony"
  | "waves"
  | "buzz";

export type StudioOutfitShape =
  | "long_dress"
  | "flared_dress"
  | "top_skirt"
  | "long_robe"
  | "suit"
  | "top_pants"
  | "uniform";

export type StudioSleeve = "none" | "short" | "long" | "wide";
export type StudioBottom = "none" | "pants" | "shorts" | "skirt" | "long_skirt";

export interface StudioHairSpec {
  style: StudioHairStyle;
  /** 发色（3D 预览用；文字描述仍以预设正文为准）。 */
  color: string;
  /** 后发长度，相对头半径；buzz 为 0。 */
  length: number;
}

export interface StudioOutfitSpec {
  style: StudioOutfitShape;
  color: string;
  sleeve: StudioSleeve;
  bottom: StudioBottom;
}

export interface StudioBodyProportions {
  /** 整体身高倍率。 */
  height: number;
  /** 整体胖瘦倍率（X/Z）。 */
  build: number;
  /** 头部倍率。 */
  head: number;
  /** 腿长倍率（大腿 + 小腿骨）。 */
  legs: number;
}

/** 造型室发型预设 id → 3D 发型。 */
export const HAIR_BY_PRESET: Record<string, StudioHairSpec> = {
  "hair-long-straight": { style: "long_straight", color: "#241d1a", length: 3.1 },
  "hair-sleek": { style: "sleek_shoulder", color: "#1f1a17", length: 1.9 },
  "hair-twin-tail": { style: "twin_tail", color: "#2a211c", length: 2.4 },
  "hair-bob": { style: "bob", color: "#2b2320", length: 1.1 },
  "hair-bun": { style: "bun", color: "#211b18", length: 0.6 },
  "hair-pony": { style: "high_pony", color: "#261f1b", length: 2.6 },
  "hair-waves": { style: "waves", color: "#31241d", length: 3.0 },
  "hair-buzz": { style: "buzz", color: "#1d1916", length: 0 },
};

/** 造型室服饰预设 id → 3D 服饰替身。 */
export const OUTFIT_BY_PRESET: Record<string, StudioOutfitSpec> = {
  "costume-qipao": {
    style: "long_dress",
    color: "#7f1d2d",
    sleeve: "short",
    bottom: "long_skirt",
  },
  "costume-lolita": {
    style: "flared_dress",
    color: "#e8d7e4",
    sleeve: "short",
    bottom: "skirt",
  },
  "costume-commute": {
    style: "top_skirt",
    color: "#3b4250",
    sleeve: "long",
    bottom: "skirt",
  },
  "costume-hanfu": {
    style: "long_robe",
    color: "#dfe6e4",
    sleeve: "wide",
    bottom: "long_skirt",
  },
  "costume-suit": {
    style: "suit",
    color: "#23262c",
    sleeve: "long",
    bottom: "pants",
  },
  "costume-sport": {
    style: "top_pants",
    color: "#2f6b5f",
    sleeve: "short",
    bottom: "pants",
  },
  "costume-uniform": {
    style: "uniform",
    color: "#2c3f5f",
    sleeve: "short",
    bottom: "skirt",
  },
  "costume-republic": {
    style: "long_robe",
    color: "#5b5146",
    sleeve: "long",
    bottom: "long_skirt",
  },
  "costume-techwear": {
    style: "top_pants",
    color: "#2a2f33",
    sleeve: "long",
    bottom: "pants",
  },
};

interface BodyDelta {
  height?: number;
  build?: number;
  head?: number;
  legs?: number;
}

/**
 * 身材预设 → 四个可实际驱动的比例轴。
 *
 * 参考产品能逐块雕形（瘦手臂/美臀/瘦脖）；我们的替身只暴露身高、胖瘦、
 * 头身比、腿长四条轴，其余预设按语义折算到最接近的轴，避免做得像又不像。
 */
const BODY_DELTAS: Record<string, BodyDelta> = {
  "body-taller": { height: 0.05 },
  "body-narrow-shoulder": { build: -0.02 },
  "body-long-legs": { legs: 0.07 },
  "body-small-head": { head: -0.07 },
  "body-slim": { build: -0.05 },
  "body-flat-belly": { build: -0.02 },
  "body-slim-arms": { build: -0.015 },
  "body-hip": { build: 0.02 },
  "body-slim-legs": { build: -0.02, legs: 0.01 },
  "body-neck": { height: 0.01, head: 0.01 },
};

export const DEFAULT_BODY_PROPORTIONS: StudioBodyProportions = {
  height: 1,
  build: 1,
  head: 1,
  legs: 1,
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** 叠加所有已选身材预设，并夹到不会把替身拉变形的范围。 */
export function bodyProportionsFromPresets(
  presetIds: readonly string[],
): StudioBodyProportions {
  let height = 1;
  let build = 1;
  let head = 1;
  let legs = 1;
  for (const id of presetIds) {
    const delta = BODY_DELTAS[id];
    if (!delta) continue;
    height += delta.height ?? 0;
    build += delta.build ?? 0;
    head += delta.head ?? 0;
    legs += delta.legs ?? 0;
  }
  return {
    height: clamp(height, 0.9, 1.1),
    build: clamp(build, 0.85, 1.08),
    head: clamp(head, 0.88, 1.06),
    legs: clamp(legs, 0.94, 1.1),
  };
}

/**
 * 身材滑杆（-100~100，0 为中性）→ 四条比例轴。
 *
 * 参考产品是逐块骨骼雕形；我们的替身只暴露身高 / 胖瘦 / 头身比 / 腿长
 * 四条轴，滑杆按语义折算到最近的轴，数值按比例叠加。
 */
export function bodyProportionsFromSliders(
  values: Readonly<Record<string, number>>,
): StudioBodyProportions {
  const v = (id: string) => (values[id] ?? 0) / 100;
  const height = 1 + v("body-taller") * 0.05 + v("body-neck") * 0.01;
  const build =
    1 +
    v("body-slim") * 0.05 +
    v("body-narrow-shoulder") * 0.02 +
    v("body-flat-belly") * 0.02 +
    v("body-slim-arms") * 0.015 +
    v("body-hip") * 0.02 +
    v("body-slim-legs") * 0.02;
  const head = 1 + v("body-small-head") * 0.07 + v("body-neck") * 0.01;
  const legs = 1 + v("body-long-legs") * 0.07 + v("body-slim-legs") * 0.01;
  return {
    height: clamp(height, 0.9, 1.1),
    build: clamp(build, 0.85, 1.08),
    head: clamp(head, 0.88, 1.06),
    legs: clamp(legs, 0.94, 1.1),
  };
}

/** 造型目录枚举名 → 3D 发型；没有可信映射时返回 null，只写文字不换 3D。 */
export function hairSpecFromEnum(enumName: string): StudioHairSpec | null {
  const base: Record<string, StudioHairStyle> = {
    LongStraight: "long_straight",
    BigWaves: "waves",
    BobCut: "bob",
    BoysBuzzCut: "buzz",
    BuzzCut: "buzz",
    TwinTails: "twin_tail",
    HighPonytail: "high_pony",
    LowPonytail: "high_pony",
    HalfUp: "sleek_shoulder",
    ShoulderLength: "bob",
    Bun: "bun",
    HighBun: "bun",
  };
  const style = base[enumName];
  if (!style) return null;
  return { style, color: "#241d1a", length: style === "buzz" ? 0 : 2.4 };
}

/** 造型目录枚举名 → 3D 服饰替身；按关键词猜最接近的轮廓，猜不中退 null。 */
export function outfitSpecFromEnum(enumName: string): StudioOutfitSpec | null {
  const name = enumName.toLowerCase();
  if (name.includes("lolita")) {
    return { style: "flared_dress", color: "#e8d7e4", sleeve: "short", bottom: "skirt" };
  }
  if (name.includes("suit") || name.includes("business")) {
    return { style: "suit", color: "#23262c", sleeve: "long", bottom: "pants" };
  }
  if (name.includes("qipao")) {
    return { style: "long_dress", color: "#7f1d2d", sleeve: "short", bottom: "long_skirt" };
  }
  if (name.includes("sport")) {
    return { style: "top_pants", color: "#2f6b5f", sleeve: "short", bottom: "pants" };
  }
  if (name.includes("uniform") || name.includes("school")) {
    return { style: "uniform", color: "#2c3f5f", sleeve: "short", bottom: "skirt" };
  }
  if (name.includes("robe") || name.includes("hanfu")) {
    return { style: "long_robe", color: "#dfe6e4", sleeve: "wide", bottom: "long_skirt" };
  }
  if (name.includes("dress") || name.includes("gown")) {
    return { style: "long_dress", color: "#8a2430", sleeve: "short", bottom: "long_skirt" };
  }
  if (name.includes("commute") || name.includes("casual")) {
    return { style: "top_skirt", color: "#3b4250", sleeve: "long", bottom: "skirt" };
  }
  return null;
}

/** 当前高亮的第一条发型/服饰预设（同组只取一条，避免两套衣服叠在一起）。 */
export function activeHairFor(activePresetIds: readonly string[]): StudioHairSpec | null {
  for (const id of activePresetIds) {
    const spec = HAIR_BY_PRESET[id];
    if (spec) return spec;
  }
  return null;
}

export function activeOutfitFor(activePresetIds: readonly string[]): StudioOutfitSpec | null {
  for (const id of activePresetIds) {
    const spec = OUTFIT_BY_PRESET[id];
    if (spec) return spec;
  }
  return null;
}
