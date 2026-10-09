// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * V1 Director Console — pure state derivation for the freezone workbench.
 * Display + guidance only; structure apply / ghost preview lands in V2.
 */
import {
  CANVAS_AGENT_SKILLS,
  selectedCanvasAgentSkills,
  type CanvasAgentNodeRef,
  type CanvasAgentSkill,
} from "@/features/superchat/canvas-agent-skills";
import {
  CANVAS_AGENT_AUTO_PAID_START_LIMIT,
  DEFAULT_CANVAS_AGENT_RUN_MODE,
  type CanvasAgentRunMode,
} from "@/features/superchat/canvas-agent-run-mode";

export type DirectorGateId =
  | "interpretation"
  | "plan"
  | "canvas_write"
  | "paid_media";

export type DirectorGateTone = "idle" | "ready" | "active" | "warn";

export interface DirectorGate {
  id: DirectorGateId;
  label: string;
  short: string;
  tone: DirectorGateTone;
  detail: string;
}

export interface DirectorNextStep {
  id: string;
  label: string;
  detail: string;
  priority: "primary" | "secondary";
}

/** Minimal plan step shape — avoids circular import with freezone UI. */
export interface DirectorPlanStepRef {
  id: string;
  label: string;
  status: "done" | "running" | "pending";
}

export interface DirectorConsoleState {
  mountedSkills: CanvasAgentSkill[];
  pinnedNodes: CanvasAgentNodeRef[];
  selectedNode: CanvasAgentNodeRef | null;
  gates: DirectorGate[];
  nextStep: DirectorNextStep;
  counts: {
    skills: number;
    pins: number;
    planSteps: number;
    attachments: number;
    skillCatalog: number;
  };
  readiness: "empty" | "partial" | "ready" | "running";
}

export function deriveDirectorConsoleState(input: {
  skillIds: readonly string[];
  pinnedNodes: readonly CanvasAgentNodeRef[];
  selectedNode: CanvasAgentNodeRef | null;
  planSteps?: readonly DirectorPlanStepRef[];
  attachmentCount?: number;
  busy?: boolean;
  hasUserMessages?: boolean;
  runMode?: CanvasAgentRunMode;
  availableSkills?: readonly CanvasAgentSkill[];
}): DirectorConsoleState {
  const availableSkills = input.availableSkills ?? CANVAS_AGENT_SKILLS;
  const mountedSkills = selectedCanvasAgentSkills(input.skillIds, availableSkills);
  const pinnedNodes = [...input.pinnedNodes];
  const selectedNode = input.selectedNode;
  const planSteps = input.planSteps ?? [];
  const attachmentCount = input.attachmentCount ?? 0;
  const busy = Boolean(input.busy);
  const hasUserMessages = Boolean(input.hasUserMessages);
  const autoMediaAuthorized = (input.runMode ?? DEFAULT_CANVAS_AGENT_RUN_MODE) === "auto";
  const skillCount = mountedSkills.length;
  const pinCount = pinnedNodes.length;
  // Selection is an operation target only. It must not silently become Agent
  // context; context enters through an explicit pin from the canvas.
  const hasContext = skillCount > 0 || pinCount > 0;

  const gates: DirectorGate[] = [
    {
      id: "interpretation",
      label: "意图门",
      short: "范围",
      tone: hasContext ? "ready" : "idle",
      detail: hasContext
        ? skillCount > 0
          ? `已挂 ${skillCount} 个 Skill，按 Skill 合同解读`
          : "已有固定节点，仍建议挂载 Skill"
        : "挂载 Skill 或钉选节点，避免空口猜意图",
    },
    {
      id: "plan",
      label: "计划门",
      short: "计划",
      tone: busy
        ? "active"
        : planSteps.length > 0
          ? "ready"
          : hasContext
            ? "idle"
            : "idle",
      detail: busy
        ? "本轮执行中 · 以工具轨迹为证据"
        : planSteps.length > 0
          ? `最近 ${planSteps.length} 步工具证据`
          : "下令后先出可观察计划，再动副作用",
    },
    {
      id: "canvas_write",
      label: "画布执行",
      short: "结构",
      tone: "ready",
      detail: "全部受支持结构命令直接执行，并自动记录回执与撤销点",
    },
    {
      id: "paid_media",
      label: autoMediaAuthorized ? "自动生成授权" : "生成权限",
      short: "生成",
      tone: autoMediaAuthorized ? "ready" : "warn",
      detail: autoMediaAuthorized
        ? `本轮自动授权已开启，最多启动 ${CANVAS_AGENT_AUTO_PAID_START_LIMIT} 次媒体任务；不再逐节点确认。`
        : "草稿模式：只搭建、修改和连线，禁止启动任何媒体生成。",
    },
  ];

  let nextStep: DirectorNextStep;
  if (busy) {
    nextStep = {
      id: "wait-run",
      label: "等待本轮工具跑完",
      detail: "完成后对照节点 ID / 任务 ID，不空口宣称已生成",
      priority: "primary",
    };
  } else if (!hasContext) {
    nextStep = {
      id: "mount-context",
      label: "挂载 Skill 或右键「加入 Agent」",
      detail: "导演台需要技能合同或真节点锚点，再开口改结构",
      priority: "primary",
    };
  } else if (skillCount === 0) {
    nextStep = {
      id: "mount-skill",
      label: "挂载至少一个导演 Skill",
      detail: "推荐：画布导演 / 分镜大师；钉选节点会随请求一并送出",
      priority: "primary",
    };
  } else if (pinCount === 0) {
    nextStep = {
      id: "pin-nodes",
      label: "钉选关键节点作真引用",
      detail: "画布右键「加入 Agent」后，真实引用会随请求进入 vision",
      priority: "secondary",
    };
  } else if (!hasUserMessages) {
    nextStep = {
      id: "first-order",
      label: "描述结构目标后发送",
      detail: autoMediaAuthorized
        ? "先搭建并核对结构；预算内媒体任务自动继续。"
        : "先读 snapshot → 计划 → 结构命令；不启动生成。",
      priority: "primary",
    };
  } else {
    nextStep = {
      id: "continue-structure",
      label: "继续结构编排或点名节点 ID",
      detail: autoMediaAuthorized
        ? "优先连线/分镜/prompt 骨架；满足前置后自动推进媒体任务。"
        : "优先连线/分镜/prompt 骨架；不启动生成。",
      priority: "primary",
    };
  }

  const readiness: DirectorConsoleState["readiness"] = busy
    ? "running"
    : skillCount > 0 && pinCount > 0
      ? "ready"
      : hasContext
        ? "partial"
        : "empty";

  return {
    mountedSkills,
    pinnedNodes,
    selectedNode,
    gates,
    nextStep,
    counts: {
      skills: skillCount,
      pins: pinCount,
      planSteps: planSteps.length,
      attachments: attachmentCount,
      skillCatalog: availableSkills.length,
    },
    readiness,
  };
}
