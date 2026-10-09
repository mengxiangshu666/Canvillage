// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { VillageAgentEvent } from "@/features/superchat/village-agent-events";
import type { ChatProgressState } from "@/features/superchat/types";
import type { WorkflowRun, WorkflowStepStatus } from "@/types/workflow-runtime";

export type AgentExecutionTimelineStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface AgentExecutionTimelineDetail {
  id: string;
  label: string;
  status: AgentExecutionTimelineStatus;
  technicalName?: string;
  count: number;
  durationMs?: number;
  message?: string;
}

export interface AgentExecutionTimelineStep {
  id: string;
  label: string;
  status: AgentExecutionTimelineStatus;
  durationMs?: number;
  message?: string;
  details: AgentExecutionTimelineDetail[];
}

export interface AgentExecutionTimeline {
  title: string;
  stageLabel: string;
  status: AgentExecutionTimelineStatus;
  elapsedMs?: number;
  completedCount: number;
  totalCount: number;
  steps: AgentExecutionTimelineStep[];
}

export interface AgentExecutionPlanStep {
  id: string;
  label: string;
  status: string;
  detail?: string;
}

interface BuildAgentExecutionTimelineInput {
  events: readonly VillageAgentEvent[];
  activeTurnId?: string | null;
  busy: boolean;
  progress?: ChatProgressState | null;
  workflowRun?: WorkflowRun | null;
  planSteps?: readonly AgentExecutionPlanStep[];
}

interface MutableDetail extends AgentExecutionTimelineDetail {
  openStarts: number[];
}

interface MutableStep extends Omit<AgentExecutionTimelineStep, "details"> {
  details: MutableDetail[];
  detailMap: Map<string, MutableDetail>;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function timestamp(value: string | undefined): number | null {
  const parsed = Date.parse(value ?? "");
  return Number.isFinite(parsed) ? parsed : null;
}

function durationBetween(startedAt: string, completedAt: string): number | undefined {
  const start = timestamp(startedAt);
  const end = timestamp(completedAt);
  return start !== null && end !== null && end >= start ? end - start : undefined;
}

function statusFromPlan(status: string): AgentExecutionTimelineStatus {
  switch (status) {
    case "done":
    case "completed":
    case "skipped":
      return "completed";
    case "failed":
    case "blocked":
    case "unverified":
      return "failed";
    case "paused":
      return "paused";
    case "cancelled":
      return "cancelled";
    case "running":
      return "running";
    default:
      return "pending";
  }
}

function statusFromWorkflow(status: WorkflowStepStatus): AgentExecutionTimelineStatus {
  return statusFromPlan(status);
}

function terminalStatus(status: string): AgentExecutionTimelineStatus {
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "paused") return "paused";
  if (status === "completed") return "completed";
  return "running";
}

function toolSubject(name: string): string {
  const normalized = name
    .replace(/^village_canvas_/, "")
    .replace(/^village\.ui\./, "")
    .toLowerCase();
  const subjects: Array<[RegExp, string]> = [
    [/character.*media|characters?/, "角色素材"],
    [/scene.*image|scenes?/, "场景素材"],
    [/sketch|storyboard/, "分镜"],
    [/episode.*media|episodes?/, "剧集素材"],
    [/first.*frame/, "首帧"],
    [/workflow/, "工作流"],
    [/production/, "生产进度"],
    [/task/, "任务状态"],
    [/canvas|command/, "画布结构"],
    [/node/, "画布节点"],
    [/tavily|search|research/, "创作资料"],
    [/prompt/, "提示词"],
    [/audio/, "音频"],
    [/video/, "视频步骤"],
    [/image|portrait/, "图像素材"],
  ];
  return subjects.find(([pattern]) => pattern.test(normalized))?.[1] ?? "创作能力";
}

export function humanizeAgentToolName(name: string): string {
  const normalized = name.toLowerCase();
  // dispatch is a canvas command router; do not let the substring "patch"
  // classify it as a mutation/update capability.
  if (normalized === "village_canvas_dispatch_action" || normalized === "village.ui.dispatch_action") {
    return "执行画布操作";
  }
  const subject = toolSubject(name);
  if (/tavily|search|research/.test(normalized)) return `联网研究${subject}`;
  if (/\.focus|\.select|navigate/.test(normalized)) return `定位${subject}`;
  if (/delete|remove/.test(normalized)) return `删除${subject}`;
  if (/apply|patch|post|create|build|save|update|command/.test(normalized)) return `更新${subject}`;
  if (/generate|render|compose|start/.test(normalized)) return `生成${subject}`;
  if (/get|list|read|status|inspect/.test(normalized)) return `读取${subject}`;
  return `调用${subject}`;
}

function currentStageLabel(progress: ChatProgressState | null | undefined): string {
  const stage = text(progress?.stage).toLowerCase();
  if (/recover|reconnect/.test(stage)) return "恢复执行";
  if (/verif|receipt/.test(stage)) return "核验结果";
  if (/canvas|patch|command/.test(stage)) return "写入画布";
  if (/workflow|step/.test(stage)) return "推进工作流";
  if (/tool/.test(stage)) return "调用能力";
  if (/plan|observ/.test(stage)) return "读取并规划";
  if (/assistant|model|stream/.test(stage)) return "生成交付结果";
  return "执行任务";
}

function detailMessage(event: VillageAgentEvent): string | undefined {
  const message = text(event.payload.message);
  const error = text(event.payload.error);
  const diagnosticCode = text(event.payload.diagnostic_code) || text(event.payload.error_code);
  if (error && diagnosticCode && !error.includes(diagnosticCode)) return `${error}（${diagnosticCode}）`;
  return message || error || diagnosticCode || undefined;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function receiptVerificationStatus(event: VillageAgentEvent): string {
  const execution = record(event.payload.execution);
  const verification = record(execution?.verification) ?? record(event.payload.verification);
  return text(verification?.status);
}

function receiptEvidenceMessage(event: VillageAgentEvent): string | undefined {
  const execution = record(event.payload.execution);
  const receipt = record(execution?.receipt) ?? record(event.payload.receipt);
  const status = receiptVerificationStatus(event);
  const revision = typeof receipt?.revision === "number"
    ? receipt.revision
    : typeof event.revision === "number" ? event.revision : undefined;
  const appliedOps = typeof receipt?.applied_ops === "number" ? receipt.applied_ops : undefined;
  if (status === "receipt_missing") return "保存回执暂未返回，正在核对";
  if (status === "receipt_verified") {
    return [
      "回执已核验",
      revision !== undefined ? `画布版本 ${revision}` : undefined,
      appliedOps !== undefined ? `应用 ${appliedOps} 项` : undefined,
    ].filter(Boolean).join(" · ");
  }
  if (receipt) {
    return [
      "已收到画布回执",
      revision !== undefined ? `画布版本 ${revision}` : undefined,
      appliedOps !== undefined ? `应用 ${appliedOps} 项` : undefined,
    ].filter(Boolean).join(" · ");
  }
  return undefined;
}

function eventEvidenceMessage(event: VillageAgentEvent): string | undefined {
  return receiptEvidenceMessage(event) || detailMessage(event);
}

function eventToolName(event: VillageAgentEvent): string {
  return text(event.payload.name) || text(event.payload.tool_name);
}

function eventMatchesActiveRun(
  event: VillageAgentEvent,
  activeTurnId: string,
  workflowRunId: string,
): boolean {
  if (workflowRunId && event.workflow_run_id === workflowRunId) return true;
  if (activeTurnId && event.turn_id === activeTurnId) return true;
  if (!activeTurnId && !workflowRunId) return true;
  return false;
}

function defaultBusinessStepForEvent(event: VillageAgentEvent): { id: string; label: string } {
  if (event.type === "run.started") return { id: "goal", label: "确认任务目标" };
  if (event.type.startsWith("tool.")) return { id: "act", label: "执行创作动作" };
  if (event.type === "canvas.receipt") return { id: "canvas", label: "同步画布变化" };
  if (event.type.startsWith("workflow.") || event.type.startsWith("step.")) {
    return { id: event.step_id ? `workflow:${event.step_id}` : "workflow", label: "推进工作流" };
  }
  if (event.type.startsWith("assistant.")) return { id: "deliver", label: "整理交付结果" };
  if (event.type === "run.failed") return { id: "recover", label: "保留现场并恢复" };
  return { id: "plan", label: "读取画布并规划" };
}

export function buildAgentExecutionTimeline({
  events,
  activeTurnId,
  busy,
  progress,
  workflowRun,
  planSteps = [],
}: BuildAgentExecutionTimelineInput): AgentExecutionTimeline | null {
  if (!busy && !workflowRun) return null;

  const activeTurn = text(activeTurnId) || text(progress?.turnId);
  const workflowRunId = text(workflowRun?.id);
  const scopedEvents = events
    .filter((event) => eventMatchesActiveRun(event, activeTurn, workflowRunId))
    .sort((left, right) => {
      const leftTime = timestamp(left.created_at) ?? 0;
      const rightTime = timestamp(right.created_at) ?? 0;
      return left.seq - right.seq || leftTime - rightTime;
    });
  const stepMap = new Map<string, MutableStep>();
  const stepOrder: string[] = [];

  const ensureStep = (
    id: string,
    label: string,
    status: AgentExecutionTimelineStatus = "pending",
    message?: string,
  ): MutableStep => {
    const existing = stepMap.get(id);
    if (existing) return existing;
    const step: MutableStep = {
      id,
      label,
      status,
      ...(message ? { message } : {}),
      details: [],
      detailMap: new Map(),
    };
    stepMap.set(id, step);
    stepOrder.push(id);
    return step;
  };

  const ensureDetail = (
    step: MutableStep,
    id: string,
    label: string,
    technicalName?: string,
  ): MutableDetail => {
    const existing = step.detailMap.get(id);
    if (existing) return existing;
    const detail: MutableDetail = {
      id,
      label,
      status: "pending",
      count: 0,
      ...(technicalName ? { technicalName } : {}),
      openStarts: [],
    };
    step.detailMap.set(id, detail);
    step.details.push(detail);
    return detail;
  };

  if (workflowRun) {
    for (const state of Object.values(workflowRun.step_states)) {
      const step = ensureStep(
        `workflow:${state.id}`,
        state.label,
        statusFromWorkflow(state.status),
        text(state.progress_message) || text(state.error) || undefined,
      );
      step.durationMs = durationBetween(state.started_at, state.completed_at);
    }
  } else {
    for (const plan of planSteps) {
      ensureStep(plan.id, plan.label, statusFromPlan(plan.status), text(plan.detail) || undefined);
    }
  }

  const findStepForEvent = (event: VillageAgentEvent): MutableStep => {
    const workflowStep = event.step_id ? stepMap.get(`workflow:${event.step_id}`) : null;
    if (workflowStep) return workflowStep;
    const defaultStep = defaultBusinessStepForEvent(event);
    const preferredId = event.type.startsWith("tool.") || event.type === "canvas.receipt"
      ? (stepMap.has("act") ? "act" : defaultStep.id)
      : event.type.startsWith("assistant.") && stepMap.has("verify")
        ? "verify"
        : event.type === "run.progress" && stepMap.has("plan")
          ? "plan"
          : defaultStep.id;
    return ensureStep(preferredId, preferredId === defaultStep.id ? defaultStep.label : stepMap.get(preferredId)?.label ?? defaultStep.label);
  };

  for (const event of scopedEvents) {
    const step = findStepForEvent(event);
    const eventTime = timestamp(event.created_at);
    if (event.type === "run.started") {
      step.status = "running";
      const detail = ensureDetail(step, "goal:accepted", "任务已进入执行器");
      detail.status = "completed";
      detail.count = 1;
      continue;
    }
    if (event.type === "run.progress") {
      if (step.status === "pending") step.status = "running";
      const stage = text(event.payload.stage) || "agent.working";
      const detail = ensureDetail(step, `progress:${stage}`, detailMessage(event) || currentStageLabel(progress), stage);
      detail.status = "running";
      detail.count = Math.max(1, detail.count);
      continue;
    }
    if (event.type === "tool.call" || event.type === "tool.ack" || event.type === "tool.progress") {
      if (step.status === "pending") step.status = "running";
      const name = eventToolName(event) || "agent.tool";
      const detail = ensureDetail(step, `tool:${name}`, humanizeAgentToolName(name), name);
      if (event.type === "tool.call") {
        detail.count += 1;
        if (eventTime !== null) detail.openStarts.push(eventTime);
      } else if (detail.count === 0) {
        detail.count = 1;
      }
      detail.status = "running";
      detail.message = detailMessage(event);
      continue;
    }
    if (event.type === "tool.result") {
      const name = eventToolName(event) || "agent.tool";
      const detail = ensureDetail(step, `tool:${name}`, humanizeAgentToolName(name), name);
      if (detail.count === 0) detail.count = 1;
      const startedAt = detail.openStarts.shift();
      if (startedAt !== undefined && eventTime !== null && eventTime >= startedAt) {
        detail.durationMs = (detail.durationMs ?? 0) + eventTime - startedAt;
      }
      const evidenceMessage = eventEvidenceMessage(event);
      const receiptMissing = receiptVerificationStatus(event) === "receipt_missing";
      detail.status = event.status === "failed"
        ? "failed"
        : receiptMissing
          ? "pending"
          : detail.openStarts.length > 0 ? "running" : "completed";
      detail.message = evidenceMessage;
      continue;
    }
    if (event.type === "canvas.receipt") {
      const evidenceMessage = eventEvidenceMessage(event);
      const receiptMissing = receiptVerificationStatus(event) === "receipt_missing";
      const detail = ensureDetail(
        step,
        "canvas:receipt",
        receiptMissing ? "等待画布回执" : "画布回执已核验",
        "canvas.receipt.v2",
      );
      detail.count += 1;
      detail.status = event.status === "failed"
        ? "failed"
        : receiptMissing ? "pending" : "completed";
      detail.message = evidenceMessage || (event.revision ? `画布版本 ${event.revision}` : undefined);
      continue;
    }
    if (event.type.startsWith("step.") || event.type.startsWith("workflow.")) {
      const detail = ensureDetail(
        step,
        `workflow:${event.step_id || event.type}`,
        event.step_id ? `工作流步骤 · ${step.label}` : "工作流状态已更新",
        event.type,
      );
      detail.count += 1;
      detail.status = terminalStatus(event.status);
      detail.message = detailMessage(event);
      if (!workflowRun) step.status = terminalStatus(event.status);
      continue;
    }
    if (event.type === "assistant.completed") {
      const detail = ensureDetail(step, "assistant:completed", "交付内容已生成", "assistant.completed");
      detail.status = "completed";
      detail.count = 1;
      if (step.status === "pending") step.status = "running";
      continue;
    }
    if (event.type === "run.failed") {
      step.status = "failed";
      const detail = ensureDetail(step, "run:failed", "执行出现问题，现场已保留", "run.failed");
      detail.status = "failed";
      detail.count = 1;
      detail.message = detailMessage(event);
    }
  }

  if (stepOrder.length === 0) {
    ensureStep("starting", "正在启动执行器", "running");
  }

  const steps = stepOrder.map((id) => {
    const step = stepMap.get(id)!;
    if (step.status === "pending" && step.details.some((detail) => detail.status === "running")) {
      step.status = "running";
    }
    if (
      step.status === "running"
      && step.details.length > 0
      && step.details.every((detail) => detail.status === "completed")
      && !busy
    ) {
      step.status = "completed";
    }
    return {
      id: step.id,
      label: step.label,
      status: step.status,
      ...(step.durationMs !== undefined ? { durationMs: step.durationMs } : {}),
      ...(step.message ? { message: step.message } : {}),
      details: step.details.map(({ openStarts: _openStarts, ...detail }) => detail),
    };
  });
  const completedCount = steps.filter((step) => step.status === "completed").length;
  const timelineStatus = workflowRun
    ? terminalStatus(workflowRun.status)
    : scopedEvents.some((event) => event.type === "run.failed" || event.status === "failed")
      ? "failed"
      : busy
        ? "running"
        : "completed";
  const elapsedSeconds = progress?.elapsedSeconds;
  return {
    title: timelineStatus === "failed" ? "执行遇到问题" : "正在继续处理你的请求",
    stageLabel: text(progress?.message) || currentStageLabel(progress),
    status: timelineStatus,
    ...(typeof elapsedSeconds === "number" && Number.isFinite(elapsedSeconds)
      ? { elapsedMs: Math.max(0, elapsedSeconds * 1000) }
      : {}),
    completedCount,
    totalCount: steps.length,
    steps,
  };
}

export function formatAgentExecutionDuration(durationMs: number | undefined): string | null {
  if (durationMs === undefined || !Number.isFinite(durationMs) || durationMs < 0) return null;
  if (durationMs < 1_000) return `${Math.max(1, Math.round(durationMs))}ms`;
  if (durationMs < 60_000) {
    const seconds = durationMs / 1_000;
    return `${seconds >= 10 ? Math.round(seconds) : seconds.toFixed(1)}s`;
  }
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.floor((durationMs % 60_000) / 1_000);
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}
