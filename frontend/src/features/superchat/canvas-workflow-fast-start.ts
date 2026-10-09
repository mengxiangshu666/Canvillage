import {
  recordWorkflowRunEvent,
  startWorkflowRun,
} from "@/api/workflow-runtime";
import { flushFreezoneCanvasRuntime } from "@/lib/freezone-canvas-runtime";
import type { CanvasAgentKernelPlan } from "@/features/superchat/canvas-agent-kernel";
import type { ChatMessage } from "@/features/superchat/types";
import {
  CANVAS_COMMAND_RECEIPT_EVENT,
  type CanvasCommandReceipt,
} from "@/features/superchat/canvas-command-receipts";
import { emitCanvasAgentCommandEnvelope } from "@/features/superchat/canvas-patch-events";
import type { StructureCommandEnvelope } from "@/features/superchat/structure-proposal-store";
import type {
  CanvasWorkflowRuntimeContext,
  WorkflowRun,
  WorkflowRunCreate,
  WorkflowRunEventCreate,
} from "@/types/workflow-runtime";

const RUNTIME_WORKFLOW_BY_INTENT: Partial<Record<CanvasAgentKernelPlan["intent"]["id"], string>> = {
  one_click_film: "one-click-film",
  storyboard: "storyboard-production",
  workflow_build: "custom-canvas-workflow",
};

export interface CanvasWorkflowFastStartInput {
  projectId: string;
  canvasId: string;
  userText: string;
  runMode: "draft" | "auto";
  plan: CanvasAgentKernelPlan;
  idempotencyKey?: string;
  receiptTimeoutMs?: number;
  canvasRevision?: number;
  selectedNodeIds?: readonly string[];
  pinnedNodeIds?: readonly string[];
}

export interface CanvasWorkflowFastStartResult {
  context: CanvasWorkflowRuntimeContext;
  run: WorkflowRun;
}

export interface CanvasWorkflowFailureCheckpoint {
  eventId: string;
  stepId: string;
  turnId: string;
  error: string;
}

export interface PendingWorkflowCanvasCommand {
  stepId: string;
  envelope: StructureCommandEnvelope;
  completionMode?: "canvas" | "media_tasks";
}

export interface WorkflowMediaNode {
  id: string;
  type?: string;
  data?: Record<string, unknown>;
}

export interface WorkflowMediaBatchCheckpoint {
  eventId: string;
  eventType: "step_progress" | "step_completed" | "step_failed";
  stepId: string;
  success?: boolean;
  error?: string;
  payload: Record<string, unknown>;
}

interface CanvasWorkflowFastStartDependencies {
  startRun: (project: string, payload: WorkflowRunCreate) => Promise<WorkflowRun>;
  recordEvent: (
    project: string,
    runId: string,
    payload: WorkflowRunEventCreate,
  ) => Promise<WorkflowRun>;
  emitCommand: (envelope: StructureCommandEnvelope) => void;
  flushCanvas: (project: string, canvas: string) => Promise<boolean | null>;
}

const DEFAULT_DEPENDENCIES: CanvasWorkflowFastStartDependencies = {
  startRun: startWorkflowRun,
  recordEvent: recordWorkflowRunEvent,
  emitCommand: emitCanvasAgentCommandEnvelope,
  flushCanvas: flushFreezoneCanvasRuntime,
};

function requestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export function workflowRuntimeIdForPlan(plan: CanvasAgentKernelPlan): string | null {
  return RUNTIME_WORKFLOW_BY_INTENT[plan.intent.id] ?? null;
}

export function buildWorkflowCanvasEnvelope(input: {
  projectId: string;
  canvasId: string;
  runId: string;
  starterWorkflowId: string;
}): StructureCommandEnvelope {
  return {
    schema: "canvas_chat_commands.v1",
    project_id: input.projectId,
    canvas_id: input.canvasId,
    run_id: input.runId,
    command_id: `workflow:${input.runId}:canvas_structure:v1`,
    canvas_command_emitted: true,
    commands: [
      {
        type: "insert_starter_workflow",
        workflow_id: input.starterWorkflowId,
        placement: { anchor: "viewport_center", layout: "grid" },
      },
    ],
  };
}

export function waitForCanvasCommandResult(
  commandId: string,
  timeoutMs = 5_000,
): Promise<CanvasCommandReceipt> {
  return new Promise((resolve, reject) => {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      window.removeEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt);
      if (timeout !== undefined) clearTimeout(timeout);
    };
    const onReceipt = (event: Event) => {
      const receipt = (event as CustomEvent<CanvasCommandReceipt>).detail;
      if (receipt?.commandId !== commandId || receipt.stage !== "result") return;
      cleanup();
      resolve(receipt);
    };
    window.addEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt);
    timeout = setTimeout(() => {
      cleanup();
      reject(new Error(`canvas command receipt timed out: ${commandId}`));
    }, timeoutMs);
  });
}

export function canvasWorkflowRuntimeContextFromRun(
  run: WorkflowRun,
): CanvasWorkflowRuntimeContext {
  const structure = run.artifacts.canvas_structure as {
    command_id?: unknown;
    created_node_ids?: unknown;
    canvas_receipt?: {
      command_id?: unknown;
      created_node_ids?: unknown;
    };
  } | undefined;
  const structureReceipt = structure?.canvas_receipt;
  const starterWorkflowId = String(run.artifacts.starter_workflow_id ?? "");
  const commandId = String(
    structureReceipt?.command_id
    ?? structure?.command_id
    ?? `workflow:${run.id}:canvas_structure:a1`,
  );
  const rawCreatedNodeIds = structureReceipt?.created_node_ids ?? structure?.created_node_ids;
  const createdNodeIds = Array.isArray(rawCreatedNodeIds)
    ? rawCreatedNodeIds.map(String)
    : [];
  const failedStep = Object.values(run.step_states).find((step) => step.status === "failed");
  return {
    workflow_run_id: run.id,
    workflow_id: run.workflow_id,
    workflow_version: run.workflow_version,
    status: run.status,
    current_frontier: [...run.current_frontier],
    current_step: run.current_frontier[0] ?? failedStep?.id ?? "",
    starter_workflow_id: starterWorkflowId,
    canvas_structure_command_id: commandId,
    structure_already_applied: run.step_states.canvas_structure?.status === "completed",
    canvas_created_node_ids: createdNodeIds,
    revision: run.revision,
    runtime_phase: run.runtime_phase,
    next_action: run.next_action,
    last_verified_canvas_revision: run.last_verified_canvas_revision,
  };
}

export function canvasWorkflowFailureCheckpoint(
  run: WorkflowRun | null | undefined,
  messages: readonly ChatMessage[],
  busy: boolean,
): CanvasWorkflowFailureCheckpoint | null {
  if (busy || !run || run.status !== "running") return null;
  const failureMessage = [...messages].reverse().find((message) => (
    message.role === "assistant"
    && /^本轮没有完成[:：]/.test(message.text.trim())
    && Boolean(message.turnId)
  ));
  if (!failureMessage?.turnId) return null;
  const matchingRequest = messages.find((message) => (
    message.role === "user"
    && message.turnId === failureMessage.turnId
    && message.text.trim() === String(run.inputs.request || "").trim()
  ));
  if (!matchingRequest) return null;
  const stepId = run.current_frontier.find(
    (id) => run.step_states[id]?.status === "running",
  );
  if (!stepId) return null;
  return {
    eventId: `agent-failure:${failureMessage.id}:${stepId}`,
    stepId,
    turnId: failureMessage.turnId,
    error: failureMessage.text.slice(0, 4_000),
  };
}

export function pendingWorkflowCanvasCommand(
  run: WorkflowRun | null | undefined,
): PendingWorkflowCanvasCommand | null {
  if (!run || run.status !== "running") return null;
  if ((run.contract_version ?? 1) >= 2) return null;
  for (const stepId of run.current_frontier) {
    const artifact = run.artifacts[stepId] as Record<string, unknown> | undefined;
    const envelope = artifact?.command_envelope as StructureCommandEnvelope | undefined;
    if (
      artifact?.kind !== "canvas_command"
      || artifact.status !== "awaiting_canvas"
      || !envelope
      || envelope.schema !== "canvas_chat_commands.v1"
      || !envelope.command_id
      || !Array.isArray(envelope.commands)
    ) {
      continue;
    }
    return {
      stepId,
      envelope,
      completionMode: artifact.completion_mode === "media_tasks" ? "media_tasks" : "canvas",
    };
  }
  return null;
}

function nonEmptyString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function generatedMediaUrl(data: Record<string, unknown>): string {
  for (const key of ["imageUrl", "previewImageUrl", "videoUrl", "resultVideoUrl", "audioUrl"]) {
    const value = nonEmptyString(data[key]);
    if (value) return value;
  }
  const batch = Array.isArray(data.generationBatch) ? data.generationBatch : [];
  return nonEmptyString(batch[0]);
}

function nodeTaskRefs(node: WorkflowMediaNode): Array<Record<string, string>> {
  const data = node.data ?? {};
  const refs = Array.isArray(data.generationTaskRefs) ? data.generationTaskRefs : [];
  const normalized = refs.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    const taskKey = nonEmptyString(value.taskKey ?? value.task_key);
    const taskType = nonEmptyString(value.taskType ?? value.task_type);
    const jobId = nonEmptyString(value.jobId ?? value.job_id);
    return taskKey && taskType && jobId
      ? [{ task_key: taskKey, task_type: taskType, job_id: jobId }]
      : [];
  });
  if (normalized.length > 0) return normalized;
  const taskKey = nonEmptyString(data.generationTaskKey);
  const taskType = nonEmptyString(data.generationTaskType);
  const jobId = nonEmptyString(data.generationTaskJobId);
  return taskKey && taskType && jobId
    ? [{ task_key: taskKey, task_type: taskType, job_id: jobId }]
    : [];
}

export function canvasWorkflowMediaBatchCheckpoint(
  run: WorkflowRun | null | undefined,
  nodes: readonly WorkflowMediaNode[],
): WorkflowMediaBatchCheckpoint | null {
  if (!run || run.status !== "running") return null;
  const stepId = run.current_frontier.find((id) => id === "media_generation");
  if (!stepId || run.step_states[stepId]?.status !== "running") return null;
  const artifact = run.artifacts[stepId] as Record<string, unknown> | undefined;
  if (
    artifact?.completion_mode !== "media_tasks"
    || artifact.status !== "monitoring"
  ) {
    return null;
  }
  const targetNodeIds = Array.isArray(artifact.target_node_ids)
    ? artifact.target_node_ids.map(String).filter(Boolean)
    : [];
  if (targetNodeIds.length === 0) return null;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const missing = targetNodeIds.filter((nodeId) => !byId.has(nodeId));
  const attempt = run.step_states[stepId]?.attempt ?? 1;
  if (missing.length > 0) {
    return {
      eventId: `media-failed:${run.id}:a${attempt}`,
      eventType: "step_failed",
      stepId,
      success: false,
      error: `工作流媒体节点已从画布移除：${missing.join(", ")}`,
      payload: { target_node_ids: targetNodeIds, missing_node_ids: missing },
    };
  }

  const targetNodes = targetNodeIds.map((nodeId) => byId.get(nodeId)!);
  const failed = targetNodes.find((node) => {
    const data = node.data ?? {};
    return nonEmptyString(data.generationError) && data.isGenerating !== true;
  });
  const jobs = targetNodes.flatMap((node) => nodeTaskRefs(node).map((job) => ({
    node_id: node.id,
    ...job,
  })));
  if (failed) {
    const error = nonEmptyString(failed.data?.generationError) || "媒体生成失败";
    return {
      eventId: `media-failed:${run.id}:a${attempt}`,
      eventType: "step_failed",
      stepId,
      success: false,
      error,
      payload: { target_node_ids: targetNodeIds, failed_node_id: failed.id, jobs },
    };
  }

  const mediaAssets = targetNodes.flatMap((node) => {
    const url = generatedMediaUrl(node.data ?? {});
    return url ? [{ node_id: node.id, node_type: node.type ?? "", url }] : [];
  });
  if (mediaAssets.length === targetNodes.length) {
    const persistedJobs = Array.isArray(artifact.jobs) ? artifact.jobs : [];
    return {
      eventId: `media-completed:${run.id}:a${attempt}`,
      eventType: "step_completed",
      stepId,
      success: true,
      payload: {
        status: "completed",
        target_node_ids: targetNodeIds,
        jobs: persistedJobs.length > 0 ? persistedJobs : jobs,
        media_assets: mediaAssets,
      },
    };
  }

  const allStarted = targetNodes.every((node) => {
    const data = node.data ?? {};
    return data.isGenerating === true
      || data.canvas_auto_generate_once === true
      || nodeTaskRefs(node).length > 0
      || Boolean(generatedMediaUrl(data));
  });
  const persistedJobs = Array.isArray(artifact.jobs) ? artifact.jobs : [];
  if (allStarted && jobs.length > 0 && persistedJobs.length === 0) {
    return {
      eventId: `media-started:${run.id}:a${attempt}`,
      eventType: "step_progress",
      stepId,
      payload: {
        status: "monitoring",
        target_node_ids: targetNodeIds,
        jobs,
      },
    };
  }
  return null;
}

export async function applyPendingWorkflowCanvasCommand(
  input: {
    projectId: string;
    canvasId: string;
    run: WorkflowRun;
    pending: PendingWorkflowCanvasCommand;
    receiptTimeoutMs?: number;
  },
  dependencies: CanvasWorkflowFastStartDependencies = DEFAULT_DEPENDENCIES,
): Promise<WorkflowRun> {
  const { envelope } = input.pending;
  const receiptPromise = waitForCanvasCommandResult(
    envelope.command_id,
    input.receiptTimeoutMs ?? 8_000,
  );
  dependencies.emitCommand(envelope);
  let receipt: CanvasCommandReceipt;
  try {
    receipt = await receiptPromise;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return dependencies.recordEvent(input.projectId, input.run.id, {
      event_id: `${envelope.command_id}:failed`,
      type: "canvas_applied",
      step_id: input.pending.stepId,
      success: false,
      error: message,
      payload: { command_id: envelope.command_id },
      expected_revision: input.run.revision,
    });
  }
  const monitorsMediaTasks = input.pending.completionMode === "media_tasks";
  const updated = await dependencies.recordEvent(input.projectId, input.run.id, {
    event_id: receipt.receiptId,
    type: monitorsMediaTasks ? "step_progress" : "canvas_applied",
    step_id: input.pending.stepId,
    success: receipt.success === true,
    error: receipt.error ?? "",
    payload: {
      ...(monitorsMediaTasks ? { status: "monitoring" } : {}),
      command_id: receipt.commandId,
      applied: receipt.applied ?? 0,
      skipped: receipt.skipped ?? 0,
      created_node_ids: receipt.createdIds ?? [],
      canvas_revision: receipt.revision ?? null,
    },
    expected_revision: input.run.revision,
  });
  if (receipt.success === true) {
    await dependencies.flushCanvas(input.projectId, input.canvasId);
  }
  return updated;
}

export async function startKnownCanvasWorkflow(
  input: CanvasWorkflowFastStartInput,
  dependencies: CanvasWorkflowFastStartDependencies = DEFAULT_DEPENDENCIES,
): Promise<CanvasWorkflowFastStartResult | null> {
  const workflowId = workflowRuntimeIdForPlan(input.plan);
  if (!workflowId || !input.projectId.trim() || !input.canvasId.trim()) {
    return null;
  }
  const idempotencyKey = input.idempotencyKey ?? `workflow-start:${requestId()}`;
  const run = await dependencies.startRun(input.projectId, {
    workflow_id: workflowId,
    canvas_id: input.canvasId,
    run_mode: input.runMode,
    inputs: {
      request: input.userText,
      intent_id: input.plan.intent.id,
      director_mode: "production",
      ...(input.plan.starterWorkflowId?.trim()
        ? { starter_workflow_id: input.plan.starterWorkflowId.trim() }
        : {}),
      ...(input.plan.useStarterWorkflow === true ? { use_starter_workflow: true } : {}),
      run_mode: input.runMode,
    },
    idempotency_key: idempotencyKey,
    contract_version: 2,
    goal: input.userText.trim(),
    success_criteria: [
      "画布结构已真实落盘并通过 revision 验收",
      "工作流声明的产物存在且可在画布继续编辑",
    ],
    canvas_revision: input.canvasRevision,
    selected_node_ids: [...(input.selectedNodeIds ?? [])],
    pinned_node_ids: [...(input.pinnedNodeIds ?? [])],
  });
  return {
    context: canvasWorkflowRuntimeContextFromRun(run),
    run,
  };
}
