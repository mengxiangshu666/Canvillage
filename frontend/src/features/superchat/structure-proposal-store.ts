// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Agent structure receipts and fallback proposal queue.
 * Every supported canvas operation auto-applies. The queue remains only for
 * unknown/future operations that the current executor cannot safely interpret.
 */

export const STRUCTURE_PROPOSAL_EVENT = "village-canvas:structure-proposals-changed";
export const STRUCTURE_APPLY_EVENT = "village-canvas:canvas-agent-command-apply";
export const STRUCTURE_GHOST_EVENT = "village-canvas:structure-ghost-preview";

export type StructureOpType =
  | "focus_node"
  | "select_node"
  | "duplicate_node"
  | "delete_node"
  | "connect_nodes"
  | "remove_edge"
  | "annotate"
  | "create_canvas_node"
  | "create_image_prompt_node"
  | "create_video_prompt_node"
  | "create_shot_sequence"
  | "insert_starter_workflow"
  | "update_node_prompt"
  | "update_node_label"
  | "update_node_data"
  | "move_node"
  | string;

export interface CanvasAgentCameraSelection {
  camera_body_id?: string;
  lens_id?: string;
  focal_length_mm?: number;
  aperture?: string;
}

export interface CanvasAgentPlacement {
  anchor?: "viewport_center" | "selected_node" | "absolute";
  layout?: "stack" | "grid" | "row" | "column";
  offset?: { x?: number; y?: number };
  gap?: number;
}

export interface StructureOperation {
  type: StructureOpType;
  node_id?: string;
  source?: string;
  target?: string;
  text?: string;
  prompt?: string;
  prompts?: string[];
  display_name?: string;
  /** Generic canvas node type, e.g. imageGenNode / videoComposeNode. */
  node_type?: string;
  /** Starter workflow id from CANVAS_STARTER_WORKFLOWS. */
  workflow_id?: string;
  /** Real node settings, kept separate from the prompt text. */
  aspect_ratio?: string;
  image_size?: string;
  video_quality?: string;
  duration_sec?: number;
  model?: string;
  skill_id?: string;
  node_data?: Record<string, unknown>;
  connect_selected?: boolean;
  edge_id?: string;
  created_edge_id?: string;
  source_handle?: string;
  target_handle?: string;
  edge_data?: Record<string, unknown>;
  generation_mode?: string;
  generate_audio?: boolean;
  count?: number;
  camera?: CanvasAgentCameraSelection;
  camera_movement?: string;
  x?: number;
  y?: number;
  /** Semantic placement resolved against the browser's live XYFlow viewport. */
  placement?: CanvasAgentPlacement;
  /** Deterministic id minted by agent/server so UI + snapshot share the same node. */
  created_node_id?: string;
  created_node_ids?: string[];
}

export interface StructureCommandEnvelope {
  schema: "canvas_chat_commands.v1";
  project_id?: string;
  canvas_id?: string;
  turn_id?: string;
  run_id?: string;
  command_id: string;
  commands: StructureOperation[];
  canvas_command_emitted?: boolean;
  /** Tool-call preview applied before the authoritative server receipt. */
  optimistic?: boolean;
  /** Authoritative revision attached to websocket canvas.patch envelopes. */
  revision?: number;
  /** True when the backend has already committed the command to the canvas. */
  server_applied?: boolean;
  snapshot_required?: boolean;
  ui_reconcile_required?: boolean;
  structure_status?: string | null;
}

export type StructureOpKind = "nav" | "create" | "update" | "wire" | "delete" | "other";

export interface StructureOpSummary {
  type: string;
  kind: StructureOpKind;
  label: string;
  nodeIds: string[];
  creates: number;
  requiresConfirm: boolean;
}

export interface StructureProposal {
  id: string;
  receivedAt: number;
  envelope: StructureCommandEnvelope;
  summaries: StructureOpSummary[];
  totalOps: number;
  createCount: number;
  writeCount: number;
  navOnly: boolean;
  affectedNodeIds: string[];
  title: string;
  risk: "low" | "medium" | "high";
}

export interface StructureApplyRecord {
  commandId: string;
  appliedAt: number;
  appliedCount: number;
  /** How many canvas undo() calls to reverse this apply (best-effort). */
  undoSteps: number;
  title: string;
}

export interface StructureProposalScope {
  projectId: string;
  canvasId: string;
}

const MAX_PENDING = 8;
const AUTO_APPLY_OPERATION_TYPES = new Set<string>([
  "focus_node",
  "select_node",
  "duplicate_node",
  "connect_nodes",
  "remove_edge",
  "annotate",
  "create_canvas_node",
  "create_image_prompt_node",
  "create_video_prompt_node",
  "create_shot_sequence",
  "insert_starter_workflow",
  "update_node_prompt",
  "update_node_label",
  "update_node_data",
  "move_node",
  "delete_node",
]);
let pending: StructureProposal[] = [];
const lastApplyByScope = new Map<string, StructureApplyRecord>();

function normalizedScope(scope?: StructureProposalScope | null): StructureProposalScope | null {
  const projectId = String(scope?.projectId || "").trim();
  const canvasId = String(scope?.canvasId || "").trim();
  return projectId && canvasId ? { projectId, canvasId } : null;
}

function envelopeScope(envelope: StructureCommandEnvelope): StructureProposalScope | null {
  return normalizedScope({
    projectId: String(envelope.project_id || ""),
    canvasId: String(envelope.canvas_id || ""),
  });
}

function scopeKey(scope: StructureProposalScope): string {
  return `${scope.projectId}::${scope.canvasId}`;
}

function proposalMatchesScope(proposal: StructureProposal, scope: StructureProposalScope): boolean {
  return proposal.envelope.project_id === scope.projectId
    && proposal.envelope.canvas_id === scope.canvasId;
}

function emitChanged(reason: string, scope?: StructureProposalScope | null): void {
  if (typeof window === "undefined") return;
  const normalized = normalizedScope(scope);
  window.dispatchEvent(
    new CustomEvent(STRUCTURE_PROPOSAL_EVENT, {
      detail: {
        reason,
        scope: normalized,
        pending: getPendingStructureProposals(normalized),
        lastApply: getLastStructureApply(normalized),
      },
    }),
  );
}

export function classifyStructureOp(op: StructureOperation): StructureOpKind {
  switch (op.type) {
    case "focus_node":
    case "select_node":
      return "nav";
    case "create_image_prompt_node":
    case "create_video_prompt_node":
    case "create_canvas_node":
    case "create_shot_sequence":
    case "insert_starter_workflow":
    case "annotate":
    case "duplicate_node":
      return "create";
    case "update_node_prompt":
    case "update_node_label":
    case "update_node_data":
    case "move_node":
      return "update";
    case "connect_nodes":
    case "remove_edge":
      return "wire";
    case "delete_node":
      return "delete";
    default:
      return "other";
  }
}

export function summarizeStructureOperation(op: StructureOperation): StructureOpSummary {
  const kind = classifyStructureOp(op);
  const nodeIds = [op.node_id, op.source, op.target].filter(
    (id): id is string => typeof id === "string" && id.trim().length > 0,
  );
  let creates = 0;
  let label = String(op.type);
  let requiresConfirm = false;

  switch (op.type) {
    case "focus_node":
      label = `聚焦 ${op.node_id ?? "?"}`;
      break;
    case "select_node":
      label = `选中 ${op.node_id ?? "?"}`;
      break;
    case "connect_nodes":
      label = `连线 ${op.source ?? "?"} → ${op.target ?? "?"}`;
      break;
    case "remove_edge":
      label = `删边 ${op.source ?? "?"} → ${op.target ?? "?"}`;
      break;
    case "annotate":
      creates = 1;
      label = `备注「${String(op.text || "").slice(0, 24)}」`;
      break;
    case "create_image_prompt_node":
      creates = 1;
      label = `新建图片节点 · ${String(op.display_name || op.prompt || "").slice(0, 20)}`;
      break;
    case "create_video_prompt_node":
      creates = 1;
      label = `新建视频节点 · ${String(op.display_name || op.prompt || "").slice(0, 20)}`;
      break;
    case "create_canvas_node":
      creates = 1;
      label = `新建画布节点 · ${String(op.display_name || op.node_type || "").slice(0, 20)}`;
      break;
    case "create_shot_sequence": {
      const n = Array.isArray(op.prompts) ? op.prompts.filter((p) => String(p || "").trim()).length : 0;
      creates = n;
      label = `分镜序列 ×${n}`;
      break;
    }
    case "insert_starter_workflow":
      creates = 1;
      label = `插入工作流 · ${String(op.display_name || op.workflow_id || "").slice(0, 20)}`;
      break;
    case "update_node_prompt":
      label = `改 prompt · ${op.node_id ?? "?"}`;
      break;
    case "update_node_data":
      label = `更新节点 · ${op.node_id ?? "?"}`;
      break;
    case "update_node_label":
      label = `改标签 · ${op.node_id ?? "?"} → ${String(op.display_name || "").slice(0, 16)}`;
      break;
    case "move_node":
      label = `移动 ${op.node_id ?? "?"}`;
      break;
    case "duplicate_node":
      creates = 1;
      label = `复制 ${op.node_id ?? "?"}`;
      break;
    case "delete_node":
      label = `删除 ${op.node_id ?? "?"}`;
      break;
    default:
      label = String(op.type);
  }

  return { type: String(op.type), kind, label, nodeIds, creates, requiresConfirm };
}

export function buildStructureProposal(envelope: StructureCommandEnvelope): StructureProposal | null {
  if (!envelope || envelope.schema !== "canvas_chat_commands.v1") return null;
  const commandId = String(envelope.command_id || "").trim();
  if (!commandId) return null;
  const commands = Array.isArray(envelope.commands) ? envelope.commands.slice(0, 20) : [];
  if (commands.length === 0) return null;

  const summaries = commands.map(summarizeStructureOperation);
  const createCount = summaries.reduce((sum, item) => sum + item.creates, 0);
  const writeCount = summaries.filter((item) => item.kind !== "nav").length;
  const navOnly = writeCount === 0;
  const affectedNodeIds = [...new Set(summaries.flatMap((item) => item.nodeIds))];
  const hasDelete = summaries.some((item) => item.kind === "delete");
  const hasPromptWrite = summaries.some((item) => item.type === "update_node_prompt");
  const risk: StructureProposal["risk"] = hasDelete
    ? "high"
    : hasPromptWrite || createCount >= 5
      ? "medium"
      : "low";

  const titleParts: string[] = [];
  if (createCount > 0) titleParts.push(`+${createCount} 节点`);
  const wires = summaries.filter((item) => item.kind === "wire").length;
  if (wires > 0) titleParts.push(`${wires} 连线`);
  const updates = summaries.filter((item) => item.kind === "update").length;
  if (updates > 0) titleParts.push(`${updates} 更新`);
  const deletes = summaries.filter((item) => item.kind === "delete").length;
  if (deletes > 0) titleParts.push(`${deletes} 删除`);
  const navs = summaries.filter((item) => item.kind === "nav").length;
  if (navs > 0 && titleParts.length === 0) titleParts.push(`${navs} 导航`);
  if (titleParts.length === 0) titleParts.push(`${commands.length} 动作`);

  return {
    id: commandId,
    receivedAt: Date.now(),
    envelope: {
      ...envelope,
      command_id: commandId,
      commands,
    },
    summaries,
    totalOps: commands.length,
    createCount,
    writeCount,
    navOnly,
    affectedNodeIds,
    title: titleParts.join(" · "),
    risk,
  };
}

export function getPendingStructureProposals(scope?: StructureProposalScope | null): StructureProposal[] {
  const normalized = normalizedScope(scope);
  return pending
    .filter((item) => !normalized || proposalMatchesScope(item, normalized))
    .map((item) => ({ ...item, summaries: [...item.summaries], affectedNodeIds: [...item.affectedNodeIds] }));
}

export function getLastStructureApply(scope?: StructureProposalScope | null): StructureApplyRecord | null {
  const normalized = normalizedScope(scope);
  if (normalized) {
    const scoped = lastApplyByScope.get(scopeKey(normalized));
    return scoped ? { ...scoped } : null;
  }
  const latest = [...lastApplyByScope.values()].sort((left, right) => right.appliedAt - left.appliedAt)[0];
  return latest ? { ...latest } : null;
}

export function queueStructureProposal(
  envelope: StructureCommandEnvelope,
  requestedScope?: StructureProposalScope | null,
): StructureProposal | null {
  const proposal = buildStructureProposal(envelope);
  if (!proposal) return null;
  const scope = normalizedScope(requestedScope) ?? envelopeScope(proposal.envelope);
  // Replace same command_id only inside the same canvas. Keep at most eight
  // proposals per canvas so one busy project cannot evict another's decision.
  if (scope) {
    const otherScopes = pending.filter((item) => !proposalMatchesScope(item, scope));
    const scoped = pending.filter((item) => proposalMatchesScope(item, scope) && item.id !== proposal.id);
    pending = [[proposal, ...scoped].slice(0, MAX_PENDING), otherScopes].flat();
  } else {
    pending = [proposal, ...pending.filter((item) => item.id !== proposal.id)].slice(0, MAX_PENDING);
  }
  emitChanged("queue", scope);
  emitGhostPreview(proposal);
  return proposal;
}

export function dismissStructureProposal(
  commandId: string,
  requestedScope?: StructureProposalScope | null,
): void {
  const scope = normalizedScope(requestedScope);
  const next = pending.filter((item) => item.id !== commandId || (scope ? !proposalMatchesScope(item, scope) : false));
  if (next.length === pending.length) return;
  pending = next;
  emitChanged("dismiss", scope);
  emitGhostPreview(getPendingStructureProposals(scope)[0] ?? null);
}

export function clearStructureProposals(requestedScope?: StructureProposalScope | null): void {
  const scope = normalizedScope(requestedScope);
  const next = scope ? pending.filter((item) => !proposalMatchesScope(item, scope)) : [];
  if (next.length === pending.length) return;
  pending = next;
  emitChanged("clear", scope);
  emitGhostPreview(null);
}

export function takeStructureProposal(
  commandId: string,
  requestedScope?: StructureProposalScope | null,
): StructureProposal | null {
  const scope = normalizedScope(requestedScope);
  const found = pending.find((item) => item.id === commandId && (!scope || proposalMatchesScope(item, scope))) ?? null;
  if (!found) return null;
  pending = pending.filter((item) => item !== found);
  emitChanged("take", scope ?? envelopeScope(found.envelope));
  return found;
}

export function recordStructureApply(input: {
  commandId: string;
  appliedCount: number;
  undoSteps: number;
  title: string;
  scope?: StructureProposalScope | null;
}): void {
  const scope = normalizedScope(input.scope);
  if (!scope) return;
  lastApplyByScope.set(scopeKey(scope), {
    commandId: input.commandId,
    appliedAt: Date.now(),
    appliedCount: input.appliedCount,
    undoSteps: input.undoSteps,
    title: input.title,
  });
  emitChanged("applied", scope);
  emitGhostPreview(getPendingStructureProposals(scope)[0] ?? null);
}

export function clearLastStructureApply(requestedScope?: StructureProposalScope | null): void {
  const scope = normalizedScope(requestedScope);
  if (!scope || !lastApplyByScope.delete(scopeKey(scope))) return;
  emitChanged("clear-last-apply", scope);
}

export function requestStructureApply(
  commandId: string,
  requestedScope?: StructureProposalScope | null,
): boolean {
  const scope = normalizedScope(requestedScope);
  const proposal = pending.find((item) => item.id === commandId && (!scope || proposalMatchesScope(item, scope)));
  if (!proposal || typeof window === "undefined") return false;
  window.dispatchEvent(
    new CustomEvent(STRUCTURE_APPLY_EVENT, {
      detail: { commandId, envelope: proposal.envelope, proposal },
    }),
  );
  return true;
}

export function emitGhostPreview(proposal: StructureProposal | null): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(STRUCTURE_GHOST_EVENT, {
      detail: proposal
        ? {
            commandId: proposal.id,
            nodeIds: proposal.affectedNodeIds,
            createCount: proposal.createCount,
            title: proposal.title,
            risk: proposal.risk,
            summaries: proposal.summaries,
          }
        : null,
    }),
  );
}

/** True when envelope is navigation-only (safe auto-apply without modal). */
export function isNavigationOnlyEnvelope(envelope: StructureCommandEnvelope): boolean {
  const proposal = buildStructureProposal(envelope);
  return Boolean(proposal?.navOnly);
}

/**
 * Highest-authority canvas coupling: every operation understood by the current
 * executor auto-applies. Unknown future operations stay in the fallback queue.
 */
export function isAutoApplySafeEnvelope(envelope: StructureCommandEnvelope): boolean {
  const proposal = buildStructureProposal(envelope);
  if (!proposal || proposal.totalOps === 0) return false;
  return proposal.summaries.every((item) => AUTO_APPLY_OPERATION_TYPES.has(item.type));
}
