// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AgentRuntimeEvent,
  AgentRuntimeSnapshot,
  AgentEngine,
  AgentEngineEntry,
  ApprovalRequest,
  ChatAttachment,
  ChatMessage,
  ChatProgressState,
  ChatRecoveryPacket,
  ChatRecoveryState,
  ChatScope,
  CanvasAgentTelemetry,
  ClientFrame,
  ModelEntry,
  RelayInstanceInfo,
  ServerFrame,
  SessionControlCommand,
  SuperChatSettings,
  AgentWorkflowState,
} from "@/features/superchat/types";
import {
  emptyCanvasAgentTelemetry,
  recordBackgroundTask,
  recordCanvasPatch,
  recordCanvasReceipt,
} from "@/features/superchat/agent-run-telemetry";
import {
  buildLocalUserMessage,
  normalizeMessage,
} from "@/features/superchat/message";
import {
  approvalRequestFromValue,
  approvalRequestMatchesScope,
  approvalRequestsFromValue,
  approvalScopeMatches,
  submitApprovalDecision,
  upsertApprovalRequest,
} from "@/features/superchat/approval-runtime";
import { hasStructuredContent } from "@/features/superchat/spec-extract";
import {
  canvasPatchNotificationFromFrame,
  emitCanvasAgentCommandEnvelope,
  emitCanvasPatchNotification,
  emitCanvasReconnectNotification,
  type CanvasPatchNotification,
} from "@/features/superchat/canvas-patch-events";
import {
  confirmOptimisticCanvasCommand,
  isSafeOptimisticCreateEnvelope,
  optimisticCanvasGraphPresent,
  prepareOptimisticCanvasEnvelope,
  registerOptimisticCanvasCommand,
  rollbackOptimisticCanvasCommandsForTurn,
  settleOptimisticCanvasCommandsForTurn,
} from "@/features/superchat/optimistic-canvas-commands";
import {
  CANVAS_COMMAND_RECEIPT_EVENT,
  type CanvasCommandReceipt,
} from "@/features/superchat/canvas-command-receipts";
import {
  acknowledgeCanvasReceiptDelivery,
  CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS,
  clearCanvasReceiptOutbox,
  deferCanvasReceiptDelivery,
  enqueueCanvasReceiptDelivery,
  loadCanvasReceiptOutbox,
  resetCanvasReceiptDeliveryRetries,
} from "@/features/superchat/canvas-command-receipt-outbox";
import {
  emitWorkflowRun,
  recentlyCreatedWorkflowRun,
  workflowRunFromEnvelope,
} from "@/features/superchat/workflow-run-live";
import {
  emitVillageAgentEvent,
  mergeVillageAgentEvents,
  normalizeVillageAgentEvent,
  VILLAGE_AGENT_EVENT,
  villageAgentEventFromChatFrame,
  villageAgentEventMatchesScope,
  type VillageAgentEvent,
} from "@/features/superchat/village-agent-events";
import {
  dispatchFeToolCall,
  FE_TOOL_LIFECYCLE_EVENT,
  feToolLifecycleMatchesScope,
  type FeToolLifecycle,
} from "@/features/superchat/fe-tool-bridge";
import { listWorkflowRuns } from "@/api/workflow-runtime";
import { api } from "@/lib/api";
import { registerAppUpdateReloadBlocker } from "@/lib/app-update-available";
import { DIRECT_MODEL_REGISTRY_CHANGED_EVENT } from "@/lib/queries/model-gateway";
import {
  createChatConversation,
  DEFAULT_CHAT_CONVERSATION_ID,
  deleteChatConversation,
  listChatConversations,
  loadActiveConversationId,
  saveActiveConversationId,
  type ChatConversation,
} from "@/features/superchat/chat-conversations";
import {
  isStaleByTtl,
  pruneLocalStorageByPrefix,
  registerStorageReclaimer,
  safeLocalStorageSet,
} from "@/lib/localStorageQuota";

const SETTINGS_KEY = "superchat:settings";
const CANVAS_COMMAND_TOOL_NAMES = new Set([
  "freezone_emit_canvas_command",
  "village_canvas_apply_commands",
  "village_canvas_dispatch_action",
]);
const SERVER_OWNED_CANVAS_TOOL_NAMES = new Set([
  ...CANVAS_COMMAND_TOOL_NAMES,
  "freezone_run_node",
  "freezone_retry_node",
  "freezone_stop_task",
]);
// Server-owned tools persist first and publish canvas.patch. Their tool.result
// frames are receipts for the Agent, never a second browser mutation channel.
const CLIENT_EXECUTABLE_CANVAS_TOOL_NAMES = new Set<string>();
const MESSAGE_CACHE_PREFIX = "superchat:messages:v2:";
const MESSAGE_CACHE_LIMIT = 50;
// Refresh-recovery caches are best-effort; expire abandoned scopes so their
// blobs (one per conversation) can't accumulate forever and exhaust the quota.
const MESSAGE_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const ACTIVE_TURN_PREFIX = "superchat:active-turn:";
const ACTIVE_TURN_TTL_MS = 60 * 60 * 1000;
const VILLAGE_ENGINE_ENTRY: AgentEngineEntry = { id: "village", label: "小树", available: true };

type ActiveTurnSnapshot = {
  turnId: string;
  startedAt: number;
};

type ChatNotificationResponse = {
  ok: boolean;
  data?: unknown;
};

type ChatSteerResponse = {
  ok: boolean;
  data?: {
    accepted?: boolean;
    message?: unknown;
  };
};

type ChatModelsResponse = {
  data?: {
    default?: unknown;
    models?: unknown;
  };
};

type ChatEnginesResponse = {
  data?: { engines?: unknown };
};

type AgentModelCatalog = {
  defaultModel: string | null;
  models: ModelEntry[];
};

/** An Agent model is executable only while the current catalog confirms it. */
export function isUsableAgentModel(model: Pick<ModelEntry, "stale" | "enabled" | "disabled"> | null | undefined): boolean {
  return Boolean(model) && model?.stale !== true && model?.enabled !== false && model?.disabled !== true;
}

/**
 * Keep an explicitly persisted model visible when a refreshed catalog no
 * longer contains it.  The entry is deliberately non-executable: replacing
 * it with the catalog default would make a conversation silently change
 * models between turns.
 */
export function modelsWithPersistedSelection(
  models: ModelEntry[],
  persistedId: string | null,
): ModelEntry[] {
  if (!persistedId || models.some((model) => model.id === persistedId)) return models;
  return [
    {
      id: persistedId,
      label: `已失效 · ${persistedId}`,
      description: "该模型已不在当前 Agent 模型目录中，请重新选择",
      stale: true,
      enabled: false,
      disabled: true,
      disabledReason: "该模型已不在当前模型目录中",
    },
    ...models,
  ];
}

export type ChatSendOptions = {
  engine?: AgentEngine;
};

function agentModelSelectionKey(scopeKey: string): string {
  return `superchat:agent-model:${scopeKey}`;
}

function loadAgentModelSelection(scopeKey: string): string | null {
  try {
    const value = localStorage.getItem(agentModelSelectionKey(scopeKey))?.trim();
    return value || null;
  } catch {
    return null;
  }
}

function normalizeAgentModelCatalog(response: ChatModelsResponse): AgentModelCatalog {
  const rawModels = Array.isArray(response.data?.models) ? response.data.models : [];
  const models: ModelEntry[] = [];
  const seen = new Set<string>();
  for (const raw of rawModels) {
    if (!raw || typeof raw !== "object") continue;
    const candidate = raw as Record<string, unknown>;
    const id = typeof candidate.id === "string" ? candidate.id.trim() : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    models.push({
      id,
      label: typeof candidate.label === "string" && candidate.label.trim() ? candidate.label : id,
      enabled: candidate.enabled !== false,
      disabled: candidate.disabled === true,
      disabledReason: typeof candidate.disabledReason === "string"
        ? candidate.disabledReason
        : undefined,
      description: typeof candidate.description === "string" ? candidate.description : undefined,
      tier: typeof candidate.tier === "string" ? candidate.tier : undefined,
      reasoning: candidate.reasoning === true,
      default: candidate.default === true,
      contextLength: typeof candidate.contextLength === "number"
        ? candidate.contextLength
        : undefined,
      maxOutputTokens: typeof candidate.maxOutputTokens === "number"
        ? candidate.maxOutputTokens
        : undefined,
      contextSource: typeof candidate.contextSource === "string"
        ? candidate.contextSource
        : undefined,
    });
  }
  const configuredDefault = typeof response.data?.default === "string"
    ? response.data.default.trim()
    : "";
  const defaultModel = models.some((model) => model.id === configuredDefault)
    ? configuredDefault
    : models.find((model) => model.default)?.id ?? models[0]?.id ?? null;
  return { defaultModel, models };
}

function loadSettings(): SuperChatSettings {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") as Partial<SuperChatSettings>;
    return {
      showToolEvents: raw.showToolEvents ?? false,
      showStructuredSourceWhileStreaming: raw.showStructuredSourceWhileStreaming ?? true,
      // identity-allow: "openclaw" 是改名前的存量取值，读旧写新一律归到 "relay"。
      uploadTarget: raw.uploadTarget === "local" ? "local" : "relay",
    };
  } catch {
    return {
      showToolEvents: false,
      showStructuredSourceWhileStreaming: true,
      uploadTarget: "relay",
    };
  }
}

function resolveChatWsUrl(): string {
  const explicit = import.meta.env.VITE_SUPERCHAT_WS_URL;
  if (explicit) return explicit;

  const url = new URL("/api/v1/chat/ws", window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function normalizeCanvasId(canvasId?: string | null): string {
  return canvasId?.trim() || "default";
}

function scopeForProject(
  project?: string,
  canvasId?: string,
  conversationId = DEFAULT_CHAT_CONVERSATION_ID,
): ChatScope {
  const name = project?.trim();
  if (name) {
    return {
      kind: "project",
      id: name,
      canvas_id: normalizeCanvasId(canvasId),
      ...(conversationId !== DEFAULT_CHAT_CONVERSATION_ID
        ? { conversation_id: conversationId }
        : {}),
    };
  }
  return { kind: "home", id: null };
}

function scopeSessionKey(scope: ChatScope): string {
  if (scope.kind === "project" && scope.id) {
    const canvasId = normalizeCanvasId(scope.canvas_id);
    const conversationId = scope.conversation_id?.trim() || DEFAULT_CHAT_CONVERSATION_ID;
    const canvasKey = canvasId === "default"
      ? `village-canvas:project:${scope.id}:main`
      : `village-canvas:project:${scope.id}:canvas:${encodeURIComponent(canvasId)}`;
    return conversationId === DEFAULT_CHAT_CONVERSATION_ID
      ? canvasKey
      : `${canvasKey}:conversation:${encodeURIComponent(conversationId)}`;
  }
  return "village-canvas:home:main";
}

function messageCacheKey(scopeKey: string): string {
  return `${MESSAGE_CACHE_PREFIX}${scopeKey}`;
}

// `normalizeMessage` stores the whole source message under `raw`. Across a
// load→save round-trip the loaded (already-normalized) object becomes the new
// `raw`, so an un-stripped `raw` nests one level deeper every refresh and the
// cached blob grows without bound — defeating MESSAGE_CACHE_LIMIT (count-only).
// No consumer reads `raw.raw` (hasStructuredContent / extractSpecsFromRaw /
// the debug panel all read raw's top level), so drop the inner `raw` to cap
// nesting at depth 1.
function denestRaw(raw: unknown): unknown {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return raw;
  if (!("raw" in raw)) return raw;
  const { raw: _nested, ...rest } = raw as Record<string, unknown>;
  return rest;
}

// Slim a message down for the refresh-recovery cache: drop the inline
// attachment payload (base64 data URLs etc. — by far the largest field, and
// redundant since url/path/metadata are kept) and the nested `raw` chain.
export function sanitizeMessagesForCache(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((message) => {
    const denestedRaw = denestRaw(message.raw);
    const attachments = message.attachments?.length
      ? message.attachments.map((attachment) => {
          if (attachment.content === undefined) return attachment;
          const { content: _content, ...rest } = attachment;
          return rest;
        })
      : message.attachments;
    if (denestedRaw === message.raw && attachments === message.attachments) {
      return message;
    }
    return { ...message, raw: denestedRaw, attachments };
  });
}

function loadCachedMessages(scopeKey: string): ChatMessage[] {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(messageCacheKey(scopeKey)) || "null",
    ) as unknown;
    // Accept both the legacy bare array and the timestamped wrapper.
    const raw = Array.isArray(parsed)
      ? parsed
      : Array.isArray((parsed as { messages?: unknown })?.messages)
        ? (parsed as { messages: unknown[] }).messages
        : [];
    return filterStaleDirectorClarificationMessages(dedupeChatMessages(
      raw
        .map((message) => normalizeMessage(message))
        .filter((message): message is ChatMessage => Boolean(message)),
    ));
  } catch {
    return [];
  }
}

function saveCachedMessages(
  scopeKey: string,
  messages: ChatMessage[],
  now = Date.now(),
) {
  const payload = {
    updatedAt: now,
    messages: sanitizeMessagesForCache(
      dedupeChatMessages(messages).slice(-MESSAGE_CACHE_LIMIT),
    ),
  };
  safeLocalStorageSet(messageCacheKey(scopeKey), JSON.stringify(payload));
}

// Reclaim message caches for conversations that haven't been touched within the
// TTL (and any legacy/malformed entries). Runs on mount and as a quota
// reclaimer so a backlog of old chats can't wedge other writes.
export function pruneOldMessageCaches(now = Date.now()): void {
  pruneLocalStorageByPrefix(MESSAGE_CACHE_PREFIX, (_key, raw) => {
    let updatedAt: number | null = null;
    try {
      const parsed = JSON.parse(raw) as { updatedAt?: unknown } | null;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        updatedAt = typeof parsed.updatedAt === "number" ? parsed.updatedAt : null;
      }
    } catch {
      updatedAt = null; // malformed
    }
    // Legacy arrays / malformed / no-timestamp → reclaim. Surviving scopes
    // rewrite themselves in the timestamped format on their next save.
    return updatedAt == null || isStaleByTtl(updatedAt, now, MESSAGE_CACHE_TTL_MS);
  });
}

registerStorageReclaimer(() => {
  pruneOldMessageCaches();
});

function activeTurnKey(scopeKey: string): string {
  return `${ACTIVE_TURN_PREFIX}${scopeKey}`;
}

function loadActiveTurn(scopeKey: string): ActiveTurnSnapshot | null {
  try {
    const raw = JSON.parse(localStorage.getItem(activeTurnKey(scopeKey)) || "null") as Partial<ActiveTurnSnapshot> | null;
    if (!raw || typeof raw.turnId !== "string" || typeof raw.startedAt !== "number") return null;
    if (!raw.turnId.trim() || Date.now() - raw.startedAt > ACTIVE_TURN_TTL_MS) {
      localStorage.removeItem(activeTurnKey(scopeKey));
      return null;
    }
    return {
      turnId: raw.turnId,
      startedAt: raw.startedAt,
    };
  } catch {
    return null;
  }
}

function saveActiveTurn(scopeKey: string, turnId: string) {
  if (!turnId.trim()) return;
  safeLocalStorageSet(
    activeTurnKey(scopeKey),
    JSON.stringify({ turnId, startedAt: Date.now() } satisfies ActiveTurnSnapshot),
  );
}

function clearActiveTurn(scopeKey: string, turnId?: string | null) {
  try {
    const current = loadActiveTurn(scopeKey);
    if (turnId && current?.turnId && current.turnId !== turnId) return;
    localStorage.removeItem(activeTurnKey(scopeKey));
  } catch {
    // best-effort cleanup
  }
}

function activeTurnIsPending(messages: ChatMessage[], turnId: string | null | undefined): boolean {
  if (!turnId) return false;
  const hasUserMessage = messages.some(
    (message) => message.role === "user" && message.turnId === turnId,
  );
  if (!hasUserMessage) return false;

  return !messages.some(
    (message) =>
      message.role === "assistant"
      && message.turnId === turnId
      && (message.text.trim().length > 0 || hasStructuredContent(message.raw)),
  );
}

function loadPendingActiveTurn(scopeKey: string, messages: ChatMessage[]): ActiveTurnSnapshot | null {
  const activeTurn = loadActiveTurn(scopeKey);
  if (!activeTurn) return null;
  if (activeTurnIsPending(messages, activeTurn.turnId)) return activeTurn;
  clearActiveTurn(scopeKey, activeTurn.turnId);
  return null;
}

function currentTurnIsLive(
  turnId: string | null | undefined,
  messages: ChatMessage[],
): boolean {
  if (!turnId) return false;
  return activeTurnIsPending(messages, turnId);
}

function workflowFromFrame(value: unknown): AgentWorkflowState | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const workflow = value as Record<string, unknown>;
  if (typeof workflow.schema !== "string" || !Array.isArray(workflow.steps)) return null;
  if (!workflow.steps.every((step) => step && typeof step === "object" && !Array.isArray(step))) return null;
  return workflow as AgentWorkflowState;
}

export type AgentRuntimeCursor = {
  sessionId: string;
  seq: number;
};

function agentRuntimeFromFrame(value: unknown): AgentRuntimeSnapshot | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const runtime = value as Record<string, unknown>;
  if (
    typeof runtime.session_id !== "string"
    || !runtime.session_id.trim()
    || typeof runtime.canvas_id !== "string"
    || !runtime.canvas_id.trim()
    || typeof runtime.seq !== "number"
    || !Number.isSafeInteger(runtime.seq)
    || runtime.seq < 0
    || typeof runtime.status !== "string"
  ) return null;
  return runtime as AgentRuntimeSnapshot;
}

function agentRuntimeEventFromValue(value: unknown): AgentRuntimeEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const event = value as Record<string, unknown>;
  const sessionId = String(event.session_id || "").trim();
  const runId = String(event.run_id || "").trim();
  const turnId = String(event.turn_id || "").trim();
  const eventId = String(event.event_id || "").trim();
  const eventType = String(event.event_type || event.last_event || "").trim();
  const seq = event.seq;
  const createdAt = event.created_at;
  if (
    !sessionId
    || !turnId
    || !eventId
    || !eventType
    || typeof seq !== "number"
    || !Number.isSafeInteger(seq)
    || seq < 1
    || typeof createdAt !== "number"
    || !Number.isFinite(createdAt)
  ) return null;
  return {
    session_id: sessionId,
    ...(runId ? { run_id: runId } : {}),
    turn_id: turnId,
    event_id: eventId,
    seq,
    event_type: eventType,
    status: String(event.status || "running"),
    created_at: createdAt,
    ...(typeof event.canvas_id === "string" ? { canvas_id: event.canvas_id } : {}),
  };
}

function mergeAgentRuntimeEvents(
  current: readonly AgentRuntimeEvent[],
  incoming: readonly AgentRuntimeEvent[],
): AgentRuntimeEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  for (const event of incoming) byId.set(event.event_id, event);
  return [...byId.values()].sort((left, right) => left.seq - right.seq).slice(-32);
}

export function advanceAgentRuntimeCursor(
  current: AgentRuntimeCursor | null,
  runtime: AgentRuntimeSnapshot | null,
): { accepted: boolean; cursor: AgentRuntimeCursor | null } {
  if (!runtime) return { accepted: true, cursor: current };
  const next = { sessionId: runtime.session_id, seq: runtime.seq };
  if (current?.sessionId === next.sessionId && runtime.seq <= current.seq) {
    return { accepted: false, cursor: current };
  }
  return { accepted: true, cursor: next };
}

export function shouldKeepActiveTurnAfterScopeSnapshot(
  serverBusy: boolean,
  turnId: string | null | undefined,
  messages: ChatMessage[],
): boolean {
  // The backend scope snapshot is authoritative after reconnect/refresh.  A
  // stale localStorage active-turn can contain a user-only optimistic message
  // from an old tab; if the server is not busy, keeping it locks the composer
  // even though the Agent is ready.
  return serverBusy && currentTurnIsLive(turnId, messages);
}

export function canvasPatchAfterOptimisticConfirmation(
  patch: CanvasPatchNotification,
  optimisticConfirmed: boolean,
): CanvasPatchNotification {
  if (!optimisticConfirmed) return patch;
  // The preview and the authoritative document can still differ in normalized
  // fields and placement. Keep the UI reconciliation enabled: the three-way
  // merge retains the visible browser placement and persists it once, while a
  // missed preview is repaired from the server without a page refresh.
  return {
    ...patch,
    snapshotRequired: false,
    uiReconcileRequired: patch.serverApplied === true
      ? true
      : patch.uiReconcileRequired,
  };
}

export function recoveryPacketKey(scopeKey: string, recoveryId: string): string {
  return `${scopeKey}:${recoveryId.trim()}`;
}

export function shouldIgnoreRecoveryPacket(
  scopeKey: string,
  packet: Pick<ChatRecoveryPacket, "recovery_id">,
  consumedKeys: ReadonlySet<string>,
  inFlightKey: string | null,
): boolean {
  const recoveryId = String(packet.recovery_id || "").trim();
  if (!recoveryId) return true;
  const key = recoveryPacketKey(scopeKey, recoveryId);
  return consumedKeys.has(key) || inFlightKey === key;
}

export function optimisticCanvasEnvelopeFromPatch(
  patch: CanvasPatchNotification,
): Parameters<typeof prepareOptimisticCanvasEnvelope>[0] | null {
  if (!patch.commandId || !patch.commands?.length) return null;
  const commands = patch.commands.filter(
    (command): command is Record<string, unknown> & { type: string } =>
      typeof command.type === "string" && Boolean(command.type.trim()),
  );
  if (commands.length === 0) return null;
  const envelope = prepareOptimisticCanvasEnvelope({
    schema: "canvas_chat_commands.v1",
    project_id: patch.projectId,
    canvas_id: patch.canvasId,
    command_id: patch.commandId,
    turn_id: patch.turnId,
    run_id: patch.runId,
    commands,
  });
  return isSafeOptimisticCreateEnvelope(envelope) ? envelope : null;
}

export function chatBusyNotice(message: unknown): string {
  const detail = typeof message === "string" ? message.trim() : "";
  return detail || "上一条 Agent 任务仍在执行，这条请求没有启动。请稍后重试。";
}

export function scopeMatches(a: ChatScope | undefined, b: ChatScope): boolean {
  if (!a) return false;
  if (a.kind !== b.kind) return false;
  if (a.kind === "home") return true;
  if ((a.id ?? null) !== (b.id ?? null)) return false;
  if (normalizeCanvasId(a.canvas_id) !== normalizeCanvasId(b.canvas_id)) return false;
  const aConversation = a.conversation_id?.trim() || DEFAULT_CHAT_CONVERSATION_ID;
  const bConversation = b.conversation_id?.trim() || DEFAULT_CHAT_CONVERSATION_ID;
  return aConversation === bConversation;
}

function isChatScope(value: unknown): value is ChatScope {
  if (!value || typeof value !== "object") return false;
  const scope = value as Record<string, unknown>;
  return (
    scope.kind === "home"
    || scope.kind === "project"
  );
}

function mergeHistory(messages: unknown[]): ChatMessage[] {
  return filterStaleDirectorClarificationMessages(dedupeChatMessages(
    messages
      .map((message) => normalizeMessage(message))
      .filter((message): message is ChatMessage => Boolean(message)),
  ));
}

function isDirectorClarificationMessage(message: ChatMessage): boolean {
  const raw = message.raw;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const value = raw as Record<string, unknown>;
    const metadata = value.metadata && typeof value.metadata === "object" && !Array.isArray(value.metadata)
      ? value.metadata as Record<string, unknown>
      : value;
    if (String(metadata.backend || "").trim().toLowerCase() === "director-preflight") return true;
  }
  // Legacy localStorage entries may contain only the rendered assistant text.
  const text = message.text.trim();
  return [
    "这支片主要给谁看、发布在哪里，还是只做内部样片？",
    "你要什么视觉风格和情绪基调？",
    "成片准备使用什么画幅和平台规格？",
    "声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
  ].some((prefix) => text.startsWith(prefix));
}

/** Prevent legacy cached interview steps from flashing before server replay. */
export function filterStaleDirectorClarificationMessages(messages: ChatMessage[]): ChatMessage[] {
  const clarificationIndices = messages
    .map((message, index) => ({ message, index }))
    .filter(({ message }) => message.role === "assistant" && isDirectorClarificationMessage(message))
    .map(({ index }) => index);
  if (clarificationIndices.length === 0) return messages;
  const latestNonClarification = messages.reduce(
    (latest, message, index) => (
      message.role === "assistant" && !isDirectorClarificationMessage(message) ? index : latest
    ),
    -1,
  );
  const keepIndex = latestNonClarification > clarificationIndices[clarificationIndices.length - 1]
    ? -1
    : clarificationIndices[clarificationIndices.length - 1];
  return messages.filter((_, index) => !clarificationIndices.includes(index) || index === keepIndex);
}

function normalizedText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function messageSignature(message: ChatMessage): string {
  return `${message.role}:${normalizedText(message.text)}`;
}

function assistantTextEquivalent(left: string, right: string): boolean {
  const leftText = normalizedText(left);
  const rightText = normalizedText(right);
  if (!leftText || !rightText) return false;
  return leftText === rightText || leftText.startsWith(rightText) || rightText.startsWith(leftText);
}

function hasEquivalentTextMessage(message: ChatMessage, history: ChatMessage[]): boolean {
  if (message.role !== "assistant") {
    const signature = messageSignature(message);
    return history.some((entry) => {
      if (messageSignature(entry) !== signature) return false;
      if (message.turnId && entry.turnId && message.turnId !== entry.turnId) return false;
      if (message.turnId && !entry.turnId && entry.timestamp < message.timestamp) return false;
      return true;
    });
  }
  return history.some(
    (entry) => {
      if (entry.role !== "assistant") return false;
      if (message.turnId && entry.turnId && message.turnId !== entry.turnId) return false;
      if (message.turnId && !entry.turnId && entry.timestamp < message.timestamp) return false;
      return assistantTextEquivalent(message.text, entry.text);
    },
  );
}

function messageSortRank(message: ChatMessage): number {
  if (message.role === "user") return 0;
  if (message.role === "tool") return 1;
  if (message.role === "assistant") return 2;
  return 3;
}

function sortMessages(messages: ChatMessage[]): ChatMessage[] {
  return [...messages].sort((left, right) => {
    if (left.turnId && right.turnId && left.turnId === right.turnId) {
      const rank = messageSortRank(left) - messageSortRank(right);
      if (rank !== 0) return rank;
    }
    return left.timestamp - right.timestamp;
  });
}

function isTransientAssistantMessage(message: ChatMessage): boolean {
  return Boolean(
    message.role === "assistant"
    && message.turnId
    && message.id === `assistant-${message.turnId}`,
  );
}

function preferredAssistantMessage(
  current: ChatMessage,
  candidate: ChatMessage,
): ChatMessage {
  const currentTransient = isTransientAssistantMessage(current);
  const candidateTransient = isTransientAssistantMessage(candidate);
  if (currentTransient !== candidateTransient) {
    return currentTransient ? candidate : current;
  }
  return candidate.timestamp >= current.timestamp ? candidate : current;
}

/**
 * Keep one assistant bubble per turn. The Village Agent sends the persisted message before
 * chat.done; the trailing stream flush must reconcile with it, not append a
 * second transient bubble. The short no-turn fallback only repairs legacy
 * instant duplicates and never merges messages from distinct identified turns.
 */
export function dedupeChatMessages(messages: ChatMessage[]): ChatMessage[] {
  const result: ChatMessage[] = [];
  const indexById = new Map<string, number>();
  const assistantIndexByTurn = new Map<string, number>();

  for (const message of sortMessages(messages)) {
    if (message.role === "assistant" && message.turnId) {
      const turnIndex = assistantIndexByTurn.get(message.turnId);
      if (turnIndex !== undefined) {
        const preferred = preferredAssistantMessage(result[turnIndex], message);
        result[turnIndex] = preferred;
        indexById.set(preferred.id, turnIndex);
        continue;
      }
    }

    const idIndex = indexById.get(message.id);
    if (idIndex !== undefined) {
      result[idIndex] = message.role === "assistant"
        ? preferredAssistantMessage(result[idIndex], message)
        : message;
      continue;
    }

    const previous = result[result.length - 1];
    if (
      message.role === "assistant"
      && !message.turnId
      && previous?.role === "assistant"
      && !previous.turnId
      && normalizedText(previous.text) === normalizedText(message.text)
      && Math.abs(message.timestamp - previous.timestamp) <= 2_000
    ) {
      const previousIndex = result.length - 1;
      const preferred = preferredAssistantMessage(previous, message);
      result[previousIndex] = preferred;
      indexById.set(preferred.id, previousIndex);
      continue;
    }

    const index = result.push(message) - 1;
    indexById.set(message.id, index);
    if (message.role === "assistant" && message.turnId) {
      assistantIndexByTurn.set(message.turnId, index);
    }
  }

  return sortMessages(result);
}

function hasSameTurnMessage(message: ChatMessage, history: ChatMessage[]): boolean {
  if (!message.turnId) return false;
  return history.some((entry) => entry.role === message.role && entry.turnId === message.turnId);
}

function hasEquivalentHistoryMessage(
  message: ChatMessage,
  history: ChatMessage[],
): boolean {
  if (history.some((entry) => entry.id === message.id)) return true;
  if (hasSameTurnMessage(message, history)) return true;
  return hasEquivalentTextMessage(message, history);
}

function hasCompletedTurnInHistory(
  message: ChatMessage,
  history: ChatMessage[],
  current: ChatMessage[],
): boolean {
  if (!message.turnId) return false;
  return turnCompletedInHistory(message.turnId, history, current);
}

function turnCompletedInHistory(
  turnId: string,
  history: ChatMessage[],
  current: ChatMessage[],
): boolean {
  const localUser = current.find(
    (entry) => entry.turnId === turnId && entry.role === "user",
  );
  if (!localUser) return false;

  const backendUser = history.find(
    (entry) =>
      entry.role === "user"
      && normalizedText(entry.text) === normalizedText(localUser.text)
      && entry.timestamp >= localUser.timestamp,
  );
  if (!backendUser) return false;

  return history.some(
    (entry) =>
      entry.role === "assistant"
      && entry.timestamp >= backendUser.timestamp
  );
}

export function mergeHistorySnapshot(
  current: ChatMessage[],
  history: ChatMessage[],
  protectedTurnId: string | null = null,
  preserveTransient = false,
): ChatMessage[] {
  if (current.length === 0) return filterStaleDirectorClarificationMessages(dedupeChatMessages(history));
  if (history.length === 0) return filterStaleDirectorClarificationMessages(dedupeChatMessages(current));
  if (!protectedTurnId && !preserveTransient) {
    return filterStaleDirectorClarificationMessages(dedupeChatMessages(history));
  }

  const preserved = current.filter((message) => {
    const isProtectedTurn = Boolean(protectedTurnId && message.turnId === protectedTurnId);
    if (protectedTurnId && !isProtectedTurn) return false;
    if (message.role === "tool") {
      if (!preserveTransient && !isProtectedTurn) return false;
      return !hasEquivalentHistoryMessage(message, history);
    }
    if (hasCompletedTurnInHistory(message, history, current)) return false;
    return !hasEquivalentHistoryMessage(message, history);
  });

  const protectedLocalUser = protectedTurnId
    ? current.find((entry) => entry.turnId === protectedTurnId && entry.role === "user")
    : null;
  const protectedBackendUser = protectedLocalUser
    ? history.find(
      (entry) =>
        entry.role === "user"
        && normalizedText(entry.text) === normalizedText(protectedLocalUser.text)
        && entry.timestamp >= protectedLocalUser.timestamp,
    )
    : null;
  const protectedBackendAssistant = protectedBackendUser
    ? history.find(
      (entry) =>
        entry.role === "assistant"
        && entry.timestamp >= protectedBackendUser.timestamp,
    )
    : null;
  const protectedToolCount = preserved.filter((message) => message.role === "tool").length;
  let protectedToolIndex = 0;
  const stablePreserved = preserved.map((message) => {
    if (message.role !== "tool" || !protectedBackendUser) return message;
    protectedToolIndex += 1;
    const end = protectedBackendAssistant?.timestamp ?? protectedBackendUser.timestamp + protectedToolCount + 1;
    const gap = Math.max(0.001, end - protectedBackendUser.timestamp);
    return {
      ...message,
      timestamp: protectedBackendUser.timestamp + (gap * protectedToolIndex) / (protectedToolCount + 1),
    };
  });

  return filterStaleDirectorClarificationMessages(dedupeChatMessages([...history, ...stablePreserved]));
}

export function upsertAssistantMessage(
  messages: ChatMessage[],
  turnId: string,
  text: string,
): ChatMessage[] {
  const id = `assistant-${turnId}`;
  const sameTurn = messages.filter(
    (message) => message.role === "assistant" && message.turnId === turnId,
  );
  const canonical = sameTurn.reduce<ChatMessage | null>(
    (current, candidate) => current
      ? preferredAssistantMessage(current, candidate)
      : candidate,
    null,
  );
  const nextMessage: ChatMessage = canonical
    ? {
        ...canonical,
        text,
        turnId,
        timestamp: isTransientAssistantMessage(canonical)
          ? Date.now()
          : canonical.timestamp,
      }
    : {
      id,
      role: "assistant",
      text,
      turnId,
      timestamp: Date.now(),
    };
  return dedupeChatMessages([
    ...messages.filter(
      (message) => !(message.role === "assistant" && message.turnId === turnId),
    ),
    nextMessage,
  ]);
}

function upsertServerAssistantMessage(
  messages: ChatMessage[],
  payload: unknown,
  turnId?: string,
): ChatMessage[] {
  const nextMessage = normalizeMessage(payload, "assistant");
  if (!nextMessage) return messages;
  const normalizedTurnId = nextMessage.turnId ?? (turnId?.trim() || undefined);
  const mergedMessage = normalizedTurnId ? { ...nextMessage, turnId: normalizedTurnId } : nextMessage;
  const existingIndex = messages.findIndex((message) => message.id === mergedMessage.id);
  const withoutTransient = normalizedTurnId
    ? messages.filter(
        (message, index) =>
          index === existingIndex ||
          !(message.role === "assistant" && message.turnId === normalizedTurnId),
      )
    : messages;
  if (existingIndex >= 0) {
    return dedupeChatMessages(
      withoutTransient.map((message) => (message.id === mergedMessage.id ? mergedMessage : message)),
    );
  }
  return dedupeChatMessages([...withoutTransient, mergedMessage]);
}

function resultText(result: unknown): string {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object") return "";
  const value = result as Record<string, unknown>;
  if (typeof value.text === "string") return value.text;
  return JSON.stringify(result, null, 2);
}

function buildToolMessage(kind: string, payload: unknown): ChatMessage {
  const data = payload && typeof payload === "object"
    ? (payload as Record<string, unknown>)
    : {};
  const label =
    typeof data.name === "string"
      ? data.name
      : typeof data.message === "string"
        ? data.message
        : kind;
  const body = "result" in data ? resultText(data.result) : JSON.stringify(payload, null, 2);
  return {
    id: `${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: "tool",
    text: body ? `${label}\n\n${body}` : label,
    turnId: typeof data.turn_id === "string" ? data.turn_id : undefined,
    timestamp: Date.now(),
    raw: payload,
  };
}

function toolEventText(payload: ServerFrame): string {
  if (payload.type !== "tool.result") return "";
  if (typeof payload.result === "string") return payload.result;
  if (payload.result && typeof payload.result === "object") {
    const text = (payload.result as Record<string, unknown>).text;
    return typeof text === "string" ? text : JSON.stringify(payload.result);
  }
  return "";
}

function findCanvasCommandEnvelope(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (
    record.schema === "canvas_chat_commands.v1"
    && record.canvas_command_emitted === true
    && typeof record.project_id === "string"
    && Boolean(record.project_id.trim())
    && typeof record.canvas_id === "string"
    && Boolean(record.canvas_id.trim())
    && typeof record.command_id === "string"
    && Boolean(record.command_id.trim())
    && Array.isArray(record.commands)
  ) return record;
  for (const child of Object.values(record)) {
    const found = findCanvasCommandEnvelope(child);
    if (found) return found;
  }
  return null;
}

function parseCanvasCommandText(text: string): Record<string, unknown> | null {
  if (!text.includes("canvas_chat_commands.v1")) return null;
  // Village Agent tool traces may contain an argument JSON object before the result.
  // The bridge envelope is emitted as its own compact JSON line, so inspect
  // candidate lines from the end instead of slicing from the first `{`.
  for (const line of text.split(/\r?\n/).reverse()) {
    const candidate = line.trim();
    if (!candidate.includes("canvas_chat_commands.v1")) continue;
    try {
      const found = findCanvasCommandEnvelope(JSON.parse(candidate));
      if (found) return found;
    } catch {
      // Continue to the bounded whole-text fallback below.
    }
  }
  const marker = text.lastIndexOf("canvas_chat_commands.v1");
  const start = text.lastIndexOf("{", marker);
  const end = text.indexOf("}", marker);
  if (start < 0 || end <= start) return null;
  for (let cursor = end; cursor < text.length; cursor = text.indexOf("}", cursor + 1)) {
    if (cursor < 0) break;
    try {
      const found = findCanvasCommandEnvelope(JSON.parse(text.slice(start, cursor + 1)));
      if (found) return found;
    } catch {
      // Nested JSON closes before the envelope; keep extending to the next brace.
    }
  }
  return null;
}

function canvasCommandEnvelopeFromToolCall(
  payload: ServerFrame,
  fallbackScope?: ChatScope,
): Parameters<typeof prepareOptimisticCanvasEnvelope>[0] | null {
  if (payload.type !== "tool.call") return null;
  if (
    typeof payload.name !== "string"
    || !CANVAS_COMMAND_TOOL_NAMES.has(payload.name)
  ) return null;
  if (!payload.input || typeof payload.input !== "object" || Array.isArray(payload.input)) {
    return null;
  }
  const input = payload.input as Record<string, unknown>;
  const projectId = (typeof input.project_id === "string" ? input.project_id.trim() : "")
    || (fallbackScope?.kind === "project" ? fallbackScope.id?.trim() || "" : "");
  const canvasId = (typeof input.canvas_id === "string" ? input.canvas_id.trim() : "")
    || (fallbackScope?.kind === "project"
      ? normalizeCanvasId(fallbackScope.canvas_id)
      : "");
  const commandId = typeof input.command_id === "string" ? input.command_id.trim() : "";
  const commands = Array.isArray(input.commands)
    ? input.commands.filter(
        (command): command is Record<string, unknown> & { type: string } =>
          Boolean(command)
          && typeof command === "object"
          && !Array.isArray(command)
          && typeof (command as Record<string, unknown>).type === "string",
      )
    : [];
  if (!projectId || !canvasId || !commandId || commands.length === 0) return null;
  const envelope = prepareOptimisticCanvasEnvelope({
    schema: "canvas_chat_commands.v1",
    project_id: projectId,
    canvas_id: canvasId,
    command_id: commandId,
    turn_id: typeof payload.turn_id === "string" ? payload.turn_id.trim() || undefined : undefined,
    commands,
  });
  return isSafeOptimisticCreateEnvelope(envelope) ? envelope : null;
}

export function canvasCommandEnvelopeFromToolFrame(
  payload: ServerFrame,
  fallbackScope?: ChatScope,
): Record<string, unknown> | null {
  if (payload.type === "tool.call") {
    return canvasCommandEnvelopeFromToolCall(
      payload,
      fallbackScope,
    ) as Record<string, unknown> | null;
  }
  if (
    payload.type !== "tool.result"
    || typeof payload.name !== "string"
    || !CLIENT_EXECUTABLE_CANVAS_TOOL_NAMES.has(payload.name)
  ) return null;
  const direct = findCanvasCommandEnvelope(payload.result);
  if (direct) return direct;
  return parseCanvasCommandText(toolEventText(payload));
}

function emitCanvasCommandFromToolEvent(
  payload: ServerFrame,
  fallbackScope?: ChatScope,
): boolean {
  if (typeof window === "undefined") return false;
  const parsed = canvasCommandEnvelopeFromToolFrame(payload, fallbackScope);
  const turnId = "turn_id" in payload && typeof payload.turn_id === "string"
    ? payload.turn_id.trim()
    : "";
  const envelope = parsed && turnId
    ? { ...parsed, turn_id: parsed.turn_id || turnId }
    : parsed;
  if (!envelope) return false;
  if (envelope.optimistic === true) {
    registerOptimisticCanvasCommand(
      envelope as unknown as Parameters<typeof registerOptimisticCanvasCommand>[0],
    );
  }
  emitCanvasAgentCommandEnvelope(envelope);
  return true;
}

function reconcileFailedOptimisticCanvasCommands(turnId: string | null | undefined): void {
  for (const scope of rollbackOptimisticCanvasCommandsForTurn(turnId)) {
    emitCanvasReconnectNotification(scope.projectId);
  }
}

export function clearConversationLocalState(
  projectId: string,
  canvasId: string,
  conversationId: string,
): void {
  const scopeKey = scopeSessionKey(
    scopeForProject(projectId, canvasId, conversationId),
  );
  clearCanvasReceiptOutbox(scopeKey);
  try {
    for (const key of [
      messageCacheKey(scopeKey),
      activeTurnKey(scopeKey),
      agentModelSelectionKey(scopeKey),
      `superchat:pinned:${scopeKey}`,
      `superchat:deleted:${scopeKey}`,
    ]) {
      localStorage.removeItem(key);
    }
  } catch {
    // 服务端删除已完成时，本地缓存清理保持 best-effort。
  }
}

function settleOptimisticCanvasCommands(turnId: string | null | undefined): void {
  for (const scope of settleOptimisticCanvasCommandsForTurn(turnId)) {
    emitCanvasReconnectNotification(scope.projectId);
  }
}

export function shouldPreserveToolMessage(payload: ServerFrame): boolean {
  const text =
    payload.type === "tool.result" && typeof payload.result === "string"
      ? payload.result
      : payload.type === "tool.result" &&
          payload.result &&
          typeof payload.result === "object" &&
          typeof (payload.result as Record<string, unknown>).text === "string"
        ? String((payload.result as Record<string, unknown>).text)
        : "";
  return (
    (payload.type === "tool.result" || payload.type === "tool.call") &&
    (
      (typeof payload.name === "string" && SERVER_OWNED_CANVAS_TOOL_NAMES.has(payload.name)) ||
      text.includes("canvas_chat_commands.v1") ||
      text.includes("canvas_command_emitted")
    )
  );
}

function upsertToolMessage(messages: ChatMessage[], kind: string, payload: unknown): ChatMessage[] {
  const nextMessage = buildToolMessage(kind, payload);
  if (!nextMessage.turnId) return sortMessages([...messages, nextMessage]);

  const existingIndex = messages.findIndex(
    (message) => message.role === "tool" && message.turnId === nextMessage.turnId,
  );
  if (existingIndex < 0) return sortMessages([...messages, nextMessage]);

  return sortMessages(
    messages.map((message, index) =>
      index === existingIndex
        ? {
          ...message,
          text: nextMessage.text,
          timestamp: nextMessage.timestamp,
          raw: nextMessage.raw,
        }
        : message,
    ),
  );
}

export function useSuperChat({
  project,
  canvasId,
  displayName,
  researchEnabled = false,
}: {
  project?: string;
  canvasId?: string;
  displayName: string;
  researchEnabled?: boolean;
}) {
  const normalizedProjectId = project?.trim() || "";
  const normalizedCanvasId = normalizeCanvasId(canvasId);
  const [activeConversationId, setActiveConversationId] = useState(() =>
    loadActiveConversationId(normalizedProjectId, normalizedCanvasId),
  );
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [conversationDeletingId, setConversationDeletingId] = useState<string | null>(null);
  const activeConversationSelectionRef = useRef(activeConversationId);
  const conversationsRequestRef = useRef(0);
  useEffect(() => {
    conversationsRequestRef.current += 1;
    const stored = loadActiveConversationId(normalizedProjectId, normalizedCanvasId);
    activeConversationSelectionRef.current = stored;
    setConversations([]);
    setConversationsLoading(false);
    setActiveConversationId(stored);
  }, [normalizedCanvasId, normalizedProjectId]);
  const desiredScope = useMemo(
    () => scopeForProject(project, canvasId, activeConversationId),
    [activeConversationId, canvasId, project],
  );
  const scopeKey = useMemo(() => scopeSessionKey(desiredScope), [desiredScope]);
  const initialScopeSnapshot = useMemo(() => {
    const cachedMessages = loadCachedMessages(scopeKey);
    const activeTurn = loadPendingActiveTurn(scopeKey, cachedMessages);
    return {
      cachedMessages,
      activeTurnId: activeTurn?.turnId ?? null,
    };
  }, [scopeKey]);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>(() => initialScopeSnapshot.cachedMessages);
  const [historyReady, setHistoryReady] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [relayInstances, setRelayInstances] = useState<RelayInstanceInfo[]>([]);
  const [selectedInstanceId, setSelectedInstanceId] = useState<string>("");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [modelCatalogs, setModelCatalogs] = useState<Record<AgentEngine, AgentModelCatalog | null>>({ village: null });
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [activeEngine] = useState<AgentEngine>("village");
  const [engines, setEngines] = useState<AgentEngineEntry[]>([VILLAGE_ENGINE_ENTRY]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => new Set());
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const [settings, setSettingsState] = useState<SuperChatSettings>(() => loadSettings());
  const [busy, setBusy] = useState(() => Boolean(initialScopeSnapshot.activeTurnId));
  const [activeTurnEngine, setActiveTurnEngine] = useState<AgentEngine | null>(null);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(initialScopeSnapshot.activeTurnId);
  const [progress, setProgress] = useState<ChatProgressState | null>(null);
  const [agentRuntime, setAgentRuntime] = useState<AgentRuntimeSnapshot | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<AgentRuntimeEvent[]>([]);
  const [villageAgentEvents, setVillageAgentEvents] = useState<VillageAgentEvent[]>([]);
  const [canvasTelemetry, setCanvasTelemetry] = useState<CanvasAgentTelemetry>(() => emptyCanvasAgentTelemetry());
  const [recovery, setRecovery] = useState<ChatRecoveryState | null>(null);
  const refreshConversations = useCallback(async () => {
    const requestId = conversationsRequestRef.current + 1;
    conversationsRequestRef.current = requestId;
    if (!normalizedProjectId) {
      setConversations([]);
      setConversationsLoading(false);
      return [];
    }
    setConversationsLoading(true);
    try {
      const next = await listChatConversations(
        normalizedProjectId,
        normalizedCanvasId,
      );
      if (conversationsRequestRef.current !== requestId) return next;
      setConversations(next);
      const selectedConversationId = activeConversationSelectionRef.current;
      if (!next.some((item) => item.id === selectedConversationId)) {
        saveActiveConversationId(
          normalizedProjectId,
          normalizedCanvasId,
          DEFAULT_CHAT_CONVERSATION_ID,
        );
        activeConversationSelectionRef.current = DEFAULT_CHAT_CONVERSATION_ID;
        setActiveConversationId(DEFAULT_CHAT_CONVERSATION_ID);
      }
      return next;
    } catch {
      return [];
    } finally {
      if (conversationsRequestRef.current === requestId) {
        setConversationsLoading(false);
      }
    }
  }, [normalizedCanvasId, normalizedProjectId]);
  const switchConversation = useCallback((conversationId: string) => {
    const normalized = conversationId.trim() || DEFAULT_CHAT_CONVERSATION_ID;
    if (busy || normalized === activeConversationId) return false;
    saveActiveConversationId(normalizedProjectId, normalizedCanvasId, normalized);
    activeConversationSelectionRef.current = normalized;
    setActiveConversationId(normalized);
    return true;
  }, [activeConversationId, busy, normalizedCanvasId, normalizedProjectId]);
  const startNewConversation = useCallback(async () => {
    if (busy || !normalizedProjectId) return null;
    conversationsRequestRef.current += 1;
    setConversationsLoading(true);
    try {
      const created = await createChatConversation(
        scopeForProject(normalizedProjectId, normalizedCanvasId),
      );
      setConversations((current) => [created, ...current]);
      saveActiveConversationId(normalizedProjectId, normalizedCanvasId, created.id);
      activeConversationSelectionRef.current = created.id;
      setActiveConversationId(created.id);
      return created;
    } catch {
      setError("新对话创建失败，请稍后再试。");
      return null;
    } finally {
      setConversationsLoading(false);
    }
  }, [busy, normalizedCanvasId, normalizedProjectId]);
  const deleteConversation = useCallback(async (conversationId: string) => {
    const normalized = conversationId.trim();
    if (!normalized || !normalizedProjectId || conversationDeletingId) return false;
    if (busy && normalized === activeConversationId) {
      setError("当前会话正在执行，结束后再删除。");
      return false;
    }

    const requestId = conversationsRequestRef.current + 1;
    conversationsRequestRef.current = requestId;
    setConversationDeletingId(normalized);
    try {
      await deleteChatConversation(
        normalizedProjectId,
        normalizedCanvasId,
        normalized,
      );
      clearConversationLocalState(
        normalizedProjectId,
        normalizedCanvasId,
        normalized,
      );

      let next = conversations.filter((item) => item.id !== normalized);
      try {
        next = (await listChatConversations(
          normalizedProjectId,
          normalizedCanvasId,
        )).filter((item, index, items) => (
          items.findIndex((candidate) => candidate.id === item.id) === index
        ));
      } catch {
        // 删除结果已经落库；列表刷新失败时先使用本地可信余量。
      }

      if (normalized === activeConversationId) {
        let fallback = next.find((item) => item.id !== normalized) ?? null;
        if (!fallback) {
          const created = await createChatConversation(
            scopeForProject(normalizedProjectId, normalizedCanvasId),
          );
          fallback = created;
          next = [
            created,
            ...next.filter((item) => (
              item.id !== created.id && item.id !== normalized
            )),
          ];
        }
        const fallbackScopeKey = scopeSessionKey(
          scopeForProject(normalizedProjectId, normalizedCanvasId, fallback.id),
        );
        const fallbackMessages = loadCachedMessages(fallbackScopeKey);
        messagesRef.current = fallbackMessages;
        setMessages(fallbackMessages);
        saveActiveConversationId(
          normalizedProjectId,
          normalizedCanvasId,
          fallback.id,
        );
        activeConversationSelectionRef.current = fallback.id;
        setActiveConversationId(fallback.id);
      }
      setConversations(next);
      setError(null);
      return true;
    } catch (caught) {
      setError(
        caught instanceof Error && caught.message.trim()
          ? caught.message
          : "历史会话删除失败，请稍后再试。",
      );
      return false;
    } finally {
      setConversationDeletingId(null);
      if (conversationsRequestRef.current === requestId) {
        setConversationsLoading(false);
      }
    }
  }, [
    activeConversationId,
    busy,
    conversationDeletingId,
    conversations,
    normalizedCanvasId,
    normalizedProjectId,
  ]);
  const chatReloadBlockedRef = useRef(Boolean(initialScopeSnapshot.activeTurnId));
  const streamTextRef = useRef("");
  const messagesRef = useRef<ChatMessage[]>(initialScopeSnapshot.cachedMessages);
  const activeTurnIdRef = useRef<string | null>(initialScopeSnapshot.activeTurnId);
  const pendingClientTurnIdRef = useRef<string | null>(null);
  const recentlyCompletedTurnIdRef = useRef<string | null>(null);
  const cancelledTurnIdsRef = useRef<Set<string>>(new Set());
  const backendThreadIdRef = useRef<string | null>(null);
  const backendTurnIdRef = useRef<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const handleFrameRef = useRef<(frame: ServerFrame) => void>(() => undefined);
  const connectRef = useRef<() => void>(() => undefined);
  const reconnectRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const closedRef = useRef(false);
  const authRejectedRef = useRef(false);
  const connectionIdRef = useRef(0);
  const reconcileCanvasAfterReconnectRef = useRef(false);
  const agentRuntimeCursorRef = useRef<AgentRuntimeCursor | null>(null);
  const canvasReceiptQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const seenCanvasReceiptIdsRef = useRef<Set<string>>(new Set());
  const seenWorkflowDispatchesRef = useRef<Set<string>>(new Set());
  const scheduledCanvasReceiptIdsRef = useRef<Set<string>>(new Set());
  const canvasReceiptRetryTimerRef = useRef<number | null>(null);
  const canvasReceiptRetryAtRef = useRef<number | null>(null);
  const flushCanvasReceiptOutboxRef = useRef<() => void>(() => undefined);
  const assistantDeltaFlushTimerRef = useRef<number | null>(null);
  const consumedRecoveryKeysRef = useRef<Set<string>>(new Set());
  const recoveryInFlightRef = useRef<{ key: string; turnId: string } | null>(null);
  const agentModelCatalogRef = useRef<Record<AgentEngine, AgentModelCatalog | null>>({
    village: null,
  });
  const agentModelRequestIdRef = useRef(0);
  const agentModelScopeKeyRef = useRef(scopeKey);
  agentModelScopeKeyRef.current = scopeKey;
  chatReloadBlockedRef.current =
    busy
    || connecting
    || Boolean(activeTurnIdRef.current ?? pendingClientTurnIdRef.current);

  useEffect(
    () => registerAppUpdateReloadBlocker(() => chatReloadBlockedRef.current),
    [],
  );

  const sendFrame = useCallback((frame: ClientFrame) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame));
    }
  }, []);

  const flushCanvasReceiptOutbox = useCallback(() => {
    if (desiredScope.kind !== "project") return;
    const scheduleRetryAt = (targetAt: number) => {
      if (
        canvasReceiptRetryTimerRef.current !== null
        && canvasReceiptRetryAtRef.current !== null
        && canvasReceiptRetryAtRef.current <= targetAt
      ) return;
      if (canvasReceiptRetryTimerRef.current !== null) {
        window.clearTimeout(canvasReceiptRetryTimerRef.current);
      }
      canvasReceiptRetryAtRef.current = targetAt;
      canvasReceiptRetryTimerRef.current = window.setTimeout(() => {
        canvasReceiptRetryTimerRef.current = null;
        canvasReceiptRetryAtRef.current = null;
        flushCanvasReceiptOutboxRef.current();
      }, Math.max(0, targetAt - Date.now()));
    };
    const now = Date.now();
    for (const delivery of loadCanvasReceiptOutbox(scopeKey, now)) {
      const receiptId = delivery.receipt.receiptId;
      if ((delivery.failedAttempts ?? 0) >= CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS) continue;
      if (delivery.nextAttemptAt !== undefined && delivery.nextAttemptAt > now) {
        scheduleRetryAt(delivery.nextAttemptAt);
        continue;
      }
      if (scheduledCanvasReceiptIdsRef.current.has(receiptId)) continue;
      scheduledCanvasReceiptIdsRef.current.add(receiptId);
      const payload = {
        scope: delivery.scope,
        turn_id: delivery.turnId,
        event: {
          type: "agent.canvas.command.receipt",
          ...delivery.receipt,
          turn_id: delivery.turnId,
        },
      };
      canvasReceiptQueueRef.current = canvasReceiptQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          try {
            await api.post("api/v1/chat/ui-events", { json: payload }).json();
            acknowledgeCanvasReceiptDelivery(scopeKey, receiptId);
          } catch {
            const deferred = deferCanvasReceiptDelivery(scopeKey, receiptId);
            if (deferred?.nextAttemptAt !== undefined) scheduleRetryAt(deferred.nextAttemptAt);
          }
        })
        .finally(() => {
          scheduledCanvasReceiptIdsRef.current.delete(receiptId);
        });
    }
  }, [desiredScope, scopeKey]);
  flushCanvasReceiptOutboxRef.current = flushCanvasReceiptOutbox;

  useEffect(() => {
    const handleOnline = () => {
      resetCanvasReceiptDeliveryRetries(scopeKey);
      flushCanvasReceiptOutbox();
    };
    window.addEventListener("online", handleOnline);
    return () => window.removeEventListener("online", handleOnline);
  }, [flushCanvasReceiptOutbox, scopeKey]);

  useEffect(() => {
    const handleCanvasCommandReceipt = (event: Event) => {
      const receipt = (event as CustomEvent<CanvasCommandReceipt>).detail;
      if (
        !receipt
        || desiredScope.kind !== "project"
        || desiredScope.id !== receipt.projectId
        || (desiredScope.canvas_id || "default") !== receipt.canvasId
      ) return;
      const turnId = receipt.turnId
        || activeTurnIdRef.current
        || pendingClientTurnIdRef.current;
      if (!turnId) return;
      if (seenCanvasReceiptIdsRef.current.has(receipt.receiptId)) {
        flushCanvasReceiptOutbox();
        return;
      }
      seenCanvasReceiptIdsRef.current.add(receipt.receiptId);
      const routedReceipt = receipt.turnId ? receipt : { ...receipt, turnId };
      enqueueCanvasReceiptDelivery(scopeKey, {
        receipt: routedReceipt,
        scope: desiredScope,
        turnId,
        queuedAt: Date.now(),
      });
      flushCanvasReceiptOutbox();
      setCanvasTelemetry((current) => recordCanvasReceipt(
        current,
        routedReceipt,
      ));
    };
    window.addEventListener(CANVAS_COMMAND_RECEIPT_EVENT, handleCanvasCommandReceipt);
    return () => {
      window.removeEventListener(CANVAS_COMMAND_RECEIPT_EVENT, handleCanvasCommandReceipt);
    };
  }, [desiredScope, flushCanvasReceiptOutbox, scopeKey]);

  const applyAgentModelCatalog = useCallback((catalog: AgentModelCatalog, engine: AgentEngine) => {
    setModelCatalogs((current) => ({ ...current, [engine]: catalog }));
    if (engine !== activeEngine) return;
    const stored = loadAgentModelSelection(scopeKey);
    const displayModels = modelsWithPersistedSelection(catalog.models, stored);
    setModels(displayModels);
    // A persisted selection is authoritative, including when it is now stale
    // or disabled.  Only a genuinely empty selection may choose a live default.
    const selectedId = stored
      ?? catalog.models.find((model) => model.id === catalog.defaultModel && isUsableAgentModel(model))?.id
      ?? catalog.models.find(isUsableAgentModel)?.id
      ?? null;
    setActiveModel(selectedId);
  }, [activeEngine, scopeKey]);

  const loadAgentModelCatalog = useCallback(async (force = false, engine = activeEngine) => {
    const cached = agentModelCatalogRef.current[engine];
    if (cached && !force) {
      applyAgentModelCatalog(cached, engine);
      return;
    }
    const requestId = agentModelRequestIdRef.current + 1;
    agentModelRequestIdRef.current = requestId;
    setModelsLoading(true);
    try {
      const response = await api
        .get(`api/v1/chat/models?engine=${encodeURIComponent(engine)}`)
        .json<ChatModelsResponse>();
      if (
        agentModelRequestIdRef.current !== requestId
        || agentModelScopeKeyRef.current !== scopeKey
      ) return;
      const catalog = normalizeAgentModelCatalog(response);
      agentModelCatalogRef.current[engine] = catalog;
      applyAgentModelCatalog(catalog, engine);
    } catch {
      if (
        agentModelRequestIdRef.current !== requestId
        || agentModelScopeKeyRef.current !== scopeKey
      ) return;
      // Do not turn a background catalog refresh into a chat failure banner.
      // The active conversation remains fully usable with its existing model.
      if (!cached) {
        const stored = loadAgentModelSelection(scopeKey);
        setModels(modelsWithPersistedSelection([], stored));
        setActiveModel(stored);
      }
    } finally {
      if (
        agentModelRequestIdRef.current === requestId
        && agentModelScopeKeyRef.current === scopeKey
      ) setModelsLoading(false);
    }
  }, [activeEngine, applyAgentModelCatalog]);

  const loadAgentEngines = useCallback(async () => {
    try {
      const response = await api.get("api/v1/chat/engines").json<ChatEnginesResponse>();
      const raw = Array.isArray(response.data?.engines) ? response.data.engines : [];
      const next = raw.flatMap((item): AgentEngineEntry[] => {
        if (!item || typeof item !== "object") return [];
        const candidate = item as Record<string, unknown>;
        if (candidate.id !== "village") return [];
        return [{
          id: candidate.id,
          label: typeof candidate.label === "string" ? candidate.label : candidate.id,
          description: typeof candidate.description === "string" ? candidate.description : undefined,
          available: candidate.available === true,
          reason: typeof candidate.reason === "string" ? candidate.reason : undefined,
          automatic: candidate.automatic === true,
        }];
      });
      if (next.length > 0) {
        setEngines(next);
      }
    } catch {
      setEngines([VILLAGE_ENGINE_ENTRY]);
    }
  }, []);

  useEffect(() => {
    const handleDirectModelRegistryChanged = (event: Event) => {
      const kind = (event as CustomEvent<{ kind?: string }>).detail?.kind;
      if (kind !== "agent") return;
      const engine: AgentEngine = "village";
      agentModelCatalogRef.current[engine] = null;
      if (engine === activeEngine) void loadAgentModelCatalog(true, engine);
    };
    window.addEventListener(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, handleDirectModelRegistryChanged);
    return () => {
      window.removeEventListener(DIRECT_MODEL_REGISTRY_CHANGED_EVENT, handleDirectModelRegistryChanged);
    };
  }, [activeEngine, loadAgentModelCatalog, scopeKey]);

  const requestHistory = useCallback(() => {
        sendFrame({
          type: "scope.set",
          scope: desiredScope,
          since_seq: agentRuntimeCursorRef.current?.seq ?? 0,
        });
  }, [desiredScope, sendFrame]);

  const markTurnActive = useCallback((turnId: string | null) => {
    if (!turnId) return;
    activeTurnIdRef.current = turnId;
    setActiveTurnId(turnId);
    recentlyCompletedTurnIdRef.current = null;
    saveActiveTurn(scopeKey, turnId);
    setBusy(true);
  }, [scopeKey]);

  const markTurnInactive = useCallback((turnId?: string | null) => {
    clearActiveTurn(scopeKey, turnId);
    streamTextRef.current = "";
    activeTurnIdRef.current = null;
    setActiveTurnId(null);
    pendingClientTurnIdRef.current = null;
    recentlyCompletedTurnIdRef.current = turnId ?? null;
    setStreamText("");
    setBusy(false);
    setActiveTurnEngine(null);
    // Keep the final server-owned workflow receipt visible as an evidence trail
    // after chat.done; the next turn's first progress frame replaces it.
    setProgress((current) => current?.workflow ? current : null);
  }, [scopeKey]);

  const resumeRecoveryPacket = useCallback((
    packet: ChatRecoveryPacket,
    autoRetrying: boolean,
  ): boolean => {
    const recoveryId = String(packet.recovery_id || "").trim();
    const ws = wsRef.current;
    if (!recoveryId || ws?.readyState !== WebSocket.OPEN) return false;
    const recoveryKey = recoveryPacketKey(scopeKey, recoveryId);
    if (
      consumedRecoveryKeysRef.current.has(recoveryKey)
      || recoveryInFlightRef.current?.key === recoveryKey
      || Boolean(activeTurnIdRef.current ?? pendingClientTurnIdRef.current)
    ) {
      return false;
    }
    const turnId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}-recovery`;
    // A recovery id is single-use on the server. Claim it before changing React
    // state so an auto-resume frame, a double click, and a duplicate frame can
    // never open competing turns in the same browser tick.
    consumedRecoveryKeysRef.current.add(recoveryKey);
    recoveryInFlightRef.current = { key: recoveryKey, turnId };
    pendingClientTurnIdRef.current = turnId;
    markTurnActive(turnId);
    setError(null);
    setRecovery((current) => current
      ? { ...current, autoRetrying }
      : { message: "正在恢复上一次执行…", packet, autoRetrying });
    sendFrame({ type: "chat.resume", recovery_id: recoveryId, turn_id: turnId });
    return true;
  }, [markTurnActive, scopeKey, sendFrame]);

  const setSettings = useCallback((patch: Partial<SuperChatSettings>) => {
    setSettingsState((current) => {
      const next = { ...current, ...patch };
      safeLocalStorageSet(SETTINGS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const flushAssistantDelta = useCallback(() => {
    assistantDeltaFlushTimerRef.current = null;
    const displayText = streamTextRef.current;
    const turnId = pendingClientTurnIdRef.current
      ?? activeTurnIdRef.current;
    if (!turnId || !displayText.trim()) return;
    setMessages((current) => upsertAssistantMessage(current, turnId, displayText));
  }, []);

  const scheduleAssistantDeltaFlush = useCallback(() => {
    if (assistantDeltaFlushTimerRef.current !== null) return;
    assistantDeltaFlushTimerRef.current = window.setTimeout(flushAssistantDelta, 32);
  }, [flushAssistantDelta]);

  const discardTransientAssistantTurn = useCallback((turnId: string | null | undefined) => {
    if (assistantDeltaFlushTimerRef.current !== null) {
      window.clearTimeout(assistantDeltaFlushTimerRef.current);
      assistantDeltaFlushTimerRef.current = null;
    }
    streamTextRef.current = "";
    setStreamText("");
    const normalizedTurnId = turnId?.trim();
    if (!normalizedTurnId) return;
    setMessages((current) => current.filter(
      (message) => !isTransientAssistantMessage(message) || message.turnId !== normalizedTurnId,
    ));
  }, []);

  const finalizeStream = useCallback(() => {
    if (assistantDeltaFlushTimerRef.current !== null) {
      window.clearTimeout(assistantDeltaFlushTimerRef.current);
      assistantDeltaFlushTimerRef.current = null;
    }
    flushAssistantDelta();
    const turnId = activeTurnIdRef.current ?? `turn-${Date.now()}`;
    if (cancelledTurnIdsRef.current.has(turnId)) {
      markTurnInactive(turnId);
      return;
    }
    setMessages((current) => {
      if (!streamTextRef.current.trim()) return current;
      return upsertAssistantMessage(current, turnId, streamTextRef.current);
    });
    markTurnInactive(turnId);
    // Post-done history refresh is intentionally disabled; final assistant
    // messages are now pushed through assistant.message.
  }, [flushAssistantDelta, markTurnInactive]);

  const handleFrame = useCallback((frame: ServerFrame) => {
    const unifiedEvent = villageAgentEventFromChatFrame(frame);
    if (unifiedEvent && villageAgentEventMatchesScope(unifiedEvent, desiredScope)) {
      emitVillageAgentEvent(unifiedEvent);
    }
    const runtime = agentRuntimeFromFrame(frame.agent_runtime);
    if (frame.type !== "scope.changed") {
      const advanced = advanceAgentRuntimeCursor(agentRuntimeCursorRef.current, runtime);
      if (!advanced.accepted) return;
      agentRuntimeCursorRef.current = advanced.cursor;
      if (runtime) setAgentRuntime(runtime);
    }
    const runtimeEvent = frame.type === "scope.changed"
      ? null
      : agentRuntimeEventFromValue(frame.agent_runtime);
    if (runtimeEvent) {
      setRuntimeEvents((current) => mergeAgentRuntimeEvents(current, [runtimeEvent]));
    }
    switch (frame.type) {
      case "scope.changed": {
        reconnectAttemptRef.current = 0;
        setConnected(true);
        setConnecting(false);
        setError(null);
        setProgress(null);
        const frameScope = isChatScope(frame.scope) ? frame.scope : undefined;
        if (!scopeMatches(frameScope, desiredScope)) break;
        setApprovals(approvalRequestsFromValue(frame.approvals, desiredScope));
        agentRuntimeCursorRef.current = runtime
          ? { sessionId: runtime.session_id, seq: runtime.seq }
          : null;
        setAgentRuntime(runtime);
        const rawRuntimeEvents = (frame as { agent_runtime_events?: unknown }).agent_runtime_events;
        const replayedRuntimeEvents = (Array.isArray(rawRuntimeEvents) ? rawRuntimeEvents : [])
          .map((event) => agentRuntimeEventFromValue(event))
          .filter((event): event is AgentRuntimeEvent => Boolean(event));
        setRuntimeEvents(mergeAgentRuntimeEvents([], replayedRuntimeEvents));
        const replayedVillageAgentEvents = (Array.isArray(frame.agent_events)
          ? frame.agent_events
          : [])
          .map((value) => {
            if (!value || typeof value !== "object" || Array.isArray(value)) return null;
            return normalizeVillageAgentEvent((value as Record<string, unknown>).agent_event);
          })
          .filter((event): event is VillageAgentEvent => Boolean(event))
          .filter((event) => villageAgentEventMatchesScope(event, desiredScope));
        setVillageAgentEvents(mergeVillageAgentEvents([], replayedVillageAgentEvents));
        const durableRecoveries = (Array.isArray(frame.recoveries) ? frame.recoveries : [])
          .map((item) => {
            if (!item || typeof item !== "object" || Array.isArray(item)) return null;
            const recovery = (item as { recovery?: unknown }).recovery;
            if (!recovery || typeof recovery !== "object" || Array.isArray(recovery)) return null;
            const packet = recovery as ChatRecoveryPacket;
            if (typeof packet.recovery_id !== "string" || !packet.recovery_id.trim()) return null;
            return {
              message: typeof (item as { message?: unknown }).message === "string"
                ? (item as { message: string }).message
                : "本轮执行已保存恢复点，可以继续。",
              packet,
            };
          })
          .filter((item): item is { message: string; packet: ChatRecoveryPacket } => Boolean(item));
        if (
          durableRecoveries.length > 0
          && !activeTurnIdRef.current
          && !pendingClientTurnIdRef.current
          && !recoveryInFlightRef.current
        ) {
          const pending = durableRecoveries[0];
          setRecovery({ message: pending.message, packet: pending.packet, autoRetrying: false });
        }
        seenCanvasReceiptIdsRef.current.clear();
        seenWorkflowDispatchesRef.current.clear();
        resetCanvasReceiptDeliveryRetries(scopeKey);
        flushCanvasReceiptOutbox();
        if (
          reconcileCanvasAfterReconnectRef.current
          && frameScope?.kind === "project"
          && typeof frameScope.id === "string"
          && frameScope.id.trim()
        ) {
          reconcileCanvasAfterReconnectRef.current = false;
          emitCanvasReconnectNotification(frameScope.id);
        }
        setHistoryReady(true);
        const history = mergeHistory(Array.isArray(frame.history) ? frame.history : []);
        const currentMessages = messagesRef.current;
        const runtimeActive = runtime?.active === true || runtime?.status === "running";
        const runtimeTurnId = typeof runtime?.turn_id === "string" && runtime.turn_id.trim()
          ? runtime.turn_id
          : null;
        const protectedTurnId = runtimeTurnId
          ?? activeTurnIdRef.current
          ?? recentlyCompletedTurnIdRef.current;
        setMessages((current) => {
          const preserveRemoteBusy = runtimeActive
            || (runtime == null && frame.busy === true && currentTurnIsLive(protectedTurnId, current));
          return mergeHistorySnapshot(current, history, protectedTurnId, preserveRemoteBusy);
        });
        const activeTurnId = activeTurnIdRef.current;
        const serverBusy = runtime ? runtimeActive : frame.busy === true;
        if (serverBusy && runtimeTurnId) {
          markTurnActive(runtimeTurnId);
          setProgress({
            turnId: runtimeTurnId,
            stage: runtime?.last_event || "agent.reconnecting",
            message: "小树已恢复当前画布任务…",
            workflow: null,
          });
        } else if (serverBusy && runtime) {
          setBusy(true);
        } else if (shouldKeepActiveTurnAfterScopeSnapshot(serverBusy, activeTurnId, currentMessages)) {
          setBusy(true);
        } else if (activeTurnId) {
          if (!serverBusy) {
            markTurnInactive(activeTurnId);
          } else if (turnCompletedInHistory(activeTurnId, history, currentMessages)) {
            markTurnInactive(activeTurnId);
          } else if (!currentTurnIsLive(activeTurnId, currentMessages)) {
            markTurnInactive(activeTurnId);
          } else {
            setBusy(true);
          }
        } else if (!activeTurnIdRef.current) {
          streamTextRef.current = "";
          recentlyCompletedTurnIdRef.current = null;
          setStreamText("");
          setBusy(false);
        }
        break;
      }
      case "chat.busy": {
        const rejectedTurnId = typeof frame.turn_id === "string" && frame.turn_id.trim()
          ? frame.turn_id
          : activeTurnIdRef.current ?? pendingClientTurnIdRef.current;
        setError(chatBusyNotice(frame.message));
        setProgress(null);
        markTurnInactive(rejectedTurnId);
        break;
      }
      case "chat.ping": {
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        const turnId =
          activeTurnIdRef.current
          ?? pendingClientTurnIdRef.current
          ?? (typeof frame.turn_id === "string" && frame.turn_id.trim() ? frame.turn_id : null);
        if (turnId) {
          markTurnActive(turnId);
        } else {
          setBusy(true);
        }
        setProgress((current) => ({
          turnId: turnId ?? undefined,
          stage: typeof frame.stage === "string" ? frame.stage : current?.stage ?? "agent.working",
            message: current?.message ?? "小树仍在处理当前任务…",
          toolName: typeof frame.tool_name === "string" ? frame.tool_name : current?.toolName,
          elapsedSeconds: current?.elapsedSeconds,
          lastProgressAgeSeconds: typeof frame.last_progress_age_seconds === "number"
            ? frame.last_progress_age_seconds
            : current?.lastProgressAgeSeconds,
          lastEvent: typeof frame.last_event === "string" ? frame.last_event : current?.lastEvent,
          workerAlive: typeof frame.worker_alive === "boolean" ? frame.worker_alive : current?.workerAlive,
          heartbeat: true,
          workflow: workflowFromFrame(frame.workflow) ?? current?.workflow ?? null,
        }));
        break;
      }
      case "chat.progress": {
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        const turnId =
          activeTurnIdRef.current
          ?? pendingClientTurnIdRef.current
          ?? (typeof frame.turn_id === "string" && frame.turn_id.trim() ? frame.turn_id : null);
        if (turnId) markTurnActive(turnId);
        setProgress({
          turnId: turnId ?? undefined,
          stage: typeof frame.stage === "string" ? frame.stage : "agent.working",
           message: typeof frame.message === "string"
             ? frame.message
              : "小树正在处理…",
          toolName: typeof frame.tool_name === "string" ? frame.tool_name : null,
          elapsedSeconds: typeof frame.elapsed_seconds === "number" ? frame.elapsed_seconds : null,
          lastProgressAgeSeconds: typeof frame.last_progress_age_seconds === "number"
            ? frame.last_progress_age_seconds
            : null,
          lastEvent: typeof frame.last_event === "string" ? frame.last_event : null,
          workerAlive: typeof frame.worker_alive === "boolean" ? frame.worker_alive : null,
          heartbeat: frame.heartbeat === true,
          workflow: workflowFromFrame(frame.workflow),
        });
        break;
      }
      case "thread.started":
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        activeTurnIdRef.current = pendingClientTurnIdRef.current
          ?? (typeof frame.turn_id === "string" && frame.turn_id.trim() ? frame.turn_id : activeTurnIdRef.current);
        if (activeTurnIdRef.current) {
          markTurnActive(activeTurnIdRef.current);
        }
        recentlyCompletedTurnIdRef.current = null;
        backendThreadIdRef.current = typeof frame.thread_id === "string" ? frame.thread_id : null;
        backendTurnIdRef.current = typeof frame.turn_id === "string" ? frame.turn_id : null;
        setRecovery(null);
        break;
      case "assistant.delta": {
        const next = typeof frame.text === "string" ? frame.text : "";
        if (!next) break;
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        setBusy(true);
        setRecovery(null);
        streamTextRef.current = frame.accumulated === false
          ? `${streamTextRef.current}${next}`
          : next;
        const turnId =
          pendingClientTurnIdRef.current
          ?? activeTurnIdRef.current
          ?? (typeof frame.turn_id === "string" && frame.turn_id.trim() ? frame.turn_id : null);
        if (turnId && streamTextRef.current.trim()) {
          markTurnActive(turnId);
          scheduleAssistantDeltaFlush();
        }
        setStreamText("");
        break;
      }
      case "assistant.message":
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        setMessages((current) =>
          upsertServerAssistantMessage(
            current,
            frame.message,
            typeof frame.turn_id === "string" ? frame.turn_id : undefined,
          ),
        );
        // The persisted message is the server's post-verification result. A
        // streamed model draft may have claimed completion before a WorkflowRun
        // or canvas receipt settled; never let chat.done flush that stale text
        // back over the authoritative final bubble.
        streamTextRef.current = "";
        setStreamText("");
        break;
      case "tool.call":
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        if (settings.showToolEvents || shouldPreserveToolMessage(frame)) {
          setMessages((current) => upsertToolMessage(current, frame.type, frame));
        }
        emitCanvasCommandFromToolEvent(frame, desiredScope);
        break;
      case "fe_tool.call":
        dispatchFeToolCall(frame, desiredScope);
        break;
      case "tool.result":
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          break;
        }
        if (typeof frame.turn_id === "string" && frame.turn_id.trim()) {
          markTurnActive(frame.turn_id);
        } else {
          setBusy(true);
        }
        const emittedCanvasPreview = emitCanvasCommandFromToolEvent(
          frame,
          desiredScope,
        );
        if (
          !emittedCanvasPreview
          && frame.success !== false
          && typeof frame.name === "string"
          && SERVER_OWNED_CANVAS_TOOL_NAMES.has(frame.name)
          && desiredScope.kind === "project"
          && desiredScope.id
        ) {
          // Canonical server tools persist before returning. A scoped pull is
          // the fail-closed path for destructive commands, generated command
          // ids, or a canvas.patch frame lost while the canvas remounts.
          emitCanvasReconnectNotification(desiredScope.id);
        }
        if (
          frame.success !== false
          && frame.name === "village_canvas_dispatch_action"
          && desiredScope.kind === "project"
          && desiredScope.id
        ) {
          const dispatchKey = [
            desiredScope.id,
            normalizeCanvasId(desiredScope.canvas_id),
            typeof frame.turn_id === "string" ? frame.turn_id : "",
          ].join("\u0000");
          if (!seenWorkflowDispatchesRef.current.has(dispatchKey)) {
            seenWorkflowDispatchesRef.current.add(dispatchKey);
            const observedAt = Date.now();
            void listWorkflowRuns(
              desiredScope.id,
              normalizeCanvasId(desiredScope.canvas_id),
            ).then((runs) => {
              const run = recentlyCreatedWorkflowRun(runs, observedAt);
              if (run) emitWorkflowRun(run);
            }).catch(() => undefined);
          }
        }
        if (settings.showToolEvents || shouldPreserveToolMessage(frame)) {
          setMessages((current) => upsertToolMessage(current, frame.type, frame));
        }
        break;
      case "canvas.patch": {
        const patch = canvasPatchNotificationFromFrame(frame);
        if (
          patch
          && desiredScope.kind === "project"
          && desiredScope.id === patch.projectId
          && normalizeCanvasId(desiredScope.canvas_id) === normalizeCanvasId(patch.canvasId)
        ) {
          const patchPreview = optimisticCanvasEnvelopeFromPatch(patch);
          if (
            patchPreview
            && patch.serverApplied !== false
            && !optimisticCanvasGraphPresent(patchPreview)
          ) {
            // A tool.call can arrive while the canvas listener is remounting.
            // Replaying the deterministic create envelope from canvas.patch
            // makes the node visible immediately; the authoritative pull below
            // then merges and persists its live viewport placement.
            registerOptimisticCanvasCommand(patchPreview);
            emitCanvasAgentCommandEnvelope(patchPreview);
          }
          const optimisticConfirmed = confirmOptimisticCanvasCommand({
            projectId: patch.projectId,
            canvasId: patch.canvasId,
            commandId: patch.commandId,
            revision: patch.revision,
          });
          const effectivePatch = canvasPatchAfterOptimisticConfirmation(
            patch,
            optimisticConfirmed,
          );
          // canvas.patch is an authoritative invalidation notice. Even when a
          // compatibility producer says `server_applied=false`, the positive
          // server revision proves this frame already belongs to the snapshot
          // stream. Dispatching its commands locally creates a second node
          // that autosave persists under the same command_id.
          emitCanvasPatchNotification(effectivePatch);
          setCanvasTelemetry((current) => recordCanvasPatch(current, effectivePatch));
        }
        break;
      }
      case "workflow.run": {
        const run = workflowRunFromEnvelope({ run: frame.run });
        if (
          run
          && desiredScope.kind === "project"
          && desiredScope.id === run.project_id
          && normalizeCanvasId(desiredScope.canvas_id) === normalizeCanvasId(run.canvas_id)
        ) {
          emitWorkflowRun(run);
        }
        break;
      }
      case "approval.requested": {
        const approval = approvalRequestFromValue(frame.approval);
        if (approval && approvalRequestMatchesScope(approval, desiredScope)) {
          setApprovals((current) => upsertApprovalRequest(current, approval));
        }
        break;
      }
      case "approval.resolved": {
        const approvalId = typeof frame.approval_id === "string"
          ? frame.approval_id.trim()
          : "";
        if (
          approvalId
          && approvalScopeMatches(frame.projectId, frame.canvasId, desiredScope)
        ) {
          setApprovals((current) => current.filter((item) => item.id !== approvalId));
        }
        break;
      }
      case "task.started":
        setCanvasTelemetry((current) => recordBackgroundTask(
          current,
          typeof frame.turn_id === "string" ? frame.turn_id : undefined,
        ));
        break;
      case "chat.done":
        if (
          recoveryInFlightRef.current
          && (
            typeof frame.turn_id !== "string"
            || frame.turn_id === recoveryInFlightRef.current.turnId
          )
        ) {
          recoveryInFlightRef.current = null;
        }
        if (
          typeof frame.turn_id === "string"
          && cancelledTurnIdsRef.current.has(frame.turn_id)
        ) {
          reconcileFailedOptimisticCanvasCommands(frame.turn_id);
          cancelledTurnIdsRef.current.delete(frame.turn_id);
          markTurnInactive(frame.turn_id);
          break;
        }
        settleOptimisticCanvasCommands(
          typeof frame.turn_id === "string"
            ? frame.turn_id
            : activeTurnIdRef.current ?? pendingClientTurnIdRef.current,
        );
        finalizeStream();
        setRecovery(null);
        break;
      case "chat.recoverable": {
        const failedTurnId = typeof frame.turn_id === "string"
          ? frame.turn_id
          : activeTurnIdRef.current ?? pendingClientTurnIdRef.current;
        // Closing the Village Agent stream is how the backend interrupts an active turn. The
        // worker therefore reports a recoverable loss while shutting down;
        // never auto-resume a turn the user explicitly stopped.
        if (failedTurnId && cancelledTurnIdsRef.current.has(failedTurnId)) {
          reconcileFailedOptimisticCanvasCommands(failedTurnId);
          cancelledTurnIdsRef.current.delete(failedTurnId);
          markTurnInactive(failedTurnId);
          setProgress(null);
          setRecovery(null);
          break;
        }
        reconcileFailedOptimisticCanvasCommands(failedTurnId);
        // The failed attempt never persisted an assistant.message. Remove its
        // optimistic bubble before starting recovery, otherwise the recovery
        // turn gets rendered beside the stale first attempt.
        discardTransientAssistantTurn(failedTurnId);
        const packet = frame.recovery as ChatRecoveryPacket | undefined;
        if (!packet || typeof packet.recovery_id !== "string" || !packet.recovery_id.trim()) {
          setError(typeof frame.message === "string" ? frame.message : "Agent 恢复点格式无效");
          setProgress(null);
          markTurnInactive(typeof frame.turn_id === "string" ? frame.turn_id : null);
          break;
        }
        if (
          shouldIgnoreRecoveryPacket(
            scopeKey,
            packet,
            consumedRecoveryKeysRef.current,
            recoveryInFlightRef.current?.key ?? null,
          )
        ) {
          // A duplicate recoverable frame is a transport replay, not a new
          // failure. Keep the already-running recovery turn untouched.
          break;
        }
        setProgress(null);
        if (
          recoveryInFlightRef.current
          && (
            typeof frame.turn_id !== "string"
            || frame.turn_id === recoveryInFlightRef.current.turnId
          )
        ) {
          recoveryInFlightRef.current = null;
        }
        markTurnInactive(typeof frame.turn_id === "string" ? frame.turn_id : null);
        setError(null);
        const nextRecovery: ChatRecoveryState = {
          message: typeof frame.message === "string"
            ? frame.message
            : packet.retry_reason === "worker_lost"
              ? "模型连接中断，输入已保留；请稍后重新发送。"
              : "本轮执行已保存恢复点，可以继续。",
          packet,
          autoRetrying: false,
        };
        setRecovery(nextRecovery);
        if (packet.auto_retry_allowed === true) {
          resumeRecoveryPacket(packet, true);
        }
        break;
      }
      case "project.created":
        setMessages((current) => [...current, buildToolMessage(frame.type, frame)]);
        break;
      case "error":
        if (
          recoveryInFlightRef.current
          && (
            typeof frame.turn_id !== "string"
            || frame.turn_id === recoveryInFlightRef.current.turnId
          )
        ) {
          recoveryInFlightRef.current = null;
        }
        reconcileFailedOptimisticCanvasCommands(
          typeof frame.turn_id === "string"
            ? frame.turn_id
            : activeTurnIdRef.current ?? pendingClientTurnIdRef.current,
        );
        setError(typeof frame.message === "string" ? frame.message : "Unknown chat error");
        // A recovery id is single-use on the server. Any ordinary error after a
        // resume must retire the banner instead of leaving a clickable stale id.
        setRecovery(null);
        if (typeof frame.message === "string" && frame.message.includes("当前用户已有 AI 对话正在处理中")) {
          setBusy(true);
          break;
        }
        if (frame.message === "unauthorized") {
          authRejectedRef.current = true;
          closedRef.current = true;
          wsRef.current?.close();
        }
        setProgress(null);
        markTurnInactive(activeTurnIdRef.current ?? pendingClientTurnIdRef.current);
        setConnecting(false);
        break;
      default:
        break;
    }
  }, [
    desiredScope,
    discardTransientAssistantTurn,
    finalizeStream,
    flushCanvasReceiptOutbox,
    markTurnActive,
    markTurnInactive,
    resumeRecoveryPacket,
    scheduleAssistantDeltaFlush,
    scopeKey,
    settings.showToolEvents,
  ]);
  // Socket ownership must not follow UI callback identity. Settings, runtime
  // events and message rendering can update this ref without tearing down the
  // live transport or interrupting an active Agent turn.
  handleFrameRef.current = handleFrame;

  useEffect(() => {
    const handleVillageAgentEvent = (raw: Event) => {
      const event = (raw as CustomEvent<VillageAgentEvent>).detail;
      if (!event || !villageAgentEventMatchesScope(event, desiredScope)) return;
      setVillageAgentEvents((current) => mergeVillageAgentEvents(current, [event]));
    };
    window.addEventListener(VILLAGE_AGENT_EVENT, handleVillageAgentEvent);
    return () => window.removeEventListener(VILLAGE_AGENT_EVENT, handleVillageAgentEvent);
  }, [desiredScope]);

  useEffect(() => {
    const handleFeToolLifecycle = (raw: Event) => {
      const event = (raw as CustomEvent<FeToolLifecycle>).detail;
      if (!event || !feToolLifecycleMatchesScope(event, desiredScope)) return;
      const attempts = event.phase === "result" ? 3 : 1;
      void (async () => {
        for (let attempt = 1; attempt <= attempts; attempt += 1) {
          try {
            await api.post("api/v1/chat/fe-tool-events", {
              json: {
                scope: event.scope,
                turn_id: event.turnId,
                call_id: event.callId,
                event_id: event.eventId,
                name: event.name,
                phase: event.phase,
                message: event.message,
                result: event.result,
                error: event.error,
              },
              timeout: 1_500,
            }).json();
            return;
          } catch {
            if (attempt >= attempts) return;
            await new Promise((resolve) => window.setTimeout(resolve, attempt * 120));
          }
        }
      })();
    };
    window.addEventListener(FE_TOOL_LIFECYCLE_EVENT, handleFeToolLifecycle);
    return () => window.removeEventListener(FE_TOOL_LIFECYCLE_EVENT, handleFeToolLifecycle);
  }, [desiredScope]);

  useEffect(() => () => {
    if (assistantDeltaFlushTimerRef.current !== null) {
      window.clearTimeout(assistantDeltaFlushTimerRef.current);
      assistantDeltaFlushTimerRef.current = null;
    }
    if (canvasReceiptRetryTimerRef.current !== null) {
      window.clearTimeout(canvasReceiptRetryTimerRef.current);
      canvasReceiptRetryTimerRef.current = null;
      canvasReceiptRetryAtRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    closedRef.current = false;
    authRejectedRef.current = false;
    const connectionId = connectionIdRef.current + 1;
    connectionIdRef.current = connectionId;
    setConnecting(true);
    setError(null);
    if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
    const previous = wsRef.current;
    if (previous) {
      previous.onopen = null;
      previous.onmessage = null;
      previous.onerror = null;
      previous.onclose = null;
      previous.close();
    }

    const ws = new WebSocket(resolveChatWsUrl());
    wsRef.current = ws;
    ws.onopen = () => {
      if (connectionIdRef.current !== connectionId || wsRef.current !== ws) return;
      sendFrame({
        type: "scope.set",
        scope: desiredScope,
        since_seq: agentRuntimeCursorRef.current?.seq ?? 0,
      });
    };
    ws.onmessage = (event) => {
      if (connectionIdRef.current !== connectionId || wsRef.current !== ws) return;
      try {
        handleFrameRef.current(JSON.parse(String(event.data)) as ServerFrame);
      } catch {
        // Ignore malformed frames from development proxies.
      }
    };
    ws.onerror = () => {
      if (connectionIdRef.current !== connectionId || wsRef.current !== ws) return;
      setError("WebSocket connection failed");
      setConnecting(false);
    };
    ws.onclose = (event) => {
      if (connectionIdRef.current !== connectionId || wsRef.current !== ws) return;
      wsRef.current = null;
      setConnected(false);
      const hasActiveTurn = Boolean(activeTurnIdRef.current ?? pendingClientTurnIdRef.current);
      setConnecting(hasActiveTurn);
      if (hasActiveTurn) {
        setBusy(true);
      }
      if (
        !closedRef.current
        && !authRejectedRef.current
        && event.code !== 1008
      ) {
        if (desiredScope.kind === "project" && desiredScope.id) {
          reconcileCanvasAfterReconnectRef.current = true;
        }
        setConnecting(true);
        const attempt = Math.min(reconnectAttemptRef.current, 4);
        reconnectAttemptRef.current += 1;
        const reconnectDelay = Math.min(12_000, 750 * 2 ** attempt)
          + Math.floor(Math.random() * 250);
        reconnectRef.current = window.setTimeout(
          () => connectRef.current(),
          reconnectDelay,
        );
      }
    };
  }, [desiredScope, sendFrame]);
  connectRef.current = connect;

  const disconnect = useCallback(() => {
    closedRef.current = true;
    connectionIdRef.current += 1;
    if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
    reconnectAttemptRef.current = 0;
    const ws = wsRef.current;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
      wsRef.current = null;
    }
    setConnected(false);
    setConnecting(false);
  }, []);

  useEffect(() => {
    setRelayInstances([]);
    setSelectedInstanceId("");
    setActiveModel(null);
    setHistoryReady(false);
    agentRuntimeCursorRef.current = null;
    streamTextRef.current = "";
    pendingClientTurnIdRef.current = null;
    recentlyCompletedTurnIdRef.current = null;
    backendThreadIdRef.current = null;
    backendTurnIdRef.current = null;
    setStreamText("");
    setProgress(null);
    setAgentRuntime(null);
    setRuntimeEvents([]);
    setVillageAgentEvents([]);
    setCanvasTelemetry(emptyCanvasAgentTelemetry());
    seenCanvasReceiptIdsRef.current.clear();
    seenWorkflowDispatchesRef.current.clear();
    consumedRecoveryKeysRef.current.clear();
    recoveryInFlightRef.current = null;
    setRecovery(null);
    const cachedMessages = loadCachedMessages(scopeKey);
    setMessages(cachedMessages);
    messagesRef.current = cachedMessages;
    const activeTurn = loadPendingActiveTurn(scopeKey, cachedMessages);
    activeTurnIdRef.current = activeTurn?.turnId ?? null;
    setActiveTurnId(activeTurn?.turnId ?? null);
    setBusy(Boolean(activeTurn));
  }, [desiredScope, scopeKey]);

  // Sweep stale/legacy message caches once on mount so abandoned conversations
  // don't accumulate and eventually exhaust the localStorage quota.
  useEffect(() => {
    pruneOldMessageCaches();
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    messagesRef.current = messages;
    saveCachedMessages(scopeKey, messages);
  }, [messages, scopeKey]);

  useEffect(() => {
    const activeTurnId = activeTurnIdRef.current;
    if (!activeTurnId || busy || activeTurnIsPending(messages, activeTurnId)) return;
    clearActiveTurn(scopeKey, activeTurnId);
    activeTurnIdRef.current = null;
    setActiveTurnId(null);
    pendingClientTurnIdRef.current = null;
    setBusy(false);
  }, [busy, messages, scopeKey]);

  useEffect(() => {
    try {
      const pinned = JSON.parse(localStorage.getItem(`superchat:pinned:${scopeKey}`) || "[]");
      const deleted = JSON.parse(localStorage.getItem(`superchat:deleted:${scopeKey}`) || "[]");
      setPinnedIds(new Set(Array.isArray(pinned) ? pinned : []));
      setDeletedIds(new Set(Array.isArray(deleted) ? deleted : []));
    } catch {
      setPinnedIds(new Set());
      setDeletedIds(new Set());
    }
  }, [scopeKey]);

  useEffect(() => {
    const connectTimer = window.setTimeout(connect, 50);
    return () => {
      window.clearTimeout(connectTimer);
      disconnect();
    };
  }, [connect, disconnect]);

  const send = useCallback((
    text: string,
    attachments: ChatAttachment[] = [],
    transportText?: string,
    _options: ChatSendOptions = {},
  ) => {
    const trimmed = text.trim();
    if (!trimmed || !connected) return false;
    const outboundEngine: AgentEngine = "village";
    if (engines.find((entry) => entry.id === outboundEngine)?.available === false) return false;
    const outboundModel = activeModel;
    const liveCatalog = modelCatalogs[outboundEngine];
    const liveModel = liveCatalog?.models.find((model) => model.id === outboundModel);
    // A completely unbound legacy/new session may still rely on the server's
    // configured default while the catalog request is in flight (or when an
    // older test/runtime does not expose the optional catalog endpoint).  Once
    // a model id is present, however, the catalog is authoritative and a
    // missing/stale entry must block rather than silently switch models.
    const unboundCatalogPending = !outboundModel && !liveCatalog;
    if (!unboundCatalogPending && (!outboundModel || !liveCatalog || !isUsableAgentModel(liveModel))) {
      setError(
        liveModel?.disabledReason?.trim()
          || (outboundModel
            ? `当前 Agent 模型已失效：${outboundModel}，请重新选择模型`
            : "请先选择一个可用的 Agent 模型"),
      );
      return false;
    }
    const outboundText = transportText?.trim() || trimmed;
    const turnId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setRecovery(null);
    setProgress(null);
    setRuntimeEvents([]);
    setVillageAgentEvents([]);
    setCanvasTelemetry(emptyCanvasAgentTelemetry(turnId));
    seenCanvasReceiptIdsRef.current.clear();
    seenWorkflowDispatchesRef.current.clear();
    pendingClientTurnIdRef.current = turnId;
    markTurnActive(turnId);
    setProgress({
      turnId,
      stage: "agent.starting",
      message: "小树正在连接当前模型…",
      toolName: null,
      elapsedSeconds: 0,
      lastProgressAgeSeconds: 0,
      lastEvent: null,
      workerAlive: null,
      heartbeat: false,
      workflow: null,
    });
    setActiveTurnEngine(outboundEngine);
    setMessages((current) => [...current, buildLocalUserMessage(trimmed, turnId, displayName, attachments)]);
    streamTextRef.current = "";
    setStreamText("");
    sendFrame({
      type: "chat.message",
      scope: desiredScope,
      text: outboundText,
      turn_id: turnId,
      agent_engine: outboundEngine,
      model: outboundModel ?? undefined,
      attachments: attachments.length > 0 ? attachments : undefined,
      research_enabled: researchEnabled || undefined,
    });
    return true;
  }, [activeModel, connected, desiredScope, displayName, engines, markTurnActive, modelCatalogs, researchEnabled, sendFrame]);

  const steer = useCallback(async (text: string): Promise<boolean> => {
    const guidance = text.trim();
    const turnId = activeTurnIdRef.current ?? pendingClientTurnIdRef.current;
    if (!guidance || !connected || !turnId) return false;
    try {
      const response = await api
        .post("api/v1/chat/steer", {
          json: {
            scope: desiredScope,
            turn_id: turnId,
            text: guidance,
          },
        })
        .json<ChatSteerResponse>();
      if (!response.data?.accepted) return false;
      const message = normalizeMessage(response.data.message, "user");
      if (message) {
        setMessages((current) => dedupeChatMessages([...current, message]));
      }
      return true;
    } catch (error) {
      console.error("[superchat] steer active turn failed", error);
      return false;
    }
  }, [connected, desiredScope]);

  const resumeRecovery = useCallback(() => {
    if (!recovery) return false;
    return resumeRecoveryPacket(recovery.packet, false);
  }, [recovery, resumeRecoveryPacket]);

  const appendNotification = useCallback(async (text: string): Promise<boolean> => {
    const trimmed = text.trim();
    if (!trimmed) return false;
    try {
      const response = await api
        .post("api/v1/chat/notifications", {
          json: {
            scope: desiredScope,
            text: trimmed,
          },
        })
        .json<ChatNotificationResponse>();
      const message = normalizeMessage(response.data, "assistant");
      if (message) {
        setMessages((current) => sortMessages([...current, message]));
      }
      return true;
    } catch (error) {
      console.error("[superchat] append notification failed", error);
      const fallback = normalizeMessage(
        {
          id: `task-notification-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          role: "assistant",
          content: trimmed,
          created_at: new Date().toISOString(),
        },
        "assistant",
      );
      if (fallback) {
        setMessages((current) => sortMessages([...current, fallback]));
      }
      return false;
    }
  }, [desiredScope]);

  const appendLocalExchange = useCallback((userText: string, assistantText: string): void => {
    const turnId = `fast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const userMessage = buildLocalUserMessage(userText.trim(), turnId, displayName);
    const assistantMessage = normalizeMessage({
      id: `assistant-${turnId}`,
      role: "assistant",
      text: assistantText.trim(),
      turn_id: turnId,
      created_at: new Date().toISOString(),
    }, "assistant");
    setMessages((current) => assistantMessage
      ? sortMessages([...current, userMessage, assistantMessage])
      : sortMessages([...current, userMessage]));
    void api.post("api/v1/chat/local-exchanges", {
      json: {
        scope: desiredScope,
        turn_id: turnId,
        user_text: userText.trim(),
        assistant_text: assistantText.trim(),
      },
    }).catch(() => undefined);
  }, [desiredScope, displayName]);

  const abort = useCallback(() => {
    const turnId = activeTurnIdRef.current ?? pendingClientTurnIdRef.current;
    if (turnId) {
      cancelledTurnIdsRef.current.add(turnId);
    }
    markTurnInactive(turnId);
    const cancelParams = new URLSearchParams({ engine: activeEngine });
    if (desiredScope.id) cancelParams.set("project", desiredScope.id);
    if (desiredScope.canvas_id) cancelParams.set("canvas_id", desiredScope.canvas_id);
    if (backendThreadIdRef.current) cancelParams.set("thread_id", backendThreadIdRef.current);
    if (backendTurnIdRef.current) cancelParams.set("turn_id", backendTurnIdRef.current);
    void api
      .post(`api/v1/chat/cancel?${cancelParams.toString()}`)
      .json<{
        ok?: boolean;
        data?: { cancelled?: boolean; runtime_cancelled?: number };
      }>()
      .then((response) => {
        const accepted = response.data?.cancelled === true
          || Number(response.data?.runtime_cancelled ?? 0) > 0;
        if (accepted) return;
        if (turnId) cancelledTurnIdsRef.current.delete(turnId);
        setError("当前 Agent 任务已经结束，或停止信号没有命中正在执行的回合。");
        requestHistory();
      })
      .catch(() => {
        if (turnId) cancelledTurnIdsRef.current.delete(turnId);
        setError("停止请求未送达，已重新同步当前 Agent 状态。");
        requestHistory();
      });
  }, [activeEngine, desiredScope.canvas_id, desiredScope.id, markTurnInactive, requestHistory]);

  const resolveApproval = useCallback((approval: ApprovalRequest, decision: "allow-once" | "allow-always" | "deny") => {
    void submitApprovalDecision(approval.id, decision)
      .then(() => {
        setApprovals((current) => current.filter((item) => item.id !== approval.id));
      })
      .catch((approvalError) => {
        setError(approvalError instanceof Error ? approvalError.message : String(approvalError));
      });
  }, []);

  const refreshRelayInstances = useCallback(() => {
    setRelayInstances([]);
  }, []);

  const selectRelayInstance = useCallback((_instanceId: string) => {
    setSelectedInstanceId("");
  }, []);

  const refreshModels = useCallback(() => {
    void loadAgentModelCatalog(true, activeEngine);
  }, [activeEngine, loadAgentModelCatalog]);

  const switchModel = useCallback((modelId: string) => {
    if (busy || modelsLoading) return;
    const selected = models.find((model) => model.id === modelId);
    if (!selected || !isUsableAgentModel(selected)) return;
    setActiveModel(selected.id);
    safeLocalStorageSet(agentModelSelectionKey(scopeKey), selected.id);
  }, [activeEngine, busy, models, modelsLoading, scopeKey]);

  useEffect(() => {
    void loadAgentModelCatalog(false, activeEngine);
  }, [activeEngine, loadAgentModelCatalog]);

  useEffect(() => {
    void loadAgentEngines();
  }, [loadAgentEngines]);

  const activeAgentModel = useMemo(
    () => modelCatalogs.village?.models.find((model) => model.id === activeModel) ?? null,
    [activeModel, modelCatalogs],
  );
  // Before the first catalog response, an empty selection means "use the
  // server-configured default" rather than a stale explicit binding.  Keep
  // the composer usable during that short window; a loaded empty/disabled
  // catalog still blocks sends below.
  const activeModelReady =
    (!activeModel && modelCatalogs.village === null)
    || isUsableAgentModel(activeAgentModel);
  const activeModelBlockedReason = activeModelReady
    ? null
    : activeModel
      ? `当前 Agent 模型已失效：${activeModel}，请重新选择模型`
      : "请先选择一个可用的 Agent 模型";

  const sessionControl = useCallback((_command: SessionControlCommand, _args?: string) => {
    // novelvideo's native chat endpoint does not expose external session-control commands.
  }, []);

  const persistMessageSet = useCallback((kind: "pinned" | "deleted", next: Set<string>) => {
    safeLocalStorageSet(`superchat:${kind}:${scopeKey}`, JSON.stringify([...next]));
  }, [scopeKey]);

  const togglePin = useCallback((id: string) => {
    setPinnedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      persistMessageSet("pinned", next);
      return next;
    });
  }, [persistMessageSet]);

  const deleteMessage = useCallback((id: string) => {
    setDeletedIds((current) => {
      const next = new Set(current);
      next.add(id);
      persistMessageSet("deleted", next);
      return next;
    });
    setPinnedIds((current) => {
      if (!current.has(id)) return current;
      const next = new Set(current);
      next.delete(id);
      persistMessageSet("pinned", next);
      return next;
    });
  }, [persistMessageSet]);

  const clearPinned = useCallback(() => {
    const next = new Set<string>();
    setPinnedIds(next);
    persistMessageSet("pinned", next);
  }, [persistMessageSet]);

  return {
    abort,
    approvals,
    activeEngine,
    activeTurnEngine,
    activeTurnId,
    busy,
    connected,
    connecting,
    error,
    engines,
    activeModel,
    activeModelReady,
    activeModelBlockedReason,
    appendLocalExchange,
    appendNotification,
    clearPinned,
    deleteMessage,
    deletedIds,
    historyReady,
    messages,
    models,
    modelCatalogs,
    modelsLoading,
    requestHistory,
    progress,
    agentRuntime,
    runtimeEvents,
    villageAgentEvents,
    canvasTelemetry,
    recovery,
    activeConversationId,
    conversations,
    conversationsLoading,
    conversationDeletingId,
    deleteConversation,
    refreshConversations,
    startNewConversation,
    switchConversation,
    resumeRecovery,
    refreshModels,
    refreshRelayInstances,
    relayInstances,
    resolveApproval,
    selectRelayInstance,
    send,
    steer,
    selectedInstanceId,
    sessionControl,
    setSettings,
    settings,
    pinnedIds,
    streamText,
    switchModel,
    togglePin,
  };
}
