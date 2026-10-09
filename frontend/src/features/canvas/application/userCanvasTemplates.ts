// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { XYPosition } from '@xyflow/react';

import type {
  CanvasUserTemplateDetail,
  CanvasUserTemplateEdge,
  CanvasUserTemplateNode,
} from '@/api/canvas';
import type {
  CanvasEdge,
  CanvasNode,
  CanvasNodeData,
  CanvasNodeType,
} from '@/features/canvas/domain/canvasNodes';
import {
  isUpstreamConnectionAllowed,
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from '@/features/canvas/domain/nodeRegistry';

import { canvasNodeFactory } from './canvasServices';

const RUNTIME_NODE_DATA_KEYS = new Set([
  'agent_command_id',
  'starter_workflow_id',
  'imageUrl',
  'previewImageUrl',
  'videoUrl',
  'resultVideoUrl',
  'generationBatch',
  'dialogueAudioUrl',
  'dialogueAudioCacheKey',
  'resourceMeta',
  'productionMetadata',
  'committed_at',
  'committed_slot_url',
  'isGenerating',
  'generationStartedAt',
  'generationError',
  'generationErrorDetails',
  'generationErrorRequestId',
  'generationErrorStage',
  'generationErrorSuggestedAction',
  'generationErrorCode',
  'generationErrorRetryable',
  'generationRecoveryJobId',
  'generationRecoveryTaskType',
  'isUploading',
  'uploadError',
  'isAnalyzing',
  'analysisResult',
  'analysisError',
  'isSeparatingAv',
  'resultMirroredAt',
  'originNodeId',
  'village_canvas_agent_viewport_placed_command',
]);

export interface CanvasUserTemplateCreatePayload {
  title: string;
  description: string;
  nodes: CanvasUserTemplateNode[];
  edges: CanvasUserTemplateEdge[];
}

export interface CanvasUserTemplateCreation {
  title: string;
  nodeIds: string[];
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

export type CanvasUserTemplateDefinition = CanvasUserTemplateDetail;

function positiveNodeSize(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

function cloneJsonObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }
  try {
    const cloned = JSON.parse(JSON.stringify(value));
    return cloned && typeof cloned === 'object' && !Array.isArray(cloned)
      ? cloned as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

export function sanitizeCanvasTemplateNodeData(
  value: unknown,
): Record<string, unknown> {
  const data = cloneJsonObject(value);
  for (const key of RUNTIME_NODE_DATA_KEYS) {
    delete data[key];
  }
  return data;
}

function collectSelectedWithDescendants(
  nodes: readonly CanvasNode[],
  selectedNodeIds: readonly string[],
): Set<string> {
  const included = new Set(selectedNodeIds.map((value) => String(value || '').trim()).filter(Boolean));
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      if (!included.has(node.id) && node.parentId && included.has(node.parentId)) {
        included.add(node.id);
        changed = true;
      }
    }
  }
  return included;
}

function positionInTemplate(
  node: CanvasNode,
  nodeById: ReadonlyMap<string, CanvasNode>,
): XYPosition {
  let x = 0;
  let y = 0;
  let current: CanvasNode | undefined = node;
  const seen = new Set<string>();
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    x += current.position.x;
    y += current.position.y;
    current = current.parentId ? nodeById.get(current.parentId) : undefined;
  }
  return { x, y };
}

export function extractCanvasUserTemplateGraph(input: {
  nodes: readonly CanvasNode[];
  edges: readonly CanvasEdge[];
  selectedNodeIds: readonly string[];
}): Omit<CanvasUserTemplateCreatePayload, 'title' | 'description'> | null {
  const included = collectSelectedWithDescendants(input.nodes, input.selectedNodeIds);
  if (included.size === 0) {
    return null;
  }
  const nodeById = new Map(input.nodes.map((node) => [node.id, node] as const));
  const selectedNodes = input.nodes.filter((node) => included.has(node.id));
  if (selectedNodes.length === 0 || selectedNodes.length > 100) {
    return null;
  }

  const positions = new Map(
    selectedNodes.map((node) => [node.id, positionInTemplate(node, nodeById)] as const),
  );
  const minX = Math.min(...[...positions.values()].map((position) => position.x));
  const minY = Math.min(...[...positions.values()].map((position) => position.y));
  const keyByNodeId = new Map(
    selectedNodes.map((node, index) => [node.id, `n${index + 1}`] as const),
  );
  const nodes: CanvasUserTemplateNode[] = selectedNodes.map((node) => {
    const position = positions.get(node.id) ?? node.position;
    const spec: CanvasUserTemplateNode = {
      key: keyByNodeId.get(node.id) as string,
      type: node.type,
      offset: {
        x: position.x - minX,
        y: position.y - minY,
      },
      data: sanitizeCanvasTemplateNodeData(node.data),
    };
    const width = positiveNodeSize(node.width);
    const height = positiveNodeSize(node.height);
    if (width !== undefined) spec.width = width;
    if (height !== undefined) spec.height = height;
    if (node.parentId && included.has(node.parentId)) {
      spec.parent_key = keyByNodeId.get(node.parentId);
    }
    return spec;
  });

  const seenEdges = new Set<string>();
  const edges: CanvasUserTemplateEdge[] = [];
  for (const edge of input.edges) {
    if (!included.has(edge.source) || !included.has(edge.target) || edge.source === edge.target) {
      continue;
    }
    const source = keyByNodeId.get(edge.source);
    const target = keyByNodeId.get(edge.target);
    if (!source || !target || seenEdges.has(`${source}->${target}`)) {
      continue;
    }
    seenEdges.add(`${source}->${target}`);
    const spec: CanvasUserTemplateEdge = { source, target };
    if (edge.sourceHandle) spec.source_handle = String(edge.sourceHandle);
    if (edge.targetHandle) spec.target_handle = String(edge.targetHandle);
    const relation = edge.data && typeof edge.data === 'object'
      ? (edge.data as Record<string, unknown>).relation
      : undefined;
    if (typeof relation === 'string' && relation.trim()) {
      spec.relation = relation.trim();
    }
    edges.push(spec);
  }
  return { nodes, edges };
}

function nodeDepth(
  node: CanvasUserTemplateNode,
  byKey: ReadonlyMap<string, CanvasUserTemplateNode>,
): number {
  let depth = 0;
  let parentKey = node.parent_key;
  const seen = new Set<string>();
  while (parentKey && !seen.has(parentKey)) {
    seen.add(parentKey);
    depth += 1;
    parentKey = byKey.get(parentKey)?.parent_key;
  }
  return depth;
}

export function createCanvasUserTemplate(
  template: CanvasUserTemplateDetail,
  origin: XYPosition,
): CanvasUserTemplateCreation | null {
  if (!Array.isArray(template.nodes) || template.nodes.length === 0) {
    return null;
  }
  const byKey = new Map(template.nodes.map((node) => [node.key, node] as const));
  const sorted = [...template.nodes].sort(
    (left, right) => nodeDepth(left, byKey) - nodeDepth(right, byKey),
  );
  const absoluteByKey = new Map(
    sorted.map((node) => [
      node.key,
      {
        x: Number(node.offset?.x ?? 0),
        y: Number(node.offset?.y ?? 0),
      },
    ] as const),
  );
  const nodeByKey = new Map<string, CanvasNode>();
  const nodes: CanvasNode[] = [];
  for (const spec of sorted) {
    const absolute = absoluteByKey.get(spec.key) ?? { x: 0, y: 0 };
    const parent = spec.parent_key ? nodeByKey.get(spec.parent_key) : undefined;
    const parentAbsolute = spec.parent_key
      ? absoluteByKey.get(spec.parent_key)
      : undefined;
    const position = parent
      ? {
          x: absolute.x - (parentAbsolute?.x ?? 0),
          y: absolute.y - (parentAbsolute?.y ?? 0),
        }
      : {
          x: origin.x + absolute.x,
          y: origin.y + absolute.y,
        };
    const node = canvasNodeFactory.createNode(
      spec.type as CanvasNodeType,
      position,
      sanitizeCanvasTemplateNodeData(spec.data) as Partial<CanvasNodeData>,
    );
    if (parent) {
      node.parentId = parent.id;
    }
    if (spec.width !== undefined) node.width = spec.width;
    if (spec.height !== undefined) node.height = spec.height;
    nodes.push(node);
    nodeByKey.set(spec.key, node);
  }

  const edges = template.edges.flatMap((spec, index) => {
    const source = nodeByKey.get(spec.source);
    const target = nodeByKey.get(spec.target);
    if (
      !source
      || !target
      || !nodeHasSourceHandle(source.type)
      || !nodeHasTargetHandle(target.type)
      || !isUpstreamConnectionAllowed(source.type, target.type)
    ) {
      return [];
    }
    return [{
      id: `user-template-${template.id}-${index}-${source.id}-${target.id}`,
      source: source.id,
      target: target.id,
      sourceHandle: spec.source_handle ?? 'source',
      targetHandle: spec.target_handle ?? 'target',
      type: 'disconnectableEdge',
      ...(spec.relation ? { data: { relation: spec.relation } } : {}),
    } satisfies CanvasEdge];
  });
  return {
    title: template.title,
    nodeIds: nodes.map((node) => node.id),
    nodes,
    edges,
  };
}
