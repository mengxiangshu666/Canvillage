// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 脚本（分镜表）字段规范。
 *
 * 字段顺序与动态角色列规则对齐 LibTV 脚本节点的分镜表（`StoryboardRowCli` 18 字段 +
 * 画布按各行 `characters` 最大数量动态加「角色」列）：这里把「角色 N」拆成可重复的
 * 槽位模板，表格按固定 2 槽渲染，导出按各行实际占用槽位数的最大值展开。
 */

export type ScriptCellRender = 'text' | 'image';

/**
 * 列的层级：决定默认可不可见（见 `scriptColumns.ts`）。
 * - `core`：读「这一镜要拍成什么样」必须看到的列；
 * - `detail`：明细列 —— 角色描述 / 角色图（后端回填）、参考帧、氛围与音效这几族，
 *   导出 CSV 里一个不少，但默认收起来，让表格宽度落在能一屏读完的量级。
 */
export type ScriptFieldTier = 'core' | 'detail';

export interface ScriptFieldDef {
  key: string;
  label: string;
  /** 像素宽度（既作为 min-width 也作为 width，避免列在长文本下抖动）。 */
  widthPx: number;
  render?: ScriptCellRender;
  /** 短数字列居中、等宽数字，便于扫读。 */
  numeric?: boolean;
  tier: ScriptFieldTier;
}

/** 角色槽位数：与后端 FreezoneStoryScriptRow 的 character_1 / character_2 对齐。 */
export const SCRIPT_CHARACTER_SLOT_COUNT = 2;

/** 表格前段：镜号 / 时长 / 画面描述（对应 LibTV 的 shotNumber / durationSeconds / plotDescription）。 */
const SCRIPT_LEAD_FIELDS: ScriptFieldDef[] = [
  { key: 'shot_no', label: '镜号', widthPx: 60, numeric: true, tier: 'core' },
  { key: 'duration', label: '时长', widthPx: 80, numeric: true, tier: 'core' },
  { key: 'visual_description', label: '画面描述', widthPx: 200, tier: 'core' },
];

/**
 * 角色槽位字段模板：`{slot}` 会替换成 1..N。
 * LibTV 是「角色 / 角色描述 / 角色图」三连列，顺序一致。
 */
const SCRIPT_CHARACTER_SLOT_FIELDS: ScriptFieldDef[] = [
  { key: 'character_{slot}', label: '角色{slot}', widthPx: 120, tier: 'core' },
  // 角色描述与角色图都是**后端回填**的资产明细（见 ports/story_script.py 的字段说明），
  // 开拍前核对交给资产视图，表格里默认收起来。
  { key: 'character_description_{slot}', label: '角色描述{slot}', widthPx: 180, tier: 'detail' },
  { key: 'character_image_{slot}', label: '角色图{slot}', widthPx: 80, render: 'image', tier: 'detail' },
];

/**
 * 表格后段：参考 / 景别 / 角色动作 / 情绪 / 场景标签 / 光影氛围 / 音效 / 对白 /
 * 分镜提示词 / 视频运动提示词。
 *
 * 「分镜提示词」「视频运动提示词」就是 LibTV 的 imageGenerationPrompt /
 * videoMotionPrompt —— 生成分镜图与视频时分别读它们。
 */
const SCRIPT_TRAIL_FIELDS: ScriptFieldDef[] = [
  { key: 'reference', label: '参考', widthPx: 80, render: 'image', tier: 'detail' },
  { key: 'shot', label: '景别', widthPx: 120, tier: 'core' },
  { key: 'character_action', label: '角色动作', widthPx: 120, tier: 'core' },
  { key: 'emotion', label: '情绪', widthPx: 120, tier: 'core' },
  { key: 'scene_tags', label: '场景标签', widthPx: 120, tier: 'core' },
  // 道具与场景同族（LibTV 资产台账的三族就是 characters / scenes / props），但默认
  // 收起来：它不是每一镜都有，一屏里为它让出 120px 会挤掉一列核心列。
  { key: 'prop_tags', label: '道具标签', widthPx: 120, tier: 'detail' },
  { key: 'prop_state_start', label: '道具起始状态', widthPx: 150, tier: 'detail' },
  { key: 'prop_state_end', label: '道具结束状态', widthPx: 150, tier: 'detail' },
  { key: 'prop_state_change', label: '道具状态变化', widthPx: 180, tier: 'detail' },
  { key: 'lighting_mood', label: '光影氛围', widthPx: 120, tier: 'detail' },
  { key: 'sound', label: '音效', widthPx: 120, tier: 'detail' },
  { key: 'dialogue', label: '对白', widthPx: 120, tier: 'core' },
  { key: 'shot_prompt', label: '分镜提示词', widthPx: 200, tier: 'core' },
  { key: 'video_motion_prompt', label: '视频运动提示词', widthPx: 200, tier: 'core' },
  { key: 'transition_plan', label: '衔接方式', widthPx: 140, tier: 'detail' },
  { key: 'shot_purpose', label: '观看目的', widthPx: 180, tier: 'detail' },
  { key: 'duration_reason', label: '时长依据', widthPx: 200, tier: 'detail' },
  { key: 'film_language', label: '拍法组合', widthPx: 200, tier: 'detail' },
  { key: 'cut_reason', label: '切镜理由', widthPx: 180, tier: 'detail' },
  { key: 'start_state', label: '首帧状态', widthPx: 180, tier: 'detail' },
  { key: 'end_state', label: '切点状态', widthPx: 180, tier: 'detail' },
  { key: 'generation_mode', label: '推荐生成方式', widthPx: 150, tier: 'detail' },
  { key: 'reference_requirements', label: '参考需求', widthPx: 180, tier: 'detail' },
  { key: 'keyframe_plan', label: '状态关键画面计划', widthPx: 240, tier: 'detail' },
];

function materializeSlotFields(slot: number): ScriptFieldDef[] {
  return SCRIPT_CHARACTER_SLOT_FIELDS.map((field) => ({
    ...field,
    key: field.key.replace('{slot}', String(slot)),
    label: field.label.replace('{slot}', String(slot)),
  }));
}

/**
 * 一套完整字段序列：前段 + `slotCount` 个角色槽 + 后段。
 * 表格用固定 2 槽；CSV 用各行实际占用槽位数的最大值。
 */
export function scriptFieldSequence(slotCount = SCRIPT_CHARACTER_SLOT_COUNT): ScriptFieldDef[] {
  const slots = Math.max(1, Math.min(SCRIPT_CHARACTER_SLOT_COUNT, Math.floor(slotCount)));
  const sequence: ScriptFieldDef[] = [...SCRIPT_LEAD_FIELDS];
  for (let slot = 1; slot <= slots; slot += 1) {
    sequence.push(...materializeSlotFields(slot));
  }
  sequence.push(...SCRIPT_TRAIL_FIELDS);
  return sequence;
}

/** 表格列（与后端 FreezoneStoryScriptRow 一一对应）。 */
export const SCRIPT_TABLE_FIELDS: ScriptFieldDef[] = scriptFieldSequence(
  SCRIPT_CHARACTER_SLOT_COUNT,
);
