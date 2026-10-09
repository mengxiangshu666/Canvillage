// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ChatScope, ServerFrame } from "@/features/superchat/types";

export const VILLAGE_AGENT_EVENT_SCHEMA = "village_agent_event.v1";
export const VILLAGE_AGENT_EVENT = "village-canvas:agent-event";

type JsonRecord = Record<string, unknown>;

export type VillageAgentEventStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | string;

export interface VillageAgentEvent {
  schema: typeof VILLAGE_AGENT_EVENT_SCHEMA;
  event_id: string;
  seq: number;
  type: string;
  status: VillageAgentEventStatus;
  created_at?: string;
  turn_id?: string;
  run_id?: string;
  workflow_run_id?: string;
  project_id?: string;
  canvas_id?: string;
  step_id?: string;
  command_id?: string;
  revision?: number;
  parent_event_id?: string;
  payload: JsonRecord;
}

const LEGACY_CHAT_EVENT_TYPES: Record<string, string> = {
  "thread.started": "run.started",
  "run.started": "run.started",
  "chat.progress": "run.progress",
  "assistant.delta": "assistant.delta",
  "assistant.message": "assistant.completed",
  "tool.call": "tool.call",
  "tool.result": "tool.result",
  "canvas.patch": "canvas.receipt",
  "workflow.run": "workflow.updated",
  "task.started": "step.started",
  "chat.done": "run.completed",
  "chat.recoverable": "run.failed",
  error: "run.failed",
};

const emittedEventIds = new Map<string, number>();
const EMITTED_EVENT_LIMIT = 512;

function record(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number"
    && Number.isSafeInteger(value)
    && value >= 0
    ? value
    : null;
}

function positiveInteger(value: unknown): number | null {
  const parsed = nonNegativeInteger(value);
  return parsed !== null && parsed > 0 ? parsed : null;
}

function eventStreamKey(event: VillageAgentEvent): string {
  return event.workflow_run_id
    || event.run_id
    || event.turn_id
    || `${event.project_id ?? ""}:${event.canvas_id ?? ""}`;
}

function eventTimestamp(event: VillageAgentEvent): number {
  const parsed = Date.parse(event.created_at ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export function normalizeVillageAgentEvent(value: unknown): VillageAgentEvent | null {
  const event = record(value);
  if (!event || event.schema !== VILLAGE_AGENT_EVENT_SCHEMA) return null;
  const eventId = nonEmptyString(event.event_id);
  const eventType = nonEmptyString(event.type);
  const status = nonEmptyString(event.status);
  const seq = nonNegativeInteger(event.seq);
  if (!eventId || !eventType || !status || seq === null) return null;
  return {
    schema: VILLAGE_AGENT_EVENT_SCHEMA,
    event_id: eventId,
    seq,
    type: eventType,
    status,
    ...(nonEmptyString(event.created_at) ? { created_at: nonEmptyString(event.created_at)! } : {}),
    ...(nonEmptyString(event.turn_id) ? { turn_id: nonEmptyString(event.turn_id)! } : {}),
    ...(nonEmptyString(event.run_id) ? { run_id: nonEmptyString(event.run_id)! } : {}),
    ...(nonEmptyString(event.workflow_run_id)
      ? { workflow_run_id: nonEmptyString(event.workflow_run_id)! }
      : {}),
    ...(nonEmptyString(event.project_id) ? { project_id: nonEmptyString(event.project_id)! } : {}),
    ...(nonEmptyString(event.canvas_id) ? { canvas_id: nonEmptyString(event.canvas_id)! } : {}),
    ...(nonEmptyString(event.step_id) ? { step_id: nonEmptyString(event.step_id)! } : {}),
    ...(nonEmptyString(event.command_id) ? { command_id: nonEmptyString(event.command_id)! } : {}),
    ...(positiveInteger(event.revision) ? { revision: positiveInteger(event.revision)! } : {}),
    ...(nonEmptyString(event.parent_event_id)
      ? { parent_event_id: nonEmptyString(event.parent_event_id)! }
      : {}),
    payload: record(event.payload) ?? {},
  };
}

function legacyEventId(frame: JsonRecord, eventType: string, seq: number): string {
  const run = record(frame.run);
  return [
    "legacy",
    eventType,
    nonEmptyString(frame.turn_id) ?? "",
    nonEmptyString(frame.command_id) ?? "",
    nonEmptyString(frame.call_id) ?? "",
    nonEmptyString(run?.id) ?? "",
    String(seq),
  ].join(":");
}

function legacyStatus(frame: JsonRecord, eventType: string): string {
  if (frame.type === "chat.done") {
    if (frame.cancelled === true) return "cancelled";
    return frame.failed === true ? "failed" : "completed";
  }
  if (frame.type === "tool.result") return frame.success === false ? "failed" : "completed";
  if (eventType.endsWith(".failed")) return "failed";
  if (eventType.endsWith(".completed") || eventType === "canvas.receipt") return "completed";
  return "running";
}

/** Normalize new frames while retaining a bounded fallback for older backends. */
export function villageAgentEventFromChatFrame(frame: ServerFrame): VillageAgentEvent | null {
  const explicit = normalizeVillageAgentEvent(frame.agent_event);
  if (explicit) return explicit;
  const raw = frame as JsonRecord;
  const legacyType = nonEmptyString(raw.type);
  const eventType = legacyType ? LEGACY_CHAT_EVENT_TYPES[legacyType] : null;
  if (!legacyType || !eventType) return null;
  const run = record(raw.run);
  const seq = positiveInteger(raw.revision)
    ?? nonNegativeInteger(run?.event_seq)
    ?? nonNegativeInteger(run?.revision)
    ?? 0;
  const projectId = nonEmptyString(raw.project_id) ?? nonEmptyString(run?.project_id);
  const canvasId = nonEmptyString(raw.canvas_id) ?? nonEmptyString(run?.canvas_id);
  const workflowRunId = nonEmptyString(run?.id) ?? nonEmptyString(raw.workflow_run_id);
  const turnId = nonEmptyString(raw.turn_id);
  return {
    schema: VILLAGE_AGENT_EVENT_SCHEMA,
    event_id: legacyEventId(raw, eventType, seq),
    seq,
    type: eventType,
    status: legacyStatus(raw, eventType),
    ...(turnId ? { turn_id: turnId } : {}),
    ...(nonEmptyString(raw.run_id) ? { run_id: nonEmptyString(raw.run_id)! } : {}),
    ...(workflowRunId ? { workflow_run_id: workflowRunId } : {}),
    ...(projectId ? { project_id: projectId } : {}),
    ...(canvasId ? { canvas_id: canvasId } : {}),
    ...(nonEmptyString(raw.command_id) ? { command_id: nonEmptyString(raw.command_id)! } : {}),
    ...(positiveInteger(raw.revision) ? { revision: positiveInteger(raw.revision)! } : {}),
    payload: { legacy_type: legacyType },
  };
}

export function villageAgentEventFromWorkflowEnvelope(value: unknown): VillageAgentEvent | null {
  const envelope = record(value);
  const explicit = normalizeVillageAgentEvent(envelope?.agent_event);
  if (explicit) return explicit;
  const event = record(envelope?.event);
  if (!event) return null;
  const runId = nonEmptyString(envelope?.run_id) ?? nonEmptyString(event.run_id);
  const sourceEventId = nonEmptyString(event.event_id);
  const seq = nonNegativeInteger(event.seq);
  const type = nonEmptyString(event.type);
  if (!runId || !sourceEventId || seq === null || !type) return null;
  return {
    schema: VILLAGE_AGENT_EVENT_SCHEMA,
    event_id: `legacy:workflow:${runId}:${sourceEventId}`,
    seq,
    type: type.includes("failed") ? "step.failed" : "workflow.updated",
    status: type.includes("failed") ? "failed" : "running",
    run_id: runId,
    workflow_run_id: runId,
    ...(nonEmptyString(event.step_id) ? { step_id: nonEmptyString(event.step_id)! } : {}),
    payload: { legacy_type: type, source_event_id: sourceEventId },
  };
}

export function isNewerVillageAgentEvent(
  current: VillageAgentEvent,
  incoming: VillageAgentEvent,
): boolean {
  if (current.event_id === incoming.event_id) {
    if (incoming.seq !== current.seq) return incoming.seq > current.seq;
    return eventTimestamp(incoming) >= eventTimestamp(current);
  }
  if (eventStreamKey(current) !== eventStreamKey(incoming)) return true;
  if (incoming.seq !== current.seq) return incoming.seq > current.seq;
  return eventTimestamp(incoming) >= eventTimestamp(current);
}

export function mergeVillageAgentEvents(
  current: readonly VillageAgentEvent[],
  incoming: readonly VillageAgentEvent[],
  limit = 96,
): VillageAgentEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  for (const event of incoming) {
    const existing = byId.get(event.event_id);
    if (!existing || isNewerVillageAgentEvent(existing, event)) byId.set(event.event_id, event);
  }
  return [...byId.values()]
    .sort((left, right) => left.seq - right.seq || eventTimestamp(left) - eventTimestamp(right))
    .slice(-Math.max(1, limit));
}

export function villageAgentEventMatchesScope(
  event: VillageAgentEvent,
  scope: ChatScope,
): boolean {
  if (scope.kind !== "project") return !event.project_id;
  if (event.project_id && event.project_id !== String(scope.id ?? "")) return false;
  return !event.canvas_id || event.canvas_id === String(scope.canvas_id || "default");
}

export function emitVillageAgentEvent(event: VillageAgentEvent | null): boolean {
  if (!event || typeof window === "undefined") return false;
  if (emittedEventIds.has(event.event_id)) return false;
  emittedEventIds.set(event.event_id, Date.now());
  while (emittedEventIds.size > EMITTED_EVENT_LIMIT) {
    const oldest = emittedEventIds.keys().next().value as string | undefined;
    if (!oldest) break;
    emittedEventIds.delete(oldest);
  }
  window.dispatchEvent(new CustomEvent<VillageAgentEvent>(VILLAGE_AGENT_EVENT, { detail: event }));
  return true;
}
