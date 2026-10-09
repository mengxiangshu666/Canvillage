// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import {
  CANVAS_STARTER_WORKFLOWS,
  type CanvasStarterWorkflowDefinition,
} from "@/features/canvas/application/starterWorkflows";
import {
  recommendCanvasStarterWorkflows,
  starterWorkflowAgentBrief,
  starterWorkflowPresentation,
} from "@/features/canvas/application/starterWorkflowCatalog";
import type { CanvasAgentRunMode } from "@/features/superchat/canvas-agent-run-mode";
import {
  type CanvasAgentIntentProfile,
  inferCanvasAgentIntent,
  mergeCanvasAgentSkillIds,
  shouldCanvasAgentProtectMediaSpend,
} from "@/features/superchat/canvas-agent-intelligence";
import {
  heroSkillIdsForIntent,
  type CanvasAgentHeroSkillId,
} from "@/features/superchat/canvas-agent-hero-skills";
import {
  buildCanvasAgentTaskAuthorization,
  type CanvasAgentTaskAuthorization,
} from "@/features/superchat/canvas-agent-intelligence";
import {
  routeCanvasAgentExecution,
  type CanvasAgentExecutionRoute,
} from "@/features/superchat/canvas-agent-execution-router";

/**
 * Reference-only deterministic fixture used for regression comparisons.
 * Production requests never serialize this plan; the Village Agent understands the full
 * request and the server ActionRouter chooses the execution lane from task facts.
 */

export type CanvasAgentKernelActionType =
  | "insert_starter_workflow"
  | "create_canvas_node"
  | "create_image_prompt_node"
  | "create_video_prompt_node"
  | "create_shot_sequence"
  | "update_node_prompt"
  | "update_node_label"
  | "move_node"
  | "duplicate_node"
  | "delete_node"
  | "connect_nodes"
  | "remove_edge"
  | "focus_node"
  | "select_node"
  | "inspect_canvas"
  | "read_failure_evidence"
  | "repair_minimum_variable"
  | "run_generation_nodes";

export type CanvasAgentKernelWorkflowCandidate = {
  id: string;
  title: string;
  reason: string;
  requiredInputs: string;
  modelHint: string;
  templateKind?: string;
  deliveryLevel?: string;
  requiredRoles?: string[];
  outputs?: string[];
  doesNotProduce?: string[];
};

export type CanvasAgentKernelDraftAction = {
  type: CanvasAgentKernelActionType;
  label: string;
  reason: string;
  command?: Record<string, unknown>;
};

export type CanvasAgentKernelPlan = {
  intent: CanvasAgentIntentProfile;
  skillIds: string[];
  heroSkillIds: CanvasAgentHeroSkillId[];
  executionRoute: CanvasAgentExecutionRoute;
  taskAuthorization: CanvasAgentTaskAuthorization;
  workflowCandidates: CanvasAgentKernelWorkflowCandidate[];
  /**
   * A starter graph is an explicit opt-in.  Candidate workflows remain
   * reference data for the model/UI, but are never selected implicitly.
   */
  starterWorkflowId?: string;
  useStarterWorkflow: boolean;
  draftActions: CanvasAgentKernelDraftAction[];
  responseMode: CanvasAgentRunMode;
  immediateGuidance: string[];
  spendGuard: {
    mayStartPaidMedia: boolean;
    reason: string;
  };
};

type KernelNodeRef = {
  id: string;
  type: string;
  label: string;
  hasPrompt?: boolean;
  hasImage?: boolean;
  hasVideo?: boolean;
};

const WORKFLOW_FALLBACK_BY_INTENT: Partial<Record<CanvasAgentIntentProfile["id"], string[]>> = {
  one_click_film: ["story-continuity-film", "storyboard-to-video", "video-composition"],
  storyboard: ["storyboard-to-video", "story-continuity-film"],
  workflow_build: ["storyboard-to-video", "single-reference-video", "video-composition"],
  identity_continuity: ["multi-reference-video"],
  prompt_polish: ["refine-then-video"],
  expression_directing: ["storyboard-to-video", "motion-reference-redraw"],
  delivery_qc: ["video-composition"],
};

const STRUCTURE_INTENTS = new Set<CanvasAgentIntentProfile["id"]>([
  "one_click_film",
  "storyboard",
  "workflow_build",
]);

function uniqueWorkflows(workflows: readonly CanvasStarterWorkflowDefinition[]): CanvasStarterWorkflowDefinition[] {
  const seen = new Set<string>();
  const result: CanvasStarterWorkflowDefinition[] = [];
  for (const workflow of workflows) {
    if (seen.has(workflow.id)) continue;
    seen.add(workflow.id);
    result.push(workflow);
  }
  return result;
}

function fallbackWorkflowsForIntent(intent: CanvasAgentIntentProfile): CanvasStarterWorkflowDefinition[] {
  const ids = WORKFLOW_FALLBACK_BY_INTENT[intent.id] ?? [];
  return ids
    .map((id) => CANVAS_STARTER_WORKFLOWS.find((workflow) => workflow.id === id))
    .filter((workflow): workflow is CanvasStarterWorkflowDefinition => Boolean(workflow));
}

function workflowCandidatesFor(userText: string, intent: CanvasAgentIntentProfile): CanvasAgentKernelWorkflowCandidate[] {
  const recommended = recommendCanvasStarterWorkflows(userText, 3);
  const fallbacks = fallbackWorkflowsForIntent(intent);
  return uniqueWorkflows([...recommended, ...fallbacks])
    .slice(0, 3)
    .map((workflow) => {
      const brief = starterWorkflowAgentBrief(workflow);
      const presentation = starterWorkflowPresentation(workflow.id);
      return {
        id: String(brief.id),
        title: brief.title,
        reason: `${intent.label} · ${presentation.category}`,
        requiredInputs: brief.required_inputs,
        modelHint: brief.model_hint,
        templateKind: brief.template_kind,
        deliveryLevel: brief.delivery_level,
        requiredRoles: [...brief.required_roles],
        outputs: [...brief.outputs],
        doesNotProduce: [...brief.does_not_produce],
      };
    });
}

function compactNode(node: KernelNodeRef | null | undefined): Record<string, unknown> | null {
  if (!node) return null;
  return {
    id: node.id,
    type: node.type,
    label: node.label,
    has: [
      node.hasPrompt ? "prompt" : null,
      node.hasImage ? "image" : null,
      node.hasVideo ? "video" : null,
    ].filter(Boolean),
  };
}

function buildDraftActions(input: {
  intent: CanvasAgentIntentProfile;
  workflowCandidates: readonly CanvasAgentKernelWorkflowCandidate[];
  starterWorkflowId?: string;
  useStarterWorkflow: boolean;
  runMode: CanvasAgentRunMode;
  selectedNode?: KernelNodeRef | null;
  pinnedNodes: readonly KernelNodeRef[];
}): CanvasAgentKernelDraftAction[] {
  const {
    intent,
    workflowCandidates,
    starterWorkflowId,
    runMode,
    selectedNode,
    pinnedNodes,
  } = input;
  const actions: CanvasAgentKernelDraftAction[] = [
    {
      type: "inspect_canvas",
      label: "读取当前画布状态",
      reason: "先确认选中节点、固定引用、断链和 revision，避免 Agent 凭空说话。",
      command: {
        name: "freezone_get_canvas_snapshot",
        selected_node: compactNode(selectedNode),
        pinned_node_ids: pinnedNodes.map((node) => node.id),
      },
    },
  ];

  // Recommendations are not selections.  A flag-only opt-in is resolved by
  // the runtime; this reference fixture emits a graph only for an explicit ID.
  if (starterWorkflowId) {
    const selectedWorkflow = workflowCandidates.find((workflow) => workflow.id === starterWorkflowId);
    actions.push({
      type: "insert_starter_workflow",
      label: `套入工作流：${selectedWorkflow?.title ?? starterWorkflowId}`,
      reason: "用户已明确选择画布模板；模板写入仍需服务端回执验证。",
      command: {
        name: "freezone_emit_canvas_command",
        command: "insert_starter_workflow",
        workflow_id: starterWorkflowId,
        mode: "draft_structure_first",
        may_start_generation: runMode === "auto",
      },
    });
  }

  if (intent.id === "prompt_polish" || intent.id === "identity_continuity" || intent.id === "expression_directing") {
    actions.push({
      type: "update_node_prompt",
      label: "改写目标节点提示词",
      reason: "先把正向提示词、负面词、引用顺序、模型限制写清楚，再由用户或自动模式决定是否生成。",
      command: {
        name: "freezone_emit_canvas_command",
        command: "update_node_prompt",
        target_node_id: selectedNode?.id ?? null,
        pinned_node_ids: pinnedNodes.map((node) => node.id),
        auto_apply_expected: true,
      },
    });
  }

  if (intent.id === "storyboard") {
    actions.push({
      type: "create_shot_sequence",
      label: "编译并写入分镜序列",
      reason: "一次提交完整镜头链，减少逐节点往返；每镜都按可拍合同验收。",
      command: {
        name: "freezone_emit_canvas_command",
        command: "create_shot_sequence",
        required_fields: ["prompts", "display_name", "placement"],
        prompt_contract: ["主体", "动作起点/过程/终点", "景别/机位", "运镜", "可切点"],
        placement: { anchor: "viewport_center", layout: "column" },
        may_start_generation: false,
      },
    });
  }

  if (intent.id === "failure_rescue") {
    actions.push({
      type: "read_failure_evidence",
      label: "读取真实失败边界",
      reason: "先锁定失败任务、错误原文、成功资产和最低恢复点，避免整链重跑。",
      command: {
        tools: [
          "village_canvas_list_tasks",
          "village_canvas_get_task",
          "village_canvas_pipeline_status",
          "village_canvas_get_production_control",
        ],
        evidence: ["task_or_run_id", "error_text", "failed_stage", "successful_assets", "retry_state"],
      },
    });
    actions.push({
      type: "repair_minimum_variable",
      label: "只修一个高影响变量",
      reason: "参数或引用问题先修画布；瞬态问题确认旧任务终止且不重复计费后再决定重试。",
      command: {
        structure_tool: "freezone_emit_canvas_command",
        retry_tool: "freezone_retry_node",
        stop_tool: "freezone_stop_task",
        generation_proposal_tool: "freezone_propose_generation",
        max_same_failure_retries: 1,
      },
    });
  } else if (intent.id === "delivery_qc" || intent.id === "model_config") {
    actions.push({
      type: "inspect_canvas",
      label: intent.id === "model_config" ? "检查模型映射" : "检查真实失败/产物",
      reason: "这类任务先定位配置、错误或质检缺口，默认不启动付费媒体。",
      command: {
        name: "freezone_get_canvas_snapshot",
        include_recent_tasks: true,
        include_model_bindings: intent.id === "model_config",
      },
    });
  }

  if (STRUCTURE_INTENTS.has(intent.id)) {
    actions.push({
      type: "connect_nodes",
      label: "补齐节点连线和默认参数",
      reason: "让画布从聊天回复变成可继续执行的生产链，而不是只输出文字方案。",
      command: {
        name: "freezone_emit_canvas_command",
        command: "connect_nodes",
        policy: "connect_new_workflow_nodes_only",
      },
    });
  }

  if (runMode === "auto" && STRUCTURE_INTENTS.has(intent.id)) {
    actions.push({
      type: "run_generation_nodes",
      label: "启动真实节点任务",
      reason: "结构、模型、参数和引用通过检查后，直接把目标节点送入正式任务队列并写回任务句柄。",
      command: {
        name: "freezone_run_node",
        retry: "freezone_retry_node",
        stop: "freezone_stop_task",
        requires: ["canvas_id", "node_id", "command_id", "task_authorization"],
        verify: ["task_key", "task_type", "job_id", "server_applied", "revision"],
      },
    });
  }

  return actions.slice(0, 5);
}

function guidanceFor(input: {
  intent: CanvasAgentIntentProfile;
  executionRoute: CanvasAgentExecutionRoute;
  runMode: CanvasAgentRunMode;
  workflowCandidates: readonly CanvasAgentKernelWorkflowCandidate[];
  starterWorkflowId?: string;
  useStarterWorkflow: boolean;
  selectedNode?: KernelNodeRef | null;
  pinnedNodes: readonly KernelNodeRef[];
}): string[] {
  const guidance = [
    `本地识别：${input.intent.label}，先按画布动作执行，不要只聊天。`,
    `本轮路由：${input.executionRoute.lane}（${input.executionRoute.reason}）。`,
    input.runMode === "auto" ? "自动模式：结构与参数通过检查后，用 freezone_run_node 直接启动真实节点任务。" : "草稿模式：只搭节点、连线、写提示词，禁止启动生成。",
  ];
  if (STRUCTURE_INTENTS.has(input.intent.id) || input.intent.id === "general_canvas") {
    guidance.push("新节点优先使用 placement(anchor=viewport_center, layout=grid)，浏览器会按实时视口落点；不要为读取视口重复拉快照。");
  }
  if (input.starterWorkflowId) {
    guidance.push(`已选择模板：${input.starterWorkflowId}。`);
  } else if (input.useStarterWorkflow) {
    guidance.push("已允许服务端选择模板；候选列表本身不产生画布写入。");
  }
  if (input.selectedNode) guidance.push(`当前选中节点：${input.selectedNode.label} (${input.selectedNode.type})。`);
  if (input.pinnedNodes.length > 0) guidance.push(`固定引用：${input.pinnedNodes.length} 个，优先保持引用关系。`);
  return guidance.slice(0, 5);
}

export function buildFastCanvasAgentKernelPlan(input: {
  userText: string;
  selectedSkillIds: readonly string[];
  runMode: CanvasAgentRunMode;
  /** Explicit template selection; omitted means dynamic composition. */
  starterWorkflowId?: string | null;
  /** Explicitly opt into template selection when no ID is supplied. */
  useStarterWorkflow?: boolean;
  pinnedNodes?: readonly KernelNodeRef[];
  selectedNode?: KernelNodeRef | null;
}): CanvasAgentKernelPlan {
  const userText = String(input.userText || "").trim();
  const intent = inferCanvasAgentIntent(userText);
  const skillIds = mergeCanvasAgentSkillIds(input.selectedSkillIds, intent);
  const runMode: CanvasAgentRunMode = input.runMode === "auto" ? "auto" : "draft";
  const pinnedNodes = input.pinnedNodes ?? [];
  const workflowCandidates = workflowCandidatesFor(userText, intent);
  const starterWorkflowId = String(input.starterWorkflowId ?? "").trim() || undefined;
  const useStarterWorkflow = input.useStarterWorkflow === true;
  const executionRoute = routeCanvasAgentExecution(userText, { intentId: intent.id });
  // Mounted skills are capabilities, not mandatory work for every later turn.
  // Only the current request's intent may enable an authoritative receipt gate.
  const heroSkillIds = heroSkillIdsForIntent(intent.id);
  const spendProtected = shouldCanvasAgentProtectMediaSpend(userText, intent);
  const taskAuthorization = buildCanvasAgentTaskAuthorization(runMode, spendProtected);
  const mayStartPaidMedia = taskAuthorization.allow_paid_media;

  return {
    intent,
    skillIds,
    heroSkillIds,
    executionRoute,
    taskAuthorization,
    workflowCandidates,
    ...(starterWorkflowId ? { starterWorkflowId } : {}),
    useStarterWorkflow,
    draftActions: buildDraftActions({
      intent,
      workflowCandidates,
      starterWorkflowId,
      useStarterWorkflow,
      runMode,
      selectedNode: input.selectedNode,
      pinnedNodes,
    }),
    responseMode: runMode,
    immediateGuidance: guidanceFor({
      intent,
      executionRoute,
      runMode,
      workflowCandidates,
      starterWorkflowId,
      useStarterWorkflow,
      selectedNode: input.selectedNode,
      pinnedNodes,
    }),
    spendGuard: {
      mayStartPaidMedia,
      reason: mayStartPaidMedia
        ? "auto 模式且未命中扣费保护词；仍需先完成结构和参数检查。"
        : (runMode === "draft" ? "草稿模式禁止启动媒体生成。" : "当前意图或用户措辞命中扣费保护，禁止付费生成。"),
    },
  };
}

export function compactCanvasAgentKernelPlan(plan: CanvasAgentKernelPlan): Record<string, unknown> {
  return {
    intent: {
      id: plan.intent.id,
      label: plan.intent.label,
      confidence: plan.intent.confidence,
    },
    skill_ids: plan.skillIds,
    hero_skill_ids: plan.heroSkillIds,
    execution_route: {
      lane: plan.executionRoute.lane,
      reason: plan.executionRoute.reason,
      may_write_canvas: plan.executionRoute.mayWriteCanvas,
      may_start_workflow: plan.executionRoute.mayStartWorkflow,
    },
    task_authorization: {
      scope: plan.taskAuthorization.scope,
      run_mode: plan.taskAuthorization.run_mode,
      allow_structure: plan.taskAuthorization.allow_structure,
      allow_paid_media: plan.taskAuthorization.allow_paid_media,
      max_paid_starts: plan.taskAuthorization.max_paid_starts,
      require_video_confirmation: plan.taskAuthorization.require_video_confirmation,
    },
    response_mode: plan.responseMode,
    template_selection: {
      mode: plan.useStarterWorkflow || plan.starterWorkflowId ? "explicit" : "dynamic",
      ...(plan.starterWorkflowId ? { starter_workflow_id: plan.starterWorkflowId } : {}),
      use_starter_workflow: plan.useStarterWorkflow,
    },
    workflow_candidates: plan.workflowCandidates.map((workflow) => ({
      id: workflow.id,
      title: workflow.title,
      reason: workflow.reason,
      required_inputs: workflow.requiredInputs,
      model_hint: workflow.modelHint,
      template_kind: workflow.templateKind,
      delivery_level: workflow.deliveryLevel,
      required_roles: workflow.requiredRoles,
      outputs: workflow.outputs,
      does_not_produce: workflow.doesNotProduce,
    })),
    draft_actions: plan.draftActions.map((action) => ({
      type: action.type,
      label: action.label,
      reason: action.reason,
      ...(action.command ? { command: action.command } : {}),
    })),
    immediate_guidance: plan.immediateGuidance,
    spend_guard: {
      may_start_paid_media: plan.spendGuard.mayStartPaidMedia,
      reason: plan.spendGuard.reason,
    },
  };
}
