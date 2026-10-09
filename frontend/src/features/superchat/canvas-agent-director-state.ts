// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type {
  DirectModelKind,
  ModelGatewayConfig,
} from "@/lib/queries/model-gateway";
import {
  sortUpstreamByReferenceOrder,
  upstreamNodesInEdgeOrder,
} from "@/features/canvas/nodes/referenceOrdering";
import {
  inferVideoReferenceRole,
  videoReferenceRoleLabel,
  type VideoReferenceRole,
} from "@/features/canvas/domain/videoReferenceRoles";
import { isRunnableDirectModel } from "@/features/canvas/domain/directModelReadiness";

// Canonical families only: agent/text/vision are aliases of the chat list.
const DIRECT_MODEL_KINDS: readonly DirectModelKind[] = [
  "chat",
  "image",
  "embedding",
];
const MAX_DIRECTOR_NODES = 80;
const MAX_DIRECTOR_EDGES = 160;
const MAX_AGENT_MODELS = 48;
const MAX_LINEAGE_PAGE_BYTES = 4_096;
const MAX_REFERENCE_MANIFEST_TARGETS = 24;
const MAX_REFERENCE_MANIFEST_ITEMS_PER_TARGET = 24;

export interface CanvasAgentModelFact {
  id: string;
  kind: DirectModelKind | "video";
  label: string;
  model_id: string;
  protocol: string;
  is_default: boolean;
  supported_modes: string[];
  use_case: string | null;
  parameter_defaults: Record<string, string | number | boolean>;
  reference_limits?: Record<string, Record<string, number>>;
}

export interface CanvasAgentModelCatalogSnapshot {
  schema: "village.direct-model-catalog.v1";
  total_models: number;
  truncated: boolean;
  models: CanvasAgentModelFact[];
  routing_policy: {
    explicit_model_first: true;
    configured_models_only: true;
    capability_match_required: true;
    default_fallback_allowed: true;
    invent_price_or_capability: false;
  };
}

type CanvasNodeLike = {
  id: string;
  type?: string | null;
  parentId?: string | null;
  data?: unknown;
};

type CanvasEdgeLike = {
  id?: string | null;
  source: string;
  target: string;
};

type ReferenceManifestKind = "image" | "video" | "audio";

export interface CanvasAgentReferenceManifestItem {
  label: string;
  kind: ReferenceManifestKind;
  type_index: number;
  node_id: string;
  node_uri?: string;
  asset_id: string | null;
  asset_uri?: string;
  display_name: string | null;
  role: VideoReferenceRole;
  role_label: string;
  order: number;
  connected: true;
}

export interface CanvasAgentReferenceManifestTarget {
  target_node_id: string;
  target_display_name: string | null;
  references: CanvasAgentReferenceManifestItem[];
}

export interface CanvasAgentReferenceManifest {
  schema: "village.reference-manifest.v1";
  target_count: number;
  truncated: boolean;
  targets: CanvasAgentReferenceManifestTarget[];
  binding_policy: {
    label_is_display_only: true;
    bind_by_node_id_or_asset_id: true;
    order_source: "referenceOrder_then_edge_order";
    do_not_guess_from_filename: true;
    ambiguous_match_requires_confirmation: true;
  };
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function trimmedString(value: unknown, maxLength = 120): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maxLength) : null;
}

function stableIdentityString(value: unknown, maxLength = 160): string | null {
  const normalized = trimmedString(value, maxLength);
  if (!normalized || /^https?:\/\//i.test(normalized)) return null;
  return normalized;
}

function firstString(data: Record<string, unknown>, keys: readonly string[], maxLength = 120): string | null {
  for (const key of keys) {
    const value = trimmedString(data[key], maxLength);
    if (value) return value;
  }
  return null;
}

export function buildCanvasNodeIdentity(
  node: CanvasNodeLike,
  canvasId = "default",
): {
  node_uri: string;
  asset_id: string | null;
  asset_uri: string | null;
  parent_id: string | null;
} {
  const data = recordOf(node.data);
  const assetId = [
    "assetId",
    "asset_id",
    "sourceAssetId",
    "source_asset_id",
    "resultAssetId",
    "result_asset_id",
    "identityId",
    "identity_id",
    "sceneId",
    "scene_id",
    "propId",
    "prop_id",
    "mediaId",
    "media_id",
    "fileId",
    "file_id",
  ].map((key) => stableIdentityString(data[key], 160)).find(Boolean) ?? null;
  const assetUri = [
    "asset_uri",
    "assetUri",
    "hogi_uri",
    "hogiUri",
    "uri",
  ].map((key) => stableIdentityString(data[key], 240)).find(Boolean) ?? null;
  const normalizedCanvasId = stableIdentityString(canvasId, 200) ?? "default";
  return {
    node_uri: `canvas://${encodeURIComponent(normalizedCanvasId)}/nodes/${encodeURIComponent(node.id)}`,
    asset_id: assetId,
    asset_uri: assetUri,
    parent_id: trimmedString(node.parentId, 200),
  };
}

function hasMedia(data: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.some((key) => Boolean(trimmedString(data[key], 2_048)));
}

function mediaKindForReference(data: Record<string, unknown>): ReferenceManifestKind | null {
  if (hasMedia(data, [
    "videoUrl",
    "video_url",
    "outputVideoUrl",
    "output_video_url",
    "resultVideoUrl",
    "result_video_url",
    "previewVideoUrl",
    "preview_video_url",
    "sourceVideoUrl",
    "source_video_url",
  ])) return "video";
  if (hasMedia(data, [
    "audioUrl",
    "audio_url",
    "outputAudioUrl",
    "output_audio_url",
    "resultAudioUrl",
    "result_audio_url",
    "previewAudioUrl",
    "preview_audio_url",
    "sourceAudioUrl",
    "source_audio_url",
  ])) return "audio";
  if (hasMedia(data, [
    "imageUrl",
    "image_url",
    "previewImageUrl",
    "preview_image_url",
    "outputImageUrl",
    "output_image_url",
    "referenceImageUrl",
    "reference_image_url",
    "committedSlotUrl",
    "committed_slot_url",
    "mediaUrl",
    "media_url",
    "fileUrl",
    "file_url",
    "sourceImageUrl",
    "source_image_url",
  ])) return "image";
  return null;
}

function isVideoReferenceTarget(node: CanvasNodeLike): boolean {
  const type = String(node.type || "").toLowerCase();
  return type === "videonode" || type === "videostorynode";
}

function buildReferenceManifestItem(
  node: CanvasNodeLike,
  kind: ReferenceManifestKind,
  typeIndex: number,
  order: number,
  canvasId: string,
): CanvasAgentReferenceManifestItem {
  const data = recordOf(node.data);
  const identity = buildCanvasNodeIdentity(node, canvasId);
  const role = kind === "audio" ? "audio" : inferVideoReferenceRole(data) ?? "generic";
  return {
    label: `${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}${typeIndex}`,
    kind,
    type_index: typeIndex,
    node_id: node.id,
    node_uri: identity.node_uri,
    asset_id: firstString(data, [
      "assetId",
      "asset_id",
      "sourceAssetId",
      "source_asset_id",
      "resultAssetId",
      "result_asset_id",
      "identityId",
      "identity_id",
      "sceneId",
      "scene_id",
      "propId",
      "prop_id",
    ], 160),
    ...(identity.asset_uri ? { asset_uri: identity.asset_uri } : {}),
    display_name: firstString(data, ["displayName", "label", "title", "name"], 120),
    role,
    role_label: videoReferenceRoleLabel(role),
    order,
    connected: true,
  };
}

/**
 * Projects the exact one-hop reference order used by the video node UI into
 * an Agent-safe manifest. Labels are for conversation only; commands must use
 * node_id or asset_id so the backend can resolve one concrete asset.
 */
export function buildCanvasReferenceManifest(
  nodes: readonly CanvasNodeLike[],
  edges: readonly CanvasEdgeLike[],
  selectedNodeId?: string | null,
  canvasId = "default",
): CanvasAgentReferenceManifest {
  const targetNodes = nodes
    .filter(isVideoReferenceTarget)
    .sort((left, right) => {
      const leftSelected = left.id === selectedNodeId;
      const rightSelected = right.id === selectedNodeId;
      if (leftSelected !== rightSelected) return leftSelected ? -1 : 1;
      return nodes.indexOf(left) - nodes.indexOf(right);
    });
  const targets = targetNodes.slice(0, MAX_REFERENCE_MANIFEST_TARGETS).map((target) => {
    const targetData = recordOf(target.data);
    const rawReferenceOrder = Array.isArray(targetData.referenceOrder)
      ? targetData.referenceOrder
      : Array.isArray(targetData.reference_order)
        ? targetData.reference_order
        : undefined;
    const upstream = sortUpstreamByReferenceOrder(
      upstreamNodesInEdgeOrder([...nodes], edges, target.id),
      rawReferenceOrder?.filter((value): value is string => typeof value === "string"),
    );
    const counters: Record<ReferenceManifestKind, number> = { image: 0, video: 0, audio: 0 };
    const references = upstream
      .map((node) => {
        const kind = mediaKindForReference(recordOf(node.data));
        if (!kind) return null;
        counters[kind] += 1;
        return { node, kind, typeIndex: counters[kind] };
      })
      .filter((item): item is { node: CanvasNodeLike; kind: ReferenceManifestKind; typeIndex: number } => item !== null)
      .slice(0, MAX_REFERENCE_MANIFEST_ITEMS_PER_TARGET)
      .map((item, index) => buildReferenceManifestItem(
        item.node,
        item.kind,
        item.typeIndex,
        index,
        canvasId,
      ));
    return {
      target_node_id: target.id,
      target_display_name: firstString(targetData, ["displayName", "label", "title", "name"], 120),
      references,
    };
  });
  return {
    schema: "village.reference-manifest.v1",
    target_count: targetNodes.length,
    truncated: targetNodes.length > targets.length,
    targets,
    binding_policy: {
      label_is_display_only: true,
      bind_by_node_id_or_asset_id: true,
      order_source: "referenceOrder_then_edge_order",
      do_not_guess_from_filename: true,
      ambiguous_match_requires_confirmation: true,
    },
  };
}

function jsonStringCharacterBytes(character: string): number {
  const codePoint = character.codePointAt(0) ?? 0;
  if (character === "\"" || character === "\\") return 2;
  if (codePoint === 0x08 || codePoint === 0x09 || codePoint === 0x0A
    || codePoint === 0x0C || codePoint === 0x0D) return 2;
  if (codePoint < 0x20 || (codePoint >= 0xD800 && codePoint <= 0xDFFF)) return 6;
  if (codePoint <= 0x7F) return 1;
  if (codePoint <= 0x7FF) return 2;
  if (codePoint <= 0xFFFF) return 3;
  return 4;
}

function paginateSerializedJson(serialized: string): string[] {
  const pages: string[] = [];
  let start = 0;
  let end = 0;
  let pageBytes = 2;
  for (const character of serialized) {
    const characterBytes = jsonStringCharacterBytes(character);
    if (pageBytes + characterBytes > MAX_LINEAGE_PAGE_BYTES) {
      pages.push(serialized.slice(start, end));
      start = end;
      pageBytes = 2;
    }
    pageBytes += characterBytes;
    end += character.length;
  }
  pages.push(serialized.slice(start));
  return pages;
}

function buildCompleteAssetLineage(
  nodes: readonly CanvasNodeLike[],
  edges: readonly CanvasEdgeLike[],
): Record<string, unknown> {
  const ids = nodes.map((node) => node.id);
  const indexById = new Map<string, number>();
  ids.forEach((id, index) => {
    if (!indexById.has(id)) indexById.set(id, index);
  });
  const internId = (id: string): number => {
    const knownIndex = indexById.get(id);
    if (knownIndex !== undefined) return knownIndex;
    const index = ids.length;
    ids.push(id);
    indexById.set(id, index);
    return index;
  };

  const hasIncoming = new Set<number>();
  const hasOutgoing = new Set<number>();
  const indexedEdges = edges.map((edge) => {
    const sourceIndex = internId(edge.source);
    const targetIndex = internId(edge.target);
    if (sourceIndex < nodes.length) hasOutgoing.add(sourceIndex);
    if (targetIndex < nodes.length) hasIncoming.add(targetIndex);
    return [sourceIndex, targetIndex, edge.id ?? null] as const;
  });
  const rootIndexes: number[] = [];
  const leafIndexes: number[] = [];
  const mediaIndexes: number[] = [];
  const parentLinks: Array<[number, number]> = [];
  const assetLinks: Array<[number, number]> = [];
  nodes.forEach((node, index) => {
    if (!hasIncoming.has(index)) rootIndexes.push(index);
    if (!hasOutgoing.has(index)) leafIndexes.push(index);
    const data = recordOf(node.data);
    if (hasMedia(data, [
      "imageUrl",
      "previewImageUrl",
      "outputImageUrl",
      "mediaUrl",
      "videoUrl",
      "outputVideoUrl",
      "audioUrl",
      "outputAudioUrl",
    ])) {
      mediaIndexes.push(index);
    }
    const parentId = trimmedString(node.parentId);
    if (parentId) parentLinks.push([index, internId(parentId)]);
    const assetId = firstString(data, [
      "assetId",
      "asset_id",
      "sourceAssetId",
      "source_asset_id",
      "resultAssetId",
      "result_asset_id",
    ]);
    if (assetId) assetLinks.push([index, internId(assetId)]);
  });

  const payload = JSON.stringify({
    i: ids,
    n: nodes.length,
    e: indexedEdges,
    r: rootIndexes,
    l: leafIndexes,
    m: mediaIndexes,
    p: parentLinks,
    a: assetLinks,
  });
  const pages = paginateSerializedJson(payload);
  return {
    schema: "village.asset-lineage.v2",
    encoding: "indexed-json-pages-v1",
    complete: true,
    total_canvas_nodes: nodes.length,
    total_edges: edges.length,
    dictionary_size: ids.length,
    root_node_count: rootIndexes.length,
    leaf_node_count: leafIndexes.length,
    media_node_count: mediaIndexes.length,
    page_max_bytes: MAX_LINEAGE_PAGE_BYTES,
    page_count: pages.length,
    pages,
    payload_contract: {
      i: "ID dictionary; first n entries are canvas nodes, remaining entries are dangling edge endpoints",
      n: "canvas node count",
      e: "every edge as [source ID index, target ID index, edge ID or null]",
      r: "root canvas node indexes into i",
      l: "leaf canvas node indexes into i",
      m: "media canvas node indexes into i",
      p: "every parent relation as [canvas node index, parent ID index]",
      a: "every asset provenance relation as [canvas node index, asset ID index]",
      decode: "JSON.parse(pages.join(''))",
    },
  };
}

export function buildCanvasAgentModelCatalog(
  config?: ModelGatewayConfig | null,
): CanvasAgentModelCatalogSnapshot {
  const models: CanvasAgentModelFact[] = [];
  for (const kind of DIRECT_MODEL_KINDS) {
    for (const model of config?.directModels?.[kind] ?? []) {
      if (!isRunnableDirectModel(model)) continue;
      models.push({
        id: `direct/${model.id}`,
        kind,
        label: model.label,
        model_id: model.modelId,
        protocol: String(model.protocol || "auto"),
        is_default: Boolean(model.isDefault),
        supported_modes: [...(model.supportedModes ?? [])],
        use_case: trimmedString(model.useCase),
        parameter_defaults: { ...(model.parameterDefaults ?? {}) },
      });
    }
  }

  let videoDefaultAssigned = false;
  for (const model of config?.directVideoModels ?? []) {
    if (!isRunnableDirectModel(model)) continue;
    const isDefault = !videoDefaultAssigned;
    videoDefaultAssigned = true;
    models.push({
      id: `direct/${model.id}`,
      kind: "video",
      label: model.label,
      model_id: model.modelId,
      protocol: model.protocol,
      is_default: isDefault,
      supported_modes: [...(model.supportedModes ?? [])],
      use_case: trimmedString(model.useCase),
      parameter_defaults: { ...(model.parameterDefaults ?? {}) },
      reference_limits: model.referenceLimits
        ? Object.fromEntries(
          Object.entries(model.referenceLimits).map(([mode, limits]) => [mode, { ...limits }]),
        )
        : undefined,
    });
  }

  const totalModels = models.length;
  return {
    schema: "village.direct-model-catalog.v1",
    total_models: totalModels,
    truncated: totalModels > MAX_AGENT_MODELS,
    models: models.slice(0, MAX_AGENT_MODELS),
    routing_policy: {
      explicit_model_first: true,
      configured_models_only: true,
      capability_match_required: true,
      default_fallback_allowed: true,
      invent_price_or_capability: false,
    },
  };
}

export function buildCanvasDirectorState(
  nodes: readonly CanvasNodeLike[],
  edges: readonly CanvasEdgeLike[],
  selectedNodeId?: string | null,
  projectStyleId?: string | null,
  canvasId = "default",
): Record<string, unknown> {
  const compactNodes = nodes.slice(0, MAX_DIRECTOR_NODES).map((node) => {
    const data = recordOf(node.data);
    const identity = buildCanvasNodeIdentity(node, canvasId);
    return {
      id: node.id,
      type: String(node.type || "unknown"),
      node_uri: identity.node_uri,
      parent_id: identity.parent_id,
      asset_id: identity.asset_id,
      asset_uri: identity.asset_uri,
      label: firstString(data, ["displayName", "label", "title", "name"], 80),
      role: firstString(data, [
        "reference_role",
        "referenceRole",
        "output_role",
        "role",
        "nodeRole",
        "assetKind",
        "media_role",
      ], 48),
      model_id: firstString(data, ["model", "modelId", "backend"], 120),
      generation_mode: firstString(data, ["generationMode", "genMode", "mode"], 64),
      capability_id: firstString(data, ["capabilityId", "capability_id"], 120),
      status: firstString(data, ["generationStatus", "taskStatus", "status"], 48),
      has_prompt: Boolean(firstString(data, ["prompt", "text", "script"], 2_048)),
      has_image: hasMedia(data, [
        "imageUrl", "image_url", "previewImageUrl", "preview_image_url",
        "outputImageUrl", "output_image_url", "referenceImageUrl", "reference_image_url",
        "committed_slot_url", "mediaUrl", "media_url",
      ]),
      has_video: hasMedia(data, [
        "videoUrl", "video_url", "outputVideoUrl", "output_video_url",
        "resultVideoUrl", "result_video_url", "previewVideoUrl", "preview_video_url",
        "sourceVideoUrl", "source_video_url",
      ]),
      has_audio: hasMedia(data, [
        "audioUrl", "audio_url", "outputAudioUrl", "output_audio_url",
        "resultAudioUrl", "result_audio_url", "previewAudioUrl", "preview_audio_url",
        "sourceAudioUrl", "source_audio_url",
      ]),
    };
  });
  const compactEdges = edges.slice(0, MAX_DIRECTOR_EDGES).map((edge) => ({
    id: trimmedString(edge.id, 120),
    source: edge.source,
    target: edge.target,
  }));
  const classify = (pattern: RegExp) => compactNodes
    .filter((node) => pattern.test(`${node.type} ${node.label ?? ""} ${node.role ?? ""}`))
    .map((node) => node.id);
  const deliveryNodes = classify(/compose|export|delivery|audio|合成|导出|交付|音频/i);
  const deliveryNodeSet = new Set(deliveryNodes);

  return {
    schema: "village.director-state.v1",
    selected_node_id: trimmedString(selectedNodeId),
    project_style_id: trimmedString(projectStyleId),
    truncated: nodes.length > MAX_DIRECTOR_NODES || edges.length > MAX_DIRECTOR_EDGES,
    total_nodes: nodes.length,
    total_edges: edges.length,
    nodes: compactNodes,
    edges: compactEdges,
    continuity: {
      identity_anchors: classify(/character|identity|portrait|角色|人物|身份/i),
      scene_anchors: classify(/scene|world|background|场景|世界|背景/i),
      shot_nodes: classify(/shot|storyboard|imagegen|video|镜头|分镜|视频/i)
        .filter((nodeId) => !deliveryNodeSet.has(nodeId)),
      delivery_nodes: deliveryNodes,
    },
    identity_policy: {
      node_uri: "canvas://{canvas_id}/nodes/{node_id}",
      label_is_display_only: true,
      bind_by: ["node_id", "asset_id", "asset_uri", "node_uri"],
      parent_id_is_group_context: true,
      do_not_guess_from_filename: true,
    },
    reference_manifest: buildCanvasReferenceManifest(nodes, edges, selectedNodeId, canvasId),
    asset_lineage: buildCompleteAssetLineage(nodes, edges),
  };
}
