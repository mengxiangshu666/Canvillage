// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export interface MoodGridItem {
  key: string;
  label: string;
  primaryExpression: string;
  /** Short visual cues only — image models prefer change+preserve over FACS essays. */
  expressionPrompt: string;
}

export const MOOD_GRID_COLUMNS = 5;
export const MOOD_GRID_ROWS = 10;
export const MOOD_GRID_CELL_COUNT = MOOD_GRID_COLUMNS * MOOD_GRID_ROWS;

/**
 * 情绪盘两轴的直观标签（用词对齐 LibTV 参考实现，见 `libtv爬取/DISTILL/13_MOOD_ADJUST_SPEC.md`）：
 * 纵轴 激动↔平静（唤醒度），横轴 疏离↔亲近（社交距离，等价于本盘原有的负向/正向效价）。
 */
export const MOOD_AXIS_LABELS = {
  excited: "激动",
  calm: "平静",
  close: "亲近",
  distant: "疏离",
} as const;

/** 光标停在锚点中心附近的死区；区间内不加方向修饰。 */
export const MOOD_AXIS_DEAD_ZONE = 0.18;

/**
 * 光标偏离锚点中心时的方向修饰，形如「偏激动」——LibTV 的 `imgEditorMoodSlightly = 偏{label}`。
 * 取偏离更大的那一轴；死区内返回空串（中心即中性，不修饰）。
 */
export function moodDirectionModifier(
  x: number,
  y: number,
  deadZone: number = MOOD_AXIS_DEAD_ZONE,
): string {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return "";
  if (Math.abs(x) <= deadZone && Math.abs(y) <= deadZone) return "";
  if (Math.abs(y) >= Math.abs(x)) {
    return `偏${y > 0 ? MOOD_AXIS_LABELS.excited : MOOD_AXIS_LABELS.calm}`;
  }
  return `偏${x > 0 ? MOOD_AXIS_LABELS.close : MOOD_AXIS_LABELS.distant}`;
}

function mood(key: string, label: string, primaryExpression: string, cue: string): MoodGridItem {
  return { key, label, primaryExpression, expressionPrompt: cue };
}

/**
 * A 10×5 valence/arousal affect field. Columns run from hostile/distressed
 * through uncertain/mixed to affiliative/joyful; rows run from high activation
 * to low activation. Each cell stores a short expression cue for image edit.
 */
export const MOOD_GRID: readonly MoodGridItem[] = [
  mood("ferocious-resolve", "决绝震怒", "angry", "brows hard-knit, piercing narrowed eyes, sneering mouth, jaw advanced"),
  mood("panic-surge", "惊慌骤起", "fear", "brows high and knit, wide searching eyes, mouth stretched open sideways"),
  mood("stunned-shock", "震愕失神", "surprised", "brows high, eyes fully open, rounded open mouth, dropped jaw"),
  mood("manic-excitement", "亢奋失控", "surprised", "high uneven brows, wide bright eyes, open excited grin"),
  mood("euphoric-burst", "狂喜迸发", "happy", "raised cheeks, squinting eyes, broad open smile"),

  mood("intense-anger", "盛怒爆发", "angry", "max lowered brows, hard glare, raised upper lip, forceful open jaw"),
  mood("grief-breakthrough", "悲伤决堤", "sad", "inner brows up, tearful squeezed eyes, sharp downturned mouth"),
  mood("shaken-fear", "惊惧动摇", "fear", "inner brows raised together, wide threat-fixed eyes, tense open mouth"),
  mood("nervous-laughter", "紧张失笑", "anxious", "worried raised brows, tense smiling eyes, unstable brief smile"),
  mood("triumphant-grin", "胜券得意", "confident", "one brow raised, narrowed confident eyes, wide controlled grin"),

  mood("disgusted-recoil", "厌恶退避", "disgust", "lowered brows, averted gaze, wrinkled nose, upper lip raised"),
  mood("startled-retreat", "受惊退缩", "fear", "uneven raised brows, darting wide eyes, sideways open mouth"),
  mood("sudden-astonishment", "蓦然惊讶", "surprised", "rapidly raised brows, wide eyes, small oval open mouth"),
  mood("anxious-hope", "忐忑期待", "anxious", "inner brows up, taut lids, parted lips, one corner up one down"),
  mood("pleasant-joy", "欣然喜悦", "happy", "natural smile brows, cheek-raised squint, warm even smile"),

  mood("restrained-anger", "隐忍怒意", "angry", "strongly lowered brows, locked glare, tightly pressed lips, clenched jaw"),
  mood("shame-collapse", "羞愧崩落", "sad", "painful inner brows, mostly closed eyes, deep downturned mouth, tucked jaw"),
  mood("awed-reverence", "敬畏屏息", "surprised", "smooth raised brows, calm wide eyes, small parted oval mouth"),
  mood("defiant-courage", "倔强无畏", "confident", "resolved lowered brows, steady narrowed eyes, pressed determined lips"),
  mood("proud-relief", "释然自豪", "confident", "relaxed brows, assured soft squint, controlled closed smile"),

  mood("jealous-restraint", "克制嫉意", "angry", "asymmetric lowered brow, sideways watchful eyes, compressed lips"),
  mood("mourning-suppressed", "强忍哀伤", "sad", "raised inner brows, tight tear-holding lids, immobilized downturned mouth"),
  mood("alert-scrutiny", "警觉审视", "fear", "concentrated lowered brows, slightly widened focused eyes, braced closed mouth"),
  mood("embarrassed-flush", "羞窘发热", "shy", "soft raised inner brows, averted lowered gaze, nervous tiny smile"),
  mood("suppressed-delight", "窃喜难藏", "happy", "tiny playful brow lift, concealed warm squint, tucked closed smile"),

  mood("contemptuous-smirk", "轻蔑冷笑", "contempt", "one raised brow, half-lidded dismissive eyes, single-corner smirk"),
  mood("silent-tears", "无声落泪", "sad", "high inner brows, moist slightly open eyes, evenly depressed mouth"),
  mood("disbelief", "难以置信", "surprised", "asymmetric brows, one eye wide one narrowed, uneven parted lips"),
  mood("tender-concern", "温柔担忧", "sad", "soft raised inner brows, gentle narrowed eyes, delicate mild downturn"),
  mood("indulgent-eyes", "宠溺眼神", "happy", "fond raised outer brow, warm squint, knowing closed smile"),

  mood("pent-up-frustration", "压抑烦闷", "angry", "uneven lowered brows, sideways fixed gaze, compressed downward mouth"),
  mood("restrained-grief", "克制悲恸", "sad", "restrained raised inner brows, heavy lids, firmly pressed sad mouth"),
  mood("guarded-curiosity", "戒备好奇", "confused", "one raised outer brow, asymmetric lids, lightly pursed mouth"),
  mood("memory-touched", "触景动容", "sad", "soft raised inner brows, upward recalling gaze, mixed mild mouth"),
  mood("affectionate-gaze", "深情凝望", "happy", "open soft brows, tender lowered lids, subtle elevated mouth corners"),

  mood("frosty-brows", "冷眉不悦", "angry", "cool shallow frown, narrowed dismissive eyes, straight pressed lips"),
  mood("hidden-heartache", "隐痛难掩", "sad", "uneven brows, asymmetric heavy lids, stronger one-sided downturn"),
  mood("micro-suspicion", "一闪狐疑", "confused", "one inner brow dip, micro-squint, one mouth corner retracted"),
  mood("bitter-amusement", "苦涩自嘲", "sad", "cocked brow, tired asymmetric lids, twisted one-up one-down mouth"),
  mood("helpless-smile", "无奈轻笑", "happy", "one brow up one down, resigned mild squint, uneven reluctant smile"),

  mood("distant-coldness", "淡漠清冷", "neutral", "outer brows slightly down, cool narrowed eyes, faint downturn, thin lips"),
  mood("lonely-longing", "孤寂眷恋", "sad", "subtle raised inner brows, faraway heavy gaze, soft depressed mouth"),
  mood("guilty-avoidance", "心虚回避", "anxious", "uneven concerned brows, averted downcast eyes, lips rolled inward"),
  mood("resigned-acceptance", "无奈释怀", "neutral", "center brows briefly lifted then soft, lowered lids, faint mixed mouth"),
  mood("gentle-smile", "温柔浅笑", "happy", "relaxed brows, soft narrowed eyes, small closed symmetric smile"),

  mood("cold-indifference", "冷漠疏离", "neutral", "level heavy brows, half-lowered lids, past-the-subject gaze, flat mouth"),
  mood("tired-blankness", "疲惫放空", "neutral", "slack brows, heavily lowered lids, unfocused drop gaze, loose parted lips"),
  mood("hesitant-words", "欲言又止", "neutral", "uncertain raised inner brows, averted glance, pursed then barely parted lips"),
  mood("calm-composure", "平静自持", "neutral", "level relaxed brows, natural lids, stable forward gaze, gently closed mouth"),
  mood("serene-contentment", "安然满足", "happy", "smooth brows, comfortable soft lids, minimal balanced closed smile"),
] as const;

/**
 * 盘心不是任何一个锚点：10 行网格没有正好落在 y=0 的行，按最近行取值会得到
 * 「难以置信」这类非中性锚点，既和面板同时显示的「中性 100%」自相矛盾，也会把错误的
 * 五官线索写进生成提示词。这里给盘心一个独立的中性条目，对齐参考实现盘心的
 * 「中性 / 淡然自若」。
 */
export const MOOD_CENTER_ROW = -1;
export const MOOD_CENTER_ITEM: MoodGridItem = {
  key: "neutral-center",
  label: "中性",
  primaryExpression: "neutral",
  expressionPrompt: "level relaxed brows, natural lids, stable forward gaze, gently closed mouth",
};
/** 盘心吸附半径：必须小于半个格子高（0.1），否则会吞掉中间两行的格心、破坏往返映射。 */
export const MOOD_CENTER_SNAP = 0.05;

export function moodCellFromAffect(x: number, y: number): { row: number; col: number; item: MoodGridItem } {
  if (Math.abs(x) <= MOOD_CENTER_SNAP && Math.abs(y) <= MOOD_CENTER_SNAP) {
    return { row: MOOD_CENTER_ROW, col: MOOD_CENTER_ROW, item: MOOD_CENTER_ITEM };
  }
  const col = Math.max(0, Math.min(MOOD_GRID_COLUMNS - 1, Math.round(((x + 1) / 2) * MOOD_GRID_COLUMNS - 0.5)));
  const row = Math.max(0, Math.min(MOOD_GRID_ROWS - 1, Math.round(((1 - y) / 2) * MOOD_GRID_ROWS - 0.5)));
  return { row, col, item: MOOD_GRID[row * MOOD_GRID_COLUMNS + col] };
}

export function affectFromMoodCell(row: number, col: number): { x: number; y: number } {
  // 盘心哨兵值没有格子坐标，直接回到 (0,0)；越界值仍按边界格子钳制。
  if (row === MOOD_CENTER_ROW || col === MOOD_CENTER_ROW) {
    return { x: 0, y: 0 };
  }
  const safeRow = Math.max(0, Math.min(MOOD_GRID_ROWS - 1, Math.round(row)));
  const safeCol = Math.max(0, Math.min(MOOD_GRID_COLUMNS - 1, Math.round(col)));
  return {
    x: ((safeCol + 0.5) / MOOD_GRID_COLUMNS) * 2 - 1,
    y: 1 - ((safeRow + 0.5) / MOOD_GRID_ROWS) * 2,
  };
}
