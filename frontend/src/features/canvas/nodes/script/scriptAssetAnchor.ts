// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ScriptShotRefEntry } from './scriptShotRefs';
import { scriptReferenceResponsibility } from './scriptCreativeHandoff';

/**
 * 资产图锚定块：把「图片N 是谁」写进提示词。
 *
 * **为什么要有它**：分镜图节点给后端的是**按位置编号**的参考图数组（`图片1`…`图片N`），
 * 而提示词里从头到尾没有一处说明「图片1 是谁」。多角色镜头里模型拿到两张脸加一段文字，
 * 只能猜谁是谁 —— 这不是「效果不够好」，是信息根本没给。
 *
 * LibTV 的对应物是 `buildPromptWithInlineRefs`：把 `@名称` 重写成 `@名称({{Image N}})`，
 * 并在提示词开头插一段「资产图锚定：」，逐行写明 `角色 张三 的参考图是 {{Image 1}}`。
 * 编号 N 就是该资产在去重后参考图数组里的**下标 + 1**。
 *
 * **只抄一半：不抄 `{{Image N}}` 语法，只用 `图片N`。** 本产品的后端按
 * `图片1/视频1/音频1` 这套标签解释参考（见 `prompt_optimizer.py` 的模型规则与
 * `clip_contract.py` 的 `label` 字段），`{{Image N}}` 不在它的解析范围内 —— 抄过来
 * 会写出模型看不懂、后端也不校验的 token。另一半（把正文里的 `@名称` 就地重写）也
 * 刻意不做：那是在改用户自己写的文字。
 *
 * 行文用「的参考图是 图片N」而不是「见 图片N」，是因为它同时充当给模型的**指代表**：
 * 正文里出现「张三」时，模型能对上图片1。
 */

/** 锚定块的标题行；也是「这段是锚定块」的唯一识别标记。 */
export const SCRIPT_ASSET_ANCHOR_TITLE = '资产图锚定：';
const SCRIPT_ASSET_BOARD_GUIDANCE =
  '参考图用于锁定对象设计与空间关系；设定板中的多个角度属于同一个对象，不代表多个人或多件道具。只生成镜头要求的单幅场景，人数、道具数量、姿态和摄影构图按镜头描述；不复制参考图的拼版、分栏、标签或俯视布局。';

export interface ScriptAssetAnchor {
  /** 真正会占 图片1..N 的条目（有序、已去重，与提交的参考图数组同序同长）。 */
  references: ScriptShotRefEntry[];
  /** 锚定块正文；没有任何带身份的参考图时为空串。 */
  block: string;
}

/**
 * 按**最终参考图数组**的顺序编号。
 *
 * 两处细节决定它必须吃整组条目而不是「资产那几条」：
 * - 编号是数组里的位置，参考帧兜底那条也占位，跳过后面的资产编号就会错位；
 * - 一个资产没有身份（参考帧）就没有行，但槽位照旧。
 */
export function buildScriptAssetAnchor(entries: readonly ScriptShotRefEntry[]): ScriptAssetAnchor {
  const lines: string[] = [];
  const references: ScriptShotRefEntry[] = [];
  entries.forEach((entry, index) => {
    if (!entry.assetId) return;
    references.push(entry);
    const wording = scriptReferenceResponsibility(entry.role);
    lines.push(`${entry.roleLabel} ${entry.name} 的参考图是 图片${index + 1}；${wording.responsibility}；${wording.prohibited}`);
  });
  if (lines.length === 0) return { references, block: '' };
  return { references, block: [SCRIPT_ASSET_ANCHOR_TITLE, ...lines, SCRIPT_ASSET_BOARD_GUIDANCE].join('\n') };
}

/**
 * 把锚定块拼到提示词前面（LibTV 同款位置：锚定表在前、镜头描述在后）。
 *
 * 空块时不加任何东西 —— 不要留下一个只有标题的空表。
 */
export function bakeScriptAssetAnchor(prompt: string, anchor: ScriptAssetAnchor): string {
  const body = prompt.trim();
  if (anchor.block.length === 0) return body;
  return body.length > 0 ? `${anchor.block}\n\n${body}` : anchor.block;
}

/**
 * 从提示词里剥掉锚定块，拿回用户/脚本原本那句话。
 *
 * 用途是判「这个提示词还是脚本给的吗」：重跑一张分镜图时，要不要把锚定块按当前资产
 * 重写，取决于节点上现在这句是不是我们当初烘进去的。拿剥完的结果与行原文比，就能区分
 * 「用户改过」与「只是我们加的锚定块过期了」—— 用户手写的句子不该被静默顶掉。
 */
export function stripScriptAssetAnchor(prompt: string): string {
  const trimmed = prompt.trimStart();
  if (!trimmed.startsWith(SCRIPT_ASSET_ANCHOR_TITLE)) return trimmed.trim();
  const rest = trimmed.slice(SCRIPT_ASSET_ANCHOR_TITLE.length);
  const separator = rest.indexOf('\n\n');
  // 只有标题、没有空行分隔：整个就是锚定块（提示词原本为空的情形）。
  if (separator === -1) return '';
  return rest.slice(separator + 2).trim();
}

/** 该提示词是不是「脚本给的」（剥掉锚定块后与行原文逐字相同）。 */
export function promptIsScriptOwned(prompt: string, rowPrompt: string): boolean {
  return stripScriptAssetAnchor(prompt) === rowPrompt.trim();
}
