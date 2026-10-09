// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { resolveVillageAgentCompanionAnchor } from "./agent-companion-anchor";
export { resolveVillageAgentCompanionAnchor } from "./agent-companion-anchor";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Canvas } from "@/features/canvas/Canvas";
import {
  CANVAS_NODE_TYPES,
  DEFAULT_NODE_WIDTH,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
  type CanvasNodeType,
} from "@/features/canvas/domain/canvasNodes";
import {
  isUpstreamConnectionAllowed,
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from "@/features/canvas/domain/nodeRegistry";
import type { CanvasStarterWorkflowId } from "@/features/canvas/application/starterWorkflows";
import { NodeReplaceDragPreview } from "@/features/canvas/ui/NodeReplaceDragPreview";
import type { CanvasProjectSummary } from "@/api/projects";
import {
  buildProjectionFromPreset,
  getProjectionStatuses,
} from "@/api/canvas";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/use-media-query";
import { currentCanvasParam } from "@/lib/app-router";
import { rememberLastCanvas, writeUrl } from "@/lib/url-params";
import { canvasOnlyProduct } from "@/lib/product-mode";
import { cn } from "@/lib/utils";
import { SuperChatPanel, type ComposerHandoffSnapshot } from "@/features/superchat/superchat-panel";
import { MyBuddyCompanion } from "@/features/companion/MyBuddyCompanion";
import {
  clampFreezoneAgentWidth,
  FREEZONE_AGENT_DRAWER_CLASS,
  FREEZONE_AGENT_WIDTH_V4_KEY,
  FREEZONE_AGENT_WIDTH_MIN,
  FREEZONE_AGENT_WIDTH_MAX,
  loadFreezoneAgentWidth,
  saveFreezoneAgentWidth,
} from "@/features/superchat/freezone-canvas-agent-ui";
import {
  recordStructureApply,
  takeStructureProposal,
  type StructureOperation,
  STRUCTURE_APPLY_EVENT,
} from "@/features/superchat/structure-proposal-store";
import {
  CANVAS_AGENT_APPLIED_EVENT,
  CANVAS_AGENT_COUPLING_VERSION,
  resolveStructureEnvelopeTargets,
} from "@/features/superchat/canvas-agent-coupling";
import { emitCanvasCommandReceipt } from "@/features/superchat/canvas-command-receipts";
import {
  acknowledgeCanvasAgentCommandEnvelope,
  CANVAS_AGENT_COMMAND_EVENT,
  replayPendingCanvasAgentCommands,
} from "@/features/superchat/canvas-patch-events";
import {
  OPTIMISTIC_CANVAS_ROLLBACK_EVENT,
  optimisticCanvasGraphPresent,
  runOptimisticCanvasMutation,
} from "@/features/superchat/optimistic-canvas-commands";
import { loadPinnedCanvasNodeIds } from "@/features/superchat/canvas-agent-pin-store";
import {
  agentNodeParameters,
  agentPromptWithCameraMovement,
} from "./agentNodeParameters";
import {
  cameraDirectionTextFromCommand,
  setCameraDirection,
} from "@/features/canvas/domain/promptCamera";
import { CommitDialog } from "./commit/CommitDialog";
import { promoteToAsset } from "./commit/promoteToAsset";
import { commitDirectorRenderFromCanvasSource } from "./commit/directorRenderCommit";
import {
  commitSceneDirectorWorldFromCanvasNode,
  hasDirectorWorldSceneState,
  isDirectorWorldSourceSlotTarget,
} from "./commit/sceneDirectorWorldCommit";
import { nodeDataAfterCommittedSlot } from "./commit/committedNodePatch";
import { isCommitCandidateData } from "./commit/commitEligibility";
import { CreateIdentityDialog } from "@/pipeline-import/CreateIdentityDialog";
import { MaskEditor } from "@/pipeline-import/MaskEditor";
import { AssetLibraryPanel } from "./AssetLibraryPanel";
import { ExpressionManagerPanel } from "./ExpressionManagerPanel";
import { CanvasStoryboardView } from "./CanvasStoryboardView";
import { FreezoneChatToggleButton } from "./FreezoneChatToggleButton";
import { useNodePowerHubStore } from "./nodePowerHubStore";
import type { PushResult, PushTarget, PushTargetKind } from "@/api/push";
import { coerceSlotTarget } from "@/features/canvas/domain/mainlineNodeTypes";
import { peekHomeCanvasHandoff, clearHomeCanvasHandoff } from "@/lib/home-handoff";
import { canvasEventBus } from "@/features/canvas/application/canvasServices";
import { beginCanvasInteraction } from "@/features/canvas/application/canvasInteractionPerformance";
import { saveOpenDirectorWorldScene } from "@/features/canvas/domain/directorWorldSceneSaveRegistry";
import {
  assetToPushTarget,
  inferDefaultTarget,
  isPlyOrGlbPushTargetKind,
  isScenePushTargetKind,
} from "@/features/freezone/commit/pushTarget";
import { useCanvasStore } from "@/stores/canvasStore";
import {
  createCanvasNodeForDataEdit,
  removeCanvasNodesForDataEdit,
} from "@/stores/canvasStore";
import {
  validateCandidateBindingRoleCandidate,
  validatePropagatingEdgeCandidate,
} from "@/features/freezone/context/mainlineContext";
import {
  deriveNodeDropInfo,
  modelSourceUrlFromNodeData,
  type DropMediaType,
} from "@/stores/assetDropStore";
import { withImageCacheBust } from "@/features/canvas/application/imageData";
import { queryKeys } from "@/lib/query-keys";
import { useCanvasSync } from "./useCanvasSync";
import {
  projectionKeysFromMetadata,
  requestFromProjectionMetadata,
  shouldClearProjectionStatuses,
  shouldFetchProjectionStatuses,
  shouldSkipProjectionStatusRevision,
  startProjectionStatusRefresh,
} from "./projectionStatusContracts";
import {
  BackupStatusIndicator,
  CanvasConflictOverlay,
  CanvasErrorOverlay,
  CanvasLoadingOverlay,
  CanvasLoadingScreen,
  CanvasMutationOutboxIndicator,
  CanvasOfflineSaveBanner,
  Toast,
} from "./CanvasStatusOverlays";
import {
  buildCanvasCommandResultReceipt, createCanvasCommandGateway,
  loadCanvasAgentCommandIds,
  persistAppliedCanvasCommand,
  saveCanvasAgentCommandIds,
  type CanvasAgentCommandEnvelope,
} from "./canvasCommandGateway";
import { prefetchFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";
import { prefetchFreezoneVideoModels } from "@/features/canvas/hooks/useFreezoneVideoModels";
import { prefetchFreezoneCameraOptions } from "@/features/canvas/hooks/useFreezoneCameraOptions";
import { prefetchFreezoneStyleTemplates } from "@/features/canvas/hooks/useFreezoneStyleTemplates";
import { prefetchFreezoneVideoCameraTemplates } from "@/features/canvas/hooks/useFreezoneVideoCameraTemplates";
import {
  projectionMetadataWithRequest,
  projectionTargetForCanvasPanel,
} from "@/features/freezone/projections";
import {
  clearCanvasProjectionStatuses,
  markCanvasProjectionFresh,
  setCanvasProjectionStatuses,
} from "@/features/freezone/projectionStatusStore";
import {
  consumeQueuedLocalFreezoneProjections,
  queueLocalFreezoneProjection,
  removeLocalFreezoneProjection,
} from "@/features/freezone/canvasSyncRuntime";
export { hasLegacyPresetCanvasMetadata } from "@/features/freezone/projections";
export {
  requestFromProjectionMetadata,
  shouldClearProjectionStatuses,
  shouldFetchProjectionStatuses,
  shouldSkipProjectionStatusRevision,
  startProjectionStatusRefresh,
} from "./projectionStatusContracts";

interface FreezoneShellProps {
  project: CanvasProjectSummary;
  canvasId: string;
}

function renderCommitSuccessMessage(target: PushTarget, result: PushResult): string {
  if (target.kind === "director_render") {
    return `已提交导演合成资产：${result.target_path}（含纯背景和元数据）`;
  }
  if (target.kind === "scene_director_world") {
    return `已提交导演世界：${result.target_path}`;
  }
  return `已提交到 ${result.target_path}`;
}

function sceneDirectorWorldDataForManifest(
  nodeData: Record<string, unknown>,
  target: PushTarget,
  result: PushResult,
  projectId?: string,
): Record<string, unknown> | null {
  const manifestNodeData = nodeDataPatchAfterCommittedSourceSlot(nodeData, target, result, projectId);
  return hasDirectorWorldSceneState(manifestNodeData) ? manifestNodeData : null;
}

export function nodeDataPatchAfterCommittedSourceSlot(
  nodeData: Record<string, unknown>,
  target: PushTarget,
  result: PushResult,
  projectId?: string,
): Record<string, unknown> | null {
  if (!isDirectorWorldSourceSlotTarget(target)) return null;
  return nodeDataAfterCommittedSlot(nodeData, target, result, projectId);
}

export function nodeDataPatchAfterCommittedTarget(
  nodeData: Record<string, unknown>,
  target: PushTarget,
  result: PushResult,
  projectId?: string,
): Record<string, unknown> | null {
  if (isDirectorWorldSourceSlotTarget(target)) return null;
  return nodeDataAfterCommittedSlot(nodeData, target, result, projectId);
}

function latestCanvasNodeData(nodeId: string): Record<string, unknown> | null {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
  return node?.data && typeof node.data === "object"
    ? node.data as Record<string, unknown>
    : null;
}

export function resolveSubmitNodeData(
  latest: Record<string, unknown> | null | undefined,
  fallback: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  return latest ?? fallback ?? null;
}

export function shouldRefreshCommittedTargetNodes(target: PushTarget): boolean {
  // scene_director_world is a structured manifest/state commit, not a media file
  // replacement. Refreshing canvas node URLs with its result corrupts the visual
  // node into a broken image/manifest preview.
  return target.kind !== "scene_director_world";
}

/**
 * Mounts the shared xyflow canvas inside the Village Infinite Canvas Beat Workbench shell.
 * Canvas switching lives inside the left AssetLibraryPanel (主线资产 / 画布 tabs).
 * Commit still lives on eligible canvas nodes. Sync status is
 * intentionally not shown — `useCanvasSync` still loads + persists via
 * /api/v1/projects/<project_id>/freezone/canvases and surfaces conflict /
 * error states via the overlays below; ready/saving states are silent.
 * The outer SPA sidebar already exposes project switching and the task center,
 * so this shell omits the back button, project picker, import/extract/
 * video-ref/3GS triggers, and the top-right Beat Workbench task entry.
 */
const canvasKey = (projectId: string, canvasId: string) => `${projectId}::${canvasId}`;

/** 上一次真正画出来的画布；跨挂载保留，用来判断重进时能否直接复用 store 里的内容。 */
let lastRenderedCanvasKey: string | null = null;

type CanvasAgentOperation = StructureOperation;

const AGENT_FALLBACK_NODE_HEIGHT = 260;
const AGENT_FALLBACK_NODE_STAGGER_X = 48;
const AGENT_FALLBACK_NODE_STAGGER_Y = 56;
// Keep generated storyboard cards separated even at the server's conservative
// layout size (580x360). The previous 220px step caused consecutive shots to
// cover each other before the next snapshot reconciliation.
const AGENT_SEQUENCE_NODE_GAP_Y = 408;

/** Prefer server-minted node ids so UI apply and snapshot re-read share identity. */
function adoptCreatedNodeId(tempId: string, preferredId?: string | null): string {
  const want = String(preferredId || "").trim();
  if (!want || want === tempId) return tempId;
  const state = useCanvasStore.getState();
  if (state.nodes.some((node) => node.id === want)) {
    // Preferred id already present (server hydrate / prior apply) — drop temp clone.
    if (tempId !== want && state.nodes.some((node) => node.id === tempId)) {
      state.deleteNode(tempId);
    }
    return want;
  }
  useCanvasStore.setState((current) => ({
    nodes: current.nodes.map((node) => (node.id === tempId ? { ...node, id: want } : node)),
    edges: current.edges.map((edge) => {
      const source = edge.source === tempId ? want : edge.source;
      const target = edge.target === tempId ? want : edge.target;
      if (source === edge.source && target === edge.target) return edge;
      return {
        ...edge,
        id: edge.id.includes(tempId) ? edge.id.split(tempId).join(want) : edge.id,
        source,
        target,
      };
    }),
    selectedNodeId: current.selectedNodeId === tempId ? want : current.selectedNodeId,
  }));
  return want;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function visibleCanvasCenterPosition(
  state: ReturnType<typeof useCanvasStore.getState>,
): { x: number; y: number } {
  const viewport = state.currentViewport;
  const width = state.canvasViewportSize?.width ?? 0;
  const height = state.canvasViewportSize?.height ?? 0;
  if (
    finiteNumber(viewport?.x)
    && finiteNumber(viewport?.y)
    && finiteNumber(viewport?.zoom)
    && viewport.zoom > 0
    && width > 0
    && height > 0
  ) {
    const zoom = Math.max(0.01, viewport.zoom);
    const centerX = (width / 2 - viewport.x) / zoom;
    const centerY = (height / 2 - viewport.y) / zoom;
    return {
      x: Math.round(
        centerX
          - DEFAULT_NODE_WIDTH / 2,
      ),
      y: Math.round(
        centerY
          - AGENT_FALLBACK_NODE_HEIGHT / 2,
      ),
    };
  }

  const selected = state.nodes.find((node) => node.id === state.selectedNodeId);
  if (selected) {
    return {
      x: selected.position.x + DEFAULT_NODE_WIDTH + 100,
      y: selected.position.y,
    };
  }
  return {
    x: 120,
    y: 160,
  };
}

function semanticPlacementPosition(
  command: Pick<StructureOperation, "placement">,
  state: ReturnType<typeof useCanvasStore.getState>,
  index: number,
): { x: number; y: number } {
  const placement = command.placement;
  let base = visibleCanvasCenterPosition(state);
  if (placement?.anchor === "absolute") {
    base = { x: 0, y: 0 };
  } else if (placement?.anchor === "selected_node") {
    const selected = state.nodes.find((node) => node.id === state.selectedNodeId);
    if (selected) {
      base = {
        x: selected.position.x + DEFAULT_NODE_WIDTH + 100,
        y: selected.position.y,
      };
    }
  }

  const gap = finiteNumber(placement?.gap)
    ? Math.max(16, Math.min(400, placement.gap))
    : 56;
  const layout = placement?.layout ?? "stack";
  let layoutX = index * AGENT_FALLBACK_NODE_STAGGER_X;
  let layoutY = index * AGENT_FALLBACK_NODE_STAGGER_Y;
  if (layout === "row") {
    layoutX = index * (DEFAULT_NODE_WIDTH + gap);
    layoutY = 0;
  } else if (layout === "column") {
    layoutX = 0;
    layoutY = index * (AGENT_FALLBACK_NODE_HEIGHT + gap);
  } else if (layout === "grid") {
    const column = index % 3;
    const row = Math.floor(index / 3);
    layoutX = column * (DEFAULT_NODE_WIDTH + gap);
    layoutY = row * (AGENT_FALLBACK_NODE_HEIGHT + gap);
  }

  return {
    x: base.x + layoutX + (finiteNumber(placement?.offset?.x) ? placement.offset.x : 0),
    y: base.y + layoutY + (finiteNumber(placement?.offset?.y) ? placement.offset.y : 0),
  };
}

function resolveAgentCommandPosition(
  command: Pick<StructureOperation, "x" | "y" | "placement">,
  index: number,
): { x: number; y: number } {
  const fallback = semanticPlacementPosition(command, useCanvasStore.getState(), index);
  return {
    x: finiteNumber(command.x) ? command.x : fallback.x,
    y: finiteNumber(command.y) ? command.y : fallback.y,
  };
}

const AGENT_VIEWPORT_PLACED_COMMAND_KEY = "agent_viewport_placed_command_id";
const AGENT_CREATABLE_NODE_TYPES = new Set<CanvasNodeType>(Object.values(CANVAS_NODE_TYPES));

function markAgentViewportPlacement(nodeId: string, commandId: string): void {
  if (!commandId) return;
  useCanvasStore.getState().updateNodeData(nodeId, {
    [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: commandId,
  } as Partial<CanvasNodeData>);
}

function reconcileExistingAgentCreatedNodePosition(
  nodeId: string,
  command: Pick<StructureOperation, "x" | "y" | "placement">,
  envelope: CanvasAgentCommandEnvelope,
  index: number,
  offset: { x?: number; y?: number } = {},
): void {
  const state = useCanvasStore.getState();
  const node = state.nodes.find((item) => item.id === nodeId);
  if (!node) return;
  const commandId = String(envelope.command_id || "");
  const data = (node.data ?? {}) as Record<string, unknown>;
  const alreadyPlacedForThisCommand = Boolean(commandId)
    && data[AGENT_VIEWPORT_PLACED_COMMAND_KEY] === commandId;
  const hasExplicitPosition = finiteNumber(command.x) || finiteNumber(command.y);
  if (alreadyPlacedForThisCommand && !hasExplicitPosition) return;

  const position = resolveAgentCommandPosition(command, index);
  state.setNodePositions({
    [nodeId]: {
      x: position.x + (offset.x ?? 0),
      y: position.y + (offset.y ?? 0),
    },
  });
  markAgentViewportPlacement(nodeId, commandId);
}

function resolveAgentCanvasNodeType(command: StructureOperation): CanvasNodeType | null {
  const rawType = String(command.node_type || "").trim();
  if (!rawType) return null;
  return AGENT_CREATABLE_NODE_TYPES.has(rawType as CanvasNodeType)
    ? rawType as CanvasNodeType
    : null;
}

/**
 * `update_node_data` 里的运镜旧键（`cameraMovement` / `camera_movement`）折进提示词。
 *
 * 运镜只存在提示词那一段里（T-154）；留在节点字段上会被当成预设 id 提交出去，后端
 * 直接 400 —— 这正是脚本派生那批节点坏掉的原因。
 */
function foldCameraMovementIntoNodeData(
  rawNodeData: unknown,
  currentData: Record<string, unknown>,
): Record<string, unknown> {
  const data = { ...((rawNodeData ?? {}) as Record<string, unknown>) };
  const legacy = data.cameraMovement ?? data.camera_movement;
  delete data.cameraMovement;
  delete data.camera_movement;
  if (legacy === undefined || legacy === null) return data;
  const cameraText = cameraDirectionTextFromCommand(legacy);
  if (!cameraText) return data;
  const currentPrompt = typeof currentData.prompt === "string" ? currentData.prompt : "";
  return { ...data, prompt: setCameraDirection(currentPrompt, cameraText) };
}

function agentGenericNodeData(
  command: StructureOperation,
  nodeType: CanvasNodeType,
  envelope: CanvasAgentCommandEnvelope,
): Partial<CanvasNodeData> {
  const text = String(command.text || command.prompt || "").trim();
  const declaredData = command.node_data ?? {};
  const base: Partial<CanvasNodeData> = {
    ...declaredData,
    displayName: command.display_name
      || (typeof declaredData.displayName === "string" ? declaredData.displayName : "Agent 工作流节点"),
    agent_command_id: envelope.command_id || "",
    [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id || "",
  };

  if (nodeType === CANVAS_NODE_TYPES.imageGen || nodeType === CANVAS_NODE_TYPES.video) {
    const promptWithCamera = agentPromptWithCameraMovement(command, text);
    return {
      ...agentNodeParameters(command, nodeType),
      ...(promptWithCamera
        ? { prompt: promptWithCamera, compiledPromptPreview: promptWithCamera }
        : {}),
      ...base,
      canvas_auto_generate_once: false,
    } as Partial<CanvasNodeData>;
  }

  if (nodeType === CANVAS_NODE_TYPES.imageEdit) {
    return {
      prompt: text,
      model: command.model,
      ...base,
      canvas_auto_generate_once: false,
    } as Partial<CanvasNodeData>;
  }

  if (
    nodeType === CANVAS_NODE_TYPES.textAnnotation
    || nodeType === CANVAS_NODE_TYPES.beatContext
  ) {
    return { content: text, ...base } as Partial<CanvasNodeData>;
  }

  if (nodeType === CANVAS_NODE_TYPES.script || nodeType === CANVAS_NODE_TYPES.threeDWorld) {
    return {
      prompt: text,
      ...(command.model ? { model: command.model } : {}),
      ...base,
      canvas_auto_generate_once: false,
    } as Partial<CanvasNodeData>;
  }

  if (nodeType === CANVAS_NODE_TYPES.audio) {
    return {
      text,
      ...(command.model ? { model: command.model } : {}),
      ...base,
      canvas_auto_generate_once: false,
    } as Partial<CanvasNodeData>;
  }

  if (nodeType === CANVAS_NODE_TYPES.skill) {
    return {
      ...base,
      ...(command.skill_id ? { skill_id: command.skill_id } : {}),
      parameters: command.node_data?.parameters ?? {},
    } as Partial<CanvasNodeData>;
  }

  return base;
}

const AGENT_BATCH_OPERATION_TYPES = new Set<string>([
  "focus_node",
  "select_node",
  "delete_node",
  "connect_nodes",
  "remove_edge",
  "annotate",
  "create_canvas_node",
  "create_image_prompt_node",
  "create_video_prompt_node",
  "create_shot_sequence",
  "update_node_prompt",
  "update_node_label",
  "update_node_data",
  "move_node",
]);

function mirrorAgentSelection(
  nodes: CanvasNode[],
  selectedNodeId: string | null,
): CanvasNode[] {
  let changed = false;
  const next = nodes.map((node) => {
    const selected = node.id === selectedNodeId;
    if (Boolean(node.selected) === selected) return node;
    changed = true;
    return { ...node, selected };
  });
  return changed ? next : nodes;
}

function setBatchedNodeData(
  nodes: CanvasNode[],
  nodeId: string,
  data: Partial<CanvasNodeData>,
): CanvasNode[] {
  return nodes.map((node) => node.id === nodeId
    ? { ...node, data: { ...node.data, ...data } as CanvasNodeData }
    : node);
}

function setBatchedNodePosition(
  nodes: CanvasNode[],
  nodeId: string,
  position: { x: number; y: number },
): CanvasNode[] {
  return nodes.map((node) => node.id === nodeId ? { ...node, position } : node);
}

function appendBatchedEdge(
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
  command: CanvasAgentOperation,
): { edges: CanvasEdge[]; accepted: boolean } {
  const source = String(command.source || "").trim();
  const target = String(command.target || "").trim();
  const sourceNode = nodes.find((node) => node.id === source);
  const targetNode = nodes.find((node) => node.id === target);
  if (!sourceNode || !targetNode || !nodeHasSourceHandle(sourceNode.type) || !nodeHasTargetHandle(targetNode.type)) {
    return { edges: [...edges], accepted: false };
  }
  if (!isUpstreamConnectionAllowed(sourceNode.type, targetNode.type)) {
    return { edges: [...edges], accepted: false };
  }
  const detailed = Boolean(
    command.edge_data
    || command.source_handle
    || command.target_handle
    || command.created_edge_id,
  );
  const edgeId = detailed
    ? command.created_edge_id || `e-${source}-${target}-${String(command.edge_data?.edgeKind || "data")}`
    : `e-${source}-${target}`;
  if (edges.some((edge) => edge.id === edgeId)) {
    // Match store.addEdge*: a known id is an idempotent successful operation.
    return { edges: [...edges], accepted: true };
  }
  const candidate: CanvasEdge = {
    id: edgeId,
    source,
    target,
    sourceHandle: String(command.source_handle || "").trim() || "source",
    targetHandle: String(command.target_handle || "").trim() || "target",
    type: "disconnectableEdge",
    ...(command.edge_data ? { data: command.edge_data } : {}),
  };
  if (!validatePropagatingEdgeCandidate([...nodes], [...edges], candidate).ok) {
    return { edges: [...edges], accepted: false };
  }
  if (!validateCandidateBindingRoleCandidate([...edges], candidate).ok) {
    return { edges: [...edges], accepted: false };
  }
  return { edges: [...edges, candidate], accepted: true };
}

/**
 * Apply the common Agent command batch with one store commit. The legacy
 * executor remains the fallback for specialized operations whose semantics are
 * richer than a graph transaction (starter workflows and cloning).
 */
function applyBatchedStructureEnvelopeToStore(
  envelope: CanvasAgentCommandEnvelope,
): number | null {
  const commands = envelope.commands.slice(0, 20);
  if (commands.length === 0 || !commands.every((command) => AGENT_BATCH_OPERATION_TYPES.has(command.type))) {
    return null;
  }

  const initial = useCanvasStore.getState();
  let nodes = initial.nodes;
  let edges = initial.edges;
  let selectedNodeId = initial.selectedNodeId;
  let focusNodeId: string | null = null;
  let contentChanged = false;
  let applied = 0;
  let createPlacementIndex = 0;
  const createdIds: string[] = [];

  const select = (nodeId: string, focus = false) => {
    selectedNodeId = nodeId;
    if (focus) focusNodeId = nodeId;
  };
  const existingNode = (nodeId: string) => nodes.find((node) => node.id === nodeId);
  const connect = (
    source: string,
    target: string,
    command?: CanvasAgentOperation,
  ) => {
    const next = appendBatchedEdge(
      nodes,
      edges,
      command ?? { type: "connect_nodes", source, target },
    );
    if (next.edges.length !== edges.length) contentChanged = true;
    edges = next.edges;
    return next.accepted;
  };
  const reconcileExisting = (
    nodeId: string,
    command: CanvasAgentOperation,
    index: number,
    offset: { x?: number; y?: number } = {},
  ) => {
    const node = existingNode(nodeId);
    if (!node) return false;
    const data = (node.data ?? {}) as Record<string, unknown>;
    const alreadyPlaced = data[AGENT_VIEWPORT_PLACED_COMMAND_KEY] === envelope.command_id;
    const hasExplicitPosition = finiteNumber(command.x) || finiteNumber(command.y);
    if (!alreadyPlaced || hasExplicitPosition) {
      const position = resolveAgentCommandPosition(command, index);
      nodes = setBatchedNodePosition(nodes, nodeId, {
        x: position.x + (offset.x ?? 0),
        y: position.y + (offset.y ?? 0),
      });
      nodes = setBatchedNodeData(nodes, nodeId, {
        [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id,
      } as Partial<CanvasNodeData>);
      contentChanged = true;
    }
    select(nodeId, true);
    createdIds.push(nodeId);
    return true;
  };
  const addNode = (
    nodeType: CanvasNodeType,
    position: { x: number; y: number },
    data: Partial<CanvasNodeData>,
    preferredId?: string | null,
  ) => {
    const existingId = String(preferredId || "").trim();
    if (existingId && existingNode(existingId)) return existingId;
    const created = createCanvasNodeForDataEdit(nodeType, position, data);
    const nodeId = existingId || created.id;
    nodes = [...nodes, nodeId === created.id ? created : { ...created, id: nodeId }];
    contentChanged = true;
    return nodeId;
  };

  for (const command of commands) {
    if (command.type === "focus_node" || command.type === "select_node") {
      const nodeId = String(command.node_id || "").trim();
      if (!existingNode(nodeId)) continue;
      select(nodeId, command.type === "focus_node");
      applied += 1;
      continue;
    }
    if (command.type === "connect_nodes") {
      if (!connect(String(command.source || ""), String(command.target || ""), command)) continue;
      applied += 1;
      continue;
    }
    if (command.type === "remove_edge") {
      const edge = command.edge_id
        ? edges.find((item) => item.id === command.edge_id)
        : edges.find((item) => item.source === command.source && item.target === command.target);
      if (!edge) continue;
      edges = edges.filter((item) => item.id !== edge.id);
      contentChanged = true;
      applied += 1;
      continue;
    }
    if (command.type === "delete_node") {
      const nodeId = String(command.node_id || "").trim();
      if (!existingNode(nodeId)) continue;
      const next = removeCanvasNodesForDataEdit(nodes, edges, [nodeId]);
      if (next.deletedIds.size === 0) continue;
      nodes = next.nodes;
      edges = next.edges;
      if (selectedNodeId && next.deletedIds.has(selectedNodeId)) selectedNodeId = null;
      if (focusNodeId && next.deletedIds.has(focusNodeId)) focusNodeId = null;
      contentChanged = true;
      applied += 1;
      continue;
    }
    if (command.type === "annotate") {
      const index = createPlacementIndex++;
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && reconcileExisting(preferredId, command, index)) {
        applied += 1;
        continue;
      }
      const nodeId = addNode(
        CANVAS_NODE_TYPES.textAnnotation,
        resolveAgentCommandPosition(command, index),
        {
          text: command.text,
          displayName: command.display_name || "Agent 导演备注",
          agent_command_id: envelope.command_id,
          [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id,
        } as Partial<CanvasNodeData>,
        preferredId,
      );
      select(nodeId, true);
      createdIds.push(nodeId);
      applied += 1;
      continue;
    }
    if (command.type === "create_canvas_node") {
      const nodeType = resolveAgentCanvasNodeType(command);
      if (!nodeType) continue;
      const index = createPlacementIndex++;
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && reconcileExisting(preferredId, command, index)) {
        applied += 1;
        continue;
      }
      const selectedBeforeCreate = selectedNodeId ? existingNode(selectedNodeId) : null;
      const nodeId = addNode(
        nodeType,
        resolveAgentCommandPosition(command, index),
        agentGenericNodeData(command, nodeType, envelope),
        preferredId,
      );
      if (selectedBeforeCreate && command.connect_selected !== false) {
        connect(selectedBeforeCreate.id, nodeId);
      }
      select(nodeId, true);
      createdIds.push(nodeId);
      applied += 1;
      continue;
    }
    if (command.type === "create_image_prompt_node" || command.type === "create_video_prompt_node") {
      if (!command.prompt) continue;
      const index = createPlacementIndex++;
      const preferredId = String(command.created_node_id || "").trim();
      const selectedBeforeCreate = selectedNodeId ? existingNode(selectedNodeId) : null;
      if (preferredId && existingNode(preferredId)) {
        if (selectedBeforeCreate && selectedBeforeCreate.id !== preferredId) {
          connect(selectedBeforeCreate.id, preferredId);
        }
        if (reconcileExisting(preferredId, command, index)) applied += 1;
        continue;
      }
      const nodeType = command.type === "create_video_prompt_node"
        ? CANVAS_NODE_TYPES.video
        : CANVAS_NODE_TYPES.imageGen;
      // 运镜归口提示词（T-154）：命令给的运镜折进提示词的运镜段，不写节点字段。
      const promptWithCamera = agentPromptWithCameraMovement(command, command.prompt);
      const nodeId = addNode(nodeType, resolveAgentCommandPosition(command, index), {
        ...agentNodeParameters(command, nodeType),
        prompt: promptWithCamera,
        compiledPromptPreview: promptWithCamera,
        displayName: command.display_name || (command.type === "create_video_prompt_node" ? "Agent 视频方案" : "Agent 图片方案"),
        canvas_auto_generate_once: false,
        agent_command_id: envelope.command_id,
        [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id,
      } as Partial<CanvasNodeData>, preferredId);
      if (selectedBeforeCreate && command.connect_selected !== false) {
        connect(selectedBeforeCreate.id, nodeId);
      }
      select(nodeId, true);
      createdIds.push(nodeId);
      applied += 1;
      continue;
    }
    if (command.type === "create_shot_sequence") {
      const prompts = Array.isArray(command.prompts) ? command.prompts.filter((item) => String(item || "").trim()) : [];
      if (prompts.length === 0) continue;
      const index = createPlacementIndex++;
      const position = resolveAgentCommandPosition(command, index);
      const preferredIds = Array.isArray(command.created_node_ids)
        ? command.created_node_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : [];
      let previousId = command.connect_selected !== false
        && selectedNodeId
        && existingNode(selectedNodeId)
        ? selectedNodeId
        : null;
      let lastId: string | null = null;
      prompts.forEach((prompt, promptIndex) => {
        const preferredId = preferredIds[promptIndex] || "";
        if (preferredId && existingNode(preferredId)) {
          reconcileExisting(preferredId, command, index, { x: promptIndex * 40, y: promptIndex * AGENT_SEQUENCE_NODE_GAP_Y });
          if (previousId) {
            connect(previousId, preferredId);
          }
          previousId = preferredId;
          lastId = preferredId;
          applied += 1;
          return;
        }
        const nodeId = addNode(CANVAS_NODE_TYPES.imageGen, {
          x: position.x + promptIndex * 40,
          y: position.y + promptIndex * AGENT_SEQUENCE_NODE_GAP_Y,
        }, {
          ...agentNodeParameters(command, CANVAS_NODE_TYPES.imageGen),
          prompt,
          compiledPromptPreview: prompt,
          displayName: command.display_name ? `${command.display_name} · 镜${promptIndex + 1}` : `Agent 分镜 ${promptIndex + 1}`,
          canvas_auto_generate_once: false,
          agent_command_id: envelope.command_id,
          [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id,
        } as Partial<CanvasNodeData>, preferredId);
        if (previousId) {
          connect(previousId, nodeId);
        }
        previousId = nodeId;
        lastId = nodeId;
        createdIds.push(nodeId);
        applied += 1;
      });
      if (lastId) select(lastId, true);
      continue;
    }
    if (command.type === "update_node_prompt") {
      const nodeId = String(command.node_id || "").trim();
      const targetNode = nodes.find((node) => node.id === nodeId);
      if (!targetNode) continue;
      nodes = setBatchedNodeData(nodes, nodeId, {
        prompt: command.prompt,
        compiledPromptPreview: command.prompt,
        ...(targetNode.type === CANVAS_NODE_TYPES.textAnnotation
          ? { text: command.prompt, content: command.prompt }
          : {}),
        ...(command.display_name ? { displayName: command.display_name } : {}),
        agent_command_id: envelope.command_id,
      } as Partial<CanvasNodeData>);
      select(nodeId, true);
      contentChanged = true;
      applied += 1;
      continue;
    }
    if (command.type === "update_node_label") {
      const nodeId = String(command.node_id || "").trim();
      if (!existingNode(nodeId) || !command.display_name?.trim()) continue;
      nodes = setBatchedNodeData(nodes, nodeId, {
        displayName: command.display_name,
        agent_command_id: envelope.command_id,
      } as Partial<CanvasNodeData>);
      select(nodeId);
      contentChanged = true;
      applied += 1;
      continue;
    }
    if (command.type === "update_node_data") {
      const nodeId = String(command.node_id || "").trim();
      const currentNode = existingNode(nodeId);
      if (!currentNode) continue;
      const currentData = (currentNode.data ?? {}) as Record<string, unknown>;
      if (envelope.command_id && currentData.agent_command_id === envelope.command_id) {
        applied += 1;
        continue;
      }
      nodes = setBatchedNodeData(nodes, nodeId, {
        ...foldCameraMovementIntoNodeData(command.node_data, currentData),
        agent_command_id: envelope.command_id,
      } as Partial<CanvasNodeData>);
      select(nodeId);
      contentChanged = true;
      applied += 1;
      continue;
    }
    if (command.type === "move_node") {
      const nodeId = String(command.node_id || "").trim();
      const node = existingNode(nodeId);
      if (!node) continue;
      nodes = setBatchedNodePosition(nodes, nodeId, {
        x: finiteNumber(command.x) ? command.x : node.position.x + 40,
        y: finiteNumber(command.y) ? command.y : node.position.y + 40,
      });
      select(nodeId);
      contentChanged = true;
      applied += 1;
    }
  }

  if (applied > 0) {
    const selectionNodes = mirrorAgentSelection(nodes, selectedNodeId);
    const selectionChanged = selectionNodes !== nodes;
    nodes = selectionNodes;
    if (contentChanged) {
      useCanvasStore.getState().applyCanvasDataEdit(nodes, edges, {
        selectedNodeId,
        focusNodeId,
      });
    } else if (selectionChanged || selectedNodeId !== initial.selectedNodeId || focusNodeId) {
      useCanvasStore.setState({
        nodes,
        selectedNodeId,
        ...(focusNodeId ? { pendingFocusNodeId: focusNodeId } : {}),
      });
    }
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(CANVAS_AGENT_APPLIED_EVENT, {
        detail: {
          coupling: CANVAS_AGENT_COUPLING_VERSION,
          commandId: envelope.command_id,
          applied,
          createdIds,
          selectedNodeId,
        },
      }));
    }
  }
  return applied;
}

/** Apply one validated structure envelope to the canvas store. Returns applied op count. */
export function applyStructureEnvelopeToStore(
  envelope: CanvasAgentCommandEnvelope,
  scope?: { projectId: string; canvasId: string },
): number {
  const batched = applyBatchedStructureEnvelopeToStore(envelope);
  if (batched !== null) return batched;
  const initialStore = useCanvasStore.getState();
  const initialPastLength = initialStore.history.past.length;
  const pinnedNodeIds = scope
    ? loadPinnedCanvasNodeIds(scope.projectId, scope.canvasId)
    : [];
  const resolved = resolveStructureEnvelopeTargets(envelope, {
    selectedNodeId: initialStore.selectedNodeId,
    pinnedNodeIds,
  });
  let applied = 0;
  let createPlacementIndex = 0;
  const createdIds: string[] = [];
  for (const command of resolved.commands.slice(0, 20)) {
    // Every operation must observe nodes/edges created by the preceding
    // operation in the same transaction. Holding the first Zustand snapshot
    // here made create→connect graph patches silently drop their edges.
    const store = useCanvasStore.getState();
    if (command.type === "focus_node" || command.type === "select_node") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id)) continue;
      store.setSelectedNode(command.node_id);
      if (command.type === "focus_node") store.requestFocusNode(command.node_id);
      applied += 1;
      continue;
    }
    if (command.type === "connect_nodes") {
      if (!command.source || !command.target) continue;
      if (!store.nodes.some((node) => node.id === command.source) || !store.nodes.some((node) => node.id === command.target)) continue;
      const edgeId = command.edge_data
        || command.source_handle
        || command.target_handle
        || command.created_edge_id
        ? store.addEdgeWithData(
            command.source,
            command.target,
            command.edge_data ?? {},
            {
              ...(command.created_edge_id ? { id: command.created_edge_id } : {}),
              ...(command.source_handle ? { sourceHandle: command.source_handle } : {}),
              ...(command.target_handle ? { targetHandle: command.target_handle } : {}),
            },
          )
        : store.addEdge(command.source, command.target);
      if (edgeId) applied += 1;
      continue;
    }
    if (command.type === "remove_edge") {
      const edge = command.edge_id
        ? store.edges.find((item) => item.id === command.edge_id)
        : store.edges.find((item) => item.source === command.source && item.target === command.target);
      if (!edge) continue;
      store.deleteEdge(edge.id);
      applied += 1;
      continue;
    }
    if (command.type === "annotate") {
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && useCanvasStore.getState().nodes.some((node) => node.id === preferredId)) {
        reconcileExistingAgentCreatedNodePosition(preferredId, command, envelope, createPlacementIndex);
        createPlacementIndex += 1;
        store.setSelectedNode(preferredId);
        store.requestFocusNode(preferredId);
        createdIds.push(preferredId);
        applied += 1;
        continue;
      }
      const position = resolveAgentCommandPosition(command, createPlacementIndex);
      createPlacementIndex += 1;
      const tempId = store.addNode(CANVAS_NODE_TYPES.textAnnotation, {
        x: position.x,
        y: position.y,
      }, {
        text: command.text,
        displayName: command.display_name || "Agent 导演备注",
        agent_command_id: envelope.command_id || "",
        [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id || "",
      } as Partial<CanvasNodeData>);
      const nodeId = adoptCreatedNodeId(tempId, preferredId);
      if (nodeId) {
        createdIds.push(nodeId);
        store.setSelectedNode(nodeId);
        store.requestFocusNode(nodeId);
      }
      applied += 1;
      continue;
    }
    if (command.type === "insert_starter_workflow") {
      const workflowId = String(command.workflow_id || "").trim() as CanvasStarterWorkflowId;
      if (!workflowId) continue;
      const position = resolveAgentCommandPosition(command, createPlacementIndex);
      createPlacementIndex += 1;
      const result = store.addStarterWorkflow(workflowId, position);
      if (!result) continue;
      result.nodeIds.forEach((nodeId) => {
        store.updateNodeData(nodeId, {
          agent_command_id: envelope.command_id || "",
          [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id || "",
        } as Partial<CanvasNodeData>);
      });
      const lastNodeId = result.nodeIds[result.nodeIds.length - 1];
      if (lastNodeId) {
        store.setSelectedNode(lastNodeId);
        store.requestFocusNode(lastNodeId);
      }
      createdIds.push(...result.nodeIds);
      applied += 1;
      continue;
    }
    if (command.type === "create_canvas_node") {
      const nodeType = resolveAgentCanvasNodeType(command);
      if (!nodeType) continue;
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && useCanvasStore.getState().nodes.some((node) => node.id === preferredId)) {
        reconcileExistingAgentCreatedNodePosition(preferredId, command, envelope, createPlacementIndex);
        createPlacementIndex += 1;
        store.setSelectedNode(preferredId);
        store.requestFocusNode(preferredId);
        createdIds.push(preferredId);
        applied += 1;
        continue;
      }
      const selected = store.nodes.find((node) => node.id === store.selectedNodeId);
      const position = resolveAgentCommandPosition(command, createPlacementIndex);
      createPlacementIndex += 1;
      const tempId = store.addNode(nodeType, position, agentGenericNodeData(command, nodeType, envelope));
      const nodeId = adoptCreatedNodeId(tempId, preferredId);
      if (selected && command.connect_selected !== false) store.addEdge(selected.id, nodeId);
      store.setSelectedNode(nodeId);
      store.requestFocusNode(nodeId);
      createdIds.push(nodeId);
      applied += 1;
      continue;
    }
    if (command.type === "create_image_prompt_node" || command.type === "create_video_prompt_node") {
      if (!command.prompt) continue;
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && useCanvasStore.getState().nodes.some((node) => node.id === preferredId)) {
        const selected = useCanvasStore.getState().nodes.find((node) => node.id === useCanvasStore.getState().selectedNodeId);
        if (selected && selected.id !== preferredId) store.addEdge(selected.id, preferredId);
        reconcileExistingAgentCreatedNodePosition(preferredId, command, envelope, createPlacementIndex);
        createPlacementIndex += 1;
        store.setSelectedNode(preferredId);
        store.requestFocusNode(preferredId);
        createdIds.push(preferredId);
        applied += 1;
        continue;
      }
      const selected = store.nodes.find((node) => node.id === store.selectedNodeId);
      const nodeType = command.type === "create_video_prompt_node"
        ? CANVAS_NODE_TYPES.video
        : CANVAS_NODE_TYPES.imageGen;
      const position = resolveAgentCommandPosition(command, createPlacementIndex);
      createPlacementIndex += 1;
      const promptWithCamera = agentPromptWithCameraMovement(command, command.prompt);
      const tempId = store.addNode(nodeType, {
        x: position.x,
        y: position.y,
      }, {
        ...agentNodeParameters(command, nodeType),
        prompt: promptWithCamera,
        compiledPromptPreview: promptWithCamera,
        displayName: command.display_name || (command.type === "create_video_prompt_node" ? "Agent 视频方案" : "Agent 图片方案"),
        canvas_auto_generate_once: false,
        agent_command_id: envelope.command_id || "",
        [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id || "",
      } as Partial<CanvasNodeData>);
      const nodeId = adoptCreatedNodeId(tempId, preferredId);
      if (selected) store.addEdge(selected.id, nodeId);
      store.setSelectedNode(nodeId);
      store.requestFocusNode(nodeId);
      createdIds.push(nodeId);
      applied += 1;
      continue;
    }
    if (command.type === "create_shot_sequence") {
      const prompts = Array.isArray(command.prompts) ? command.prompts.filter((item) => String(item || "").trim()) : [];
      if (prompts.length === 0) continue;
      const selected = store.nodes.find((node) => node.id === store.selectedNodeId);
      const position = resolveAgentCommandPosition(command, createPlacementIndex);
      createPlacementIndex += 1;
      const baseX = position.x;
      const baseY = position.y;
      const preferredIds = Array.isArray(command.created_node_ids)
        ? command.created_node_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : [];
      let previousId = selected?.id ?? null;
      let lastId: string | null = null;
      prompts.forEach((prompt, index) => {
        const preferredId = preferredIds[index] || "";
        if (preferredId && useCanvasStore.getState().nodes.some((node) => node.id === preferredId)) {
          reconcileExistingAgentCreatedNodePosition(preferredId, command, envelope, createPlacementIndex - 1, {
            x: index * 40,
            y: index * AGENT_SEQUENCE_NODE_GAP_Y,
          });
          if (previousId) store.addEdge(previousId, preferredId);
          previousId = preferredId;
          lastId = preferredId;
          createdIds.push(preferredId);
          applied += 1;
          return;
        }
        const tempId = store.addNode(CANVAS_NODE_TYPES.imageGen, {
          x: baseX + index * 40,
          y: baseY + index * AGENT_SEQUENCE_NODE_GAP_Y,
        }, {
          ...agentNodeParameters(command, CANVAS_NODE_TYPES.imageGen),
          prompt,
          compiledPromptPreview: prompt,
          displayName: command.display_name
            ? `${command.display_name} · 镜${index + 1}`
            : `Agent 分镜 ${index + 1}`,
          canvas_auto_generate_once: false,
          agent_command_id: envelope.command_id || "",
          [AGENT_VIEWPORT_PLACED_COMMAND_KEY]: envelope.command_id || "",
        } as Partial<CanvasNodeData>);
        const nodeId = adoptCreatedNodeId(tempId, preferredId);
        if (previousId) store.addEdge(previousId, nodeId);
        previousId = nodeId;
        lastId = nodeId;
        createdIds.push(nodeId);
        applied += 1;
      });
      if (lastId) {
        store.setSelectedNode(lastId);
        store.requestFocusNode(lastId);
      }
      continue;
    }
    if (command.type === "update_node_prompt") {
      const targetNode = store.nodes.find((node) => node.id === command.node_id);
      if (!command.node_id || !targetNode) continue;
      store.updateNodeData(command.node_id, {
        prompt: command.prompt,
        compiledPromptPreview: command.prompt,
        ...(targetNode.type === CANVAS_NODE_TYPES.textAnnotation
          ? { text: command.prompt, content: command.prompt }
          : {}),
        ...(command.display_name ? { displayName: command.display_name } : {}),
        agent_command_id: envelope.command_id || "",
      } as Partial<CanvasNodeData>);
      store.setSelectedNode(command.node_id);
      store.requestFocusNode(command.node_id);
      applied += 1;
      continue;
    }
    if (command.type === "update_node_label") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id) || !command.display_name?.trim()) continue;
      store.updateNodeData(command.node_id, {
        displayName: command.display_name,
        agent_command_id: envelope.command_id || "",
      } as Partial<CanvasNodeData>);
      store.setSelectedNode(command.node_id);
      applied += 1;
      continue;
    }
    if (command.type === "update_node_data") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id)) continue;
      const currentNode = store.nodes.find((node) => node.id === command.node_id);
      const currentData = (currentNode?.data ?? {}) as Record<string, unknown>;
      if (envelope.command_id && currentData.agent_command_id === envelope.command_id) {
        applied += 1;
        continue;
      }
      store.updateNodeData(command.node_id, {
        ...foldCameraMovementIntoNodeData(command.node_data, currentData),
        agent_command_id: envelope.command_id || "",
      } as Partial<CanvasNodeData>);
      store.setSelectedNode(command.node_id);
      applied += 1;
      continue;
    }
    if (command.type === "move_node") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id)) continue;
      const current = store.nodes.find((node) => node.id === command.node_id);
      if (!current) continue;
      store.setNodePositions({
        [command.node_id]: {
          x: typeof command.x === "number" ? command.x : current.position.x + 40,
          y: typeof command.y === "number" ? command.y : current.position.y + 40,
        },
      });
      store.setSelectedNode(command.node_id);
      applied += 1;
      continue;
    }
    if (command.type === "duplicate_node") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id)) continue;
      const preferredId = String(command.created_node_id || "").trim();
      if (preferredId && useCanvasStore.getState().nodes.some((node) => node.id === preferredId)) {
        store.setSelectedNode(preferredId);
        store.requestFocusNode(preferredId);
        createdIds.push(preferredId);
        applied += 1;
        continue;
      }
      const created = store.duplicateNodesAsSiblings([command.node_id]);
      if (created[0]) {
        const nodeId = adoptCreatedNodeId(created[0], preferredId);
        store.updateNodeData(nodeId, {
          agent_command_id: envelope.command_id || "",
        } as Partial<CanvasNodeData>);
        store.setSelectedNode(nodeId);
        store.requestFocusNode(nodeId);
        createdIds.push(nodeId);
        applied += 1;
      }
      continue;
    }
    if (command.type === "delete_node") {
      if (!command.node_id || !store.nodes.some((node) => node.id === command.node_id)) continue;
      store.deleteNode(command.node_id);
      applied += 1;
    }
  }
  if (applied > 0) {
    const state = useCanvasStore.getState();
    const selectedNodeId = state.selectedNodeId
      && state.nodes.some((node) => node.id === state.selectedNodeId)
      ? state.selectedNodeId
      : null;
    const selectionNodes = mirrorAgentSelection(state.nodes, selectedNodeId);
    if (selectionNodes !== state.nodes || selectedNodeId !== state.selectedNodeId) {
      useCanvasStore.setState({
        nodes: selectionNodes,
        selectedNodeId,
      });
    }
  }
  if (applied > 0 && typeof window !== "undefined") {
    const nextHistory = useCanvasStore.getState().history;
    if (nextHistory.past.length > initialPastLength) {
      const transactionSnapshot = nextHistory.past[initialPastLength];
      useCanvasStore.setState({
        history: {
          past: transactionSnapshot
            ? [...nextHistory.past.slice(0, initialPastLength), transactionSnapshot]
            : nextHistory.past.slice(0, initialPastLength),
          future: [],
        },
      });
    }
    window.dispatchEvent(
      new CustomEvent(CANVAS_AGENT_APPLIED_EVENT, {
        detail: {
          coupling: CANVAS_AGENT_COUPLING_VERSION,
          commandId: envelope.command_id,
          applied,
          createdIds,
          selectedNodeId: useCanvasStore.getState().selectedNodeId,
        },
      }),
    );
  }
  return applied;
}

export function FreezoneShell({ project, canvasId }: FreezoneShellProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const projectId = project.id;
  const [pushState, setPushState] = useState<PushPrompt | null>(null);
  const [createIdentitySource, setCreateIdentitySource] =
    useState<SelectedImageSummary | null>(null);
  const [maskTarget, setMaskTarget] = useState<{
    url: string;
    label: string;
  } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [assetLibraryReloadToken, setAssetLibraryReloadToken] = useState(0);
  // Project-centre handoff: an idea typed on the home hero (and optionally a
  // starter route) is waiting for this canvas. Peeked during render, then
  // consumed in an effect so StrictMode's double render cannot eat it.
  const [homeHandoff] = useState(() => peekHomeCanvasHandoff(projectId));
  // The canvas is the primary workspace, so Agent starts collapsed.
  const [chatOpen, setChatOpen] = useState(
    () => resolveInitialFreezoneAgentOpen(canvasOnlyProduct) || Boolean(homeHandoff?.draft),
  );
  const executedCanvasCommandIdsRef = useRef(new Set<string>());
  const [canvasView, setCanvasView] = useState<"workflow" | "storyboard">("workflow");
  const activePowerTool = useNodePowerHubStore((state) => state.activeTool);
  const powerToolNodeId = useNodePowerHubStore((state) => state.nodeId);
  const closePowerTool = useNodePowerHubStore((state) => state.close);
  const selectedPowerNodeId = useCanvasStore((state) => state.selectedNodeId);
  // Personal CE is the primary product: its local canvas gets the full embedded Agent.
  const showChatDock = true;
  const handleChatOpenChange = useCallback((open: boolean) => {
    if (open) closePowerTool();
    setChatOpen(open);
  }, [closePowerTool]);
  const sync = useCanvasSync(projectId, canvasId);
  // Keep the command listener stable while the sync revision advances. A
  // revision update must not create a window in which a one-shot patch event
  // has no listener.
  const syncRevisionRef = useRef<number | null>(sync.revision);
  syncRevisionRef.current = sync.revision;
  const syncStatusRef = useRef(sync.status);
  syncStatusRef.current = sync.status;
  const syncFlushRef = useRef(sync.flush);
  syncFlushRef.current = sync.flush;
  const syncGetRevisionRef = useRef(sync.getRevision);
  syncGetRevisionRef.current = sync.getRevision;

  useEffect(() => {
    if (activePowerTool) setChatOpen(false);
  }, [activePowerTool]);

  // Consume the home handoff exactly once per entry. The startup route is laid
  // down only after the canvas has hydrated and only while it is still empty:
  // landing a starter graph on top of existing nodes would stack a stranger's
  // structure into work the user already did.
  const homeHandoffConsumedRef = useRef(false);
  const homeStarterAppliedRef = useRef(false);
  useEffect(() => {
    if (homeHandoffConsumedRef.current) return;
    homeHandoffConsumedRef.current = true;
    clearHomeCanvasHandoff();
  }, []);
  useEffect(() => {
    const workflowId = homeHandoff?.starterWorkflowId;
    if (!workflowId || homeStarterAppliedRef.current) return;
    if (sync.status === "loading") return;
    homeStarterAppliedRef.current = true;
    const store = useCanvasStore.getState();
    if (store.nodes.length > 0) return;
    const result = store.addStarterWorkflow(
      workflowId,
      semanticPlacementPosition({}, store, 0),
    );
    if (!result) {
      setToast("起步路线加载失败，可在画布左下角「起步器」里重选");
      return;
    }
    const lastNodeId = result.nodeIds[result.nodeIds.length - 1];
    if (lastNodeId) {
      store.setSelectedNode(lastNodeId);
      store.requestFocusNode(lastNodeId);
    }
  }, [homeHandoff?.starterWorkflowId, sync.status]);

  useEffect(() => {
    executedCanvasCommandIdsRef.current = loadCanvasAgentCommandIds(projectId, canvasId);
    const commandGateway = createCanvasCommandGateway({
      projectId,
      canvasId,
      executedIds: executedCanvasCommandIdsRef.current,
      currentRevision: () => syncRevisionRef.current,
      setToast,
      saveExecutedIds: (ids) => saveCanvasAgentCommandIds(projectId, canvasId, ids),
      optimisticGraphPresent: optimisticCanvasGraphPresent,
    });

    const runApply = async (
      envelope: CanvasAgentCommandEnvelope,
      meta?: { title?: string; fromProposal?: boolean; auto?: boolean },
    ): Promise<void> => {
      const commandId = envelope.command_id.trim();
      const attempt = commandGateway.nextAttempt(commandId);
      emitCanvasCommandReceipt({
        commandId,
        projectId,
        canvasId,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        attempt,
        stage: "progress",
        success: true,
      });
      const initialNodeIds = new Set(useCanvasStore.getState().nodes.map((node) => node.id));
      const beforeRevision = syncGetRevisionRef.current() ?? syncRevisionRef.current ?? null;
      let applied = 0;
      const requested = envelope.commands.length;
      try {
        applied = envelope.optimistic === true
          ? runOptimisticCanvasMutation(
              envelope,
              () => applyStructureEnvelopeToStore(envelope, { projectId, canvasId }),
            )
          : applyStructureEnvelopeToStore(envelope, { projectId, canvasId });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        emitCanvasCommandReceipt({
          commandId,
          projectId,
          canvasId,
          turnId: envelope.turn_id,
          revision: envelope.revision,
          attempt,
          stage: "result",
          success: false,
          applied: 0,
          requested,
          skipped: requested,
          error: message,
        });
        return;
      }
      const skipped = Math.max(0, requested - applied);
      if (applied > 0) {
        const undoSteps = 1;
        recordStructureApply({
          commandId,
          appliedCount: applied,
          undoSteps,
          title: meta?.title || `${applied} 个结构动作`,
          scope: { projectId, canvasId },
        });
        setToast(
          meta?.fromProposal
            ? `已应用结构提案 · ${applied} 步 · 可在导演台撤销`
            : meta?.auto
              ? `Agent 已落画布 · ${applied} 步 · ${CANVAS_AGENT_COUPLING_VERSION}`
              : `Agent 已执行 ${applied} 个导航动作`,
        );
        let persistedRevision = envelope.revision ?? syncRevisionRef.current ?? undefined;
        const shouldPersistBeforeReceipt = commandId.startsWith("workflow:")
          || (
            envelope.canvas_command_emitted === true
            && envelope.optimistic !== true
            && envelope.commands.some((command) => !["focus_node", "select_node"].includes(command.type))
          );
        if (shouldPersistBeforeReceipt) {
          const persisted = await persistAppliedCanvasCommand({
            beforeRevision,
            getStatus: () => syncStatusRef.current,
            getRevision: () => syncGetRevisionRef.current() ?? syncRevisionRef.current ?? undefined,
            flush: () => syncFlushRef.current(),
          });
          persistedRevision = persisted.revision ?? persistedRevision;
          if (!persisted.saved || !persistedRevision) {
            emitCanvasCommandReceipt({
              commandId,
              projectId,
              canvasId,
              turnId: envelope.turn_id,
              attempt,
              stage: "result",
              success: false,
              applied,
              requested,
              ...(skipped > 0 ? { skipped } : {}),
              error: "canvas_persistence_failed",
            });
            return;
          }
        }
        commandGateway.markExecuted(commandId);
        const appliedState = useCanvasStore.getState();
        emitCanvasCommandReceipt(buildCanvasCommandResultReceipt({
          commandId,
          projectId,
          canvasId,
          turnId: envelope.turn_id,
          revision: persistedRevision,
          attempt,
          applied,
          requested,
          ...(skipped > 0 ? { skipped } : {}),
          createdIds: appliedState.nodes
            .filter((node) => !initialNodeIds.has(node.id))
            .map((node) => node.id),
          selectedNodeId: appliedState.selectedNodeId,
          // 乐观预览无落盘 revision，不得报完成；完成信号由服务端 canvas.patch 给出。
          optimistic: envelope.optimistic === true,
        }));
      } else {
        setToast("结构命令没有可安全执行的动作（检查选中/固定节点是否存在）");
        emitCanvasCommandReceipt({
          commandId,
          projectId,
          canvasId,
          turnId: envelope.turn_id,
          revision: envelope.revision,
          attempt,
          stage: "result",
          success: false,
          applied: 0,
          requested,
          skipped: requested,
          error: "no_applicable_canvas_operation",
        });
      }
    };

    // Highest-authority coupling: every supported structure operation applies immediately.
    const onAgentCommand = (event: Event) => {
      const envelope = (event as CustomEvent<CanvasAgentCommandEnvelope>).detail;
      if (envelope?.command_id) {
        emitCanvasCommandReceipt({
          commandId: envelope.command_id,
          projectId: envelope.project_id || projectId,
          canvasId: envelope.canvas_id || canvasId,
          turnId: envelope.turn_id,
          revision: envelope.revision,
          stage: "ack",
          success: true,
        });
      }
      if (!commandGateway.reserve(envelope)) return;
      // Canvas Agent is the authoritative dispatcher for this canvas. Apply
      // the whole validated envelope in one local transaction so delete,
      // update, connect and create actions do not wait for a second approval
      // round. Unsupported operations are reported as skipped by runApply.
      void runApply(envelope as CanvasAgentCommandEnvelope, { auto: true })
        .finally(() => commandGateway.complete(envelope.command_id.trim()));
    };

    const onStructureApply = (event: Event) => {
      const detail = (event as CustomEvent<{
        commandId?: string;
        envelope?: CanvasAgentCommandEnvelope;
        proposal?: { title?: string };
      }>).detail;
      const commandId = detail?.commandId?.trim();
      if (!commandId) return;
      const proposal = takeStructureProposal(commandId, { projectId, canvasId });
      const envelope = proposal?.envelope ?? detail?.envelope;
      if (!envelope) {
        setToast("提案已失效或不存在");
        return;
      }
      if (!commandGateway.reserve(envelope)) return;
      void runApply(envelope as CanvasAgentCommandEnvelope, {
        fromProposal: true,
        title: proposal?.title || detail?.proposal?.title,
      }).finally(() => commandGateway.complete(envelope.command_id.trim()));
    };

    const onOptimisticRollback = (event: Event) => {
      const detail = (event as CustomEvent<{
        projectId?: string;
        canvasId?: string;
        commandId?: string;
      }>).detail;
      if (
        detail?.projectId !== projectId
        || detail.canvasId !== canvasId
        || !detail.commandId
      ) return;
      commandGateway.forgetExecuted(detail.commandId);
      acknowledgeCanvasAgentCommandEnvelope({
        project_id: projectId,
        canvas_id: canvasId,
        command_id: detail.commandId,
      });
    };

    window.addEventListener(CANVAS_AGENT_COMMAND_EVENT, onAgentCommand);
    window.addEventListener(STRUCTURE_APPLY_EVENT, onStructureApply);
    window.addEventListener(OPTIMISTIC_CANVAS_ROLLBACK_EVENT, onOptimisticRollback);
    replayPendingCanvasAgentCommands(projectId, canvasId);
    return () => {
      window.removeEventListener(CANVAS_AGENT_COMMAND_EVENT, onAgentCommand);
      window.removeEventListener(STRUCTURE_APPLY_EVENT, onStructureApply);
      window.removeEventListener(OPTIMISTIC_CANVAS_ROLLBACK_EVENT, onOptimisticRollback);
    };
  }, [canvasId, projectId]);

  useEffect(() => {
    if (!activePowerTool || !powerToolNodeId) return;
    if (selectedPowerNodeId !== powerToolNodeId) closePowerTool();
  }, [activePowerTool, closePowerTool, powerToolNodeId, selectedPowerNodeId]);

  // Re-entrancy guard for in-flight projection sync/remove lives in the refs;
  // there is no UI bound to a syncing/removing value, so no state is kept.
  const syncingProjectionRef = useRef<string | null>(null);
  const removingProjectionRef = useRef<string | null>(null);
  // 顶栏在「村长画布 / 镜头工艺」之间切换会整体卸载再挂载本组件，但画布数据留在全局 store 里。
  // 如果这里从 false 起步，回到村长画布就会先把画面换成「正在加载画布…」，等 hydrate 回来
  // 才重新画出来 —— 看着就是卡。同一个画布重进时直接渲染 store 里的既有内容，
  // hydrate 期间只叠一层轻量 overlay。
  const [hasRenderedCanvas, setHasRenderedCanvas] = useState(
    () =>
      lastRenderedCanvasKey === canvasKey(projectId, canvasId) &&
      useCanvasStore.getState().nodes.length > 0,
  );
  const [projectionStatusRefreshToken, setProjectionStatusRefreshToken] = useState(0);
  const [projectionMonitoringExpired, setProjectionMonitoringExpired] = useState(false);
  const lastProjectionStatusRevisionRef = useRef<{
    canvasId: string;
    revision: number;
    refreshToken: number;
  } | null>(null);

  const invalidateCommittedTargetQueries = useCallback((target: PushTarget) => {
    if (isDirectorWorldSourceSlotTarget(target) || target.kind === "scene_director_world") {
      queryClient.invalidateQueries({
        queryKey: queryKeys.sceneDirectorStageManifest(projectId, target.scene_id),
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.scenes(projectId) });
      return;
    }
    if (isScenePushTargetKind(target.kind) && "scene_id" in target) {
      queryClient.invalidateQueries({ queryKey: queryKeys.scenes(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.scene(projectId, target.scene_id) });
    }
  }, [projectId, queryClient]);
  const handleBlankPaneClick = useCallback(() => {
    setChatOpen(false);
    closePowerTool();
  }, [closePowerTool]);

  // Warm the shared image-model store the moment we enter a project, so the
  // request is in-flight before any picker / panel mounts.
  useEffect(() => {
    if (!showChatDock) {
      setChatOpen(false);
    }
  }, [showChatDock]);

  useEffect(() => {
    prefetchFreezoneImageModels(projectId);
    prefetchFreezoneVideoModels(projectId);
    prefetchFreezoneCameraOptions(projectId);
    prefetchFreezoneStyleTemplates(projectId);
    prefetchFreezoneVideoCameraTemplates(projectId);
  }, [projectId]);

  useEffect(() => {
    rememberLastCanvas(projectId, canvasId);
    if (canvasId !== "default" && currentCanvasParam() !== canvasId) {
      writeUrl({ canvas: canvasId }, { replace: true, notify: false });
    }
  }, [canvasId, projectId]);

  useEffect(() => {
    if (sync.status === "ready" && sync.hydratedCanvasId === canvasId) {
      lastRenderedCanvasKey = canvasKey(projectId, canvasId);
      setHasRenderedCanvas(true);
    }
  }, [canvasId, projectId, sync.hydratedCanvasId, sync.status]);

  const projectionKeys = useMemo(
    () => projectionKeysFromMetadata(sync.metadata),
    [sync.metadata],
  );
  useEffect(() => {
    if (projectionMonitoringExpired) return;
    if (!shouldFetchProjectionStatuses({
      canvasId,
      hydratedCanvasId: sync.hydratedCanvasId,
      projectionKeyCount: projectionKeys.length,
      revision: sync.revision,
      sessionExpired: projectionMonitoringExpired,
      syncStatus: sync.status,
    })) {
      return;
    }
    const bump = () => setProjectionStatusRefreshToken((value) => value + 1);
    return startProjectionStatusRefresh(bump, () => {
      setProjectionMonitoringExpired(true);
    });
  }, [
    canvasId,
    projectionMonitoringExpired,
    projectionKeys.length,
    sync.hydratedCanvasId,
    sync.revision,
    sync.status,
  ]);
  useEffect(() => {
    if (projectionMonitoringExpired) return;
    if (shouldClearProjectionStatuses({
      canvasId,
      hydratedCanvasId: sync.hydratedCanvasId,
      projectionKeyCount: projectionKeys.length,
    })) {
      clearCanvasProjectionStatuses();
      return;
    }
    const revision = sync.revision;
    if (!shouldFetchProjectionStatuses({
      canvasId,
      hydratedCanvasId: sync.hydratedCanvasId,
      projectionKeyCount: projectionKeys.length,
      revision,
      sessionExpired: projectionMonitoringExpired,
      syncStatus: sync.status,
    })) {
      return;
    }
    // shouldFetchProjectionStatuses already returns false when revision is null;
    // this redundant guard narrows the type for the non-null usages below.
    if (revision == null) {
      return;
    }
    if (shouldSkipProjectionStatusRevision({
      canvasId,
      revision,
      refreshToken: projectionStatusRefreshToken,
      lastChecked: lastProjectionStatusRevisionRef.current,
    })) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const result = await getProjectionStatuses(projectId, canvasId, projectionKeys);
        if (!cancelled) {
          lastProjectionStatusRevisionRef.current = {
            canvasId,
            revision,
            refreshToken: projectionStatusRefreshToken,
          };
          setCanvasProjectionStatuses(result.projections);
        }
      } catch {
        if (!cancelled) {
          clearCanvasProjectionStatuses();
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    canvasId,
    projectId,
    projectionMonitoringExpired,
    projectionKeys,
    projectionStatusRefreshToken,
    sync.hydratedCanvasId,
    sync.revision,
    sync.status,
  ]);

  const handleSyncProjection = useCallback(async (projectionKey: string) => {
    if (syncingProjectionRef.current) return;
    const request = requestFromProjectionMetadata(sync.metadata, projectionKey);
    if (!request) {
      setToast(t("freezone.projections.syncMissingRequest"));
      return;
    }
    syncingProjectionRef.current = projectionKey;
    try {
      const target = projectionTargetForCanvasPanel({ currentCanvasId: canvasId, request });
      const projection = await buildProjectionFromPreset(projectId, {
        ...request,
        projection_key: target.projectionKey,
        base_revision: 0,
        force_refresh: true,
      });
      queueLocalFreezoneProjection(projectId, target.targetCanvasId, {
        projectionKey: target.projectionKey,
        nodes: (projection.nodes ?? []) as CanvasNode[],
        edges: (projection.edges ?? []) as CanvasEdge[],
        metadata: projectionMetadataWithRequest(
          projection.metadata ?? null,
          target.projectionKey,
          request,
          projection.facts_signature,
        ),
      });
      consumeQueuedLocalFreezoneProjections(projectId, target.targetCanvasId);
      markCanvasProjectionFresh(target.projectionKey);
      setToast(t("freezone.projections.syncSuccess"));
    } catch (error) {
      setToast(error instanceof Error ? error.message : String(error));
    } finally {
      syncingProjectionRef.current = null;
    }
  }, [canvasId, projectId, sync.metadata, t]);

  const handleRemoveProjection = useCallback(async (projectionKey: string) => {
    if (removingProjectionRef.current) return;
    removingProjectionRef.current = projectionKey;
    try {
      const removed = removeLocalFreezoneProjection(projectId, canvasId, projectionKey);
      if (!removed) {
        throw new Error(t("freezone.projections.removeBlocked"));
      }
      setToast(t("freezone.projections.removeSuccess"));
    } catch (error) {
      setToast(error instanceof Error ? error.message : String(error));
    } finally {
      removingProjectionRef.current = null;
    }
  }, [canvasId, projectId, sync, t]);

  // 节点 toolbar 上的 Commit 按钮通过 canvasEventBus 触发；这里订阅、查节点、
  // 推 CommitDialog。比 AssetLibraryPanel 的 Commit 宽松：任何带 imageUrl 的
  // 节点都允许提交，slot_target 只是给 dialog 一个 default，缺失也能让用户手选目标。
  useEffect(() => {
    return canvasEventBus.subscribe("freezone/commit-node", ({ nodeId, auto, successMessage }) => {
      const node = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      if (!node) {
        setToast("当前节点没有可提交的内容");
        return;
      }
      // 泛化:不再只认 imageUrl,而是按节点类型推断媒体 url(图像/视频/音频/3GS)。
      const info = deriveNodeDropInfo(node);
      if (!info?.sourceUrl) {
        setToast("当前节点没有可提交的内容");
        return;
      }
      const sourceUrl = info.sourceUrl;
      const data = (node.data ?? {}) as Record<string, unknown>;
      const preview =
        typeof data.previewImageUrl === "string" && data.previewImageUrl
          ? data.previewImageUrl
          : info.mediaType === "image"
            ? sourceUrl
            : null;
      const sourceMeta = data.__freezone_source as Record<string, unknown> | undefined;
      const defaultTarget =
        coerceSlotTarget(data.slot_target) ??
        coerceSlotTarget(data.capabilityDefaultPushTarget) ??
        assetToPushTarget(sourceMeta) ??
        undefined;
      if (!auto) {
        void (async () => {
          try {
            const savedOpenScene = await saveOpenDirectorWorldScene(nodeId);
            if (savedOpenScene) {
              const flushed = await sync.flush();
              if (!flushed) {
                throw new Error("当前画布未保存成功，处理冲突后再提交");
              }
            }
            const latestNode = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
            if (!latestNode) {
              setToast("当前节点没有可提交的内容");
              return;
            }
            const latestInfo = deriveNodeDropInfo(latestNode);
            if (!latestInfo?.sourceUrl) {
              setToast("当前节点没有可提交的内容");
              return;
            }
            const latestData = (latestNode.data ?? {}) as Record<string, unknown>;
            const latestPreview =
              typeof latestData.previewImageUrl === "string" && latestData.previewImageUrl
                ? latestData.previewImageUrl
                : latestInfo.mediaType === "image"
                  ? latestInfo.sourceUrl
                  : null;
            const latestSourceMeta = latestData.__freezone_source as Record<string, unknown> | undefined;
            setPushState({
              nodeId,
              sourceUrl: latestInfo.sourceUrl,
              previewUrl: latestPreview,
              mediaType: latestInfo.mediaType,
              defaultTarget:
                coerceSlotTarget(latestData.slot_target) ??
                coerceSlotTarget(latestData.capabilityDefaultPushTarget) ??
                assetToPushTarget(latestSourceMeta) ??
                defaultTarget,
              sourceLabel: latestInfo.label,
              directorControlBundle: latestInfo.directorControlBundle,
              nodeData: latestData,
            });
          } catch (err) {
            setToast(err instanceof Error ? err.message : String(err));
          }
        })();
        return;
      }
      if (!defaultTarget) {
        setToast("当前节点没有可自动提交的主线目标");
        return;
      }
      void (async () => {
        setToast("正在写入当前背景…");
        try {
          const flushed = await sync.flush();
          if (!flushed) {
            throw new Error("当前画布未保存成功，处理冲突后再提交");
          }
          const latestData = resolveSubmitNodeData(latestCanvasNodeData(nodeId), data) ?? data;
          const latestSourceUrl =
            info.mediaType === "model"
              ? modelSourceUrlFromNodeData(latestData) ?? sourceUrl
              : sourceUrl;
          const target = defaultTarget as PushTarget;
          const result = target.kind === "director_render"
            ? await commitDirectorRenderFromCanvasSource(projectId, target, {
                sourceUrl: latestSourceUrl,
                previewUrl: preview,
                bundle: info.directorControlBundle,
                sourceNodeId: nodeId,
                label: typeof latestData.displayName === "string" ? latestData.displayName : undefined,
              })
            : target.kind === "scene_director_world"
              ? await commitSceneDirectorWorldFromCanvasNode(projectId, target, latestData)
              : await promoteToAsset(projectId, latestSourceUrl, target, {
                mark_stale: false,
              });
          const nodeDataPatch = nodeDataPatchAfterCommittedTarget(latestData, target, result, projectId);
          if (nodeDataPatch) {
            useCanvasStore.getState().updateNodeData(nodeId, nodeDataPatch);
          }
          const manifestNodeData = nodeDataPatch && hasDirectorWorldSceneState(nodeDataPatch)
            ? nodeDataPatch
            : sceneDirectorWorldDataForManifest(latestData, target, result, projectId);
          if (manifestNodeData && isDirectorWorldSourceSlotTarget(target)) {
            await commitSceneDirectorWorldFromCanvasNode(projectId, {
              kind: "scene_director_world",
              scene_id: target.scene_id,
            }, manifestNodeData, { pruneStale: false });
          }
          refreshCommittedTargetNodes(target, result);
          invalidateCommittedTargetQueries(target);
          markCommitCandidatePushed(nodeId, target, result);
          setAssetLibraryReloadToken((token) => token + 1);
          setToast(
            successMessage ??
              `${renderCommitSuccessMessage(target, result)}${
                manifestNodeData ? "；已同步导演世界状态" : ""
              }`,
          );
          void sync.flush();
        } catch (err) {
          setToast(err instanceof Error ? err.message : String(err));
        }
      })();
    });
  }, [projectId, sync]);

  useEffect(() => {
    const unsubscribeSync = canvasEventBus.subscribe(
      "freezone/projection-sync",
      ({ projectionKey }) => {
        void handleSyncProjection(projectionKey);
      },
    );
    const unsubscribeRemove = canvasEventBus.subscribe(
      "freezone/projection-remove",
      ({ projectionKey }) => {
        void handleRemoveProjection(projectionKey);
      },
    );

    return () => {
      unsubscribeSync();
      unsubscribeRemove();
    };
  }, [handleRemoveProjection, handleSyncProjection]);

  useEffect(() => {
    return canvasEventBus.subscribe("freezone/assets-updated", () => {
      setAssetLibraryReloadToken((token) => token + 1);
    });
  }, []);

  const canvasDefaultTarget = normalizePushTarget(
    (sync.metadata?.default_push_target ?? null) as
      | (Partial<PushTarget> & { kind?: PushTargetKind })
      | null,
  );
  const presetDefaultCharacter =
    defaultCharacterFromMetadata(sync.metadata) ??
    (
      canvasDefaultTarget?.kind === "identity" ||
      canvasDefaultTarget?.kind === "identity_costume" ||
      canvasDefaultTarget?.kind === "identity_portrait" ||
      canvasDefaultTarget?.kind === "portrait"
        ? canvasDefaultTarget.character
        : null
    );

  const handleMaskEditResult = async (newUrl: string) => {
    const { CANVAS_NODE_TYPES, DEFAULT_NODE_WIDTH } = await import(
      "@/features/canvas/domain/canvasNodes"
    );
    const addNode = useCanvasStore.getState().addNode;
    const baseLabel = maskTarget?.label ?? "edit";
    addNode(
      CANVAS_NODE_TYPES.upload,
      { x: 100, y: 1100 },
      {
        displayName: `${baseLabel} (mask)`,
        imageUrl: newUrl,
        previewImageUrl: newUrl,
        aspectRatio: "1:1",
        sourceFileName: `${baseLabel}-mask`,
      } as Record<string, unknown>,
    );
    setToast(`Mask edit 完成 — 新图已入画布`);
    void DEFAULT_NODE_WIDTH; // unused but keep import alive
  };

  const showBlockingLoading = sync.status === "loading" && !hasRenderedCanvas;
  const showLoadingOverlay = sync.status === "loading" && hasRenderedCanvas;
  const locateStoryboardNode = (nodeId: string) => {
    const store = useCanvasStore.getState();
    store.setSelectedNode(nodeId);
    store.requestFocusNode(nodeId);
    setCanvasView("workflow");
  };

  return (
    <div
      className={cn(
        "relative flex h-full w-full flex-col overflow-hidden",
        canvasOnlyProduct && "village-canvas-product",
      )}
      data-canvas-product={canvasOnlyProduct ? "village" : "village_canvas"}
    >
      <div className="village-canvas-workspace relative flex min-h-0 flex-1">
        <main className="village-canvas-main relative h-full min-w-0 flex-1">
          {canvasView === "workflow" && activePowerTool === "expression" && (
            <ExpressionManagerPanel
              onToast={setToast}
              onRequestClose={closePowerTool}
              projectId={projectId}
              canvasId={canvasId}
            />
          )}
          {showBlockingLoading ? (
            <CanvasLoadingScreen />
          ) : canvasView === "workflow" ? (
            <Canvas
              onBlankPaneClick={handleBlankPaneClick}
              controlsPlacement="bottom-right"
              projectId={projectId}
              canvasId={canvasId}
            />
          ) : (
            <CanvasStoryboardView onLocateNode={locateStoryboardNode} />
          )}
          {showLoadingOverlay && <CanvasLoadingOverlay />}
          {sync.status === "error" && (
            <CanvasErrorOverlay
              error={sync.error}
              onRetry={() => {
                void sync.retrySave();
              }}
            />
          )}
          {sync.status === "offline" && (
            <CanvasOfflineSaveBanner
              message={sync.error}
              onRetry={() => {
                void sync.retrySave();
              }}
            />
          )}
          {sync.status === "conflict" && (
            <CanvasConflictOverlay
              error={sync.error}
              canvasId={canvasId}
              onRefresh={sync.retry}
              onSaveCopy={async () => {
                const copyCanvasId = await sync.saveCopy();
                setAssetLibraryReloadToken((token) => token + 1);
                writeUrl({ canvas: copyCanvasId });
              }}
              readConflictSnapshot={sync.readConflictSnapshot}
            />
          )}
          <CanvasMutationOutboxIndicator status={sync.outboxStatus} />
          <BackupStatusIndicator status={sync.backupStatus} />
          <AssetLibraryPanel
            project={projectId}
            metadata={sync.metadata}
            canvasView={canvasView}
            onCanvasViewChange={(view) => {
              setCanvasView(view);
              if (view === "storyboard") closePowerTool();
            }}
            currentCanvasId={canvasId}
            reloadToken={assetLibraryReloadToken}
            onRestoreMainlineDefault={async () => {
              try {
                await sync.restoreMainlineDefault();
                setToast("已按当前主流程事实同步主线视图");
              } catch (err) {
                setToast(err instanceof Error ? err.message : String(err));
              }
            }}
            onReplaced={(payload, message) => {
              if (payload) {
                refreshCommittedTargetNodes(payload.target, payload.result);
                setAssetLibraryReloadToken((token) => token + 1);
              }
              setToast(message);
            }}
          />
        </main>
        {showChatDock && (
          <FreezoneChatDock
            key={canvasId}
            open={chatOpen}
            onOpenChange={handleChatOpenChange}
            canvasId={canvasId}
            canvasRevision={sync.revision}
            projectStyleId={typeof project.visual_style === "string" ? project.visual_style : null}
            initialDraft={homeHandoff?.draft}
            initialAttachments={homeHandoff?.attachments}
            initialSkillIds={homeHandoff?.skillIds}
            autoSendInitialDraft={homeHandoff?.autoSend}
            title={canvasOnlyProduct ? "小树" : t("freezone.chat.title")}
            description={canvasOnlyProduct ? "把想法变成可执行的画布结构" : t("freezone.chat.description")}
            toggleLabel={canvasOnlyProduct ? "打开小树" : t("freezone.chat.toggle")}
          />
        )}
      </div>
      {shouldShowVillageCompanionAgentEntry(canvasOnlyProduct, chatOpen) ? (
        <MyBuddyCompanion
          agentEntryMode
          agentActive={chatOpen}
          onActivateAgent={() => handleChatOpenChange(!chatOpen)}
        />
      ) : null}
      <NodeReplaceDragPreview />
      {pushState && (
        <CommitDialog
          project={projectId}
          sourceUrl={pushState.sourceUrl}
          previewUrl={pushState.previewUrl ?? undefined}
          sourceLabelOverride={pushState.sourceLabel}
          mediaType={pushState.mediaType}
          defaultTarget={pushState.defaultTarget}
          directorControlBundle={pushState.directorControlBundle}
          nodeData={pushState.nodeData}
          getNodeData={() => resolveSubmitNodeData(latestCanvasNodeData(pushState.nodeId), pushState.nodeData)}
          onClose={() => setPushState(null)}
          onSuccess={(msg, result, target, nodeDataPatch) => {
            if (nodeDataPatch) {
              useCanvasStore.getState().updateNodeData(pushState.nodeId, nodeDataPatch);
            }
            refreshCommittedTargetNodes(target, result);
            invalidateCommittedTargetQueries(target);
            markCommitCandidatePushed(pushState.nodeId, target, result);
            setAssetLibraryReloadToken((token) => token + 1);
            setPushState(null);
            setToast(msg);
          }}
        />
      )}
      {createIdentitySource && (
        <CreateIdentityDialog
          project={projectId}
          sourceUrl={createIdentitySource.imageUrl}
          previewUrl={createIdentitySource.previewUrl ?? undefined}
          defaultCharacter={presetDefaultCharacter}
          onClose={() => setCreateIdentitySource(null)}
          onSuccess={(msg) => {
            setCreateIdentitySource(null);
            setToast(msg);
          }}
        />
      )}
      {maskTarget && (
        <MaskEditor
          project={projectId}
          baseUrl={maskTarget.url}
          baseLabel={maskTarget.label}
          onClose={() => setMaskTarget(null)}
          onResult={handleMaskEditResult}
        />
      )}
      {toast && <Toast text={toast} onClose={() => setToast(null)} />}
    </div>
  );
}

export function resolveInitialFreezoneAgentOpen(isCanvasOnlyProduct: boolean): boolean {
  return !isCanvasOnlyProduct;
}

export function shouldShowVillageCompanionAgentEntry(
  isCanvasOnlyProduct: boolean,
  _agentOpen: boolean,
): boolean {
  return isCanvasOnlyProduct;
}

type VillageAgentPresentation = "docked" | "floating";

type VillageAgentFloatingFrame = {
  x: number;
  y: number;
  width: number;
  height: number;
};

const VILLAGE_AGENT_PRESENTATION_KEY = "village.canvas.agent.presentation";
const VILLAGE_AGENT_FLOATING_FRAME_KEY = "village.canvas.agent.floatingFrame.v3";
const VILLAGE_AGENT_FLOATING_MIN_WIDTH = 560;
const VILLAGE_AGENT_FLOATING_MIN_HEIGHT = 560;
const VILLAGE_AGENT_FLOATING_MARGIN = 18;

function clearVillageAgentCompanionAnchor(root: HTMLElement): void {
  root.style.removeProperty("--village-agent-companion-left");
  root.style.removeProperty("--village-agent-companion-top");
  delete root.dataset.villageAgentCompanionPresentation;
}

function defaultVillageAgentFloatingFrame(): VillageAgentFloatingFrame {
  const viewportWidth = typeof window === "undefined" ? 1440 : window.innerWidth;
  const viewportHeight = typeof window === "undefined" ? 950 : window.innerHeight - 52;
  const width = Math.min(780, Math.max(VILLAGE_AGENT_FLOATING_MIN_WIDTH, viewportWidth - 160));
  const height = Math.min(820, Math.max(VILLAGE_AGENT_FLOATING_MIN_HEIGHT, viewportHeight - 34));
  return {
    x: VILLAGE_AGENT_FLOATING_MARGIN,
    y: VILLAGE_AGENT_FLOATING_MARGIN,
    width,
    height,
  };
}

function clampVillageAgentFloatingFrame(
  frame: VillageAgentFloatingFrame,
  boundsWidth = typeof window === "undefined" ? 1440 : window.innerWidth,
  boundsHeight = typeof window === "undefined" ? 900 : window.innerHeight - 52,
): VillageAgentFloatingFrame {
  const maxWidth = Math.max(
    VILLAGE_AGENT_FLOATING_MIN_WIDTH,
    boundsWidth - VILLAGE_AGENT_FLOATING_MARGIN * 2,
  );
  const maxHeight = Math.max(
    VILLAGE_AGENT_FLOATING_MIN_HEIGHT,
    boundsHeight - VILLAGE_AGENT_FLOATING_MARGIN * 2,
  );
  const width = Math.min(maxWidth, Math.max(VILLAGE_AGENT_FLOATING_MIN_WIDTH, frame.width));
  const height = Math.min(maxHeight, Math.max(VILLAGE_AGENT_FLOATING_MIN_HEIGHT, frame.height));
  return {
    x: Math.min(
      Math.max(VILLAGE_AGENT_FLOATING_MARGIN, frame.x),
      Math.max(VILLAGE_AGENT_FLOATING_MARGIN, boundsWidth - width - VILLAGE_AGENT_FLOATING_MARGIN),
    ),
    y: Math.min(
      Math.max(VILLAGE_AGENT_FLOATING_MARGIN, frame.y),
      Math.max(VILLAGE_AGENT_FLOATING_MARGIN, boundsHeight - height - VILLAGE_AGENT_FLOATING_MARGIN),
    ),
    width,
    height,
  };
}

function loadVillageAgentPresentation(): VillageAgentPresentation {
  if (typeof window === "undefined") return "docked";
  return window.localStorage.getItem(VILLAGE_AGENT_PRESENTATION_KEY) === "floating"
    ? "floating"
    : "docked";
}

function saveVillageAgentPresentation(presentation: VillageAgentPresentation) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(VILLAGE_AGENT_PRESENTATION_KEY, presentation);
}

function loadVillageAgentFloatingFrame(): VillageAgentFloatingFrame {
  const fallback = defaultVillageAgentFloatingFrame();
  if (typeof window === "undefined") return fallback;
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(VILLAGE_AGENT_FLOATING_FRAME_KEY) ?? "null",
    ) as Partial<VillageAgentFloatingFrame> | null;
    if (!parsed) return fallback;
    const frame = {
      x: typeof parsed.x === "number" ? parsed.x : fallback.x,
      y: typeof parsed.y === "number" ? parsed.y : fallback.y,
      width: typeof parsed.width === "number" ? parsed.width : fallback.width,
      height: typeof parsed.height === "number" ? parsed.height : fallback.height,
    };
    return clampVillageAgentFloatingFrame(frame);
  } catch {
    return fallback;
  }
}

function saveVillageAgentFloatingFrame(frame: VillageAgentFloatingFrame) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(VILLAGE_AGENT_FLOATING_FRAME_KEY, JSON.stringify(frame));
}

export function FreezoneChatDock({
  open,
  onOpenChange,
  canvasId,
  canvasRevision,
  projectStyleId,
  initialDraft,
  initialAttachments,
  initialSkillIds,
  autoSendInitialDraft,
  title,
  description,
  toggleLabel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canvasId: string;
  canvasRevision: number | null;
  projectStyleId: string | null;
  initialDraft?: string;
  initialAttachments?: import("@/features/superchat/types").ChatAttachment[];
  initialSkillIds?: string[];
  autoSendInitialDraft?: boolean;
  title: string;
  description: string;
  toggleLabel: string;
}) {
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const composerHandoffRef = useRef<ComposerHandoffSnapshot>({
    draft: initialDraft ?? "", attachments: initialAttachments ?? [], autoSendClaimed: false,
  });
  const [panelVisible, setPanelVisible] = useState(open);
  const [drawerWidth, setDrawerWidth] = useState(() => loadFreezoneAgentWidth(
    canvasOnlyProduct ? FREEZONE_AGENT_WIDTH_V4_KEY : undefined,
  ));
  const [resizing, setResizing] = useState(false);
  const [presentation, setPresentation] = useState<VillageAgentPresentation>(() =>
    canvasOnlyProduct ? loadVillageAgentPresentation() : "docked",
  );
  const [floatingFrame, setFloatingFrame] = useState<VillageAgentFloatingFrame>(
    loadVillageAgentFloatingFrame,
  );
  const floatingFrameRef = useRef(floatingFrame);
  const floatingAsideRef = useRef<HTMLElement | null>(null);
  const floatingFrameRafRef = useRef<number | null>(null);
  const pendingFloatingFrameRef = useRef<VillageAgentFloatingFrame | null>(null);
  const endFloatingPerformanceRef = useRef<(() => void) | null>(null);
  const floatingInteractionRef = useRef<{
    kind: "move" | "resize";
    startClientX: number;
    startClientY: number;
    startFrame: VillageAgentFloatingFrame;
    boundsWidth: number;
    boundsHeight: number;
    startViewportLeft: number;
    startViewportTop: number;
  } | null>(null);
  const floating = canvasOnlyProduct && presentation === "floating";

  useLayoutEffect(() => {
    if (!canvasOnlyProduct || typeof document === "undefined") return;
    const root = document.documentElement;
    const agentFrame = floatingAsideRef.current;
    if (!isDesktop || !open || !panelVisible || !agentFrame) {
      clearVillageAgentCompanionAnchor(root);
      return;
    }
    const anchor = resolveVillageAgentCompanionAnchor(agentFrame.getBoundingClientRect(), {
      width: window.innerWidth,
      height: window.innerHeight,
    });
    root.style.setProperty("--village-agent-companion-left", `${anchor.left}px`);
    root.style.setProperty("--village-agent-companion-top", `${anchor.top}px`);
    root.dataset.villageAgentCompanionPresentation = floating ? "floating" : "docked";
  }, [drawerWidth, floating, floatingFrame, isDesktop, open, panelVisible]);

  useEffect(() => {
    if (!canvasOnlyProduct || typeof document === "undefined") return;
    const root = document.documentElement;
    return () => clearVillageAgentCompanionAnchor(root);
  }, []);

  useEffect(() => {
    if (!canvasOnlyProduct || typeof document === "undefined") return;
    const root = document.documentElement;
    const dockedOpen = isDesktop && open && !floating;
    if (dockedOpen) {
      root.style.setProperty("--village-agent-safe-right", `${drawerWidth}px`);
      root.style.setProperty(
        "--village-agent-quickbar-shift-x",
        `${-drawerWidth / 2}px`,
      );
      root.dataset.villageAgentDocked = "true";
    } else {
      root.style.removeProperty("--village-agent-safe-right");
      root.style.removeProperty("--village-agent-quickbar-shift-x");
      delete root.dataset.villageAgentDocked;
    }
    return () => {
      root.style.removeProperty("--village-agent-safe-right");
      root.style.removeProperty("--village-agent-quickbar-shift-x");
      delete root.dataset.villageAgentDocked;
    };
  }, [drawerWidth, floating, isDesktop, open]);

  useEffect(() => {
    floatingFrameRef.current = floatingFrame;
  }, [floatingFrame]);

  useEffect(() => {
    if (!isDesktop) {
      setPanelVisible(open);
      return;
    }
    if (open) {
      const frame = window.requestAnimationFrame(() => setPanelVisible(true));
      return () => window.cancelAnimationFrame(frame);
    }
    setPanelVisible(false);
  }, [isDesktop, open]);

  useEffect(() => {
    if (!resizing) return;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    const root = document.documentElement;
    const agentFrame = floatingAsideRef.current;
    const endPerformanceMode = beginCanvasInteraction('agent-resize');
    let frame = 0;
    let nextWidth = clampFreezoneAgentWidth(drawerWidth);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const applyWidth = () => {
      frame = 0;
      if (agentFrame) agentFrame.style.width = `${nextWidth}px`;
      if (canvasOnlyProduct) {
        root.style.setProperty('--village-agent-safe-right', `${nextWidth}px`);
        root.style.setProperty('--village-agent-quickbar-shift-x', `${-nextWidth / 2}px`);
      }
    };
    const onMove = (event: PointerEvent) => {
      nextWidth = clampFreezoneAgentWidth(window.innerWidth - event.clientX - 16);
      if (!frame) frame = window.requestAnimationFrame(applyWidth);
    };
    const onUp = () => {
      if (frame) {
        window.cancelAnimationFrame(frame);
        applyWidth();
      }
      setResizing(false);
      setDrawerWidth(nextWidth);
      saveFreezoneAgentWidth(
        nextWidth,
        canvasOnlyProduct ? FREEZONE_AGENT_WIDTH_V4_KEY : undefined,
      );
      endPerformanceMode();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener('blur', onUp);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      endPerformanceMode();
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener('blur', onUp);
    };
  }, [drawerWidth, resizing]);

  useEffect(() => {
    if (!floating) return;
    const handleResize = () => {
      const parentRect = floatingAsideRef.current?.offsetParent?.getBoundingClientRect();
      const next = clampVillageAgentFloatingFrame(
        floatingFrameRef.current,
        parentRect?.width,
        parentRect?.height,
      );
      floatingFrameRef.current = next;
      setFloatingFrame(next);
      saveVillageAgentFloatingFrame(next);
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [floating]);

  useEffect(() => {
    const applyFloatingFrame = () => {
      floatingFrameRafRef.current = null;
      const interaction = floatingInteractionRef.current;
      const next = pendingFloatingFrameRef.current;
      const agentFrame = floatingAsideRef.current;
      if (!interaction || !next || !agentFrame) return;
      floatingFrameRef.current = next;
      if (interaction.kind === 'move') {
        const deltaX = next.x - interaction.startFrame.x;
        const deltaY = next.y - interaction.startFrame.y;
        agentFrame.style.transform = `translate3d(${deltaX}px, ${deltaY}px, 0)`;
        const anchor = resolveVillageAgentCompanionAnchor(
          {
            left: interaction.startViewportLeft + deltaX,
            top: interaction.startViewportTop + deltaY,
          },
          { width: window.innerWidth, height: window.innerHeight },
        );
        const root = document.documentElement;
        root.style.setProperty('--village-agent-companion-left', `${anchor.left}px`);
        root.style.setProperty('--village-agent-companion-top', `${anchor.top}px`);
      } else {
        agentFrame.style.width = `${next.width}px`;
        agentFrame.style.height = `${next.height}px`;
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      const interaction = floatingInteractionRef.current;
      if (!interaction) return;
      const deltaX = event.clientX - interaction.startClientX;
      const deltaY = event.clientY - interaction.startClientY;
      const draft = interaction.kind === "move"
        ? {
            ...interaction.startFrame,
            x: interaction.startFrame.x + deltaX,
            y: interaction.startFrame.y + deltaY,
          }
        : {
            ...interaction.startFrame,
            width: interaction.startFrame.width + deltaX,
            height: interaction.startFrame.height + deltaY,
          };
      const next = clampVillageAgentFloatingFrame(
        draft,
        interaction.boundsWidth,
        interaction.boundsHeight,
      );
      pendingFloatingFrameRef.current = next;
      if (floatingFrameRafRef.current === null) {
        floatingFrameRafRef.current = window.requestAnimationFrame(applyFloatingFrame);
      }
    };
    const onPointerUp = () => {
      const interaction = floatingInteractionRef.current;
      if (!interaction) return;
      if (floatingFrameRafRef.current !== null) {
        window.cancelAnimationFrame(floatingFrameRafRef.current);
        applyFloatingFrame();
      }
      const next = pendingFloatingFrameRef.current ?? floatingFrameRef.current;
      const agentFrame = floatingAsideRef.current;
      if (agentFrame) {
        agentFrame.style.left = `${next.x}px`;
        agentFrame.style.top = `${next.y}px`;
        agentFrame.style.width = `${next.width}px`;
        agentFrame.style.height = `${next.height}px`;
        agentFrame.style.transform = '';
      }
      floatingFrameRef.current = next;
      pendingFloatingFrameRef.current = null;
      floatingInteractionRef.current = null;
      setFloatingFrame(next);
      endFloatingPerformanceRef.current?.();
      endFloatingPerformanceRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      saveVillageAgentFloatingFrame(next);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    window.addEventListener('blur', onPointerUp);
    return () => {
      if (floatingFrameRafRef.current !== null) {
        window.cancelAnimationFrame(floatingFrameRafRef.current);
        floatingFrameRafRef.current = null;
      }
      endFloatingPerformanceRef.current?.();
      endFloatingPerformanceRef.current = null;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      window.removeEventListener('blur', onPointerUp);
    };
  }, []);

  const beginFloatingInteraction = (
    event: ReactPointerEvent<HTMLElement>,
    kind: "move" | "resize",
  ) => {
    if (!floating) return;
    const parentRect = floatingAsideRef.current?.offsetParent?.getBoundingClientRect();
    const frameRect = floatingAsideRef.current?.getBoundingClientRect();
    endFloatingPerformanceRef.current?.();
    endFloatingPerformanceRef.current = beginCanvasInteraction(
      kind === 'move' ? 'agent-move' : 'agent-resize',
    );
    pendingFloatingFrameRef.current = floatingFrameRef.current;
    floatingInteractionRef.current = {
      kind,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startFrame: floatingFrameRef.current,
      boundsWidth: parentRect?.width ?? window.innerWidth,
      boundsHeight: parentRect?.height ?? window.innerHeight - 52,
      startViewportLeft: frameRect?.left ?? floatingFrameRef.current.x,
      startViewportTop: frameRect?.top ?? floatingFrameRef.current.y,
    };
    document.body.style.cursor = kind === "move" ? "grabbing" : "nwse-resize";
    document.body.style.userSelect = "none";
    event.preventDefault();
  };

  const handleFloatingPointerDownCapture = (event: ReactPointerEvent<HTMLElement>) => {
    if (!floating) return;
    const target = event.target as HTMLElement;
    if (target.closest("[data-agent-resize-handle='true']")) {
      beginFloatingInteraction(event, "resize");
      return;
    }
    if (!target.closest("[data-agent-drag-handle='true']")) return;
    if (target.closest("button, a, input, textarea, select, [role='button']")) return;
    beginFloatingInteraction(event, "move");
  };

  const togglePresentation = () => {
    const next: VillageAgentPresentation = floating ? "docked" : "floating";
    if (next === "floating") {
      const parentRect = floatingAsideRef.current?.offsetParent?.getBoundingClientRect();
      const current = floatingFrameRef.current;
      const baseline = current.width < 640 || current.height < 560
        ? defaultVillageAgentFloatingFrame()
        : current;
      const clamped = clampVillageAgentFloatingFrame(
        baseline,
        parentRect?.width,
        parentRect?.height,
      );
      floatingFrameRef.current = clamped;
      setFloatingFrame(clamped);
      saveVillageAgentFloatingFrame(clamped);
    }
    setPresentation(next);
    saveVillageAgentPresentation(next);
  };

  if (!isDesktop) {
    return (
      <>
        {!open && !canvasOnlyProduct ? (
          <FreezoneChatToggleButton
            label={toggleLabel}
            expanded={open}
            onClick={() => onOpenChange(true)}
          />
        ) : null}
        <Sheet open={open} onOpenChange={onOpenChange}>
          <SheetContent
            keepMounted
            side="right"
            data-agent-drawer={canvasOnlyProduct ? "village-agent-v4-libtv" : "libtv-parity"}
            data-agent-presentation={canvasOnlyProduct ? "mobile" : "mobile-legacy"}
            className={cn(
              "flex w-full flex-col gap-0 p-0",
              !canvasOnlyProduct && "sm:!max-w-[560px]",
            )}
          >
            <SheetHeader className="sr-only">
              <SheetTitle>{title}</SheetTitle>
              <SheetDescription>{description}</SheetDescription>
            </SheetHeader>
            <SuperChatPanel
              variant="freezone"
              canvasId={canvasId}
              canvasRevision={canvasRevision}
              projectStyleId={projectStyleId}
              initialDraft={initialDraft}
              initialAttachments={initialAttachments}
              initialSkillIds={initialSkillIds}
              autoSendInitialDraft={autoSendInitialDraft}
              composerHandoffRef={composerHandoffRef}
              onRequestClose={() => onOpenChange(false)}
            />
          </SheetContent>
        </Sheet>
      </>
    );
  }

  return (
    <>
      {!open && !canvasOnlyProduct && (
        <FreezoneChatToggleButton
          label={toggleLabel}
          expanded={false}
          onClick={() => onOpenChange(true)}
        />
      )}
      <aside
        ref={floatingAsideRef}
        className={cn(
          FREEZONE_AGENT_DRAWER_CLASS,
          "absolute z-40 hidden origin-right flex-col overflow-hidden transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:flex",
          !canvasOnlyProduct && "neo-canvas-agent-frame bg-[#0b0b0c]/92 shadow-none backdrop-blur-2xl",
          floating
            ? "village-agent-floating"
            : "bottom-0 right-0 top-0",
          panelVisible ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0",
          !open && "pointer-events-none invisible",
          resizing && "transition-none",
        )}
        style={floating
          ? {
              left: floatingFrame.x,
              top: floatingFrame.y,
              width: floatingFrame.width,
              height: floatingFrame.height,
              zIndex: 90,
              maxWidth: "calc(100% - 36px)",
              maxHeight: "calc(100% - 36px)",
            }
          : {
              width: drawerWidth,
              maxWidth: "min(560px, calc(100vw - 280px))",
            }}
        aria-label={title}
        data-agent-drawer={canvasOnlyProduct ? "village-agent-v4-libtv" : "libtv-parity"}
        data-agent-presentation={floating ? "floating" : "docked"}
        onPointerDownCapture={handleFloatingPointerDownCapture}
      >
        {!floating ? (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="调整小树宽度"
            tabIndex={0}
            aria-valuemin={FREEZONE_AGENT_WIDTH_MIN}
            aria-valuemax={FREEZONE_AGENT_WIDTH_MAX}
            aria-valuenow={drawerWidth}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const next = clampFreezoneAgentWidth(event.key === "Home" ? FREEZONE_AGENT_WIDTH_MIN
                : event.key === "End" ? FREEZONE_AGENT_WIDTH_MAX
                  : drawerWidth + (event.key === "ArrowLeft" ? 20 : -20));
              setDrawerWidth(next);
              saveFreezoneAgentWidth(next, canvasOnlyProduct ? FREEZONE_AGENT_WIDTH_V4_KEY : undefined);
            }}
            data-dragging={resizing ? "true" : "false"}
            className="canvas-agent-drawer-resize-handle absolute bottom-0 left-0 top-0 z-[100] w-3 shrink-0 cursor-col-resize touch-none select-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
            onPointerDown={(event) => {
              event.preventDefault();
              setResizing(true);
            }}
          />
        ) : null}
        <SuperChatPanel
          variant="freezone"
          presentation={floating ? "floating" : "docked"}
          canvasId={canvasId}
          canvasRevision={canvasRevision}
          projectStyleId={projectStyleId}
          initialDraft={initialDraft}
          initialAttachments={initialAttachments}
          initialSkillIds={initialSkillIds}
          autoSendInitialDraft={autoSendInitialDraft}
          composerHandoffRef={composerHandoffRef}
          onRequestPresentationToggle={togglePresentation}
          onRequestClose={() => onOpenChange(false)}
        />
        {floating ? (
          <div
            aria-label="调整小树浮窗大小"
            role="separator"
            aria-orientation="vertical"
            tabIndex={0}
            aria-valuenow={floatingFrame.width}
            aria-valuemin={VILLAGE_AGENT_FLOATING_MIN_WIDTH}
            aria-valuetext={`${floatingFrame.width} × ${floatingFrame.height}`}
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
              event.preventDefault();
              const next = clampVillageAgentFloatingFrame({ ...floatingFrame,
                width: floatingFrame.width + (event.key === "ArrowRight" ? 20 : event.key === "ArrowLeft" ? -20 : 0),
                height: floatingFrame.height + (event.key === "ArrowDown" ? 20 : event.key === "ArrowUp" ? -20 : 0),
              });
              floatingFrameRef.current = next;
              setFloatingFrame(next);
              saveVillageAgentFloatingFrame(next);
            }}
            data-agent-resize-handle="true"
            className="village-agent-floating-resize absolute bottom-0 right-0 z-[120] size-7 cursor-nwse-resize touch-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          />
        ) : null}
      </aside>
    </>
  );
}

function normalizePushTarget(
  target: (Partial<PushTarget> & { kind?: PushTargetKind }) | null,
): (Partial<PushTarget> & { kind: PushTargetKind }) | null {
  if (!target?.kind) return null;
  return target as Partial<PushTarget> & { kind: PushTargetKind };
}

function refreshCommittedTargetNodes(
  target: PushTarget,
  result: PushResult,
): void {
  if (!shouldRefreshCommittedTargetNodes(target)) return;
  const targetUrl = result.target_url;
  if (!targetUrl) return;
  const previewUrl = withImageCacheBust(targetUrl, Date.now());

  const store = useCanvasStore.getState();
  for (const node of store.nodes) {
    const data = (node.data ?? {}) as Record<string, unknown>;
    if (data.user_spawned === true) continue;
    const sourceMeta = data.__freezone_source as
      | { kind?: string; role?: string; meta?: Record<string, unknown> }
      | undefined;
    const nodeTarget =
      coerceSlotTarget(data.slot_target) ??
      inferCanonicalRefreshTarget(sourceMeta);
    if (!nodeTarget || !pushTargetsEqual(nodeTarget, target)) continue;

    const baseUpdate =
      target.kind === "video"
        ? { videoUrl: targetUrl, previewImageUrl: previewUrl }
        : target.kind === "beat_audio"
          ? { audioUrl: targetUrl, url: targetUrl }
          : isPlyOrGlbPushTargetKind(target.kind)
            ? { fileUrl: targetUrl, modelUrl: targetUrl, plyUrl: targetUrl, url: targetUrl }
            : { imageUrl: targetUrl, previewImageUrl: previewUrl };
    store.updateNodeData(node.id, {
      ...baseUpdate,
      committed_slot_url: targetUrl,
    } as Record<string, unknown>);
  }
}

function markCommitCandidatePushed(
  nodeId: string,
  target: PushTarget,
  result: PushResult,
): void {
  const store = useCanvasStore.getState();
  const node = store.nodes.find((candidate) => candidate.id === nodeId);
  const data = (node?.data ?? {}) as Record<string, unknown>;
  if (!isCommitCandidateData(data)) return;
  const slot = coerceSlotTarget(data.slot_target);
  if (!slot || !pushTargetsEqual(slot, target)) return;

  const update: Record<string, unknown> = {
    committed_at: new Date().toISOString(),
  };
  if (typeof result.target_url === "string" && result.target_url.length > 0) {
    update.committed_slot_url = result.target_url;
  }
  store.updateNodeData(nodeId, update);
}

function inferCanonicalRefreshTarget(
  source:
    | { kind?: string; role?: string; meta?: Record<string, unknown> }
    | undefined,
): (Partial<PushTarget> & { kind: PushTargetKind }) | undefined {
  if (!source?.kind) return undefined;
  return inferDefaultTarget(source);
}

function pushTargetsEqual(
  a: Partial<PushTarget> & { kind: PushTargetKind },
  b: PushTarget,
): boolean {
  if (a.kind !== b.kind) return false;
  const av = a as Record<string, unknown>;
  if (
    b.kind === "frame" ||
    b.kind === "sketch" ||
    b.kind === "director_render" ||
    b.kind === "selected_background" ||
    b.kind === "video" ||
    b.kind === "beat_audio"
  ) {
    return av.episode === b.episode && av.beat === b.beat;
  }
  if (
    b.kind === "identity" ||
    b.kind === "identity_costume" ||
    b.kind === "identity_portrait"
  ) {
    return av.character === b.character && av.identity_id === b.identity_id;
  }
  if (b.kind === "portrait") {
    return av.character === b.character;
  }
  if (isScenePushTargetKind(b.kind)) {
    return av.scene_id === (b as unknown as Record<string, unknown>).scene_id;
  }
  if (b.kind === "prop_ref") {
    return av.prop_id === b.prop_id;
  }
  return false;
}

function defaultCharacterFromMetadata(metadata: Record<string, unknown> | null): string | null {
  const preset = metadata?.preset as { character?: unknown } | undefined;
  return typeof preset?.character === "string" && preset.character ? preset.character : null;
}

interface PushPrompt {
  nodeId: string;
  sourceUrl: string;
  previewUrl: string | null;
  sourceLabel: string;
  mediaType: DropMediaType;
  defaultTarget?: Partial<PushTarget> & { kind: PushTargetKind };
  directorControlBundle?: Record<string, unknown> | null;
  nodeData?: Record<string, unknown> | null;
}

interface SelectedImageSummary {
  nodeId: string;
  imageUrl: string;
  previewUrl: string | null;
  defaultTarget?: Partial<PushTarget> & { kind: PushTargetKind };
  label: string;
}
