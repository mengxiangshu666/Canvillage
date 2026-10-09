// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasGraphPatch, CanvasGraphPatchOperation } from "@/api/skills";
import type {
  StructureCommandEnvelope,
  StructureOperation,
} from "@/features/superchat/structure-proposal-store";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function operationToCanvasCommands(
  operation: CanvasGraphPatchOperation,
): StructureOperation[] {
  if (operation.op === "add_node") {
    const node = record(operation.node);
    const data = record(node.data);
    const position = record(node.position);
    const nodeType = text(node.type);
    const nodeId = text(node.id);
    if (!nodeType || !nodeId) return [];
    return [{
      type: "create_canvas_node",
      node_type: nodeType,
      created_node_id: nodeId,
      display_name: text(data.displayName) || text(data.label),
      prompt: text(data.prompt) || text(data.content) || text(data.text),
      text: text(data.content) || text(data.text),
      model: text(data.model),
      skill_id: text(data.skill_id),
      node_data: data,
      connect_selected: false,
      x: finite(position.x),
      y: finite(position.y),
    }];
  }
  if (operation.op === "update_node") {
    const nodeId = text(operation.node_id) || text(record(operation.node).id);
    if (!nodeId) return [];
    return [{ type: "update_node_data", node_id: nodeId, node_data: record(operation.data) }];
  }
  if (operation.op === "delete_node") {
    const nodeId = text(operation.node_id) || text(record(operation.node).id);
    return nodeId ? [{ type: "delete_node", node_id: nodeId }] : [];
  }
  if (operation.op === "add_edge") {
    const edge = record(operation.edge);
    const source = text(edge.source);
    const target = text(edge.target);
    if (!source || !target) return [];
    return [{
      type: "connect_nodes",
      source,
      target,
      created_edge_id: text(edge.id),
      source_handle: text(edge.sourceHandle),
      target_handle: text(edge.targetHandle),
      edge_data: record(edge.data),
    }];
  }
  if (operation.op === "delete_edge") {
    const edge = record(operation.edge);
    return [{
      type: "remove_edge",
      edge_id: text(operation.edge_id) || text(edge.id),
      source: text(edge.source),
      target: text(edge.target),
    }];
  }
  if (operation.op === "update_edge") {
    const edge = record(operation.edge);
    const source = text(edge.source);
    const target = text(edge.target);
    if (!source || !target) return [];
    return [
      {
        type: "remove_edge",
        edge_id: text(operation.edge_id) || text(edge.id),
        source,
        target,
      },
      {
        type: "connect_nodes",
        source,
        target,
        created_edge_id: text(edge.id),
        source_handle: text(edge.sourceHandle),
        target_handle: text(edge.targetHandle),
        edge_data: { ...record(edge.data), ...record(operation.data) },
      },
    ];
  }
  return [];
}

export function graphPatchToCanvasEnvelope(
  patch: CanvasGraphPatch,
  input: {
    projectId: string;
    canvasId: string;
    commandId: string;
  },
): StructureCommandEnvelope | null {
  if (patch.schema_version !== "graph_patch.v1" || !Array.isArray(patch.operations)) {
    return null;
  }
  const commands = patch.operations.flatMap(operationToCanvasCommands).slice(0, 40);
  if (commands.length === 0) return null;
  return {
    schema: "canvas_chat_commands.v1",
    project_id: input.projectId,
    canvas_id: input.canvasId,
    command_id: input.commandId,
    canvas_command_emitted: true,
    server_applied: false,
    snapshot_required: true,
    commands,
  };
}

export function emitSkillGraphPatch(
  patch: CanvasGraphPatch,
  input: { projectId: string; canvasId: string; commandId: string },
): boolean {
  if (typeof window === "undefined") return false;
  const envelope = graphPatchToCanvasEnvelope(patch, input);
  if (!envelope) return false;
  window.dispatchEvent(
    new CustomEvent("village-canvas:canvas-agent-command", { detail: envelope }),
  );
  return true;
}
