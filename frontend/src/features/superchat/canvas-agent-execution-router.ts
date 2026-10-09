// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export type CanvasAgentExecutionLane =
  | "direct_chat"
  | "plan_only"
  | "canvas_execute"
  | "workflow";

export interface CanvasAgentExecutionRoute {
  lane: CanvasAgentExecutionLane;
  reason: string;
  mayWriteCanvas: boolean;
  mayStartWorkflow: boolean;
}

export interface CanvasAgentExecutionContext {
  /** @deprecated Semantic routing belongs to the model and server ActionRouter. */
  intentId?: string | null;
}

const GREETING_PATTERN = /^(?:嗨|哈[喽啰]|你好|您好|在吗|hi|hello|hey)[\s，,。.!！?？~～]*$/iu;
const PLAN_FIRST_PATTERN = /(?:先给|只给|暂时只|这轮只).{0,36}(?:方案|提案|规划|计划|路径|建议|分析|检查)|(?:先|只|暂时|这轮).{0,10}(?:聊聊|聊一下|讨论|分析|诊断|看看|给方案|说说)|(?:方案|提案|规划|计划|最短路径).{0,12}(?:先|再).{0,6}(?:确认|采用|定|看|说)|(?:需要|如果|要是).{0,24}(?:改|调整|写入|创建|生成|执行).{0,18}(?:先提案|先说明|先确认)|(?:生成前|执行前|写入前|改结构前).{0,18}(?:说明|提案|确认)/iu;
/**
 * A no-write boundary only counts when the negation binds its own action:
 * the verb must sit directly behind the negation (one adverb is allowed).
 * Earlier gaps of `.{0,16}` let an unrelated clause reach the verb, so
 * "不要只给我方案，直接动手" and "别问了，直接动手" were read as plan-only
 * even though the user was demanding exactly the opposite. Confirm/select
 * wording needs the same treatment: 方案…再确认 is plan-first, 方案我确认了 is
 * a go-ahead.
 */
const NO_WRITE_PATTERN = /(?:先别|暂不|暂时不|不要|别|禁止)(?:再|急着|马上|立刻|直接)?(?:改画布|动画布|写入|创建节点|修改节点|启动流程|执行流程|动手|操作画布)/iu;
/** 「不要只给方案」否定的是「只给方案」，不能当成「先给方案」。 */
const NEGATED_PLAN_MARKER = /(?:不要|不用|无需|先别|暂不|暂时不|禁止|别)\s*(?:再)?\s*(?:先给|只给|暂时只|这轮只|先聊|只聊|先说|只说|先看|只看)/gu;
/**
 * Apply only explicit no-action boundaries before the model turn. Creative
 * semantics and canvas-vs-workflow routing belong to the model plus the
 * server-owned ActionRouter, not to browser keyword classifiers.
 */
export function routeCanvasAgentExecution(
  userText: string,
  _context: CanvasAgentExecutionContext = {},
): CanvasAgentExecutionRoute {
  const text = String(userText || "").trim();
  if (!text) {
    return {
      lane: "direct_chat",
      reason: "empty_or_chat",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    };
  }
  if (GREETING_PATTERN.test(text)) {
    return {
      lane: "direct_chat",
      reason: "explicit_greeting",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    };
  }
  const planText = text.replace(NEGATED_PLAN_MARKER, "");
  if (PLAN_FIRST_PATTERN.test(planText) || NO_WRITE_PATTERN.test(text)) {
    return {
      lane: "plan_only",
      reason: "user_requested_plan_before_write",
      mayWriteCanvas: false,
      mayStartWorkflow: false,
    };
  }
  return {
    lane: "canvas_execute",
    reason: "model_decides_from_full_request",
    mayWriteCanvas: true,
    mayStartWorkflow: false,
  };
}
