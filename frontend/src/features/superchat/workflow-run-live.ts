// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  emitCanvasPatchNotification,
  type CanvasPatchNotification,
} from "@/features/superchat/canvas-patch-events";
import {
  emitVillageAgentEvent,
  villageAgentEventFromWorkflowEnvelope,
} from "@/features/superchat/village-agent-events";
import {
  workflowReleaseReadinessFromRun,
  workflowReleaseRequiresNotice,
} from "@/features/superchat/workflow-release-readiness";
import type {
  WorkflowRun,
  WorkflowRunEvent,
} from "@/types/workflow-runtime";

const WORKFLOW_CURSOR_KEY_PREFIX = "village-canvas.workflow-event-cursor.v1";
export const WORKFLOW_RUN_EVENT = "village-canvas:workflow-run";

type JsonRecord = Record<string, unknown>;

export interface WorkflowRunLiveOptions {
  projectId: string;
  canvasId: string;
  runId: string;
  onRun: (run: WorkflowRun) => void;
  onEvent?: (event: WorkflowRunEvent) => void;
  onError?: (event: Event) => void;
}

export interface WorkflowRunLiveSubscription {
  close: () => void;
  cursor: () => number;
}

function workflowRunTimestamp(value: string | undefined): number {
  const parsed = Date.parse(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Merge snapshots without allowing a delayed list request or SSE replay to
 * roll the UI back from a newer live run.
 */
export function mergeWorkflowRun(
  current: WorkflowRun | null,
  incoming: WorkflowRun | null,
): WorkflowRun | null {
  if (!incoming) return current;
  if (!current) return incoming;

  if (
    current.project_id !== incoming.project_id
    || current.canvas_id !== incoming.canvas_id
  ) {
    return incoming;
  }

  if (current.id === incoming.id) {
    if (incoming.revision !== current.revision) {
      return incoming.revision > current.revision ? incoming : current;
    }
    if (incoming.event_seq !== current.event_seq) {
      return incoming.event_seq > current.event_seq ? incoming : current;
    }
    const incomingUpdatedAt = workflowRunTimestamp(incoming.updated_at);
    const currentUpdatedAt = workflowRunTimestamp(current.updated_at);
    return incomingUpdatedAt > currentUpdatedAt ? incoming : current;
  }

  const incomingCreatedAt = workflowRunTimestamp(incoming.created_at);
  const currentCreatedAt = workflowRunTimestamp(current.created_at);
  if (incomingCreatedAt !== currentCreatedAt) {
    return incomingCreatedAt > currentCreatedAt ? incoming : current;
  }

  const incomingUpdatedAt = workflowRunTimestamp(incoming.updated_at);
  const currentUpdatedAt = workflowRunTimestamp(current.updated_at);
  return incomingUpdatedAt > currentUpdatedAt ? incoming : current;
}

export function activeWorkflowRunFromList(
  runs: WorkflowRun[],
): WorkflowRun | null {
  return runs.find((run) => (
    run.status === "running"
    || run.status === "paused"
    || run.status === "failed"
    || (
      run.status === "completed"
      && workflowReleaseRequiresNotice(workflowReleaseReadinessFromRun(run))
    )
  )) ?? null;
}

function record(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number"
    && Number.isSafeInteger(value)
    && value > 0
    ? value
    : null;
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number"
    && Number.isSafeInteger(value)
    && value >= 0
    ? value
    : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function workflowRunCursorStorageKey(
  projectId: string,
  runId: string,
): string {
  return `${WORKFLOW_CURSOR_KEY_PREFIX}:${projectId.trim()}:${runId.trim()}`;
}

function loadCursor(projectId: string, runId: string): number {
  if (typeof window === "undefined") return 0;
  try {
    const value = Number.parseInt(
      window.sessionStorage.getItem(workflowRunCursorStorageKey(projectId, runId)) ?? "0",
      10,
    );
    return Number.isSafeInteger(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

function saveCursor(projectId: string, runId: string, cursor: number): void {
  if (typeof window === "undefined" || cursor <= 0) return;
  try {
    window.sessionStorage.setItem(
      workflowRunCursorStorageKey(projectId, runId),
      String(cursor),
    );
  } catch {
    // Cursor persistence is an optimization; the server stream remains replay-safe.
  }
}

function canvasRevisionFromEvent(event: WorkflowRunEvent): number | null {
  const payload = record(event.payload);
  if (!payload) return null;
  const direct = positiveInteger(payload.canvas_revision)
    ?? positiveInteger(payload.revision);
  if (direct) return direct;
  const receipt = record(payload.canvas_receipt);
  const verification = record(payload.verification);
  return positiveInteger(receipt?.canvas_revision)
    ?? positiveInteger(receipt?.revision)
    ?? positiveInteger(verification?.canvas_revision)
    ?? positiveInteger(verification?.revision);
}

function commandIdFromEvent(event: WorkflowRunEvent): string | null {
  const payload = record(event.payload);
  const receipt = record(payload?.canvas_receipt);
  const verification = record(payload?.verification);
  return nonEmptyString(payload?.command_id)
    ?? nonEmptyString(receipt?.command_id)
    ?? nonEmptyString(verification?.command_id);
}

export function workflowCanvasPatchFromEvent(
  projectId: string,
  canvasId: string,
  runId: string,
  event: WorkflowRunEvent,
): CanvasPatchNotification | null {
  if (!["receipt_recorded", "verification_passed", "step_progress"].includes(event.type)) {
    return null;
  }
  const revision = canvasRevisionFromEvent(event);
  if (!revision) return null;
  const commandId = commandIdFromEvent(event);
  return {
    projectId,
    canvasId,
    runId,
    revision,
    ...(commandId ? { commandId } : {}),
    schema: "canvas_command_receipt.v2",
    serverApplied: true,
    snapshotRequired: true,
    uiReconcileRequired: true,
    structureStatus: event.type === "verification_passed" ? "verified" : "applied",
  };
}

export function workflowCanvasPatchFromRun(
  run: WorkflowRun,
): CanvasPatchNotification | null {
  const revision = positiveInteger(run.last_verified_canvas_revision);
  const projectId = nonEmptyString(run.project_id);
  const canvasId = nonEmptyString(run.canvas_id);
  const runId = nonEmptyString(run.id);
  if (!revision || !projectId || !canvasId || !runId) return null;
  return {
    projectId,
    canvasId,
    runId,
    revision,
    schema: "workflow_run_snapshot.v2",
    serverApplied: true,
    snapshotRequired: true,
    uiReconcileRequired: true,
    structureStatus: run.status === "completed" ? "verified" : "applied",
  };
}

export function recentlyCreatedWorkflowRun(
  runs: WorkflowRun[],
  observedAt: number,
  maxAgeMs = 15_000,
): WorkflowRun | null {
  const minimum = observedAt - Math.max(0, maxAgeMs);
  const maximum = observedAt + 5_000;
  return runs.reduce<WorkflowRun | null>((latest, run) => {
    const createdAt = workflowRunTimestamp(run.created_at);
    if (createdAt < minimum || createdAt > maximum) return latest;
    if (!latest) return run;
    return createdAt > workflowRunTimestamp(latest.created_at) ? run : latest;
  }, null);
}

function parseMessageData(event: Event): JsonRecord | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== "string") {
    return null;
  }
  try {
    return record(JSON.parse(event.data));
  } catch {
    return null;
  }
}

export function workflowRunFromEnvelope(value: unknown): WorkflowRun | null {
  const envelope = record(value);
  const run = record(envelope?.run);
  const status = nonEmptyString(run?.status);
  const runMode = nonEmptyString(run?.run_mode);
  return run
    && nonEmptyString(run.id)
    && nonEmptyString(run.workflow_id)
    && nonEmptyString(run.project_id)
    && nonEmptyString(run.canvas_id)
    && positiveInteger(run.workflow_version)
    && nonNegativeInteger(run.revision) !== null
    && nonNegativeInteger(run.event_seq) !== null
    && (runMode === "draft" || runMode === "auto")
    && ["running", "paused", "failed", "completed", "cancelled"].includes(status ?? "")
    && Array.isArray(run.current_frontier)
    && record(run.step_states)
    && record(run.inputs)
    && record(run.artifacts)
    ? run as unknown as WorkflowRun
    : null;
}

export function emitWorkflowRun(run: WorkflowRun): void {
  if (typeof window === "undefined") return;
  const patch = workflowCanvasPatchFromRun(run);
  if (patch) emitCanvasPatchNotification(patch);
  window.dispatchEvent(new CustomEvent<WorkflowRun>(WORKFLOW_RUN_EVENT, { detail: run }));
}

function workflowEventFromEnvelope(value: JsonRecord | null): WorkflowRunEvent | null {
  const event = record(value?.event);
  return event
    && positiveInteger(event.seq)
    && nonEmptyString(event.event_id)
    ? event as unknown as WorkflowRunEvent
    : null;
}

export function subscribeWorkflowRunLive(
  options: WorkflowRunLiveOptions,
): WorkflowRunLiveSubscription {
  const projectId = options.projectId.trim();
  const canvasId = options.canvasId.trim();
  const runId = options.runId.trim();
  let cursor = loadCursor(projectId, runId);
  let closed = false;

  if (
    typeof window === "undefined"
    || typeof EventSource === "undefined"
    || !projectId
    || !canvasId
    || !runId
  ) {
    return { close: () => undefined, cursor: () => cursor };
  }

  const params = new URLSearchParams({ after_seq: String(cursor) });
  const source = new EventSource(
    `/api/v1/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/events/stream?${params.toString()}`,
    { withCredentials: true },
  );

  const updateCursor = (next: number) => {
    if (next <= cursor) return;
    cursor = next;
    saveCursor(projectId, runId, cursor);
  };
  const onWorkflowEvent = (raw: Event) => {
    const data = parseMessageData(raw);
    const event = workflowEventFromEnvelope(data);
    if (!event || event.seq <= cursor) return;
    emitVillageAgentEvent(villageAgentEventFromWorkflowEnvelope(data));
    const patch = workflowCanvasPatchFromEvent(
      projectId,
      canvasId,
      runId,
      event,
    );
    if (patch) emitCanvasPatchNotification(patch);
    options.onEvent?.(event);
    updateCursor(event.seq);
  };
  const onSnapshot = (raw: Event) => {
    const data = parseMessageData(raw);
    emitVillageAgentEvent(villageAgentEventFromWorkflowEnvelope(data));
    const run = workflowRunFromEnvelope(data);
    if (run) options.onRun(run);
    const next = positiveInteger(data?.cursor);
    if (next) updateCursor(next);
  };
  const onTerminal = (raw: Event) => {
    onSnapshot(raw);
    closed = true;
    source.close();
  };
  const onError = (event: Event) => {
    if (!closed) options.onError?.(event);
  };

  source.addEventListener("workflow.event", onWorkflowEvent);
  source.addEventListener("workflow.snapshot", onSnapshot);
  source.addEventListener("workflow.terminal", onTerminal);
  source.addEventListener("error", onError);

  return {
    close: () => {
      if (closed) return;
      closed = true;
      source.close();
    },
    cursor: () => cursor,
  };
}
