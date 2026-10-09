// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 脚本合同（`freezone.script-contract.v1`）与单镜重写的前端契约。
 *
 * 刻意独立成一个模块：`api/ops.ts` 已是登记在案的巨型文件（仓库门禁要求它只能缩小），
 * 而这两件东西都有清晰的边界——一份是服务端合同报告的形状，一份是一次请求的额外字段。
 * 放这里既能被节点与弹层直接引用，又不让 ops.ts 继续膨胀。
 */

export interface FreezoneStoryScriptRow {
  keyframe_plan?: { role?: 'action_state' | 'contact_state' | 'ending_state' | 'other' | string; state?: string; purpose?: string; required?: boolean }[] | null;
  sequence_ids?: string[] | null;
  character_state_start?: Record<string, string> | null;
  character_state_end?: Record<string, string> | null;
  shot_id?: string | null; shot_order?: number | null; display_shot_no?: string | null; shot_no?: string | number | null;
  duration?: string | number | null;
  visual_description?: string | null;
  shot_purpose?: string | null;
  duration_reason?: string | null;
  duration_policy?: 'single_action_line' | 'multi_beat' | 'fixed_timing' | '' | null;
  film_language?: string | null;
  cut_reason?: string | null;
  start_state?: string | null;
  end_state?: string | null;
  content_intent?: string | null;
  character_1?: string | null;
  character_description_1?: string | null;
  character_image_1?: string | null;
  character_2?: string | null;
  character_description_2?: string | null;
  character_image_2?: string | null;
  reference?: string | null;
  keyframe_index?: number | null;
  shot?: string | null;
  character_action?: string | null;
  emotion?: string | null;
  scene_tags?: string | null;
  scene_descriptions?: Record<string, string> | null;
  prop_descriptions?: Record<string, string> | null;
  prop_tags?: string | null;
  prop_state_start?: string | null;
  prop_state_end?: string | null;
  prop_state_change?: string | null;
  lighting_mood?: string | null;
  sound?: string | null;
  dialogue?: string | null;
  shot_prompt?: string | null;
  video_motion_prompt?: string | null;
  transition_plan?: string | null;
  generation_mode?: string | null;
  reference_requirements?: string | null;
  [key: string]: unknown;
}

/** 一条合同缺陷；`fixed` 为真表示服务端已就地改正（角色卡、风格段、技术段、时长）。 */
export interface ScriptContractIssue {
  rule_id: string;
  severity: 'blocking' | 'advisory';
  message: string;
  row_index: number;
  shot_no: string;
  field: string;
  fixed: boolean;
  detail?: Record<string, unknown>;
}

/**
 * 生成后由服务端跑的合同报告。
 *
 * 与 `rows` 分开：表格只渲染干净的表，这张报告单独说明「哪一镜不符合合同、修了什么」。
 * 角色卡逐字一致与第 7/8 段全篇唯一一直是靠提示词约束的，报告是它们第一次变成可读的结果。
 */
export interface ScriptContractReport {
  schema?: string;
  rules_checked?: string[];
  issue_count?: number;
  fixed_count?: number;
  blocking_count?: number;
  advisory_count?: number;
  /** 这份报告对应的整表内容指纹；与当前行不一致时报告已过期。 */
  rows_fingerprint?: string;
  /**
   * 可看性口径的实测值（台词占比、景别分布、动作占比、静戏最长段、最长单镜、呼吸镜数）。
   *
   * 它不进任何判定，只回答「这部片子长什么样」。上面那些规则管的是**对不对**；
   * 实测有过全绿的片子照样没法看——台词镜占九成、整片都是胸像——所以还要有这一层读数。
   */
  metrics?: ScriptContractMetrics;
  issues?: ScriptContractIssue[];
  /**
   * 一键优化的执行读数（只有走 `repair_mode=script-contract` 的那一次才有）。
   *
   * 它回答「刚才那次点击到底改了几镜、哪几镜没改成」——没有它，界面只能笼统地说
   * 「已完成」，用户无法分辨「优化了 4 镜」和「一镜都没改」。
   */
  repair?: ScriptContractRepairSummary;
}

/** 一条没能被文字模型改写的目标镜头。 */
export interface ScriptContractRepairFailure {
  row_index: number;
  rule_ids: string[];
  error: string;
}

/** `contract_report.repair` 的形状；键名与后端 `village_script_contract_repair.v1` 一致。 */
export interface ScriptContractRepairSummary {
  schema?: string;
  targets?: number;
  applied?: number;
  rejected?: number;
  failed?: number;
  failures?: ScriptContractRepairFailure[];
  rule_ids?: string[];
  /** 本轮因为行级硬伤占满目标而没被处理的全片问题；再点一次会继续推进。 */
  deferred_rule_ids?: string[];
  passes?: number;
  max_passes?: number;
  remaining_issue_count?: number;
  needs_more_repair?: boolean;
  stop_reason?: 'complete' | 'budget_exhausted' | 'review_required';
  scope?: 'sequence' | 'shot';
  target_results?: { row_index: number; row_indices?: number[]; sequence_ids?: string[]; diagnosis?: string; reason?: string; rule_ids: string[]; pass: number; outcome: string }[];
}

/** `contract_report.metrics` 的形状；键名与后端 `viewability_metrics` 一致。 */
export interface ScriptContractMetrics {
  shot_count?: number;
  total_seconds?: number;
  dialogue_shot_count?: number;
  dialogue_shot_share?: number;
  dialogue_seconds?: number;
  dialogue_seconds_share?: number;
  action_shot_count?: number;
  action_shot_share?: number;
  breath_shot_count?: number;
  longest_shot_seconds?: number;
  longest_standoff_seconds?: number;
  framing_families?: string[];
  portrait_shot_count?: number;
  portrait_share?: number;
}

/**
 * 故事脚本接口的结果。`contract_report` 与 `rows` 分开：表格只渲染那张干净的表，
 * 报告单独说明「哪一镜不符合合同、服务端修了什么」。
 */
export interface FreezoneStoryScriptResult {
  title?: string | null;
  director_plan?: FreezoneStoryDirectorPlan | null;
  rows: FreezoneStoryScriptRow[];
  contract_report?: ScriptContractReport | null;
}

export interface FreezoneStoryDirectorPlan {
  target_duration_seconds?: number | null;
  story_promise?: string;
  protagonist_goal?: string;
  core_conflict?: string;
  ending_change?: string;
  visual_bible?: {
    visual_style?: string; texture?: string; color_progression?: string;
    lighting?: string; camera_language?: string;
  };
  rhythm_curve?: string;
  sound_plan?: string;
  assumptions?: string[];
  sequences?: {
    sequence_id?: string; title?: string; dramatic_goal?: string;
    resistance?: string; escalation?: string; turn?: string; release?: string;
    staging_plan?: string; performance_plan?: string;
    shot_nos?: number[];
  }[];
}

/**
 * 单镜重写模式的请求字段。
 *
 * 给出 `currentRows` 即进入重写模式：只改 `rewriteShotId`（或 `rewriteIndex`）指定的那一行，
 * 其余行由服务端原样保留。带上整表是**必要条件**——只发一句"改第 3 镜"，后端无从保证
 * 其余行不被顺手润色。
 */
export interface ScriptVideoFeedback {
  issue_id: string; shot_id: string; video_node_id: string; row_fingerprint: string;
  timestamp_seconds: number;
  category: 'artifact' | 'continuity' | 'identity' | 'action' | 'performance' | 'composition' | 'prop_state' | 'lighting' | 'sound' | 'story_clarity';
  description: string; audience_effect: string; repair_direction: string;
}

export interface ScriptShotRewritePayloadFields {
  videoModel?: string;
  videoFeedback?: ScriptVideoFeedback[];
  /** 当前整张脚本表。 */
  currentRows?: Record<string, unknown>[];
  directorPlan?: FreezoneStoryDirectorPlan | null;
  /** 目标镜头的稳定身份（改名不改 ID），定位首选。 */
  rewriteShotId?: string;
  /** 目标行序（0 基），没有 shot_id 时的兜底。 */
  rewriteIndex?: number;
  /** 目标导演段落的稳定身份；与单镜/合同修复互斥。 */
  rewriteSequenceId?: string;
  /** 故事脚本标题；重写模式下用来保留原标题，不让模型重新起名。 */
  title?: string;
  /** `script-contract`：按合同报告批量修复问题镜头，而不是只改一镜。 */
  repairMode?: 'script-contract';
  /** 当前合同报告里未修复、且允许文字模型处理的问题。 */
  repairIssues?: ScriptContractIssue[];
  /** 最多连续验收修复轮数；默认一轮，批量优化可请求最多三轮。 */
  repairPasses?: number;
}

/**
 * 把重写字段翻成请求体片段；不在重写模式时返回空对象。
 *
 * 行对象在这里**拷一份**发出：请求层不该和画布 store 里的对象共享引用，
 * 否则任何后续的本地编辑都会改到已经发出去的那份报文上。
 */
export function storyScriptRewriteBody(
  payload: ScriptShotRewritePayloadFields,
): Record<string, unknown> {
  const rows = payload.currentRows;
  const body: Record<string, unknown> = payload.videoModel != null ? { video_model: payload.videoModel } : {};
  if (!rows || rows.length === 0) return body;
  body.current_rows = rows.map((row) => ({ ...row }));
  if (payload.videoFeedback?.length) body.video_feedback = structuredClone(payload.videoFeedback);
  if (payload.rewriteShotId != null) body.rewrite_shot_id = payload.rewriteShotId;
  if (payload.rewriteIndex != null) body.rewrite_index = payload.rewriteIndex;
  if (payload.rewriteSequenceId != null) body.rewrite_sequence_id = payload.rewriteSequenceId;
  if (payload.title != null) body.title = payload.title;
  if (payload.directorPlan != null) body.director_plan = structuredClone(payload.directorPlan);
  if (payload.repairMode != null) body.repair_mode = payload.repairMode;
  if (payload.repairPasses != null) body.repair_passes = payload.repairPasses;
  if (payload.repairIssues && payload.repairIssues.length > 0) {
    body.repair_issues = payload.repairIssues.map((issue) => ({
      ...issue,
      detail: issue.detail ? { ...issue.detail } : {},
    }));
  }
  return body;
}
