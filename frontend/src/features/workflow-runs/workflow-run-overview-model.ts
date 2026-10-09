// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type {
  WorkflowRun,
  WorkflowRunStatus,
  WorkflowStepState,
} from "@/types/workflow-runtime";

const WORKFLOW_TITLE: Record<string, string> = {
  "freezone-final-film": "一键成片",
  "freezone-script-contract": "脚本合同",
};

const RUN_STATUS_LABEL: Record<WorkflowRunStatus, string> = {
  running: "执行中",
  paused: "已暂停",
  failed: "执行失败",
  completed: "已完成",
  cancelled: "已终止",
};

const RUN_STATUS_TONE: Record<WorkflowRunStatus, string> = {
  running: "border-sky-400/25 bg-sky-400/[0.08] text-sky-300",
  paused: "border-amber-400/25 bg-amber-400/[0.08] text-amber-300",
  failed: "border-red-400/25 bg-red-400/[0.08] text-red-300",
  completed: "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300",
  cancelled: "border-white/10 bg-white/[0.03] text-muted-foreground",
};

export function workflowRunTitle(workflowId: string): string {
  const id = workflowId.trim();
  return WORKFLOW_TITLE[id] ?? (id || "工作流");
}

export function workflowRunStatusLabel(status: WorkflowRunStatus): string {
  return RUN_STATUS_LABEL[status] ?? status;
}

export function workflowRunStatusTone(status: WorkflowRunStatus): string {
  return RUN_STATUS_TONE[status] ?? RUN_STATUS_TONE.cancelled;
}

export function sortWorkflowRunsNewestFirst(runs: WorkflowRun[]): WorkflowRun[] {
  return [...runs].sort((left, right) =>
    String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")),
  );
}

/** Keep in-flight runs visible even when newer completed runs fill the recent list. */
export function workflowRunOverviewVisibleRuns(
  runs: WorkflowRun[],
  recentLimit = 3,
  pinnedRunId?: string | null,
): WorkflowRun[] {
  const ordered = sortWorkflowRunsNewestFirst(runs);
  const visibleIds = new Set(
    ordered
      .slice(0, Math.max(0, recentLimit))
      .map((run) => run.id),
  );
  for (const run of ordered) {
    if (
      run.status === "running" ||
      run.status === "paused" ||
      run.status === "failed"
    ) {
      visibleIds.add(run.id);
    }
  }
  if (pinnedRunId && ordered.some((run) => run.id === pinnedRunId)) {
    visibleIds.add(pinnedRunId);
  }
  return ordered.filter((run) => visibleIds.has(run.id));
}

export interface WorkflowRunProgress {
  completed: number;
  total: number;
  ratio: number;
}

export function workflowRunProgress(run: WorkflowRun): WorkflowRunProgress {
  const steps = Object.values(run.step_states ?? {});
  const total = steps.length;
  const completed = steps.filter((step) => step.status === "completed").length;
  return {
    completed,
    total,
    ratio: total > 0 ? completed / total : 0,
  };
}

/**
 * The step the run is on right now: a running step wins, then the declared
 * frontier, then the first step that has not finished yet.
 */
export function workflowRunActiveStep(run: WorkflowRun): WorkflowStepState | null {
  const steps = Object.values(run.step_states ?? {});
  const running = steps.find((step) => step.status === "running");
  if (running) return running;
  const frontierId = (run.current_frontier ?? [])[0];
  if (frontierId) {
    const frontier = run.step_states?.[frontierId];
    if (frontier) return frontier;
  }
  return steps.find((step) => step.status !== "completed") ?? null;
}

export function workflowRunFailedStep(run: WorkflowRun): WorkflowStepState | null {
  return (
    Object.values(run.step_states ?? {}).find((step) => step.status === "failed") ??
    null
  );
}

export interface WorkflowRunRecoveryGuidance {
  title: string;
  instruction: string;
}

const RECOVERY_ACTION_LABELS: Record<string, string> = {
  repair_script_contract: "先修复脚本合同",
  retry_script_contract: "核对后重试脚本合同",
  repair_canvas_snapshot: "先修复画布快照",
  repair_canvas_asset_binding: "先修复画布素材绑定",
  repair_asset_ledger: "先修复素材台账",
  request_media_authorization: "需要媒体授权",
  retry_failed_items: "只重试失败项目",
  request_compose_authorization: "需要最终合成授权",
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** Show only a recovery instruction bound to this failed run and exact step. */
export function workflowRunRecoveryGuidance(
  run: WorkflowRun,
  failedStep = workflowRunFailedStep(run),
): WorkflowRunRecoveryGuidance | null {
  if (run.status !== "failed" || !failedStep || failedStep.status !== "failed") {
    return null;
  }

  const artifact = record(run.artifacts?.[failedStep.id]);
  const receipt = record(artifact?.canvas_receipt);
  const candidates = [artifact?.recovery, receipt?.recovery];
  for (const value of candidates) {
    const recovery = record(value);
    if (!recovery || typeof recovery.instruction !== "string") continue;
    if (
      recovery.schema !== "workflow_step_recovery.v1" &&
      recovery.schema !== "canvas_command_recovery.v1"
    ) {
      continue;
    }
    if (recovery.step_id !== failedStep.id) continue;
    if (
      typeof recovery.workflow_run_id === "string" &&
      recovery.workflow_run_id !== run.id
    ) {
      continue;
    }
    const instruction = recovery.instruction.trim().slice(0, 1000);
    if (!instruction) continue;
    const action = String(recovery.action ?? "");
    const title = typeof recovery.title === "string" && recovery.title.trim()
      ? recovery.title.trim().slice(0, 120)
      : RECOVERY_ACTION_LABELS[action] ?? "恢复建议";
    return { title, instruction };
  }
  return null;
}

export interface WorkflowRunRetryTarget {
  stepId: string;
  label: string;
}

/**
 * Retry is offered only where the runtime already allows a whole-step retry
 * without a separate paid-media authorization; media steps keep their existing
 * authorization path instead of failing here with a confusing error.
 */
export function workflowRunRetryTarget(run: WorkflowRun): WorkflowRunRetryTarget | null {
  if (run.status !== "failed") return null;
  const step = workflowRunFailedStep(run);
  if (!step) return null;
  if ((step.retry_policy ?? "manual") === "never") return null;
  if ((step.requires ?? []).includes("task_authorization:auto")) return null;
  return { stepId: step.id, label: step.label || step.id };
}
