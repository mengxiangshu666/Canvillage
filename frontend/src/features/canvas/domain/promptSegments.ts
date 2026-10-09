// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 分镜提示词 / 视频运动提示词的段级读取与整段替换（纯函数，无副作用）。
 *
 * 规范里 shot_prompt 是 8 段、video_motion_prompt 是 6 段，段间用 ` + ` 连接，
 * 第 7 段风格共享，第 8 段技术参数逐镜选择。约束由 freezone/script_contract.py 判定与修复；
 * 前端这里只做解析与整段替换，不复制服务端的判定逻辑。
 *
 * 解析刻意宽松：模型偶尔漏一对最外层方括号、偶尔多一个空段。前端不该因为格式小瑕疵
 * 就显示不出段内容，那正是最需要说清楚的时候。
 *
 * 放在 domain/ 而不是脚本节点目录里：运镜写入（promptCamera.ts）与脚本节点的只读展示
 * 用的是同一套切段口径，两边共用一份实现才不会各切各的。
 */

/** 8 段式的段序标签，与服务端 SHOT_SEGMENT_LABELS_ZH 同一口径。 */
export const SHOT_SEGMENT_PREFIXES = [
  '画面构图',
  '角色卡',
  '主体/人物空间',
  '极具体的微表情',
  '明确的场景环境',
  '光影几何',
  '视觉风格',
  '技术参数',
] as const;

/** 6 段式的段序标签。 */
export const MOTION_SEGMENT_PREFIXES = [
  '明确的摄影机运镜',
  '主体极其具体的物理动作',
  '环境物理动态',
  '音效与氛围',
  '对话台词',
  '时长',
] as const;

/**
 * 按 ` + ` 切成原始段块（保留方括号与标点，只去首尾空白）。
 *
 * 只切「至少一侧带空白」的加号：段内容里可能出现「冷蓝A+B主调」这种写法，
 * 只切最外层加号，角色卡内的多角色连接保留；与服务端 split_prompt_segments 同口径。
 */
export function splitPromptSegmentChunks(text: string | null | undefined, splitBlockLines = false): string[] {
  const raw = typeof text === 'string' ? text.trim() : '';
  if (!raw) return [];
  const chunks: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < raw.length; index += 1) {
    const character = raw[index];
    if (character === '[') { depth += 1; continue; }
    if (character === ']') { depth = Math.max(0, depth - 1); continue; }
    const blockLine = splitBlockLines && character === '\n' && /^\s*\[/.test(raw.slice(index + 1));
    if ((character !== '+' && !blockLine) || depth > 0) continue;
    const before = raw[index - 1] ?? '';
    const after = raw[index + 1] ?? '';
    if (!blockLine && !/\s/.test(before) && !/\s/.test(after)) continue;
    const chunk = raw.slice(start, index).trim();
    if (chunk) chunks.push(chunk);
    start = index + 1;
  }
  const tail = raw.slice(start).trim();
  if (tail) chunks.push(tail);
  return chunks;
}

/** 段块按规范重新连接（与写进提示词时的分隔符一致）。 */
export function joinPromptSegmentChunks(chunks: readonly string[]): string {
  return chunks
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.length > 0)
    .join(' + ');
}

/** 去掉段块最外层的一对方括号（只在两端都成对时剥）。 */
export function stripSegmentBrackets(chunk: string): string {
  const text = chunk.trim();
  return text.startsWith('[') && text.endsWith(']') ? text.slice(1, -1).trim() : text;
}

/**
 * 把一个段块拆成 `{ label, body }`。三种写法都要认：
 *
 * - `[明确的摄影机运镜轨迹与速度：极慢速推进…]` → 标签在方括号内、冒号之前；
 * - `[运镜轨迹] 固定机位，极慢微推` → 方括号本身是标签，正文在括号之后；
 * - `运镜：缓慢推近` / 纯正文 → 按冒号切，或整段都是正文。
 */
export function parsePromptSegment(chunk: string): { label: string; body: string } {
  const text = chunk.trim();
  if (text.startsWith('[')) {
    let close = -1;
    let depth = 0;
    for (let index = 0; index < text.length; index += 1) {
      if (text[index] === '[') depth += 1;
      if (text[index] === ']') {
        depth -= 1;
        if (depth === 0) { close = index; break; }
      }
    }
    if (close > 0) {
      const head = text.slice(1, close);
      const headColon = head.search(/[:：]/);
      if (headColon > 0) {
        return { label: head.slice(0, headColon).trim(), body: head.slice(headColon + 1).trim() };
      }
      return {
        label: head.trim(),
        body: text.slice(close + 1).replace(/^\s*[:：]\s*/, '').trim(),
      };
    }
  }
  const colon = text.search(/[:：]/);
  if (colon > 0) {
    return { label: text.slice(0, colon).trim(), body: text.slice(colon + 1).trim() };
  }
  return { label: '', body: text };
}

export function scriptVideoReferenceBlock(prompt: string): { start: number; end: number; text: string } | null {
  let depth = 0;
  let start = -1;
  for (let index = 0; index < prompt.length; index += 1) {
    if (prompt[index] === '[') {
      if (depth === 0 && /^\[(?:视频资产引用|视频参考用途)[:：]/.test(prompt.slice(index))) start = index;
      depth += 1;
    } else if (prompt[index] === ']' && depth > 0) {
      depth -= 1;
      if (depth === 0 && start >= 0) return { start, end: index + 1, text: prompt.slice(start, index + 1) };
    }
  }
  return null;
}

export function stripManagedScriptVideoReferences(prompt: string): string {
  for (let block = scriptVideoReferenceBlock(prompt); block; block = scriptVideoReferenceBlock(prompt)) {
    prompt = prompt.slice(0, block.start) + prompt.slice(block.end);
  }
  return prompt.trim();
}

/** 段块的标签；没有标签时返回空串。 */
export function segmentLabel(chunk: string): string {
  return parsePromptSegment(chunk).label;
}

/** 段块的正文：去掉标签与紧跟的冒号 / 空白。 */
export function segmentBody(chunk: string): string {
  return parsePromptSegment(chunk).body;
}

/** 按 ` + ` 切段，并去掉每段最外层的一对方括号。 */
export function splitPromptSegments(text: string | null | undefined): string[] {
  return splitPromptSegmentChunks(text).map(stripSegmentBrackets);
}

/** shot_prompt 的段数组。 */
export function splitShotPromptSegments(text: string | null | undefined): string[] {
  return splitPromptSegments(text);
}

/**
 * 取某一段的内容：按段序标签匹配，找不到返回空串。
 *
 * prefix 用段序标签的前缀（例如 `视觉风格`），与 SHOT_SEGMENT_PREFIXES 一致。
 */
export function shotPromptSegment(
  text: string | null | undefined,
  prefix: string,
): string {
  return splitPromptSegments(text).find((segment) => segment.startsWith(prefix)) ?? '';
}

/**
 * 这一段是不是「全片唯一」的那几段之一。
 *
 * 人物身份与全片风格由服务端保留；焦段、光圈、景深可随本镜目的变化。
 */
export function isFrozenShotSegment(segment: string): boolean {
  return (
    segment.startsWith('角色卡') ||
    segment.startsWith('主体描述') ||
    segment.startsWith('视觉风格')
  );
}

/** 这一镜是否具备成片的段级形态（8 段齐全）。用于把「格式没写全」如实说出来。 */
export function hasEightSegments(text: string | null | undefined): boolean {
  return splitPromptSegments(text).length === SHOT_SEGMENT_PREFIXES.length;
}
