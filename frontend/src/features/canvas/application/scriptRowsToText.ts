// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 把脚本节点产出的分镜表渲染成下游可读的文本。
 *
 * 为什么需要这一层：脚本节点往下游传的必须是**它产出的那张表**，不是它自己的
 * 输入指令（`data.prompt` 是「帮我生成不会被安全拦截的脚本」这类 steering 文本）。
 * 视频模型拿到的是一坨自然语言，它分不清「创作要求」和「画面内容」——
 * 实测会把上游那段指令当成台词念出来（H3 出片音轨的 ASR 结果就是逐字朗读）。
 * 图片 / 视频节点两条分支早已拒绝把自身指令传给下游，脚本节点这条原先漏了。
 *
 * 只渲染「拍这一镜要看什么」的核心列：镜号、时长、画面描述、景别、角色动作、
 * 情绪、场景标签、对白、分镜提示词、视频运动提示词。角色图 / 参考帧 / 角色描述
 * 这些资产明细列不进文本（它们是 URL 和排版信息，不是内容）。
 *
 * 两处刻意加工（都不改用户数据，只改渲染结果）：
 * - 逐行时长槽 `[时长：3.0s]` 摘掉 —— 它是**本行**的时长，等于同行 `duration`
 *   单元格，对下游没有意义，却会把前后端同口径的「提示词里的时长声明」闸门
 *   直接顶爆（分镜表一进下游，15 秒的节点会读到表里第一行的 3 秒）。
 * - 裸文本对白补引号 —— 后端认「说话来源」只认引号 / `说：` 标记 / 结构化字段，
 *   裸文本会被判成「没有任何说话来源」。
 *
 * 对白刻意保留：它既是画面里的口型依据，也是后端 audio_type 判定的输入。
 * 纯函数，无副作用。
 */

/** 与 `scriptFields.ts` 的核心列同口径（不含角色三连与资产明细列）。 */
const TEXT_ROW_FIELDS: readonly { key: string; label: string }[] = [
  { key: 'shot_no', label: '镜号' },
  { key: 'duration', label: '时长' },
  { key: 'visual_description', label: '画面描述' },
  { key: 'shot', label: '景别' },
  { key: 'character_action', label: '角色动作' },
  { key: 'emotion', label: '情绪' },
  { key: 'scene_tags', label: '场景标签' },
  { key: 'dialogue', label: '对白' },
  { key: 'shot_prompt', label: '分镜提示词' },
  { key: 'video_motion_prompt', label: '视频运动提示词' },
];

/** 「没有台词」的占位写法，不能当成对白渲染出去。 */
const NO_DIALOGUE_VALUES = new Set(['', '无', '没有', '无台词', '无对白', '没有台词', '没有对白', 'none', 'n/a']);

/**
 * 后端识别「这句是要说出来的台词」只认三种写法（见
 * `freezone/video_request_contract.py` 的 `extract_spoken_dialogue`）：
 * 引号包裹、`说：` 这类标记、以及结构化的 `dialogue_text` 字段。
 *
 * 真机数据里 871 条非空对白有 808 条自带引号、63 条是裸文本（如「退下！」）。
 * 裸文本经这条通路传下去，后端既抽不出 `spoken_dialogue` 也认不到标记，
 * 于是给这个请求追加「本镜头没有必须说出的台词」—— 而提示词里明明有台词行，
 * 自相矛盾，还会把该说的人声压没。渲染时补一层引号即可，不改用户数据。
 */
const QUOTE_CHARS = ['“', '”', '「', '」', '『', '』', '"'];
/** 与后端 `_UNAUTHORIZED_DIALOGUE_MARKER_PATTERN` 同口径。 */
const DIALOGUE_MARKER = /(?:说|说道|喊道|低声道|Says)\s*[:：]/i;

function dialogueForUpstream(value: string): string {
  if (QUOTE_CHARS.some((quote) => value.includes(quote))) return value;
  if (DIALOGUE_MARKER.test(value)) return value;
  return `“${value}”`;
}

/**
 * 逐行时长槽 `[时长：4.0s]` 不进下游文本。
 *
 * 它是**单行**的时长，等于同行 `duration` 单元格（真机 1899 行逐行相等），
 * 对下游那个视频节点没有意义 —— 下游要生成多少秒由它自己的时长决定。
 *
 * 另一个理由是它会让下游提示词出现互相冲突的时间描述。节点自己的时长才是
 * 提交参数；逐行 `[时长：3.0s]` 只是分镜表字段，不该混进画面描述。
 */
const DURATION_SLOT_PATTERN = /(\s*\+\s*)?\[\s*(?:时长|duration)\s*[:：][^\]]*\]/giu;

function stripPerShotDuration(value: string): string {
  return value
    .replace(DURATION_SLOT_PATTERN, '')
    // 槽位被拿掉后会留下落单的分隔符。只处理「两侧都有空白的加号」——
    // 正文里贴着写的 `a+b` 不能被当成分隔符合并掉。
    .replace(/(?:\s\+\s)+/gu, ' + ')
    .replace(/^\s*\+\s*/u, '')
    .replace(/\s*\+\s*$/u, '')
    .trim();
}

function cellText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '';
}

function isNoDialogue(value: string): boolean {
  return NO_DIALOGUE_VALUES.has(value.trim().toLowerCase());
}

/** 一行的角色列：`character_1` / `character_2` … 按行内实际占用展开。 */
function characterNames(row: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (let slot = 1; slot <= 8; slot += 1) {
    const name = cellText(row[`character_${slot}`]);
    if (name) out.push(name);
  }
  return out;
}

/** 一行的渲染结果：`镜号 1｜时长 3s｜画面描述 …`，空字段自动省略。 */
export function scriptRowToText(row: unknown): string {
  if (!row || typeof row !== 'object') return '';
  const record = row as Record<string, unknown>;
  const parts: string[] = [];

  const header = cellText(record.shot_no);
  if (header) parts.push(`镜号 ${header}`);

  const characters = characterNames(record);
  if (characters.length > 0) parts.push(`角色 ${characters.join('、')}`);

  for (const field of TEXT_ROW_FIELDS) {
    if (field.key === 'shot_no') continue;
    const raw = cellText(record[field.key]);
    if (!raw) continue;
    if (field.key === 'dialogue' && isNoDialogue(raw)) continue;
    // 逐行时长槽先摘掉：它是本行的时长，不是下游那条视频的时长（见 DURATION_SLOT_PATTERN）。
    const value = stripPerShotDuration(raw);
    if (!value) continue;
    const rendered = field.key === 'dialogue' ? dialogueForUpstream(value) : value;
    parts.push(`${field.label} ${rendered}`);
  }

  return parts.join('｜');
}

/**
 * 把 `scriptResult`（后端的 `{ title, rows[] }`）渲染成多行文本。
 * 没有可读行时返回空串 —— 调用方据此回落成「不携带 text」。
 */
export function scriptResultToUpstreamText(scriptResult: unknown): string | undefined {
  if (!scriptResult || typeof scriptResult !== 'object') return undefined;
  const rows = (scriptResult as { rows?: unknown }).rows;
  if (!Array.isArray(rows)) return undefined;

  const lines = rows
    .map((row) => scriptRowToText(row))
    .filter((line) => line.length > 0);
  if (lines.length === 0) return undefined;
  return lines.join('\n');
}
