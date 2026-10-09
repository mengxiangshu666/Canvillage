// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { StructureCommandEnvelope } from "./structure-proposal-store";
import { sha1Text } from "@/lib/sha1";
import { useCanvasStore, type CanvasEdge, type CanvasNode } from "@/stores/canvasStore";
import {
  acknowledgeCanvasMutation,
  hasCanvasMutationCommand,
  registerCanvasMutationIntent,
  rollbackCanvasMutationsForTurn,
  runCanvasMutation,
  settleCanvasMutationsForTurn,
  type CanvasMutationSource,
} from "@/features/canvas/application/canvasMutationKernel";

type OptimisticCanvasCommand = {
  projectId: string;
  canvasId: string;
  commandId: string;
  turnId?: string;
};

export const OPTIMISTIC_CANVAS_ROLLBACK_EVENT =
  "village-canvas:optimistic-canvas-command-rollback";

const activeMutationDepth = new Map<string, number>();
const pendingCommands = new Map<string, OptimisticCanvasCommand>();

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

function scopeKey(projectId: string, canvasId: string): string {
  return `${projectId}\u0000${canvasId}`;
}

function commandKey(projectId: string, canvasId: string, commandId: string): string {
  return `${scopeKey(projectId, canvasId)}\u0000${commandId}`;
}

function commandFromEnvelope(
  envelope: Pick<
    StructureCommandEnvelope,
    "project_id" | "canvas_id" | "command_id" | "turn_id"
  >,
): OptimisticCanvasCommand | null {
  const projectId = nonEmptyString(envelope.project_id);
  const canvasId = nonEmptyString(envelope.canvas_id);
  const commandId = nonEmptyString(envelope.command_id);
  if (!projectId || !canvasId || !commandId) return null;
  const turnId = nonEmptyString(envelope.turn_id);
  return {
    projectId,
    canvasId,
    commandId,
    ...(turnId ? { turnId } : {}),
  };
}

/** Remember a tool.call transaction until its authoritative canvas.patch arrives. */
export function registerOptimisticCanvasCommand(
  envelope: StructureCommandEnvelope,
): void {
  const command = commandFromEnvelope(envelope);
  if (!command) return;
  pendingCommands.set(
    commandKey(command.projectId, command.canvasId, command.commandId),
    command,
  );
  registerCanvasMutationIntent({
    ...command,
    source: "agent" satisfies CanvasMutationSource,
    baseRevision: typeof envelope.revision === "number" ? envelope.revision : null,
    ...(envelope.command_id ? { commandPayload: envelope } : {}),
  });
}

/** Mark one optimistic transaction as confirmed by the server revision stream. */
export function confirmOptimisticCanvasCommand(input: {
  projectId: string;
  canvasId: string;
  commandId?: string;
  revision?: number | null;
}): boolean {
  const commandId = nonEmptyString(input.commandId);
  if (!commandId) return false;
  const removed = pendingCommands.delete(commandKey(input.projectId, input.canvasId, commandId));
  const confirmed = acknowledgeCanvasMutation({
    projectId: input.projectId,
    canvasId: input.canvasId,
    commandId,
    revision: input.revision,
  });
  return removed || confirmed;
}

function removeCommandCreatedGraph<T extends { nodes: CanvasNode[]; edges: CanvasEdge[] }>(
  value: T,
  commandId: string,
): T {
  const removedIds = new Set(
    value.nodes
      .filter((node) => String(node.data?.agent_command_id || "") === commandId)
      .map((node) => node.id),
  );
  if (removedIds.size === 0) return value;
  return {
    ...value,
    nodes: value.nodes.filter((node) => !removedIds.has(node.id)),
    edges: value.edges.filter(
      (edge) => !removedIds.has(edge.source) && !removedIds.has(edge.target),
    ),
  };
}

/** Roll back unfinished create previews without touching unrelated user edits. */
export function rollbackOptimisticCanvasCommandsForTurn(
  turnId: string | null | undefined,
): Array<{ projectId: string; canvasId: string }> {
  const normalizedTurnId = nonEmptyString(turnId);
  if (!normalizedTurnId) return [];
  const scopes = new Map<string, { projectId: string; canvasId: string }>();
  const commands = [...pendingCommands.entries()].filter(
    ([, command]) => command.turnId === normalizedTurnId,
  );
  const kernelCommandIds = new Set(
    commands
      .map(([, command]) => command.commandId)
      .filter((commandId) => hasCanvasMutationCommand(commandId)),
  );
  rollbackCanvasMutationsForTurn(normalizedTurnId).forEach((scope) => {
    scopes.set(scopeKey(scope.projectId, scope.canvasId), scope);
  });
  for (const [key, command] of commands) {
    pendingCommands.delete(key);
    // The mutation kernel owns field-level inverse patches. Keep the legacy
    // graph cleanup only for envelopes that predate the kernel.
    if (kernelCommandIds.has(command.commandId)) {
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(OPTIMISTIC_CANVAS_ROLLBACK_EVENT, {
          detail: {
            ...command,
            rollbackOwnedBy: "canvas-mutation-kernel",
          },
        }));
      }
      continue;
    }
    scopes.set(scopeKey(command.projectId, command.canvasId), {
      projectId: command.projectId,
      canvasId: command.canvasId,
    });
    const mutationKey = scopeKey(command.projectId, command.canvasId);
    activeMutationDepth.set(mutationKey, (activeMutationDepth.get(mutationKey) ?? 0) + 1);
    try {
      useCanvasStore.setState((state) => {
        const next = removeCommandCreatedGraph(state, command.commandId);
        if (next === state) return state;
        const selectedNodeId = state.selectedNodeId
          && next.nodes.some((node) => node.id === state.selectedNodeId)
          ? state.selectedNodeId
          : null;
        const pendingFocusNodeId = state.pendingFocusNodeId
          && next.nodes.some((node) => node.id === state.pendingFocusNodeId)
          ? state.pendingFocusNodeId
          : null;
        // 多节点聚焦同样只保留还活着的成员；整批都没了就清空。
        const pendingFocusNodeIds = state.pendingFocusNodeIds
          ? state.pendingFocusNodeIds.filter((nodeId) =>
              next.nodes.some((node) => node.id === nodeId))
          : null;
        return {
          ...next,
          selectedNodeId,
          pendingFocusNodeId,
          pendingFocusNodeIds: pendingFocusNodeIds && pendingFocusNodeIds.length > 0
            ? pendingFocusNodeIds
            : null,
          history: {
            past: state.history.past.map((snapshot) =>
              removeCommandCreatedGraph(snapshot, command.commandId),
            ),
            future: state.history.future.map((snapshot) =>
              removeCommandCreatedGraph(snapshot, command.commandId),
            ),
          },
        };
      });
    } finally {
      const nextDepth = (activeMutationDepth.get(mutationKey) ?? 1) - 1;
      if (nextDepth > 0) activeMutationDepth.set(mutationKey, nextDepth);
      else activeMutationDepth.delete(mutationKey);
    }
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(OPTIMISTIC_CANVAS_ROLLBACK_EVENT, {
        detail: command,
      }));
    }
  }
  return [...scopes.values()];
}

/** A successful turn without canvas.patch still needs one authoritative pull. */
export function settleOptimisticCanvasCommandsForTurn(
  turnId: string | null | undefined,
): Array<{ projectId: string; canvasId: string }> {
  const normalizedTurnId = nonEmptyString(turnId);
  if (!normalizedTurnId) return [];
  const scopes = new Map<string, { projectId: string; canvasId: string }>();
  settleCanvasMutationsForTurn(normalizedTurnId).forEach((scope) => {
    scopes.set(scopeKey(scope.projectId, scope.canvasId), scope);
  });
  for (const [key, command] of pendingCommands) {
    if (command.turnId !== normalizedTurnId) continue;
    pendingCommands.delete(key);
    scopes.set(scopeKey(command.projectId, command.canvasId), {
      projectId: command.projectId,
      canvasId: command.canvasId,
    });
  }
  return [...scopes.values()];
}

export function resetOptimisticCanvasCommandStateForTests(): void {
  pendingCommands.clear();
  activeMutationDepth.clear();
}

/**
 * Zustand subscriptions run synchronously. This guard lets useCanvasSync skip
 * only the immediate optimistic mutation while still saving subsequent user
 * edits normally.
 */
export function runOptimisticCanvasMutation<T>(
  envelope: StructureCommandEnvelope,
  mutation: () => T,
): T {
  const command = commandFromEnvelope(envelope);
  if (!command) return mutation();
  const key = scopeKey(command.projectId, command.canvasId);
  activeMutationDepth.set(key, (activeMutationDepth.get(key) ?? 0) + 1);
  try {
    return runCanvasMutation({
      ...command,
      source: "agent",
      baseRevision: typeof envelope.revision === "number" ? envelope.revision : null,
      commandPayload: envelope,
    }, mutation);
  } finally {
    const nextDepth = (activeMutationDepth.get(key) ?? 1) - 1;
    if (nextDepth > 0) activeMutationDepth.set(key, nextDepth);
    else activeMutationDepth.delete(key);
  }
}

export function isOptimisticCanvasMutationActive(
  projectId: string,
  canvasId: string,
): boolean {
  return (activeMutationDepth.get(scopeKey(projectId, canvasId)) ?? 0) > 0;
}

/** Test-only visibility without exporting mutable runtime state. */
export function pendingOptimisticCanvasCommandCount(): number {
  return pendingCommands.size;
}

/**
 * Command types the server mints a deterministic `created_node_id` for (see
 * `src/novelvideo/freezone/canvas_agent_ids.py::attach_agent_created_node_ids`).
 * The browser must mint the identical id so the optimistic node reconciles by
 * identity instead of duplicating. Keep this set in lockstep with the server;
 * a type missing here silently disables optimistic rendering for its batch.
 */
const CREATED_NODE_COMMAND_TYPES = new Set([
  "annotate",
  "create_canvas_node",
  "create_image_prompt_node",
  "create_video_prompt_node",
  "duplicate_node",
]);

/**
 * Node-creating commands that are deliberately NOT rendered optimistically.
 *
 * `insert_starter_workflow` expands into a workflow-specific set of nodes whose
 * ids are chosen by the server transaction, so the browser cannot predict them;
 * an optimistic apply would leave orphans that duplicate once the authoritative
 * snapshot lands. These commands are dropped from the optimistic preview only —
 * they are still applied authoritatively — so a batch that mixes one of them
 * with deterministic creates keeps rendering its safe nodes immediately instead
 * of losing the whole preview.
 */
const OPTIMISTIC_EXCLUDED_COMMAND_TYPES = new Set(["insert_starter_workflow"]);

export function optimisticCreatedNodeIds(
  envelope: StructureCommandEnvelope,
): string[] {
  const ids: string[] = [];
  for (const command of envelope.commands) {
    if (CREATED_NODE_COMMAND_TYPES.has(command.type)) {
      const nodeId = nonEmptyString(command.created_node_id);
      if (nodeId) ids.push(nodeId);
      continue;
    }
    if (command.type === "create_shot_sequence") {
      for (const rawNodeId of command.created_node_ids ?? []) {
        const nodeId = nonEmptyString(rawNodeId);
        if (nodeId) ids.push(nodeId);
      }
    }
  }
  return [...new Set(ids)];
}

/**
 * A pending command only proves that its tool frame was observed. Verify the
 * deterministic nodes as well before treating the optimistic preview as
 * rendered; the canvas listener can mount after that one-shot frame.
 */
export function optimisticCanvasGraphPresent(
  envelope: StructureCommandEnvelope,
): boolean {
  const expectedNodeIds = optimisticCreatedNodeIds(envelope);
  if (expectedNodeIds.length === 0) return false;
  const canvasNodeIds = new Set(useCanvasStore.getState().nodes.map((node) => node.id));
  return expectedNodeIds.every((nodeId) => canvasNodeIds.has(nodeId));
}

export function isSafeOptimisticCreateEnvelope(
  envelope: StructureCommandEnvelope,
): boolean {
  const createdIds = new Set<string>();
  for (const command of envelope.commands) {
    if (CREATED_NODE_COMMAND_TYPES.has(command.type)) {
      if (!command.created_node_id) return false;
      // A duplicate is only reconcilable when its source node is known; the
      // executor silently skips a source-less duplicate, which would leave the
      // preview permanently "missing" its deterministic id.
      if (command.type === "duplicate_node" && !command.node_id) return false;
      createdIds.add(command.created_node_id);
      continue;
    }
    if (command.type === "create_shot_sequence") {
      if (!command.created_node_ids?.length) return false;
      command.created_node_ids.forEach((nodeId) => createdIds.add(nodeId));
    }
  }
  if (createdIds.size === 0) return false;
  return envelope.commands.every((command) => {
    if (CREATED_NODE_COMMAND_TYPES.has(command.type) || command.type === "create_shot_sequence") {
      return true;
    }
    if (command.type === "connect_nodes") {
      return Boolean(
        command.source
        && command.target
        && (createdIds.has(command.source) || createdIds.has(command.target)),
      );
    }
    if (
      command.type === "focus_node"
      || command.type === "select_node"
      || command.type === "move_node"
      || command.type === "update_node_prompt"
      || command.type === "update_node_label"
    ) {
      return Boolean(command.node_id && createdIds.has(command.node_id));
    }
    // Unknown / preview-incompatible commands reject the envelope. This is the
    // safe fallback: the executor applies the whole envelope as one unit, so an
    // unaccounted node-creating command would duplicate its nodes once the
    // authoritative snapshot arrives. Known-but-unrenderable creates are
    // removed upstream by `prepareOptimisticCanvasEnvelope` instead.
    return false;
  });
}

/** Mirror the server plugin's `_mint_agent_node_id(command_id, seq)`. */
export function deterministicAgentNodeId(commandId: string, sequence: number): string {
  return `agent-${sha1Text(commandId).slice(0, 12)}-${sequence}`;
}

/**
 * Add the same deterministic node ids the server transaction will use. This
 * lets the optimistic graph reconcile by identity instead of producing a
 * random browser clone beside the authoritative node.
 *
 * Commands that cannot be reconciled by identity (see
 * `OPTIMISTIC_EXCLUDED_COMMAND_TYPES`) are removed from the preview here, so a
 * single unsupported command no longer disqualifies the rest of the batch.
 */
export function prepareOptimisticCanvasEnvelope(
  envelope: StructureCommandEnvelope,
): StructureCommandEnvelope {
  let sequence = 0;
  const commands = envelope.commands
    .filter((command) => !OPTIMISTIC_EXCLUDED_COMMAND_TYPES.has(command.type))
    .map((command) => {
    if (command.type === "create_shot_sequence") {
      const prompts = Array.isArray(command.prompts)
        ? command.prompts.filter((prompt) => String(prompt || "").trim())
        : [];
      const createdNodeIds = prompts.map(() => {
        sequence += 1;
        return deterministicAgentNodeId(envelope.command_id, sequence);
      });
      return createdNodeIds.length > 0
        ? {
            ...command,
            connect_selected: false,
            created_node_ids: createdNodeIds,
            created_node_id: createdNodeIds[createdNodeIds.length - 1],
          }
        : command;
    }
    if (!CREATED_NODE_COMMAND_TYPES.has(command.type)) return command;
    sequence += 1;
    return command.created_node_id
      ? { ...command, connect_selected: false }
      : {
          ...command,
          connect_selected: false,
          created_node_id: deterministicAgentNodeId(envelope.command_id, sequence),
        };
  });
  return {
    ...envelope,
    commands,
    canvas_command_emitted: true,
    optimistic: true,
    server_applied: false,
    snapshot_required: false,
    ui_reconcile_required: false,
  };
}
