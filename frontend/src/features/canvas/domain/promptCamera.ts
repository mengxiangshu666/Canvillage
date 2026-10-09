// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 运镜的**归口**：一段可读可改的话，放在提示词里，而不是一个必须命中目录的 id。
 *
 * 为什么这么改：23 条运镜目录只是「省打字」的快捷方式（`docs/research/_qa_aigc_execution_v6`
 * §4.2.1 明写「名称只从表内取，速度、终点、程度可以自己写」），但此前节点上有一个
 * `cameraMovement` 字段被当成**预设 id** 提交给后端，脚本派生又往同一字段写自由文本，
 * 于是每一镜都在后端撞 `unknown camera_template_id: …` 400。运镜唯一的存放位置就是
 * 提示词里那一段：选卡片＝把那一段的正文换成该条正文，之后随便改。
 *
 * 段的口径与 `script_contract.MOTION_SEGMENT_ORDER` 的 camera 一栏同源：
 * 标签以 `运镜` / `摄影机运镜` / `明确的摄影机运镜` / `camera` 开头即为运镜段。
 * 纯函数，无副作用。
 */

import {
  CAMERA_MOVEMENT_PRESETS,
  findCameraMovementPreset,
  type CameraMovementPreset,
} from '@/features/canvas/domain/cameraMovementPresets';
import {
  parsePromptSegment,
  scriptVideoReferenceBlock,
  splitPromptSegmentChunks,
  stripManagedScriptVideoReferences,
  stripSegmentBrackets,
} from '@/features/canvas/domain/promptSegments';

/** 运镜段的标签前缀，与服务端 camera 段的识别关键词同一口径。 */
const CAMERA_SEGMENT_LABEL_RE = /^(运镜|摄影机运镜|明确的摄影机运镜|摄影机安排|镜头安排|camera)/i;
const CAMERA_BODY_RE = /^(?:(?:前半段|后半段|起始|开始时|先)\s*)?(?:固定(?:在|观察|镜头|机位|(?:的)?[^。；，\n]{0,12}机位)|静止机位|锁定机位|摄影机|镜头|机位|跟拍|推轨|摇镜|横移|环绕|locked\s+off|static\s+camera|tracking\s+shot)/i;

function cameraSegmentText(chunk: string): string {
  const { label, body } = parsePromptSegment(chunk);
  if (CAMERA_SEGMENT_LABEL_RE.test(label)) return body;
  const text = stripSegmentBrackets(chunk);
  return chunk.trim().startsWith('[') && chunk.trim().endsWith(']') && CAMERA_BODY_RE.test(text) ? text : '';
}

/** 插入新运镜段时使用的标签。 */
export const CAMERA_DIRECTION_SEGMENT_LABEL = '运镜轨迹';

/** 去掉两端空白与结尾的句号/分号，便于与目录正文比对。 */
function normalizeCameraText(value: string | null | undefined): string {
  return (typeof value === 'string' ? value : '')
    .trim()
    .replace(/[。．.；;]+$/, '')
    .trim();
}

/** 这一个段块是不是运镜段。 */
export function isCameraSegment(chunk: string): boolean {
  const { label, body } = parsePromptSegment(chunk);
  return CAMERA_SEGMENT_LABEL_RE.test(label || body) || Boolean(cameraSegmentText(chunk));
}

/** 提示词里的运镜正文；没有运镜段时返回空串。 */
export function cameraDirectionText(prompt: string | null | undefined): string {
  const chunk = splitPromptSegmentChunks(stripManagedScriptVideoReferences(prompt ?? ''), true).find(isCameraSegment);
  return chunk ? cameraSegmentText(chunk) : '';
}

/** 提示词里是否已经有运镜段。 */
export function hasCameraDirection(prompt: string | null | undefined): boolean {
  return splitPromptSegmentChunks(stripManagedScriptVideoReferences(prompt ?? ''), true).some(isCameraSegment);
}

/** shortcut: explicit Chinese contradictions only; ambiguous routes still require director review. */
export function cameraDirectionNeedsReview(text: string): boolean {
  const authored = text
    .replace(/(?:不要|禁止|不得|不是|并非)[^。；;，,\n]*/g, '')
    .replace(/(?:前半段|后半段|[^。；;，,\n]*期间)[^。；;，,\n]*/g, '')
    .replace(/[^。；;，,\n]*(?:停下|停住|停止|站定)(?:后|时)[^。；;，,\n]*/g, '');
  const alwaysMoving = /(?:摄影机|镜头|机位)(?:始终|全程|一直)(?:保持)?(?:跟随|跟拍|移动|横移|平移|推进|推近|拉远|环绕)/.test(authored);
  const stopped = /(?:摄影机|镜头|机位)(?:随后|此时|保持)?(?:不再移动|不移动|静止|固定不动|停住|停止移动)/.test(authored);
  const alwaysStill = /(?:摄影机|镜头|机位)(?:始终|全程|一直)(?:保持)?(?:静止|不动|固定(?!构图|景别|中景|背影))/.test(authored);
  const moving = /(?:摄影机|镜头|机位)(?:缓慢|快速|随后|开始|向左|向右|向前|向后)*(?:跟随|跟拍|移动|横移|平移|推进|推近|拉远|环绕)/.test(authored);
  return (alwaysMoving && stopped) || (alwaysStill && moving);
}

/**
 * 写入运镜：有运镜段就**只换正文**（标签、方括号、段序都不动，6/8 段式的段数不变），
 * 没有就在最前插一段 `[运镜轨迹] …`。传空文本等于清除。
 */
export function setCameraDirection(
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
  const clean = normalizeCameraText(text);
  const index = chunks.findIndex(isCameraSegment);
  const withReferences = (value: string) => [...references, value].filter(Boolean).join('\n');

  if (!clean) {
    if (index < 0) return withReferences(source);
    const chunk = chunks[index];
    const start = source.indexOf(chunk);
    const before = source.slice(0, start);
    const after = source.slice(start + chunk.length);
    return withReferences((/\s*\+\s*$/.test(before)
      ? before.replace(/\s*\+\s*$/, '') + after
      : before + after.replace(/^\s*\+\s*/, '')).trim());
  }

  if (index >= 0) {
    const chunk = chunks[index];
    const body = cameraSegmentText(chunk);
    const at = body ? chunk.lastIndexOf(body) : -1;
    const next = at >= 0 ? `${chunk.slice(0, at)}${clean}${chunk.slice(at + body.length)}`
      : /[:：]\s*\]?$/.test(chunk) ? chunk.replace(/([:：]\s*)(\]?)$/, (_match, colon, close) => `${colon}${clean}${close}`)
      : `${chunk} ${clean}`;
    return withReferences(source.replace(chunk, () => next));
  }

  const lead = `[${CAMERA_DIRECTION_SEGMENT_LABEL}] ${clean}`;
  return withReferences(source ? `${lead} + ${source}` : lead);
}

/** 删除运镜段（清除语义里「没有原文可还原」的那一半）。 */
export function clearCameraDirection(prompt: string | null | undefined): string {
  return setCameraDirection(prompt, '');
}

/**
 * 还原运镜：脚本派生的节点回到该行运动稿原文；其它节点删掉运镜段。
 *
 * 不能用「删除」统一处理：脚本行的运动稿本来就是 6 段式，删掉第 1 段会直接撞服务端
 * `script.motion.segments.v1`（blocking）。
 */
export function resetCameraDirection(
  prompt: string | null | undefined,
  original: string | null | undefined,
): string {
  const base = typeof original === 'string' ? original : '';
  if (base.trim().length > 0 && hasCameraDirection(base)) return base;
  return clearCameraDirection(prompt);
}

/**
 * 反解：这段运镜正文对应目录里的哪一条（用作卡片高亮）。
 *
 * 先比正文全等（点卡片写进去的就是目录正文），再比前缀（用户在其后补了速度/终点）。
 * 都对不上返回 null —— 手写的运镜不点亮任何卡片，这是刻意的，不是错误。
 */
export function resolveCameraPresetForText(
  templates: ReadonlyArray<CameraMovementPreset>,
  text: string | null | undefined,
): CameraMovementPreset | null {
  const needle = normalizeCameraText(text);
  if (!needle) return null;
  for (const preset of templates) {
    if (
      normalizeCameraText(preset.promptFragment) === needle ||
      normalizeCameraText(preset.label) === needle
    ) {
      return preset;
    }
  }
  for (const preset of templates) {
    const fragment = normalizeCameraText(preset.promptFragment);
    const label = normalizeCameraText(preset.label);
    if (fragment && needle.startsWith(fragment)) return preset;
    if (label && needle.startsWith(label)) return preset;
  }
  return null;
}

/** 提示词当前点亮的运镜卡片；手写正文返回 null。 */
export function cameraPresetForPrompt(
  templates: ReadonlyArray<CameraMovementPreset>,
  prompt: string | null | undefined,
): CameraMovementPreset | null {
  return resolveCameraPresetForText(templates, cameraDirectionText(prompt));
}

/**
 * 按目录名称 / 目录正文**全等**找那一条（不做前缀）。
 *
 * Agent 命令写的是目录里的名字时要用目录自己的正文；但「镜头前推，然后停住」这种
 * 自写的完整句子不该被前缀吃掉半句，所以这里只认全等。
 */
function presetByExactName(value: string): CameraMovementPreset | null {
  const needle = normalizeCameraText(value);
  if (!needle) return null;
  return (
    CAMERA_MOVEMENT_PRESETS.find(
      (preset) =>
        normalizeCameraText(preset.label) === needle ||
        normalizeCameraText(preset.promptFragment) === needle,
    ) ?? null
  );
}

/**
 * Agent 命令里的 `camera_movement` → 运镜段正文。
 *
 * 命令写目录 id 或目录名称就用该条正文；写自由文本就原样用（语言优先，不要求命中
 * 目录）。带 `[运镜轨迹] ` 壳的写法先剥壳，免得折进提示词后多一层方括号。
 */
export function cameraDirectionTextFromCommand(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim() : '';
  if (!raw) return '';
  const preset =
    findCameraMovementPreset(CAMERA_MOVEMENT_PRESETS, raw) ?? presetByExactName(raw);
  if (preset) return preset.promptFragment || preset.label;
  return parsePromptSegment(raw).body || raw;
}

/**
 * 水合旧画布：把已经废弃的 `data.cameraMovement` 折进提示词。
 *
 * 规则只有一条：**只补空，不覆盖，也不重复**。
 *
 * - 旧值是目录 id / kebab 别名 → 取该条正文；旧值是自由文本 → 取正文部分；
 * - 提示词里**已经有运镜段** → 不动它（提示词是真相）；
 * - 提示词正文里**已经出现这句话** → 也不插段（旧值常常就是整条提示词的副本，或者
 *   运镜本来就写在正文里；插一段等于把同一个运镜说两遍，模型会当成两条指令）。
 *
 * 返回 `null` 表示「没有可折的东西」，调用方据此跳过写回。
 */
export function foldLegacyCameraMovement(
  prompt: string | null | undefined,
  legacy: unknown,
): string | null {
  const raw = typeof legacy === 'string' ? legacy.trim() : '';
  if (!raw) return null;

  const preset = findCameraMovementPreset(
    // 只用内置目录（水合是同步的，后端目录可能还没拉回来）。
    // 别名解析与后端 VIDEO_CAMERA_TEMPLATE_ALIASES 同一张表。
    CAMERA_MOVEMENT_PRESETS,
    raw,
  );
  // 旧值本身可能带着 `[运镜轨迹] ` 前缀（脚本派生就是这么写的）：只取正文。
  const cameraText = preset?.promptFragment
    ? preset.promptFragment
    : normalizeCameraText(parsePromptSegment(raw).body);
  if (!cameraText) return null;

  const current = typeof prompt === 'string' ? prompt : '';
  if (hasCameraDirection(current)) return null;
  if (current.includes(cameraText)) return null;

  const next = setCameraDirection(current, cameraText);
  return next === current ? null : next;
}
