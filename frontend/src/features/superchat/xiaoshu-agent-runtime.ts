// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { CanvasFastCommandContext } from "./canvas-fast-command";
import { buildCanvasAgentTaskAuthorization } from "./canvas-agent-intelligence";
import { emitCanvasPatchNotification } from "./canvas-patch-events";
import { api } from "@/lib/api";

export type XiaoshuTaskControlOperation = "run_node" | "retry_node" | "stop_task";

export type XiaoshuRequestRoute =
  | {
      kind: "model_collaboration";
      plan: null;
      requiresModelConnection: true;
    }
  | {
      kind: "direct_task_control";
      operation: XiaoshuTaskControlOperation;
      nodeId: string;
      reply: string;
      requiresModelConnection: false;
    };

export interface RouteXiaoshuRequestInput {
  userText: string;
  canvasContext?: Pick<CanvasFastCommandContext, "selectedNodeId"> | null;
  hasExplicitAttachments?: boolean;
}

function directTaskControl(
  text: string,
  selectedNodeId: string | null | undefined,
): Omit<Extract<XiaoshuRequestRoute, { kind: "direct_task_control" }>, "kind" | "requiresModelConnection"> | null {
  const nodeId = selectedNodeId?.trim();
  if (!nodeId) return null;
  const request = text.trim().replace(/[。！!？?]+$/u, "").trim();
  if (/^(?:请)?(?:帮我)?(?:停止|终止|取消)(?:一下)?(?:当前|这个|选中)(?:节点|任务|生成)$/u.test(request)) {
    return { operation: "stop_task", nodeId, reply: "已停止当前节点任务" };
  }
  if (/^(?:请)?(?:帮我)?(?:重新生成|重新运行|重试|再跑一次)(?:一下)?(?:当前|这个|选中)(?:的)?节点$/u.test(request)) {
    return { operation: "retry_node", nodeId, reply: "已重新启动当前节点" };
  }
  if (/^(?:请)?(?:帮我)?(?:运行|执行|生成|开始生成|启动)(?:一下)?(?:当前|这个|选中)(?:的)?节点$/u.test(request)) {
    return { operation: "run_node", nodeId, reply: "已启动当前节点" };
  }
  return null;
}

/**
 * Only narrow task controls bypass the model. Every creative or structural
 * request must be understood by the Agent before a formal canvas tool runs.
 */
export function routeXiaoshuRequest(input: RouteXiaoshuRequestInput): XiaoshuRequestRoute {
  const taskControl = !input.hasExplicitAttachments
    ? directTaskControl(input.userText, input.canvasContext?.selectedNodeId)
    : null;
  if (taskControl) {
    return {
      kind: "direct_task_control",
      ...taskControl,
      requiresModelConnection: false,
    };
  }
  return {
    kind: "model_collaboration",
    plan: null,
    requiresModelConnection: true,
  };
}

interface XiaoshuCanvasControlResponse {
  ok: boolean;
  data: {
    schema?: string;
    project_id: string;
    canvas_id: string;
    command_id?: string;
    revision: number;
    commands?: Array<Record<string, unknown>>;
    server_applied?: boolean;
    snapshot_required?: boolean;
    ui_reconcile_required?: boolean;
    structure_status?: string | null;
  };
}

export interface ExecuteXiaoshuTaskControlInput {
  projectId: string;
  canvasId: string;
  nodeId: string;
  operation: XiaoshuTaskControlOperation;
  reply: string;
  runMode: "draft" | "auto";
  userText: string;
  appendExchange: (userText: string, assistantText: string) => void;
}

/** Run task controls over REST so they remain available while chat reconnects. */
export async function executeXiaoshuTaskControl(
  input: ExecuteXiaoshuTaskControlInput,
): Promise<XiaoshuCanvasControlResponse["data"]> {
  const commandId = `xiaoshu-control-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  const taskAuthorization = buildCanvasAgentTaskAuthorization(input.runMode, false);
  const response = await api.post("api/v1/chat/xiaoshu/canvas-control", {
    json: {
      scope: {
        kind: "project",
        id: input.projectId,
        canvas_id: input.canvasId,
      },
      operation: input.operation,
      node_id: input.nodeId,
      command_id: commandId,
      ...(input.operation === "stop_task"
        ? {}
        : { task_authorization: taskAuthorization }),
    },
  }).json<XiaoshuCanvasControlResponse>();
  const patch = response.data;
  emitCanvasPatchNotification({
    projectId: patch.project_id,
    canvasId: patch.canvas_id,
    revision: patch.revision,
    ...(patch.command_id ? { commandId: patch.command_id } : {}),
    ...(patch.schema ? { schema: patch.schema } : {}),
    ...(patch.commands?.length ? { commands: patch.commands } : {}),
    ...(typeof patch.server_applied === "boolean" ? { serverApplied: patch.server_applied } : {}),
    ...(typeof patch.snapshot_required === "boolean" ? { snapshotRequired: patch.snapshot_required } : {}),
    ...(typeof patch.ui_reconcile_required === "boolean" ? { uiReconcileRequired: patch.ui_reconcile_required } : {}),
    ...(patch.structure_status !== undefined ? { structureStatus: patch.structure_status } : {}),
  });
  const revisionText = patch.revision ? ` · revision ${patch.revision}` : "";
  input.appendExchange(input.userText, `${input.reply}${revisionText}`);
  return patch;
}
