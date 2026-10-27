// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 角色造型室的预设库：皮肤 / 身材 / 妆造 / 造型（发型）/ 服饰。
 *
 * 参考语料 libtv §20_语料/character_studio/00_INDEX.md（r76，2026-10-27）里
 * 造型室是 3D 白模 + 真实换装；本仓没有 3D 资产，所以这里只复刻**同一套参数
 * 分组**，每条预设落到已有的文本字段上（面部锚点 / 体型 / 服饰造型），
 * 不伪造换装效果。预设正文是提示词片段，不是素材。
 *
 * 纯数据 + 纯函数，无副作用。
 */

export type StudioPresetTarget = "face" | "body" | "appearance";

export type StudioPresetAudience = "female" | "male" | "other" | "limited";

export interface StudioPreset {
  id: string;
  label: string;
  /** 写进目标字段的描述，同一字段可叠加多条。 */
  prompt: string;
  /** 筛选分类；参考产品的发型/服饰库按人群分栏。 */
  audience?: StudioPresetAudience;
}

export interface StudioPresetGroup {
  id: "skin" | "body" | "makeup" | "hair" | "costume";
  label: string;
  target: StudioPresetTarget;
  presets: StudioPreset[];
}

export const CHARACTER_STUDIO_PRESET_GROUPS: StudioPresetGroup[] = [
  {
    id: "skin",
    label: "皮肤",
    target: "face",
    presets: [
      { id: "skin-texture", label: "真实纹理", prompt: "保留真实皮肤纹理与细微毛孔" },
      { id: "skin-smooth", label: "AI 美肌", prompt: "皮肤细腻平滑，质感干净" },
      { id: "skin-photo", label: "摄影质感", prompt: "皮肤纹理清晰，真实摄影质感" },
      { id: "skin-translucent", label: "皮肤透亮", prompt: "皮肤通透有光泽" },
      { id: "skin-rosy", label: "皮肤红润", prompt: "肤色红润健康" },
      { id: "skin-fair", label: "皮肤美白", prompt: "肤色白皙均匀" },
      { id: "skin-clear", label: "色斑祛斑", prompt: "肤色干净无斑点" },
    ],
  },
  {
    id: "body",
    label: "身材",
    target: "body",
    presets: [
      { id: "body-taller", label: "AI 增高", prompt: "身形高挑" },
      { id: "body-narrow-shoulder", label: "缩肩", prompt: "肩线收窄" },
      { id: "body-long-legs", label: "长腿", prompt: "腿部修长，比例匀称" },
      { id: "body-small-head", label: "小头", prompt: "头部比例偏小，头身比修长" },
      { id: "body-slim", label: "瘦身", prompt: "身形纤瘦" },
      { id: "body-flat-belly", label: "瘦肚子", prompt: "腹部平坦" },
      { id: "body-slim-arms", label: "瘦手臂", prompt: "手臂纤细" },
      { id: "body-hip", label: "美臀", prompt: "臀线饱满" },
      { id: "body-slim-legs", label: "瘦腿", prompt: "双腿纤细" },
      { id: "body-neck", label: "瘦脖", prompt: "颈部修长" },
    ],
  },
  {
    id: "makeup",
    label: "妆造",
    target: "face",
    presets: [
      { id: "makeup-bare", label: "清透素颜", prompt: "妆容清透自然，近似素颜" },
      { id: "makeup-peach", label: "白桃气色", prompt: "肤色白皙，颊部淡粉" },
      { id: "makeup-retro", label: "复古红唇", prompt: "眼妆克制，复古红唇" },
      { id: "makeup-cool", label: "冷调眼妆", prompt: "冷调眼妆，眉眼清晰" },
      { id: "makeup-warm", label: "暖调气色", prompt: "暖调妆容，气色红润" },
      { id: "makeup-smokey", label: "加深轮廓", prompt: "眼影加深，轮廓立体" },
    ],
  },
  {
    id: "hair",
    label: "造型",
    target: "appearance",
    presets: [
      { id: "hair-long-straight", audience: "female", label: "长直发", prompt: "长直发，自然垂落" },
      { id: "hair-sleek", audience: "female", label: "无肩精致发", prompt: "利落无肩中长发，发丝服帖" },
      { id: "hair-twin-tail", audience: "female", label: "双马尾", prompt: "双马尾，发梢微卷" },
      { id: "hair-bob", audience: "female", label: "齐肩短发", prompt: "齐肩短发，发尾内扣" },
      { id: "hair-bun", audience: "female", label: "低盘发", prompt: "低盘发，发髻整洁" },
      { id: "hair-pony", audience: "female", label: "高马尾", prompt: "高马尾，发丝利落" },
      { id: "hair-waves", audience: "female", label: "长卷发", prompt: "长卷发，波浪层次" },
      { id: "hair-buzz", audience: "male", label: "短寸头", prompt: "短寸头，发际线干净" },
    ],
  },
  {
    id: "costume",
    label: "服饰",
    target: "appearance",
    presets: [
      { id: "costume-qipao", audience: "female", label: "旗袍", prompt: "立领盘扣旗袍，剪裁贴身" },
      { id: "costume-lolita", audience: "female", label: "洛丽塔", prompt: "洛丽塔裙装，荷叶边与蝴蝶结" },
      { id: "costume-commute", audience: "other", label: "通勤", prompt: "通勤套装，简洁利落" },
      { id: "costume-hanfu", audience: "other", label: "汉服", prompt: "交领汉服，宽袖垂坠" },
      { id: "costume-suit", audience: "other", label: "西装", prompt: "深色西装套装" },
      { id: "costume-sport", audience: "other", label: "运动", prompt: "运动套装，面料轻便" },
      { id: "costume-uniform", audience: "other", label: "校服", prompt: "制式校服套装" },
      { id: "costume-republic", audience: "other", label: "民国长衫", prompt: "民国长衫，布面素净" },
      { id: "costume-techwear", audience: "other", label: "机能风", prompt: "机能风外套，束带与多口袋" },
    ],
  },
];

/** 预设是否已经在这个字段里（用于高亮与再次点击移除）。 */export function hasStudioPreset(text: string | null | undefined, preset: StudioPreset): boolean {
  const source = typeof text === "string" ? text : "";
  return source.includes(preset.prompt);
}

function collapseFragments(text: string): string {
  return text
    .replace(/\s*[，,；;、]\s*(?=[，,；;、])/g, "")
    .replace(/^[\s，,；;、]+/, "")
    .replace(/[\s，,；;、]+$/, "")
    .trim();
}

/**
 * 把一条预设写进字段或从字段里移除。
 *
 * 预设正文是完整短语，移除时按原串删除并收敛分隔符；手工改过的文字保持原样。
 */
export function toggleStudioPreset(
  text: string | null | undefined,
  preset: StudioPreset,
): string {
  const source = typeof text === "string" ? text : "";
  if (hasStudioPreset(source, preset)) {
    return collapseFragments(source.replace(preset.prompt, ""));
  }
  const base = collapseFragments(source);
  return base ? `${base}，${preset.prompt}` : preset.prompt;
}

// ———— 皮肤 / 身材滑杆（libtv r79 DOM 直读实证：皮肤 0–100、身材 -100~100，step 1） ————

export interface StudioSliderDef {
  id: string;
  label: string;
  /** 正向拨动时写进字段的描述；身材滑杆是 ± 双向，负向用 negative。 */
  prompt: string;
  negative?: string;
  min: number;
  max: number;
}

/** 皮肤 tab 的 7 条滑杆，量程 0–100。 */
export const SKIN_SLIDERS: StudioSliderDef[] = [
  { id: "skin-rough", label: "粗糙皮肤", prompt: "皮肤粗糙真实颗粒感", min: 0, max: 100 },
  { id: "skin-ai-smooth", label: "AI 美肌", prompt: "AI 美肌，皮肤细腻平滑", min: 0, max: 100 },
  { id: "skin-texture", label: "皮肤纹理", prompt: "皮肤纹理清晰自然", min: 0, max: 100 },
  { id: "skin-translucent", label: "皮肤透亮", prompt: "皮肤通透有光泽", min: 0, max: 100 },
  { id: "skin-rosy", label: "皮肤红润", prompt: "肤色红润健康", min: 0, max: 100 },
  { id: "skin-fair", label: "皮肤美白", prompt: "肤色白皙均匀", min: 0, max: 100 },
  { id: "skin-spot-clear", label: "色斑祛斑", prompt: "肤色干净无斑点", min: 0, max: 100 },
];

/** 身材 tab 的 10 条 ± 双向滑杆，量程 -100~100，0 为中性。 */
export const BODY_SLIDERS: StudioSliderDef[] = [
  { id: "body-taller", label: "AI 增高", prompt: "身形高挑", negative: "身形偏矮", min: -100, max: 100 },
  { id: "body-narrow-shoulder", label: "缩肩", prompt: "肩线收窄", negative: "肩线舒展", min: -100, max: 100 },
  { id: "body-long-legs", label: "长腿", prompt: "腿部修长", negative: "腿部偏短", min: -100, max: 100 },
  { id: "body-small-head", label: "小头", prompt: "头部比例偏小，头身比修长", negative: "头部比例饱满", min: -100, max: 100 },
  { id: "body-slim", label: "瘦身", prompt: "身形纤瘦", negative: "身形丰润", min: -100, max: 100 },
  { id: "body-flat-belly", label: "瘦肚子", prompt: "腹部平坦紧致", negative: "腹部圆润", min: -100, max: 100 },
  { id: "body-slim-arms", label: "瘦手臂", prompt: "手臂纤细", negative: "手臂饱满", min: -100, max: 100 },
  { id: "body-hip", label: "美臀", prompt: "臀线饱满上提", negative: "臀线收敛", min: -100, max: 100 },
  { id: "body-slim-legs", label: "瘦腿", prompt: "双腿纤细", negative: "双腿健实", min: -100, max: 100 },
  { id: "body-neck", label: "瘦脖子", prompt: "颈部修长", negative: "颈部厚实", min: -100, max: 100 },
];

/** 把一组滑杆的取值折成一段「<前缀>：a，b」描述；0/中性项不写入。 */
export function composeSliderClause(
  prefix: string,
  sliders: ReadonlyArray<StudioSliderDef>,
  values: Readonly<Record<string, number>>,
): string {
  const parts = sliders
    .map((slider) => {
      const value = values[slider.id] ?? 0;
      if (!value) return null;
      const text = value > 0 || !slider.negative ? slider.prompt : slider.negative;
      return `${text}${Math.abs(value)}%`;
    })
    .filter(Boolean);
  if (!parts.length) return "";
  return `${prefix}：${parts.join("，")}`;
}

function taggedClauseRe(prefix: string): RegExp {
  return new RegExp(`(?:^|[；;])\\s*${prefix}[:：][^；;]*`, "g");
}

/** 用新的「<前缀>：…」段替换字段里旧的同名段，其余手工文字保持不变。 */
export function applyTaggedClause(
  text: string | null | undefined,
  prefix: string,
  clause: string,
): string {
  const source = typeof text === "string" ? text : "";
  const base = source
    .replace(taggedClauseRe(prefix), "")
    .replace(/^[；;\s]+|[；;\s]+$/g, "");
  if (!clause.trim()) return base;
  return base ? `${base}；${clause}` : clause;
}

/** 从字段里读回「<前缀>：…」段；没有返回空串。 */
export function readTaggedClause(
  text: string | null | undefined,
  prefix: string,
): string {
  const source = typeof text === "string" ? text : "";
  let found = "";
  for (const match of source.matchAll(taggedClauseRe(prefix))) {
    found = match[0]
      .replace(/^[；;]\s*/, "")
      .replace(new RegExp(`^${prefix}[:：]\\s*`), "")
      .trim();
  }
  return found;
}

// ———— 妆造色板（参考产品：肤色 / 眼妆 / 唇色 三行预设 + 三个滑杆） ————

export interface MakeupSwatch {
  id: string;
  label: string;
  /** 色板显示色，也是写进提示词的描述依据。 */
  color: string;
  prompt: string;
}

export const MAKEUP_SWATCH_ROWS: Array<{
  id: "skin" | "eye" | "lip";
  label: string;
  swatches: MakeupSwatch[];
}> = [
  {
    id: "skin",
    label: "肤色",
    swatches: [
      { id: "porcelain", label: "瓷白", color: "#f2e4d6", prompt: "肤色瓷白" },
      { id: "natural", label: "自然", color: "#e7c9ac", prompt: "肤色自然" },
      { id: "honey", label: "蜜色", color: "#d8a878", prompt: "肤色蜜色" },
      { id: "wheat", label: "小麦", color: "#c08a58", prompt: "肤色小麦" },
      { id: "tan", label: "深棕", color: "#9a6a43", prompt: "肤色深棕" },
      { id: "cool", label: "冷白", color: "#f4ece8", prompt: "肤色冷白" },
    ],
  },
  {
    id: "eye",
    label: "眼妆",
    swatches: [
      { id: "nude", label: "裸棕", color: "#a98268", prompt: "眼妆裸棕" },
      { id: "warm", label: "暖橘", color: "#c97a52", prompt: "眼妆暖橘" },
      { id: "rose", label: "玫红", color: "#b65368", prompt: "眼妆玫红" },
      { id: "smoke", label: "烟灰", color: "#6d6a70", prompt: "眼妆烟灰" },
      { id: "wine", label: "酒红", color: "#7a2f3a", prompt: "眼妆酒红" },
      { id: "blue", label: "蓝调", color: "#4a5c78", prompt: "眼妆蓝调" },
    ],
  },
  {
    id: "lip",
    label: "唇色",
    swatches: [
      { id: "bean", label: "豆沙", color: "#a45b58", prompt: "唇色豆沙" },
      { id: "red", label: "正红", color: "#b8242c", prompt: "唇色正红" },
      { id: "coral", label: "珊瑚", color: "#d4695a", prompt: "唇色珊瑚" },
      { id: "nude-pink", label: "裸粉", color: "#c98f86", prompt: "唇色裸粉" },
      { id: "berry", label: "莓果", color: "#8e2f4f", prompt: "唇色莓果" },
      { id: "orange", label: "橘调", color: "#c96238", prompt: "唇色橘调" },
    ],
  },
];

export interface StudioMakeupState {
  skin: string;
  eye: string;
  lip: string;
  /** 0-100：眉妆 / 眼影 / 唇色 浓度。 */
  brow: number;
  eyeshadow: number;
  lipIntensity: number;
  autoSet: boolean;
}

export const DEFAULT_MAKEUP: StudioMakeupState = {
  skin: "natural",
  eye: "nude",
  lip: "bean",
  brow: 50,
  eyeshadow: 40,
  lipIntensity: 55,
  autoSet: true,
};

const MAKEUP_CLAUSE_RE = /(?:^|[；;])\s*妆造[:：][^；;]*/g;

function makeupSwatch(rowId: string, swatchId: string): MakeupSwatch | null {
  const row = MAKEUP_SWATCH_ROWS.find((item) => item.id === rowId);
  return row?.swatches.find((item) => item.id === swatchId) ?? null;
}

/** 妆造面板 → 一段可写进面部锚点的描述。 */
export function composeMakeupClause(state: StudioMakeupState): string {
  const skin = makeupSwatch("skin", state.skin);
  const eye = makeupSwatch("eye", state.eye);
  const lip = makeupSwatch("lip", state.lip);
  const parts = [
    skin?.prompt,
    eye?.prompt,
    lip?.prompt,
    `眉妆${Math.round(state.brow)}%`,
    `眼影${Math.round(state.eyeshadow)}%`,
    `唇色${Math.round(state.lipIntensity)}%`,
  ].filter(Boolean);
  if (state.autoSet) parts.push("定妆自然");
  return parts.join("，");
}

/** 用新的妆造描述替换旧的那一段，其余手工文字保持不变。 */
export function applyMakeupClause(
  facePrompt: string | null | undefined,
  clause: string,
): string {
  const source = typeof facePrompt === "string" ? facePrompt : "";
  const base = source.replace(MAKEUP_CLAUSE_RE, "").replace(/^[；;\s]+|[；;\s]+$/g, "");
  if (!clause.trim()) return base;
  return base ? `${base}；妆造：${clause}` : `妆造：${clause}`;
}

/** 从面部锚点里读回妆造描述；没有返回空串。 */
export function readMakeupClause(facePrompt: string | null | undefined): string {
  const source = typeof facePrompt === "string" ? facePrompt : "";
  let found = "";
  for (const match of source.matchAll(MAKEUP_CLAUSE_RE)) {
    found = match[0].replace(/^[；;]\s*/, "").replace(/^妆造[:：]\s*/, "").trim();
  }
  return found;
}
