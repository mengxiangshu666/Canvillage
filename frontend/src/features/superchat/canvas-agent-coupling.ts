// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Tight Agent↔Canvas coupling helpers (LibTV canvas_patch + OiiOii co-state style).
 * - Default targets: $selected / $pinned
 * - Resolve live node aliases before the full-authority executor applies them
 */

import type {
  StructureCommandEnvelope,
  StructureOperation,
} from "@/features/superchat/structure-proposal-store";

export const CANVAS_AGENT_COUPLING_VERSION = "tight-v1";
export const CANVAS_AGENT_APPLIED_EVENT = "village-canvas:canvas-agent-command-applied";

const SELECTED_ALIASES = new Set([
  "",
  "$selected",
  "selected",
  "@selected",
  "SELECTED",
  "current",
  "$current",
]);

const PINNED_ALIASES = new Set(["$pinned", "pinned", "@pinned", "PINNED"]);

export function isSelectedAlias(ref: string | undefined | null): boolean {
  return SELECTED_ALIASES.has(String(ref ?? "").trim());
}

export function isPinnedAlias(ref: string | undefined | null): boolean {
  const raw = String(ref ?? "").trim();
  if (PINNED_ALIASES.has(raw)) return true;
  return /^\$pinned:\d+$/i.test(raw) || /^pinned:\d+$/i.test(raw);
}

/** Resolve agent node ref against live selection + pin list. */
export function resolveCanvasNodeRef(
  ref: string | undefined | null,
  input: {
    selectedNodeId: string | null | undefined;
    pinnedNodeIds?: readonly string[];
  },
): string | null {
  const raw = String(ref ?? "").trim();
  const pinned = (input.pinnedNodeIds ?? []).filter(Boolean);

  if (isSelectedAlias(raw)) {
    return input.selectedNodeId?.trim() || null;
  }

  if (PINNED_ALIASES.has(raw)) {
    return pinned[0] ?? null;
  }

  const pinnedIndex = raw.match(/^\$?pinned:(\d+)$/i);
  if (pinnedIndex) {
    const idx = Number(pinnedIndex[1]);
    if (!Number.isFinite(idx) || idx < 0) return null;
    return pinned[idx] ?? null;
  }

  return raw || null;
}

/** Rewrite command refs so apply path always sees concrete node ids when possible. */
export function resolveStructureEnvelopeTargets(
  envelope: StructureCommandEnvelope,
  input: {
    selectedNodeId: string | null | undefined;
    pinnedNodeIds?: readonly string[];
  },
): StructureCommandEnvelope {
  const commands = (envelope.commands ?? []).map((op) => {
    const next: StructureOperation = { ...op };
    if ("node_id" in next || isSelectedAlias(op.node_id) || isPinnedAlias(op.node_id) || !op.node_id) {
      if (
        op.type === "focus_node"
        || op.type === "select_node"
        || op.type === "update_node_prompt"
        || op.type === "update_node_label"
        || op.type === "update_node_data"
        || op.type === "move_node"
        || op.type === "duplicate_node"
        || op.type === "delete_node"
        || isSelectedAlias(op.node_id)
        || isPinnedAlias(op.node_id)
      ) {
        const resolved = resolveCanvasNodeRef(op.node_id, input);
        if (resolved) next.node_id = resolved;
      }
    }
    if (op.type === "connect_nodes" || op.type === "remove_edge") {
      const source = resolveCanvasNodeRef(op.source ?? "$selected", input);
      const target = resolveCanvasNodeRef(op.target, input);
      if (source) next.source = source;
      if (target) next.target = target;
    }
    return next;
  });
  return { ...envelope, commands };
}
