// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { extractCanvasAgentUserRequest } from "./message";
import { buildCanvasWorkflowManifest } from "@/features/canvas/application/canvasWorkflowManifest";
import {
  DEFAULT_CANVAS_AGENT_RUN_MODE,
  type CanvasAgentRunMode,
} from "@/features/superchat/canvas-agent-run-mode";
import {
  routeCanvasAgentExecution,
  type CanvasAgentExecutionLane,
} from "@/features/superchat/canvas-agent-execution-router";
import type { CanvasWorkflowRuntimeContext } from "@/types/workflow-runtime";
import {
  buildCanvasAgentTaskAuthorization,
  shouldCanvasAgentProtectMediaSpend,
} from "@/features/superchat/canvas-agent-intelligence";
import type { CanvasAgentKernelPlan } from "@/features/superchat/canvas-agent-kernel";
import {
  compactHeroSkillContract,
  heroSkillContractsFor,
} from "@/features/superchat/canvas-agent-hero-skills";
import type { SkillStoreExecutionContract } from "@/api/skill-store";

export interface CanvasAgentSkill {
  id: string;
  skillKey: string;
  label: string;
  description: string;
  activation: string;
  accent: string;
  coverImage?: string;
  triggers?: readonly string[];
  executionContract?: SkillStoreExecutionContract;
}

export interface CanvasAgentNodeRef {
  id: string;
  type: string;
  label: string;
  /** Stable metadata for pinned references; never contains a local file path. */
  assetId?: string | null;
  assetUri?: string | null;
  nodeUri?: string | null;
  parentId?: string | null;
  role?: string | null;
  hasPrompt?: boolean;
  hasImage?: boolean;
  hasVideo?: boolean;
  /** Render-safe thumbnail for the composer only; never sent in the Agent request. */
  previewUrl?: string;
  /** Optional poster for a video preview. */
  previewPosterUrl?: string;
  previewKind?: "image" | "video" | "audio" | "text";
}

export const CANVAS_AGENT_SKILLS: readonly CanvasAgentSkill[] = [
  {
    id: "one-click-film",
    skillKey: "village-canvas-one-click-film",
    label: "一键成片",
    description: "先体检项目，再用可恢复生产运行交付成片",
    accent: "from-fuchsia-500/25 to-violet-500/10 border-fuchsia-300/25",
    activation: "读取真实 production/control 与 pipeline/status，建立阶段计划；自动模式在本轮预算内直接运行图片、视频和音频节点，草稿/费用保护只落结构；最后以真实任务和交付文件验收。",
  },
  {
    id: "canvas-director",
    skillKey: "village-canvas-canvas-director",
    label: "画布导演",
    description: "读取节点、连线、引用和任务，规划最短执行路径",
    accent: "from-sky-500/20 to-cyan-500/5 border-sky-300/20",
    activation: "freezone_get_canvas_snapshot 后按真实 ID 操作；写结构 freezone_emit_canvas_command；自动运行 freezone_run_node；控制 freezone_stop_task / freezone_retry_node。",
  },
  {
    id: "template-director",
    skillKey: "village-canvas-template-director",
    label: "镜头模板导演",
    description: "按任务、素材槽位和模型能力选工作流，再落成可编辑节点图",
    accent: "from-violet-500/25 to-indigo-500/10 border-violet-300/25",
    activation: "优先读取 workflow_manifest 与 workflow_templates；用户要求插入模板时用 insert_starter_workflow，要求补节点时用 create_canvas_node/node_type，连线用 connect_nodes；也可直接 update_node_prompt、update_node_label、move_node、duplicate_node、delete_node、remove_edge；模板插入不等于媒体已生成，自动模式继续用 freezone_run_node 执行真实节点。",
  },
  {
    id: "storyboard",
    skillKey: "village-canvas-storyboard",
    label: "分镜大师",
    description: "把剧情编译成可拍、可生成、可验收的逐镜合同",
    accent: "from-amber-500/20 to-orange-500/5 border-amber-300/20",
    activation: "读取剧本、beat、角色、场景与 PINNED_NODES，用 create_shot_sequence / create_image_prompt_node 落成可拍分镜节点；实战模式在依赖和参数通过校验后可直接提交真实媒体任务，草稿模式只落结构。",
  },
  {
    id: "identity-continuity",
    skillKey: "village-canvas-continuity",
    label: "角色一致性",
    description: "建立人物圣经、世界观与跨镜连续性锁定",
    accent: "from-emerald-500/20 to-teal-500/5 border-emerald-300/20",
    activation: "检查身份、骨相、年龄、体型、服装、场景、道具、左右关系、时间和光线连续性；给出资产锚点和逐节点修复方案。",
  },
  {
    id: "prompt-engineer",
    skillKey: "village-canvas-prompt-director",
    label: "提示词总监",
    description: "按目标模型、模式、参数和引用编译生成合同",
    accent: "from-indigo-500/20 to-blue-500/5 border-indigo-300/20",
    activation: "读取目标模型/API 模式/参数/上游上下文/参考图顺序，输出原稿、优化稿和依据；可用 update_node_prompt 直写目标节点，优化本身绝不触发生成。",
  },
  {
    id: "expression-director",
    skillKey: "village-canvas-expression-director",
    label: "角色表演导演",
    description: "设计动作、视线、呼吸、潜台词与跨镜表演递进",
    accent: "from-rose-500/20 to-pink-500/5 border-rose-300/20",
    activation: "根据剧情目的、人物关系、潜台词和镜头距离设计整体表演；精确面部状态复用画布现有 50 锚点合同，不另造第二套情绪系统。",
  },
  {
    id: "failure-rescue",
    skillKey: "village-canvas-failure-rescue",
    label: "失败救援",
    description: "依据真实错误定位最低成本恢复点",
    accent: "from-red-500/20 to-rose-500/5 border-red-300/20",
    activation: "读取失败任务、错误原文、输入参数、依赖和既有产物，先分类根因再给恢复路径；禁止盲目重试和重复扣费。",
  },
  {
    id: "delivery-qc",
    skillKey: "village-canvas-delivery-qc",
    label: "成片质检",
    description: "按阻断、严重、建议三级验收最终交付",
    accent: "from-violet-500/20 to-purple-500/5 border-violet-300/20",
    activation: "检查镜头完整性、黑跳帧、连续性、节奏、对白口型、声音、字幕安全区、分辨率、帧率和交付文件，只对真实问题提出返工。",
  },
] as const;

const SKILL_BY_ID = new Map(CANVAS_AGENT_SKILLS.map((skill) => [skill.id, skill]));
const CANVAS_AGENT_SKILL_SELECTION_PREFIX = "st.freezone.agentSkills.v1";
const LEGACY_AUTO_MOUNTED_SKILL_ID = "canvas-director";
const CANVAS_AGENT_REQUEST_V2_OPEN = "[CANVAS_AGENT_REQUEST_V2]";
const CANVAS_AGENT_REQUEST_V2_CLOSE = "[/CANVAS_AGENT_REQUEST_V2]";

/**
 * A canvas request is usually a production workflow, not a single chat answer.
 * The backend enforces this budget independently; the envelope lets the Agent
 * choose a complete Observe -> Plan -> Act -> Verify pass without guessing
 * whether it may continue after a successful structural write.
 */
const DIRECTOR_WORKFLOW_PERFORMANCE = {
  response_mode: "workflow_canvas",
  max_reply_chars: 800,
  execution_mode: "observe_plan_act_verify",
  snapshot_policy: "before_write_if_facts_missing_or_receipt_unverified",
  execution_budget: {
    observation_steps: 6,
    structural_write_steps: 6,
    paid_media_starts: 1,
    paid_media_confirmation: "current_turn_auto",
  },
} as const;
const FAST_CANVAS_TOOL_POLICY = {
  mode: "canvas_executor",
  no_skill_lookup: true,
  no_file_patch_for_canvas: true,
  avoid_background_tools: ["skill_manage", "read_file", "patch", "memory"],
  canvas_tools: [
    "freezone_get_canvas_snapshot",
    "freezone_emit_canvas_command",
    "freezone_propose_generation",
  ],
} as const;
const MODEL_DIRECTED_DIRECTOR_CONTRACT = {
  mode: "model_directed",
  semantic_source: "完整 request + 当前 canvas/pins + 按需工具回执",
  rule: "直接理解用户真正要交付的结果；禁止用关键词分类、预设模板或前端候选计划代替语义判断。",
  working_ledger: [
    "objective",
    "constraints",
    "known_facts",
    "unknowns",
    "interaction_mode",
    "target_strategy",
    "target_node_ids",
    "existing_run_id",
    "creation_reason",
    "chosen_action",
    "success_criteria",
  ],
  decision_policy: [
    "先区分用户是在询问、规划还是要求执行，并同时理解叙事目标、审美约束和交付深度。",
    "只有缺失事实会改变成本、不可逆结果或作品方向时才追问；否则采用最小可逆假设继续推进。",
    "需要新事实时按需读取；已有足够事实时直接把完整理解编译为 task、commands、goal 和 success_criteria。",
    "修改、续做、恢复类请求默认使用 reuse_existing，并绑定真实 target_node_ids；只有现有对象确实无法承载时才用 create_missing，且必须给出 creation_reason。",
    "workflow_runtime 已给出 running/paused/failed 的 workflow_run_id 时，续做请求必须把它作为 existing_run_id 交给 dispatch_action；禁止创建平行 WorkflowRun。",
    "是否需要持久 WorkflowRun 只由计划的真实步骤、依赖、恢复、交付和媒体事实决定，不由题材词决定。",
  ],
  evidence_loop: "Observe → Decide → Act → Verify；每次根据新回执更新判断，完成只认真节点、任务、revision、产物和 verifier 证据。",
} as const;
const WORKFLOW_RUNTIME_TOOLS = [
  "village_canvas_list_workflows",
  "village_canvas_list_workflow_runs",
  "village_canvas_start_workflow_run",
  "village_canvas_get_workflow_run",
  "village_canvas_command_workflow_run",
] as const;
const WORKFLOW_CANVAS_TOOL_POLICY = {
  ...FAST_CANVAS_TOOL_POLICY,
  canvas_tools: [
    ...FAST_CANVAS_TOOL_POLICY.canvas_tools,
    ...WORKFLOW_RUNTIME_TOOLS,
  ],
} as const;
const AUTO_CANVAS_TOOL_POLICY = {
  ...FAST_CANVAS_TOOL_POLICY,
  canvas_tools: [
    ...FAST_CANVAS_TOOL_POLICY.canvas_tools,
    "freezone_run_node",
    "freezone_retry_node",
    "freezone_stop_task",
  ],
} as const;
const AUTO_WORKFLOW_CANVAS_TOOL_POLICY = {
  ...AUTO_CANVAS_TOOL_POLICY,
  canvas_tools: [
    ...AUTO_CANVAS_TOOL_POLICY.canvas_tools,
    ...WORKFLOW_RUNTIME_TOOLS,
  ],
} as const;

const REFERENCE_BINDING_CONTRACT = {
  source: "canvas.director_state.reference_manifest",
  rule: "凡用户提到参考图、角色图、场景图、分镜图、图片N、替换某张图或保留某张图，先读取 manifest 建立唯一映射，再执行写入或生成。",
  stable_keys: ["node_id", "asset_id"],
  display_labels: "图片N/视频N/音频N 仅用于对话展示，不得直接作为绑定值。",
  ambiguity: "display_name、文件名或自然语言匹配不唯一时暂停并请求确认，不静默猜测。",
  ordering: "图片N、视频N、音频N 使用 manifest 的 order/type_index；与 UI referenceOrder 和提交顺序一致。",
} as const;

function isSelectableSkillId(id: string): boolean {
  return SKILL_BY_ID.has(id) || /^store:[a-z0-9._-]+$/i.test(id);
}

function normalizedSkillIds(ids: readonly string[]): string[] {
  return [...new Set(ids.map((id) => String(id || "").trim()).filter(isSelectableSkillId))];
}

function skillSelectionStorageKey(projectId: string, canvasId: string): string {
  return `${CANVAS_AGENT_SKILL_SELECTION_PREFIX}:${encodeURIComponent(projectId.trim())}:${encodeURIComponent(canvasId.trim())}`;
}

/** Load skills explicitly selected for one project/canvas. Empty means automatic routing. */
export function loadCanvasAgentSkillIds(projectId: string, canvasId: string): string[] {
  if (typeof window === "undefined" || !projectId.trim() || !canvasId.trim()) return [];
  try {
    const storageKey = skillSelectionStorageKey(projectId, canvasId);
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const normalized = normalizedSkillIds(parsed.map(String));
    if (normalized.length === 1 && normalized[0] === LEGACY_AUTO_MOUNTED_SKILL_ID) {
      window.localStorage.setItem(storageKey, "[]");
      return [];
    }
    return normalized;
  } catch {
    return [];
  }
}

/** Persist mounted skills without allowing unknown or cross-canvas entries. */
export function saveCanvasAgentSkillIds(
  projectId: string,
  canvasId: string,
  ids: readonly string[],
): string[] {
  const next = normalizedSkillIds(ids);
  if (typeof window !== "undefined" && projectId.trim() && canvasId.trim()) {
    try {
      window.localStorage.setItem(
        skillSelectionStorageKey(projectId, canvasId),
        JSON.stringify(next),
      );
    } catch {
      // Storage can be unavailable in privacy mode; the in-memory selection remains valid.
    }
  }
  return next;
}

export function selectedCanvasAgentSkills(
  ids: readonly string[],
  additionalSkills: readonly CanvasAgentSkill[] = [],
): CanvasAgentSkill[] {
  const catalog = new Map(SKILL_BY_ID);
  for (const skill of additionalSkills) catalog.set(skill.id, skill);
  return ids.map((id) => catalog.get(id)).filter((skill): skill is CanvasAgentSkill => Boolean(skill));
}

function unwrapReplayedCanvasAgentText(value: string): string {
  let current = String(value ?? "").trim();
  for (let pass = 0; pass < 8; pass += 1) {
    const contextStart = current.indexOf("[VILLAGE_CANVAS_USER_CONTEXT]");
    const messageStart = current.indexOf("[USER_MESSAGE]");
    if (contextStart >= 0 && messageStart > contextStart) {
      current = current.slice(messageStart + "[USER_MESSAGE]".length).trimStart();
      continue;
    }
    const humanRequest = extractCanvasAgentUserRequest(current);
    if (humanRequest && humanRequest !== current) {
      current = humanRequest;
      continue;
    }
    break;
  }
  return current;
}

function parseCanvasContext(value: string): Record<string, unknown> {
  const source = String(value ?? "").trim();
  if (!source) return {};
  try {
    const parsed = JSON.parse(source) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { raw: source };
    }
    const { tool_contract: _toolContract, ...canvas } = parsed as Record<string, unknown>;
    return canvas;
  } catch {
    return { raw: source };
  }
}

function compactPinnedNode(node: CanvasAgentNodeRef): Record<string, unknown> {
  const has = [
    node.hasPrompt ? "prompt" : null,
    node.hasImage ? "image" : null,
    node.hasVideo ? "video" : null,
  ].filter((value): value is string => value !== null);
  return {
    id: node.id,
    type: node.type,
    label: node.label,
    ...(node.assetId ? { asset_id: node.assetId } : {}),
    ...(node.assetUri ? { asset_uri: node.assetUri } : {}),
    ...(node.nodeUri ? { node_uri: node.nodeUri } : {}),
    ...(node.parentId ? { parent_id: node.parentId } : {}),
    ...(node.role ? { role: node.role } : {}),
    ...(has.length > 0 ? { has } : {}),
  };
}

function compactSkillContract(skill: CanvasAgentSkill): Record<string, unknown> {
  return {
    key: skill.skillKey,
    label: skill.label,
    activation: skill.activation,
    ...(skill.executionContract
      ? {
        execution_contract: {
          schema_version: skill.executionContract.schema_version,
          maturity: skill.executionContract.maturity,
          readiness_score: skill.executionContract.readiness_score,
          purpose: skill.executionContract.purpose,
          inputs: skill.executionContract.inputs,
          workflow: skill.executionContract.workflow,
          output_contract: skill.executionContract.output_contract,
          quality_gate: skill.executionContract.quality_gate,
          canvas_commands: skill.executionContract.canvas_commands,
          completion_rule: skill.executionContract.completion_rule,
        },
      }
      : {}),
  };
}

function compactDirectorMethod(skill: CanvasAgentSkill): Record<string, unknown> {
  return {
    key: skill.skillKey,
    label: skill.label,
    purpose: String(skill.executionContract?.purpose || skill.description).slice(0, 160),
  };
}

export function buildCanvasAgentRequest(input: {
  userText: string;
  skillIds: readonly string[];
  /** Skills manually mounted by the user; available this turn, never an implicit receipt gate. */
  explicitSkillIds?: readonly string[];
  canvasContext: string;
  pinnedNodes?: readonly CanvasAgentNodeRef[];
  runMode?: CanvasAgentRunMode;
  executionLane?: CanvasAgentExecutionLane;
  fastKernelPlan?: CanvasAgentKernelPlan;
  workflowRuntime?: CanvasWorkflowRuntimeContext | null;
  additionalSkills?: readonly CanvasAgentSkill[];
  /** Browser-issued, server-validated ticket for one final-film recovery. */
  composeAuthorizationId?: string;
  /** Optional per-turn paid-media start cap; recovery uses a single start. */
  maxPaidStarts?: number;
  /** Freezone always forces structured envelope so Agent gets real canvas facts. */
  forceStructured?: boolean;
}): string {
  const userText = unwrapReplayedCanvasAgentText(input.userText);
  const pins = input.pinnedNodes ?? [];
  if (input.skillIds.length === 0 && pins.length === 0 && !input.forceStructured) {
    return userText;
  }
  // Selected skills are explicit execution contracts. The model chooses any
  // additional director method semantically from the compact catalog below.
  const effectiveSkillIds = normalizedSkillIds(input.skillIds);
  const skills = selectedCanvasAgentSkills(effectiveSkillIds, input.additionalSkills);
  const heroSkillContracts = heroSkillContractsFor({
    intentId: "general_canvas",
    skillIds: input.explicitSkillIds ?? input.skillIds,
  });
  const availableMethods = [...CANVAS_AGENT_SKILLS, ...(input.additionalSkills ?? [])]
    .filter((skill) => skill.executionContract?.maturity !== "reference_only")
    .slice(0, 24)
    .map(compactDirectorMethod);
  const runMode: CanvasAgentRunMode = input.runMode ?? DEFAULT_CANVAS_AGENT_RUN_MODE;
  const executionLane = input.executionLane ?? routeCanvasAgentExecution(userText).lane;
  if (executionLane === "direct_chat" || executionLane === "plan_only") {
    const payload = {
      v: 2,
      request: userText,
      execution_lane: executionLane,
      execution_contract: executionLane === "plan_only"
        ? {
          canvas_write: false,
          workflow_start: false,
          media_start: false,
          response: "只根据当前画布事实给出最短可执行方案；列出拟修改内容，等待用户确认后再写入。",
        }
        : {
          canvas_write: false,
          workflow_start: false,
          media_start: false,
          response: "直接回答当前问题；不启动工具，不声称已经修改画布。",
        },
      ...(executionLane === "plan_only"
        ? {
          director_reasoning_contract: MODEL_DIRECTED_DIRECTOR_CONTRACT,
          director_method_catalog: skills.length > 0
            ? skills.map(compactDirectorMethod)
            : availableMethods,
          director_method_policy: "本轮只做语义理解和方案；方法目录不代表已经执行，禁止产生工具回执或完成声明。",
        }
        : {}),
      reference_binding_contract: REFERENCE_BINDING_CONTRACT,
      canvas: parseCanvasContext(input.canvasContext),
      pins: pins.map(compactPinnedNode),
    };
    return `${CANVAS_AGENT_REQUEST_V2_OPEN}${JSON.stringify(payload)}${CANVAS_AGENT_REQUEST_V2_CLOSE}`;
  }
  // This is a cost/safety guard only; it never selects the creative intent.
  const mediaSpendProtected = shouldCanvasAgentProtectMediaSpend(userText);
  const taskAuthorization = buildCanvasAgentTaskAuthorization(
    runMode,
    mediaSpendProtected,
    input.composeAuthorizationId,
    input.maxPaidStarts,
  );
  const performance = {
    ...DIRECTOR_WORKFLOW_PERFORMANCE,
    execution_budget: {
      ...DIRECTOR_WORKFLOW_PERFORMANCE.execution_budget,
      paid_media_starts: taskAuthorization.max_paid_starts,
      paid_media_confirmation: taskAuthorization.allow_paid_media
        ? "current_turn_auto_all_media"
        : "disabled",
    },
  };
  const needsWorkflowManifest = skills.some((skill) => skill.id === "template-director")
    || Boolean(input.workflowRuntime);
  const hasWorkflowCapability = executionLane === "workflow" || Boolean(input.workflowRuntime);
  const payload = {
    v: 2,
    request: userText,
    execution_lane: executionLane,
    run_mode: runMode,
    task_authorization: taskAuthorization,
    run_mode_contract: runMode === "auto"
      ? (taskAuthorization.allow_paid_media
        ? "自动生成模式：本轮预算内启动并运行图片、视频和音频节点，并以真实任务回执验收。"
        : "自动生成模式已命中费用保护：本轮只搭建与修复结构，禁止启动图片/视频/音频生成。")
      : "草稿模式：允许搭节点、连线、填提示词和参数；禁止启动图片、视频、音频生成任务。",
    performance,
    director_contract: {
      model_routing: "只用 canvas.model_catalog 中已配置且模式匹配的模型；显式模型优先，无价格/速度元数据时不编造。",
      continuity: "按 canvas.director_state 的身份、场景和血缘保持人物、服装、道具、空间、时间与光线连续。",
      execution: "批量写结构并核对 command_id、revision、applied_ops；媒体完成只认真实任务和产物回执。",
      quality_gate: "交付前验模型、引用、参数、连续性、失败任务和最终媒体，不用聊天文本冒充完成。",
      identity_binding: "node_id、asset_id、asset_uri、node_uri 是唯一绑定依据；display_name、文件名和画布位置只用于展示或排序，禁止据此猜测对象。修改/续做默认复用已有身份；缺少稳定身份时先读取真实画布再决定。",
      reference_bindings: "只从 canvas.director_state.reference_manifest 选择稳定 node_id/asset_id/asset_uri；先核对目标视频节点的一跳引用和 order，再生成 reference_bindings。",
    },
    reference_binding_contract: REFERENCE_BINDING_CONTRACT,
    director_reasoning_contract: MODEL_DIRECTED_DIRECTOR_CONTRACT,
    ...(skills.length === 0
      ? {
        director_method_catalog: availableMethods,
        director_method_policy: "目录只提供可选导演方法，不代表已经选中或执行。根据完整语义选择最少必要方法；用户手动指定的 skill_contracts 优先。",
      }
      : {}),
    ACTIVE_SKILLS: [...new Set(skills.map((skill) => skill.skillKey))],
    skill_contracts: skills.map(compactSkillContract),
    ...(heroSkillContracts.length > 0
      ? {
        hero_skill_contracts: heroSkillContracts.map(compactHeroSkillContract),
        hero_skill_policy: {
          mode: "runtime_contract_first",
          rule: "先按 hero_skill_contracts 执行，完成条件必须由工具回执证明；不能用自然语言代替。",
          no_parallel_retries: true,
          preserve_successful_assets: true,
        },
      }
      : {}),
    ...(skills.some((skill) => skill.executionContract)
      ? {
        skill_execution_policy: {
          completion: "Skill 只有在真实写入画布或产出可追踪结果，并核对 command_id、revision、applied_ops 后才算完成。",
          reference_only: "reference_only 技能只用于人工参考；除非用户手动指定，否则禁止自动路由。",
          verification: "按 execution_contract.output_contract 与 quality_gate 验收；不以聊天承诺代替节点、任务或媒体回执。",
        },
      }
      : {}),
    tool_policy: hasWorkflowCapability
      ? (runMode === "auto" ? AUTO_WORKFLOW_CANVAS_TOOL_POLICY : WORKFLOW_CANVAS_TOOL_POLICY)
      : (runMode === "auto" ? AUTO_CANVAS_TOOL_POLICY : FAST_CANVAS_TOOL_POLICY),
    ...(input.workflowRuntime
      ? {
        workflow_runtime: input.workflowRuntime,
        workflow_runtime_policy: input.workflowRuntime.structure_already_applied
          ? "该 V2 工作流由服务端 executor、CanvasCommandGateway 和 verifier 推进步骤；禁止重复插入模板，也禁止调用 village_canvas_update_workflow_run 冒充完成。用 get 读取真实状态，用 command 做暂停、恢复、重试和调整方向。"
          : "该 V2 工作流已保留失败断点；用 village_canvas_command_workflow_run 重试并沿用 workflow_run_id，禁止创建平行重复工作流，禁止外部伪造 step_completed。",
      }
      : {}),
    canvas: parseCanvasContext(input.canvasContext),
    pins: pins.map(compactPinnedNode),
    ...(needsWorkflowManifest ? { workflow_manifest: buildCanvasWorkflowManifest() } : {}),
  };
  return `${CANVAS_AGENT_REQUEST_V2_OPEN}${JSON.stringify(payload)}${CANVAS_AGENT_REQUEST_V2_CLOSE}`;
}
