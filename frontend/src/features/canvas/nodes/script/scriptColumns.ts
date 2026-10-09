// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { scriptFieldSequence, type ScriptFieldDef } from './scriptFields';

/**
 * 分镜表列显隐 + 按节点宽度选列。
 *
 * 表格是宽表，而脚本节点默认宽 800px
 * （`DEFAULT_WIDTH_WITH_RESULT`，与 LibTV 同款）—— 内容区只有约 782px，一屏看得到
 * 不到三分之一，用户得横向滚动才找得到「这条镜头怎么拍」。LibTV 的解法是 `viewMode`
 * 之外的 `views[].tableConfig.columnVisibility`（按 key 布尔的列集，缺省 `?? true`
 * **默认可见** —— 与我们「存被收起的列」的黑名单语义等价；「全显」/「隐藏全部」两枚按钮
 * 就是 `SCRIPT_FIELD_META.forEach` 一把写 true/false。面板挂在表格工具条的两枚 tab
 * 「字段」/「过滤」下。**同层的 `columnFilters` 是行过滤，contains/equals/gt/lt，
 * 我们没有做，别跟列集混**）。这里落成节点上的两个字段：
 *
 * - `columnMode`：`default`（宽度自适应、核心列优先）/ `all`（全列）/ `manual`（逐列勾）；
 * - `hiddenColumns`：manual 模式下被收起的列键；
 * - 缺席时一律按 `default` 走，老画布无需迁移。
 *
 * **还没抄的那半段（LibTV 真正的宽度算法）**：它的列规格 `oR` 每列带一种 kind ——
 * `fixed`（固定宽）/ `compact`（min–max，富余时先按比例长到 max）/ `flex`（min + 权重分
 * 剩余宽，最后一列吸收余数），例：`plotDescription{flex,min:200,weight:7}`、
 * `shotNumber{compact,min:40,max:68}`、`operations{compact,min:40,max:68}`。
 * 也就是说 **LibTV 默认是把列压窄、不是把列藏起来**，藏是用户主动的动作。我们的列是
 * 固定像素宽 + 按可用宽度丢/带回列，只解决了「看不全」，没解决「列本身能屈能伸」。
 * 要做等价效果得把 `scriptFields.ts` 的 `widthPx` 升级成 kind 规格 —— 单独立项。
 *
 * 「存被收起的列」而不是「存可见的列」是刻意的：列集会随角色槽位展开而增长，
 * 黑名单让新增列**默认可见**，白名单会让它们凭空消失。
 *
 * 选列是**贪心带回**（必留列先摆上，再按优先级依次塞放得下的列），不是「按次序丢到
 * 放得下为止」：同样 1182px 可用宽度，丢法会停在 980px 就收手（必留 740 + 下一列 240
 * 已超），带法能补进一列 80px 的参考帧 —— 同样的宽度多看一列。放不下的列只跳过不终止。
 * **这两个宽度数字是按列宽表推算的，不是量出来的。**
 */

export type ScriptColumnMode = 'default' | 'all' | 'manual';

export const SCRIPT_COLUMN_MODES: readonly ScriptColumnMode[] = ['default', 'all', 'manual'];

/** 读外部输入（节点 data）时收敛到已知模式，未知值回落默认。 */
export function resolveScriptColumnMode(value: unknown): ScriptColumnMode {
  return typeof value === 'string' && (SCRIPT_COLUMN_MODES as readonly string[]).includes(value)
    ? (value as ScriptColumnMode)
    : 'default';
}

function readHiddenColumns(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter((entry): entry is string => typeof entry === 'string');
}

/**
 * 无论节点多窄都留着的列。
 *
 * 少了任何一个，这一行就不再是「可执行的镜头」：镜号（这是第几镜）、时长、画面描述，
 * 以及两条提示词（生成分镜图与视频分别读它们）。这 5 列合计 740px，正好压住默认
 * 节点宽度下的可用空间 —— 再宽一档才开始带回其余核心列。
 */
export const FIT_ALWAYS_KEEP: readonly string[] = [
  'shot_no',
  'duration',
  'visual_description',
  'shot_prompt',
  'video_motion_prompt',
];

/**
 * 除必留列外，宽度每宽一档就优先带回来的列。
 *
 * 次序口径是「这一列缺了之后损失多少」：怎么拍（景别）> 有谁（角色 1）> 在做什么
 * （角色动作）> 什么调子（情绪 / 对白 / 场景标签）> 第二主角。角色只排在景别之后是
 * 因为角色图与角色描述由资产视图完整承担，而景别只在这里出现一次。
 */
export const CORE_ADD_BACK_ORDER: readonly string[] = [
  'shot',
  'character_1',
  'character_action',
  'emotion',
  'dialogue',
  'scene_tags',
  'character_2',
];

/** 明细列带回的次序：全部排在核心列之后，宽度很富余时才会出现。 */
export const DETAIL_ADD_BACK_ORDER: readonly string[] = [
  'reference',
  'transition_plan',
  'shot_purpose',
  'duration_reason',
  'film_language',
  'cut_reason',
  'start_state',
  'end_state',
  'generation_mode',
  'reference_requirements',
  'keyframe_plan',
  'character_description_1',
  'character_description_2',
  'prop_tags',
  'prop_state_start',
  'prop_state_end',
  'prop_state_change',
  'lighting_mood',
  'sound',
  'character_image_1',
  'character_image_2',
];

/**
 * 选列的完整优先级：必留列（隐式，永远第一）+ 核心列 + 明细列。
 * 与 `scriptFields.ts` 的字段清单必须一一对应（有测试兜）。
 */
export const SCRIPT_COLUMN_PRIORITY: readonly string[] = [
  ...CORE_ADD_BACK_ORDER,
  ...DETAIL_ADD_BACK_ORDER,
];

/** 字段序列的像素总宽。 */
export function fieldsTotalWidth(fields: readonly ScriptFieldDef[]): number {
  return fields.reduce((sum, field) => sum + field.widthPx, 0);
}

/**
 * 贪心选列：必留列先摆上，再按 `priority` 依次尝试塞入放得下的列。
 * 返回**要收起的列键**（与 `hiddenColumns` 同口径），不是可见列。
 */
export function pickColumnsWithinWidth(params: {
  fields: readonly ScriptFieldDef[];
  availableWidthPx: number;
  priority: readonly string[];
}): string[] {
  const keep = new Set(FIT_ALWAYS_KEEP);
  const byKey = new Map(params.fields.map((field) => [field.key, field]));
  let total = fieldsTotalWidth(params.fields.filter((field) => keep.has(field.key)));

  for (const key of params.priority) {
    const field = byKey.get(key);
    if (!field || keep.has(key)) continue;
    if (total + field.widthPx > params.availableWidthPx) continue;
    keep.add(key);
    total += field.widthPx;
  }

  return params.fields.filter((field) => !keep.has(field.key)).map((field) => field.key);
}

/** 一次算清「当前该显示哪些列」。 */
export function resolveVisibleScriptColumns(params: {
  slotCount?: number;
  mode: unknown;
  hidden?: unknown;
  /** 节点内容区可用宽度（像素）；缺省视作无限宽（单测与 CSV 用）。 */
  availableWidthPx?: number;
}): ScriptFieldDef[] {
  const fields = scriptFieldSequence(params.slotCount);
  const mode = resolveScriptColumnMode(params.mode);
  const availableWidthPx = params.availableWidthPx ?? Number.POSITIVE_INFINITY;
  let hiddenKeys: string[];

  if (mode === 'all') {
    hiddenKeys = [];
  } else if (mode === 'manual') {
    // 逐列勾选过就完全听黑名单；没勾过（例如刚切到自定义）退回默认的宽度自适应。
    hiddenKeys =
      readHiddenColumns(params.hidden) ??
      pickColumnsWithinWidth({
        fields,
        availableWidthPx,
        priority: CORE_ADD_BACK_ORDER,
      });
  } else {
    // default：核心列优先带回，明细列一律不进来（那是「全部列」与逐列勾的事）。
    hiddenKeys = pickColumnsWithinWidth({
      fields,
      availableWidthPx,
      priority: CORE_ADD_BACK_ORDER,
    });
  }

  const hidden = new Set(hiddenKeys);
  return fields.filter((field) => !hidden.has(field.key));
}

/** 列菜单里逐列勾选时用：翻转一列，并回到 manual 模式。 */
export function toggleScriptColumn(params: {
  fields: readonly ScriptFieldDef[];
  visibleKeys: readonly string[];
  key: string;
}): { mode: ScriptColumnMode; hiddenColumns: string[] } {
  const visible = new Set(params.visibleKeys);
  if (visible.has(params.key)) {
    // 至少留一列，免得整张表空掉。
    if (visible.size <= 1) {
      return {
        mode: 'manual',
        hiddenColumns: params.fields.filter((f) => !visible.has(f.key)).map((f) => f.key),
      };
    }
    visible.delete(params.key);
  } else {
    visible.add(params.key);
  }
  return {
    mode: 'manual',
    hiddenColumns: params.fields.filter((f) => !visible.has(f.key)).map((f) => f.key),
  };
}

/** 列菜单上「已显示 n / 共 m 列」那句计数。 */
export function scriptColumnSummary(visible: number, total: number): string {
  return `${visible} / ${total} 列`;
}

/**
 * `toggleScriptColumn` 的结果落成**节点数据补丁**。
 *
 * 存在的理由是键名：纯函数用 `mode`，节点数据用 `columnMode`。直接
 * `updateNodeData(id, toggleScriptColumn(...))` 会把档位写进一个没人读的 `data.mode`，
 * 而 `CanvasNodeData` 的索引签名又会吞掉这个拼写错误 —— 症状是「菜单勾选态跟着变、
 * 表格列数纹丝不动」（本项在真机上就是这么挂的）。改名只在这里做一次，并由单测钉住。
 */
export function toggleScriptColumnPatch(params: {
  fields: readonly ScriptFieldDef[];
  visibleKeys: readonly string[];
  key: string;
}): { columnMode: ScriptColumnMode; hiddenColumns: string[] } {
  const next = toggleScriptColumn(params);
  return { columnMode: next.mode, hiddenColumns: next.hiddenColumns };
}

/**
 * 列菜单要用的完整字段序列。
 *
 * 单独导出（而不是让调用方自己 import `scriptFieldSequence`）是为了把「菜单永远列
 * 全部字段、表格才按模式收列」这条分工钉在一个地方 —— 菜单如果跟着表格一起收，
 * 用户就再也没有地方把列打开。
 */
export function scriptFieldSequenceForToggle(): ScriptFieldDef[] {
  return scriptFieldSequence();
}

export { scriptFieldSequence };
