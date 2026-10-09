// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  CANVAS_AGENT_AUTO_PAID_START_LIMIT,
  type CanvasAgentRunMode,
} from "./canvas-agent-run-mode";

export type CanvasAgentIntentId =
  | "one_click_film"
  | "storyboard"
  | "prompt_polish"
  | "identity_continuity"
  | "expression_directing"
  | "failure_rescue"
  | "delivery_qc"
  | "workflow_build"
  | "model_config"
  | "general_canvas";

export type CanvasAgentIntentProfile = {
  id: CanvasAgentIntentId;
  label: string;
  confidence: number;
  recommendedSkillIds: string[];
  executionFocus: string[];
  stopCondition: string;
  verification: string[];
};

type IntentRule = {
  id: CanvasAgentIntentId;
  label: string;
  skillIds: string[];
  patterns: RegExp[];
  executionFocus: string[];
  stopCondition: string;
  verification: string[];
};

const INTENT_RULES: readonly IntentRule[] = [
  {
    id: "failure_rescue",
    label: "失败救援",
    skillIds: ["failure-rescue", "canvas-director", "delivery-qc"],
    patterns: [/失败|报错|错误|failed|error|超时|timeout|卡住|96|503|400|不能生成|无法生成|没效果|断联|恢复|重试/i],
    executionFocus: ["读取最近失败任务和真实错误", "分类根因", "只修最低成本恢复点", "禁止盲目重试"],
    stopCondition: "拿到真实错误分类、可执行恢复点或明确缺少哪个前置事实。",
    verification: ["失败任务 ID 或错误原文已引用", "恢复动作不重复扣费", "已有成功产物不被覆盖"],
  },
  {
    id: "one_click_film",
    label: "一键成片",
    skillIds: ["one-click-film", "canvas-director", "template-director", "storyboard", "delivery-qc"],
    patterns: [/成片|短片|短剧|出片|完整视频|一键|全自动|做成片子|直接生成|水果短剧|广告片|宣传片/i],
    executionFocus: ["先搭可编辑生产链", "补齐脚本/分镜/生图/视频/合成节点", "按模式决定是否启动生成", "交付前质检"],
    stopCondition: "草稿模式停在完整可编辑节点图；自动生成模式停在预算内任务已启动或可恢复失败点。",
    verification: ["画布有脚本/分镜/图片/视频/合成链路", "节点已连线", "媒体生成受 run_mode 约束"],
  },
  {
    id: "storyboard",
    label: "分镜规划",
    skillIds: ["storyboard", "canvas-director", "identity-continuity", "template-director"],
    patterns: [/分镜|镜头|剧情|故事板|脚本|剧本|镜头表|shot|storyboard|beat/i],
    executionFocus: ["把剧情拆成镜头", "给每镜头素材槽和生成提示", "落成可编辑节点", "不直接生成媒体"],
    stopCondition: "镜头数量、节点结构和每镜头提示词足够进入后续生成。",
    verification: ["每个镜头有目标/主体/景别/运动", "角色与场景引用明确", "节点可继续下游生成"],
  },
  {
    id: "prompt_polish",
    label: "提示词优化",
    skillIds: ["prompt-engineer", "canvas-director", "identity-continuity"],
    patterns: [/提示词|prompt|优化|润色|负面词|正面词|参数|模型参数|参考图|参考顺序/i],
    executionFocus: ["读取目标节点/模型/参考", "给正负提示词与参数", "写回前说明依据", "不启动生成"],
    stopCondition: "优化稿、负面词、参数和适用模型都明确。",
    verification: ["保留用户核心意图", "正负提示词分离", "目标模型限制已考虑"],
  },
  {
    id: "identity_continuity",
    label: "角色一致性",
    skillIds: ["identity-continuity", "prompt-engineer", "expression-director", "canvas-director"],
    patterns: [/一致性|角色|人物|脸|人脸|宠物|怪物|身份|连续性|服装|骨相|长相|不像|跑脸/i],
    executionFocus: ["建立身份锚点", "锁定脸/体型/服装/场景连续性", "检查跨节点引用", "给修复节点计划"],
    stopCondition: "身份锚点和跨镜连续性约束可直接写入节点。",
    verification: ["角色锚点明确", "跨镜差异被列出", "修复不会破坏已有素材"],
  },
  {
    id: "expression_directing",
    label: "表演导演",
    skillIds: ["expression-director", "storyboard", "prompt-engineer"],
    patterns: [/表演|动作|眼神|情绪|微表情|呼吸|潜台词|姿态|走位|演技/i],
    executionFocus: ["把剧情目的转成动作表演", "设计视线/呼吸/节奏", "保证跨镜递进", "写入镜头提示"],
    stopCondition: "每个关键镜头都有可生成的动作和表演描述。",
    verification: ["动作单向明确", "情绪递进合理", "没有互相矛盾的运动"],
  },
  {
    id: "delivery_qc",
    label: "交付质检",
    skillIds: ["delivery-qc", "failure-rescue", "canvas-director"],
    patterns: [/质检|检查|验收|交付|成品|导出|字幕|节奏|黑帧|跳帧|安全区|最终/i],
    executionFocus: ["按阻断/严重/建议分级", "检查真实产物和节点", "只返工真实问题", "输出交付清单"],
    stopCondition: "给出通过/不通过结论和最小返工清单。",
    verification: ["有真实产物或节点依据", "问题分级明确", "交付文件路径/缺口明确"],
  },
  {
    id: "workflow_build",
    label: "工作流搭建",
    skillIds: ["template-director", "canvas-director", "storyboard"],
    patterns: [/工作流|流程|链路|节点|连线|搭建|画布|模板|结构|自动搭/i],
    executionFocus: ["选择最匹配模板", "批量创建节点和连线", "填默认参数", "验证 revision 回执"],
    stopCondition: "画布结构完成且可继续手动或自动生成。",
    verification: ["created_node_ids 存在", "连线关系完整", "revision 已更新或说明为何未写"],
  },
  {
    id: "model_config",
    label: "模型配置",
    skillIds: ["canvas-director", "failure-rescue"],
    patterns: [
      /模型|model|通道|渠道/i,
      /api|key|base url|直连|配置/i,
      /参数适配|映射|性价比|wokey|卡藏|可灵|seedance/i,
    ],
    executionFocus: ["区分 Agent/文字/视觉/生图/视频/向量/音频", "只改配置或节点映射", "不发起生成测试", "给出可验证检查"],
    stopCondition: "模型配置入口、映射关系或错误原因明确。",
    verification: ["没有付费生成调用", "模型 ID 和显示名一致", "禁用/启用状态明确"],
  },
] as const;

const DEFAULT_PROFILE: CanvasAgentIntentProfile = {
  id: "general_canvas",
  label: "画布协作",
  confidence: 0.45,
  recommendedSkillIds: ["canvas-director"],
  executionFocus: ["先理解当前画布", "最小必要修改", "写入后验证回执"],
  stopCondition: "完成用户明确要求，或停在一个清晰可执行的下一步。",
  verification: ["不猜节点 ID", "不启动非请求的媒体生成", "回复只说结果和下一步"],
};

export type CanvasAgentTaskAuthorization = {
  scope: "current_turn";
  run_mode: CanvasAgentRunMode;
  allow_structure: true;
  allow_paid_media: boolean;
  max_paid_starts: number;
  require_video_confirmation: false;
  compose_authorization_id?: string;
};

// Legacy reference classifier retained for fixture comparison and migration
// tests. Production turns are model-directed and must not use these rules for
// intent, Skill, template, canvas/workflow, or completion decisions.

// Cost protection is opt-out semantics. Mentioning "paid" by itself is not
// a refusal: requests such as "允许付费生成" must reach the real executor.
const MEDIA_SPEND_PROTECTION_PATTERN =
  /(?:不要|不许|不允许|不准|别|禁止|先不|暂不|暂时不|无需|不需要|不想).{0,16}(?:测试|调用|生成|重试|花钱|扣费|付费|预扣)|(?:不花钱|免费(?:测试|生成)?|降低成本|控制成本|省钱|零成本|不要测试生成)/i;
const EXPLICIT_MEDIA_ALLOW_PATTERN =
  /(?:允许|可以|同意|授权|放开|直接).{0,16}(?:付费|花钱|扣费|生成|出图|出视频|媒体|测试|实践|跑通)/i;
const EXPLICIT_MEDIA_RETRY_PATTERN =
  /(?:重试|重新生成|重新运行|再跑(?:一次)?|再生成|继续生成|直接生成|开始生成|启动生成)/i;
const MAINTENANCE_ONLY_PATTERN =
  /(?:检查|排查|配置|诊断|分析|质检|验收).{0,36}(?:模型|通道|渠道|api|key|映射|参数|失败|错误|任务|产物)/i;
const FAILURE_CONTEXT_PATTERN =
  /(?:失败|报错|错误|failed|error|超时|timeout|卡住|503|不能生成|没效果|断联|恢复)/i;

function uniqueSkillIds(ids: readonly string[]): string[] {
  const result: string[] = [];
  for (const id of ids) {
    if (!result.includes(id)) result.push(id);
  }
  return result;
}

export function inferCanvasAgentIntent(userText: string): CanvasAgentIntentProfile {
  const text = String(userText || "").trim();
  if (!text) return DEFAULT_PROFILE;
  const scored = INTENT_RULES.map((rule) => {
    const hits = rule.patterns.reduce((count, pattern) => count + (pattern.test(text) ? 1 : 0), 0);
    return { rule, hits };
  })
    .filter((item) => item.hits > 0)
    .sort((a, b) => b.hits - a.hits);
  const best = scored[0]?.rule;
  if (!best) return DEFAULT_PROFILE;
  const relatedSkillIds = uniqueSkillIds([
    ...best.skillIds,
    ...scored.slice(1, 3).flatMap((item) => item.rule.skillIds.slice(0, 2)),
  ]).slice(0, 6);
  return {
    id: best.id,
    label: best.label,
    confidence: Math.min(0.96, 0.62 + scored[0].hits * 0.14),
    recommendedSkillIds: relatedSkillIds,
    executionFocus: best.executionFocus,
    stopCondition: best.stopCondition,
    verification: best.verification,
  };
}

export function mergeCanvasAgentSkillIds(
  selectedSkillIds: readonly string[],
  intent: CanvasAgentIntentProfile,
): string[] {
  return uniqueSkillIds([
    ...selectedSkillIds,
    ...intent.recommendedSkillIds,
  ]).slice(0, 7);
}

export function shouldCanvasAgentProtectMediaSpend(
  userText: string,
  _intent?: CanvasAgentIntentProfile,
): boolean {
  const text = String(userText || "");
  if (MEDIA_SPEND_PROTECTION_PATTERN.test(text) || MAINTENANCE_ONLY_PATTERN.test(text)) {
    return true;
  }
  if (EXPLICIT_MEDIA_ALLOW_PATTERN.test(text)) return false;
  // This is a fail-closed cost guard, not semantic intent routing. Failure
  // investigation stays free unless the user explicitly asks to restart it.
  return FAILURE_CONTEXT_PATTERN.test(text) && !EXPLICIT_MEDIA_RETRY_PATTERN.test(text);
}

export function buildCanvasAgentTaskAuthorization(
  runMode: CanvasAgentRunMode,
  mediaSpendProtected: boolean,
  composeAuthorizationId?: string,
  maxPaidStarts?: number,
): CanvasAgentTaskAuthorization {
  const allowPaidMedia = runMode === "auto" && !mediaSpendProtected;
  const paidStartLimit = (
    typeof maxPaidStarts === "number"
    && Number.isInteger(maxPaidStarts)
    && maxPaidStarts >= 1
    && maxPaidStarts <= CANVAS_AGENT_AUTO_PAID_START_LIMIT
  )
    ? maxPaidStarts
    : CANVAS_AGENT_AUTO_PAID_START_LIMIT;
  const normalizedComposeAuthorizationId = String(
    composeAuthorizationId || "",
  ).trim();
  return {
    scope: "current_turn",
    run_mode: runMode,
    allow_structure: true,
    allow_paid_media: allowPaidMedia,
    max_paid_starts: allowPaidMedia ? paidStartLimit : 0,
    require_video_confirmation: false,
    ...(normalizedComposeAuthorizationId
      ? { compose_authorization_id: normalizedComposeAuthorizationId }
      : {}),
  };
}

export function canvasAgentStrategyForRunMode(
  runMode: CanvasAgentRunMode,
  intent: CanvasAgentIntentProfile,
  mediaSpendProtected = false,
): Record<string, unknown> {
  const autoAllowed = runMode === "auto";
  const mayStartPaidMedia = autoAllowed && !mediaSpendProtected;
  return {
    intent: {
      id: intent.id,
      label: intent.label,
      confidence: intent.confidence,
    },
    mode: runMode,
    autonomy: autoAllowed ? "draft_build_then_budget_guarded_generation" : "draft_structure_only",
    execution_focus: intent.executionFocus,
    stop_condition: intent.stopCondition,
    verification: intent.verification,
    anti_loop: {
      max_same_tool_retries: 1,
      require_new_evidence_after_failure: true,
      stop_after_two_noop_steps: true,
    },
    media_guard: {
      may_start_paid_media: mayStartPaidMedia,
      spend_protected: mediaSpendProtected,
      draft_mode_media_policy: autoAllowed ? "not_applicable" : "forbidden",
      auto_mode_media_policy: mayStartPaidMedia
        ? "image/video/audio nodes may start after parameter and dependency validation"
        : "disabled",
      video_confirmation_required: false,
    },
  };
}
