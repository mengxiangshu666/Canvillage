// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 把特效库的一条画面描述写进节点提示词。
 *
 * 与运镜一样，特效的唯一真相是提示词里的文字，不新增节点字段：
 * - 分镜/运动稿已经是 6 段或 8 段时，**不能**再加一段，否则会撞
 *   `script.shot_prompt.segments.v1`；特效折进「光影几何」段（没有就折进
 *   「环境物理动态」段），用 `；特效：…` 标记，换一条时只替换标记后的正文。
 * - 自由提示词没有段结构时，才新增 `[特效] …` 段。
 *
 * 纯函数，无副作用。
 */

import {
  joinPromptSegmentChunks,
  parsePromptSegment,
  scriptVideoReferenceBlock,
  splitPromptSegmentChunks,
} from '@/features/canvas/domain/promptSegments';

export const EFFECT_SEGMENT_LABEL = '特效';

const EFFECT_SEGMENT_LABEL_RE = /^(视觉特效|特效)$/;
const LIGHTING_SEGMENT_LABEL_RE = /^(光影几何|大气效果|光影)/;
const ENVIRONMENT_SEGMENT_LABEL_RE =
  /^(场景环境|环境元素|环境物理动态|环境动态|前景\/背景道具|前景背景道具)/;
const EFFECT_CLAUSE_RE = /(?:^|[；;])\s*特效[:：]\s*[^；;]*/g;

function normalizeEffectText(value: string | null | undefined): string {
  return (typeof value === 'string' ? value : '')
    .trim()
    .replace(/[。．.；;]+$/, '')
    .trim();
}

function findClosingBracket(text: string): number {
  let depth = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '[') depth += 1;
    if (text[index] === ']') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

/** 只换段块正文，标签、方括号与段序保持原样。 */
function replaceChunkBody(chunk: string, body: string): string {
  const text = chunk.trim();
  if (text.startsWith('[')) {
    const close = findClosingBracket(text);
    if (close > 0) {
      const head = text.slice(1, close);
      const headColon = head.search(/[:：]/);
      if (headColon > 0) {
        return `[${head.slice(0, headColon)}${head[headColon]}${body}]`;
      }
      return body ? `[${head}] ${body}` : `[${head}]`;
    }
  }
  const colon = text.search(/[:：]/);
  if (colon > 0) return `${text.slice(0, colon)}${text[colon]}${body}`;
  return body;
}

function splitEffectClause(body: string): { base: string; effect: string } {
  const effects: string[] = [];
  for (const match of body.matchAll(EFFECT_CLAUSE_RE)) {
    const text = (match[0].replace(/^[；;]\s*/, '').replace(/^特效[:：]\s*/, '') ?? '').trim();
    if (text) effects.push(text);
  }
  const base = body.replace(EFFECT_CLAUSE_RE, '').replace(/^[；;\s]+/, '').replace(/[；;\s]+$/, '').trim();
  return { base, effect: effects[effects.length - 1] ?? '' };
}

function withEffectClause(body: string, effect: string): string {
  const { base } = splitEffectClause(body);
  if (!effect) return base;
  return base ? `${base}；特效：${effect}` : `特效：${effect}`;
}

function isEffectSegment(chunk: string): boolean {
  return EFFECT_SEGMENT_LABEL_RE.test(parsePromptSegment(chunk).label);
}

function isLightingSegment(chunk: string): boolean {
  return LIGHTING_SEGMENT_LABEL_RE.test(parsePromptSegment(chunk).label);
}

function isEnvironmentSegment(chunk: string): boolean {
  return ENVIRONMENT_SEGMENT_LABEL_RE.test(parsePromptSegment(chunk).label);
}

/** 提示词当前挂着的特效正文；没有返回空串。 */
export function effectDirectionText(prompt: string | null | undefined): string {
  const chunks = splitPromptSegmentChunks(prompt ?? '', true);
  const explicit = chunks.find(isEffectSegment);
  if (explicit) return parsePromptSegment(explicit).body.trim();
  let found = '';
  for (const chunk of chunks) {
    const effect = splitEffectClause(parsePromptSegment(chunk).body).effect;
    if (effect) found = effect;
  }
  return found;
}

export function hasEffectDirection(prompt: string | null | undefined): boolean {
  return effectDirectionText(prompt).length > 0;
}

/**
 * 写入特效。传空文本等于清除；同一条重复点选不会重复堆字。
 */
export function setEffectDirection(
  prompt: string | null | undefined,
  text: string | null | undefined,
): string {
  let source = prompt?.trim() ?? '';
  const references: string[] = [];
  for (let block = scriptVideoReferenceBlock(source); block; block = scriptVideoReferenceBlock(source)) {
    references.push(block.text);
    source = source.slice(0, block.start) + source.slice(block.end);
  }
  source = source.trim();
  const chunks = splitPromptSegmentChunks(source, true);
  const clean = normalizeEffectText(text);
  const withReferences = (value: string) => [...references, value].filter(Boolean).join('\n');

  const effectIndex = chunks.findIndex(isEffectSegment);
  if (effectIndex >= 0) {
    const chunk = chunks[effectIndex];
    if (!clean) {
      const next = chunks.filter((_, index) => index !== effectIndex);
      return withReferences(joinPromptSegmentChunks(next));
    }
    if (parsePromptSegment(chunk).body.trim() === clean) return withReferences(source);
    return withReferences(source.replace(chunk, () => replaceChunkBody(chunk, clean)));
  }

  const hostIndex = chunks.findIndex(isLightingSegment);
  const fallbackIndex = hostIndex >= 0 ? hostIndex : chunks.findIndex(isEnvironmentSegment);
  if (fallbackIndex >= 0) {
    const chunk = chunks[fallbackIndex];
    const body = parsePromptSegment(chunk).body;
    const nextBody = withEffectClause(body, clean);
    if (nextBody === body.trim()) return withReferences(source);
    return withReferences(source.replace(chunk, () => replaceChunkBody(chunk, nextBody)));
  }

  if (chunks.length >= 2) {
    // 已经多段但没有可挂靠的段：挂到最后一段，保住段数（分镜 6/8 段合同）。
    const index = chunks.length - 1;
    const chunk = chunks[index];
    const body = parsePromptSegment(chunk).body;
    const nextBody = withEffectClause(body, clean);
    if (nextBody === body.trim()) return withReferences(source);
    return withReferences(source.replace(chunk, () => replaceChunkBody(chunk, nextBody)));
  }

  if (!clean) return withReferences(source);
  const lead = `[${EFFECT_SEGMENT_LABEL}] ${clean}`;
  return withReferences(source ? `${source} + ${lead}` : lead);
}

/** 删除提示词里的特效（段或 `；特效：…` 标记都清）。 */
export function clearEffectDirection(prompt: string | null | undefined): string {
  return setEffectDirection(prompt, '');
}
