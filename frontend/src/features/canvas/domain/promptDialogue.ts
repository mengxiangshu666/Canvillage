// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 从画布视频节点的提示词里抽取「要说出来的台词」。
 *
 * 背景：视频模型拿到夹在画面描述里的台词时只能自己发挥，实测会念错。正确的做法是把
 * 台词交给 TTS 合成音频，再作为音频参考喂给模型做口型同步；台词本身则从视觉 prompt
 * 里剥离（后端 `normalize_video_prompt_for_submission_result` 负责剥离）。
 *
 * 抽取口径与后端 `novelvideo/freezone/video_request_contract.py` 的
 * `extract_spoken_dialogue` 保持一致，另补两类画布上常见的写法：
 *   - 引号：``“…”`` / ``「…」`` / ``『…』`` / ``"…"``
 *   - 槽位：`[对话台词与语气：…]` / `[台词：…]` / `[对白：…]`
 *   - 句式：`台词：…` / `对白：…` / `说：…`（到标点或换行为止）
 *
 * 纯函数，无副作用。
 */

/** 与后端 `_QUOTED_DIALOGUE_CAPTURE_PATTERN` 同口径的四种引号。 */
const QUOTED_PATTERN = /“([^”]*)”|「([^」]*)」|『([^』]*)』|"([^"]*)"/g;

/** 方括号槽位：项目自带的文本节点模板教用户这样写台词。 */
const BRACKET_SLOT_PATTERN =
  /\[(?:对话台词与语气|对话台词|台词|对白)\s*[:：]\s*([^\]\n]{1,240})\]/g;

/**
 * 无引号句式：`台词：xxx` 直到标点/换行。方括号、引号、竖线都必须排除，
 * 否则 `[台词：“…”]` 会被连壳一起吞掉。
 */
const CLAUSE_PATTERN =
  /(?:台词|对白|说|说道|喊道|低声道|Says)\s*[:：]\s*([^，,。！？!?；;\n\]\[“”「」『』"|]{1,240})/gi;

/** 明确的「没有台词」占位，不能被当成台词。 */
const NO_DIALOGUE_VALUES = new Set([
  '无',
  '没有',
  '无台词',
  '无对白',
  '没有台词',
  '没有对白',
  '无。',
  'none',
  'no dialogue',
  'n/a',
]);

/** 去掉台词两端的引号与空白（槽位/句式命中时可能自带引号）。 */
function normalizeDialogue(value: string): string {
  return value
    .trim()
    .replace(/^["“”「」『』'\s]+/, '')
    .replace(/["“”「」『』'\s]+$/, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function isNoDialogue(value: string): boolean {
  const lowered = value.trim().toLowerCase();
  return lowered.length === 0 || NO_DIALOGUE_VALUES.has(lowered);
}

/**
 * 按出现顺序抽出台词，去重后返回。抽不到（或全是「无」占位）时返回空数组。
 */
export function extractPromptDialogue(prompt: string | null | undefined): string[] {
  const source = typeof prompt === 'string' ? prompt : '';
  if (source.trim().length === 0) return [];

  const hits: Array<{ index: number; text: string }> = [];

  for (const match of source.matchAll(QUOTED_PATTERN)) {
    const text = match.slice(1).find((group) => typeof group === 'string' && group.trim().length > 0);
    if (text) hits.push({ index: match.index ?? 0, text });
  }
  for (const match of source.matchAll(BRACKET_SLOT_PATTERN)) {
    if (match[1]) hits.push({ index: match.index ?? 0, text: match[1] });
  }
  for (const match of source.matchAll(CLAUSE_PATTERN)) {
    if (match[1]) hits.push({ index: match.index ?? 0, text: match[1] });
  }

  hits.sort((a, b) => a.index - b.index);

  const out: string[] = [];
  const seen = new Set<string>();
  for (const hit of hits) {
    const text = normalizeDialogue(hit.text);
    if (isNoDialogue(text) || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

/**
 * 配音缓存的比较键。同一段台词 + 同一把嗓音才允许复用已合成的音频。
 */
export function dialogueDubbingCacheKey(lines: readonly string[], voiceKey: string): string {
  return `${voiceKey}\u0000${lines.join('\u0001')}`;
}
