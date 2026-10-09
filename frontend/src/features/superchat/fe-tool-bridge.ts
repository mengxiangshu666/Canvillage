// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { canvasEventBus } from "@/features/canvas/application/canvasServices";
import { NODE_TOOL_TYPES, type NodeToolType } from "@/features/canvas/domain/canvasNodes";
import type { ChatScope, ServerFrame } from "@/features/superchat/types";
import { useCanvasStore } from "@/stores/canvasStore";
import { downloadUrlAsFile } from "@/lib/browserDownload";

export const FE_TOOL_LIFECYCLE_EVENT = "village-canvas:fe-tool-lifecycle";
export const FE_TOOL_BRIDGE_VERSION = "village_fe_tool_bridge.v1";

export type FeToolPhase = "ack" | "progress" | "result";

export interface FeToolCall {
  bridge: typeof FE_TOOL_BRIDGE_VERSION;
  callId: string;
  eventId: string;
  name: string;
  input: Record<string, unknown>;
  turnId: string | null;
  scope: ChatScope;
  timeoutMs: number;
}

export interface FeToolLifecycle {
  bridge: typeof FE_TOOL_BRIDGE_VERSION;
  phase: FeToolPhase;
  callId: string;
  eventId: string;
  name: string;
  turnId: string | null;
  scope: ChatScope;
  message?: string;
  result?: unknown;
  error?: string;
}

export interface FeToolContext {
  call: FeToolCall;
  reportProgress: (message: string, result?: unknown) => void;
}

export type FeToolHandler = (
  input: Record<string, unknown>,
  context: FeToolContext,
) => unknown | Promise<unknown>;

export interface FeToolRegistration {
  name: string;
  description: string;
  timeoutMs?: number;
  handler: FeToolHandler;
}

const registrations = new Map<string, FeToolRegistration>();
const activeCalls = new Set<string>();
const completedCalls = new Map<string, number>();
const COMPLETED_CALL_LIMIT = 256;
const DEFAULT_TIMEOUT_MS = 8_000;
const MAX_TIMEOUT_MS = 20_000;

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function normalizedCanvasId(value: unknown): string {
  return nonEmptyString(value) || "default";
}

function callIdFromFrame(frame: ServerFrame): string | null {
  if (frame.type !== "fe_tool.call") return null;
  return nonEmptyString(frame.call_id);
}

function emitLifecycle(event: FeToolLifecycle): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<FeToolLifecycle>(FE_TOOL_LIFECYCLE_EVENT, {
    detail: event,
  }));
}

function pruneCompletedCalls(): void {
  const now = Date.now();
  for (const [key, createdAt] of completedCalls) {
    if (now - createdAt > 10 * 60 * 1000) completedCalls.delete(key);
  }
  while (completedCalls.size > COMPLETED_CALL_LIMIT) {
    const oldest = completedCalls.keys().next().value as string | undefined;
    if (!oldest) break;
    completedCalls.delete(oldest);
  }
}

function inputRecord(value: unknown): Record<string, unknown> {
  return record(value) ?? {};
}

function scopeMatches(left: ChatScope, right: ChatScope): boolean {
  return left.kind === right.kind
    && String(left.id ?? "") === String(right.id ?? "")
    && normalizedCanvasId(left.canvas_id) === normalizedCanvasId(right.canvas_id);
}

function nodeIdInput(input: Record<string, unknown>): string | null {
  return nonEmptyString(input.node_id)
    ?? nonEmptyString(input.nodeId);
}

function requireCanvasNode(input: Record<string, unknown>): string {
  const nodeId = nodeIdInput(input);
  if (!nodeId) throw new Error("node_id is required");
  if (!useCanvasStore.getState().nodes.some((node) => node.id === nodeId)) {
    throw new Error(`canvas node not found: ${nodeId}`);
  }
  return nodeId;
}

export function registerFeTool(registration: FeToolRegistration): () => void {
  const name = registration.name.trim();
  if (!name || !registration.description.trim()) {
    throw new Error("FE tool name and description are required");
  }
  registrations.set(name, { ...registration, name });
  return () => {
    if (registrations.get(name)?.handler === registration.handler) registrations.delete(name);
  };
}

export function listRegisteredFeTools(): FeToolRegistration[] {
  return [...registrations.values()].map((item) => ({ ...item }));
}

export function dispatchFeToolCall(
  frame: ServerFrame,
  scope: ChatScope,
): boolean {
  if (
    frame.type !== "fe_tool.call"
    || frame.schema !== FE_TOOL_BRIDGE_VERSION
    || scope.kind !== "project"
    || nonEmptyString(frame.project_id) !== nonEmptyString(scope.id)
    || normalizedCanvasId(frame.canvas_id) !== normalizedCanvasId(scope.canvas_id)
  ) {
    return false;
  }
  const name = nonEmptyString(frame.name);
  const callId = callIdFromFrame(frame);
  if (!name || !callId) return false;
  const registration = registrations.get(name);
  if (!registration || activeCalls.has(callId) || completedCalls.has(callId)) return false;

  const call: FeToolCall = {
    bridge: FE_TOOL_BRIDGE_VERSION,
    callId,
    eventId: nonEmptyString(frame.agent_event && record(frame.agent_event)?.event_id)
      ?? `legacy:${callId}`,
    name,
    input: inputRecord(frame.input),
    turnId: nonEmptyString(frame.turn_id),
    scope,
    timeoutMs: Math.min(
      MAX_TIMEOUT_MS,
      Math.max(
        250,
        typeof frame.timeout_ms === "number" && Number.isFinite(frame.timeout_ms)
          ? frame.timeout_ms
          : registration.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      ),
    ),
  };
  activeCalls.add(callId);
  emitLifecycle({
    bridge: FE_TOOL_BRIDGE_VERSION,
    phase: "ack",
    callId,
    eventId: `${call.eventId}:ack`,
    name,
    turnId: call.turnId,
    scope,
    message: "前端 UI 工具已接收",
  });

  const reportProgress = (message: string, result?: unknown) => {
    if (!activeCalls.has(callId)) return;
    emitLifecycle({
      bridge: FE_TOOL_BRIDGE_VERSION,
      phase: "progress",
      callId,
      eventId: `${call.eventId}:progress:${Date.now()}`,
      name,
      turnId: call.turnId,
      scope,
      message: message.trim() || "前端 UI 工具执行中",
      ...(result === undefined ? {} : { result }),
    });
  };

  void (async () => {
    try {
      reportProgress("正在执行前端 UI 操作");
      let timeoutId: number | null = null;
      const result = await Promise.race([
        Promise.resolve(registration.handler(call.input, { call, reportProgress })),
        new Promise<never>((_, reject) => {
          timeoutId = window.setTimeout(
            () => reject(new Error("FE tool timeout")),
            call.timeoutMs,
          );
        }),
      ]).finally(() => {
        if (timeoutId !== null) window.clearTimeout(timeoutId);
      });
      activeCalls.delete(callId);
      completedCalls.set(callId, Date.now());
      pruneCompletedCalls();
      emitLifecycle({
        bridge: FE_TOOL_BRIDGE_VERSION,
        phase: "result",
        callId,
        eventId: `${call.eventId}:result`,
        name,
        turnId: call.turnId,
        scope,
        message: "前端 UI 操作已完成",
        result,
      });
    } catch (error) {
      activeCalls.delete(callId);
      completedCalls.set(callId, Date.now());
      pruneCompletedCalls();
      emitLifecycle({
        bridge: FE_TOOL_BRIDGE_VERSION,
        phase: "result",
        callId,
        eventId: `${call.eventId}:result`,
        name,
        turnId: call.turnId,
        scope,
        message: "前端 UI 操作失败",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  })();
  return true;
}

export function registerBuiltInCanvasFeTools(): () => void {
  const unregister: Array<() => void> = [];
  unregister.push(registerFeTool({
    name: "village.ui.select_node",
    description: "选中当前画布中的一个节点，不修改画布结构。",
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      useCanvasStore.getState().setSelectedNode(nodeId);
      return { node_id: nodeId, selected: true };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.focus_node",
    description: "选中并平滑聚焦到当前画布中的一个节点，不修改画布结构。",
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      const store = useCanvasStore.getState();
      store.setSelectedNode(nodeId);
      store.requestFocusNode(nodeId);
      return { node_id: nodeId, focused: true };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.fit_view",
    description: "调整当前画布视口以显示全部节点，不修改节点和连线。",
    handler: (input) => {
      const duration = typeof input.duration_ms === "number" ? input.duration_ms : 220;
      const maxZoom = typeof input.max_zoom === "number" ? input.max_zoom : undefined;
      canvasEventBus.publish("canvas/ui-fit-view", { duration, maxZoom });
      return { fitted: true, duration_ms: duration, ...(maxZoom === undefined ? {} : { max_zoom: maxZoom }) };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.open_tool_dialog",
    description: "打开选中节点的真实画布工具对话框，不执行工具。",
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      const rawToolType = nonEmptyString(input.tool_type) as NodeToolType | null;
      if (!rawToolType || !Object.values(NODE_TOOL_TYPES).includes(rawToolType)) {
        throw new Error("unsupported node tool type");
      }
      canvasEventBus.publish("tool-dialog/open", { nodeId, toolType: rawToolType });
      return { node_id: nodeId, tool_type: rawToolType, opened: true };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.close_tool_dialog",
    description: "关闭当前画布工具对话框。",
    handler: () => {
      canvasEventBus.publish("tool-dialog/close", undefined);
      return { closed: true };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.video_capture_frame",
    description: "从指定视频节点截取首帧、尾帧或当前帧，并等待图片节点真实创建回执。",
    timeoutMs: 20_000,
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      const mode = nonEmptyString(input.mode);
      if (mode !== "first" && mode !== "last" && mode !== "current") {
        throw new Error("mode must be first, last, or current");
      }
      return new Promise((resolve) => {
        let settled = false;
        const settle = (receipt: unknown) => {
          if (settled) return;
          settled = true;
          resolve(receipt);
        };
        canvasEventBus.publish("video-node/capture-frame", {
          nodeId,
          mode,
          onComplete: settle,
        });
        window.setTimeout(() => settle({
          ok: false,
          nodeId,
          mode,
          error: "video node did not return a capture receipt",
        }), 19_500);
      });
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.video_set_operation",
    description: "在指定视频节点打开剪辑或去字幕真实操作面板。",
    timeoutMs: 4_000,
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      const operation = nonEmptyString(input.operation);
      if (operation !== "clip" && operation !== "subtitle-smart" && operation !== "subtitle-box") {
        throw new Error("operation must be clip, subtitle-smart, or subtitle-box");
      }
      return new Promise((resolve) => {
        let settled = false;
        const settle = (receipt: unknown) => {
          if (settled) return;
          settled = true;
          resolve(receipt);
        };
        canvasEventBus.publish("video-node/set-operation", {
          nodeId,
          operation,
          onComplete: settle,
        });
        window.setTimeout(() => settle({
          ok: false,
          nodeId,
          operation,
          error: "video node did not return an operation receipt",
        }), 3_500);
      });
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.video_download",
    description: "下载指定视频节点的已完成结果，不修改画布。",
    timeoutMs: 20_000,
    handler: async (input) => {
      const nodeId = requireCanvasNode(input);
      const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
      const data = record(node?.data) ?? {};
      const videoUrl = nonEmptyString(data.videoUrl) ?? nonEmptyString(data.resultVideoUrl);
      if (!videoUrl) throw new Error("video output is required");
      const rawName = nonEmptyString(data.sourceFileName)
        ?? nonEmptyString(data.displayName)
        ?? `video-${nodeId}.mp4`;
      const filename = /\.[a-z0-9]{2,5}$/i.test(rawName) ? rawName : `${rawName}.mp4`;
      await downloadUrlAsFile(videoUrl, filename);
      return { node_id: nodeId, downloaded: true, filename };
    },
  }));
  unregister.push(registerFeTool({
    name: "village.ui.video_fullscreen",
    description: "在当前画布查看器中全屏打开指定视频节点，不修改画布。",
    handler: (input) => {
      const nodeId = requireCanvasNode(input);
      const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
      const data = record(node?.data) ?? {};
      const videoUrl = nonEmptyString(data.videoUrl) ?? nonEmptyString(data.resultVideoUrl);
      if (!videoUrl) throw new Error("video output is required");
      const title = nonEmptyString(data.displayName) ?? undefined;
      canvasEventBus.publish("video-viewer/open", { videoUrl, title });
      return { node_id: nodeId, opened: true, fullscreen_requested: true };
    },
  }));
  return () => unregister.reverse().forEach((remove) => remove());
}

let builtInsInstalled = false;
if (typeof window !== "undefined" && !builtInsInstalled) {
  registerBuiltInCanvasFeTools();
  builtInsInstalled = true;
}

export function feToolLifecycleMatchesScope(
  event: FeToolLifecycle,
  scope: ChatScope,
): boolean {
  return scopeMatches(event.scope, scope);
}
