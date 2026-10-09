// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  DEFAULT_NODE_WIDTH,
  type CanvasNode,
  type CanvasNodeType,
} from './canvasNodes';

export interface NodePlacementSize {
  width: number;
  height: number;
}

export interface NodePlacementPoint {
  x: number;
  y: number;
}

const PLACEMENT_GAP = 24;
const PLACEMENT_RING_LIMIT = 12;
const DEFAULT_NODE_HEIGHT = 240;

// Before React Flow measures a new node, use its conservative minimum footprint.
// The estimate only affects collision avoidance; the node keeps its own sizing rules.
const NEW_NODE_SIZES: Partial<Record<CanvasNodeType, NodePlacementSize>> = {
  [CANVAS_NODE_TYPES.imageGen]: { width: 520, height: 380 },
  [CANVAS_NODE_TYPES.imageEdit]: { width: 560, height: 460 },
  [CANVAS_NODE_TYPES.video]: { width: 520, height: 400 },
  [CANVAS_NODE_TYPES.videoStory]: { width: 520, height: 360 },
  [CANVAS_NODE_TYPES.audio]: { width: 400, height: 240 },
  [CANVAS_NODE_TYPES.textAnnotation]: { width: 420, height: 280 },
  [CANVAS_NODE_TYPES.script]: { width: 440, height: 300 },
  [CANVAS_NODE_TYPES.upload]: { width: 480, height: 360 },
  [CANVAS_NODE_TYPES.exportImage]: { width: 480, height: 360 },
  [CANVAS_NODE_TYPES.beatContext]: { width: 460, height: 600 },
  [CANVAS_NODE_TYPES.storyboardSplit]: { width: 520, height: 420 },
  [CANVAS_NODE_TYPES.storyboardGen]: { width: 520, height: 520 },
};

function sizeOfExistingNode(node: CanvasNode): NodePlacementSize {
  const styleWidth = typeof node.style?.width === 'number' ? node.style.width : undefined;
  const styleHeight = typeof node.style?.height === 'number' ? node.style.height : undefined;
  return {
    width: node.measured?.width ?? node.width ?? styleWidth ?? DEFAULT_NODE_WIDTH,
    height: node.measured?.height ?? node.height ?? styleHeight ?? DEFAULT_NODE_HEIGHT,
  };
}

export function estimateNewNodePlacementSize(type: CanvasNodeType): NodePlacementSize {
  return NEW_NODE_SIZES[type] ?? { width: DEFAULT_NODE_WIDTH, height: DEFAULT_NODE_HEIGHT };
}

function overlaps(
  point: NodePlacementPoint,
  size: NodePlacementSize,
  node: CanvasNode,
): boolean {
  const nodeSize = sizeOfExistingNode(node);
  return (
    point.x < node.position.x + nodeSize.width + PLACEMENT_GAP
    && point.x + size.width + PLACEMENT_GAP > node.position.x
    && point.y < node.position.y + nodeSize.height + PLACEMENT_GAP
    && point.y + size.height + PLACEMENT_GAP > node.position.y
  );
}

function collides(
  point: NodePlacementPoint,
  size: NodePlacementSize,
  nodes: readonly CanvasNode[],
): boolean {
  return nodes.some((node) => !node.parentId && overlaps(point, size, node));
}

/** Keep an explicitly chosen drop point unless it would visibly cover another node. */
export function resolveCollisionFreeNodePlacement(
  desired: NodePlacementPoint,
  type: CanvasNodeType,
  nodes: readonly CanvasNode[],
): NodePlacementPoint {
  const size = estimateNewNodePlacementSize(type);
  if (!collides(desired, size, nodes)) {
    return desired;
  }

  const stepX = size.width + PLACEMENT_GAP;
  const stepY = size.height + PLACEMENT_GAP;
  const candidates: NodePlacementPoint[] = [];
  for (let ring = 1; ring <= PLACEMENT_RING_LIMIT; ring += 1) {
    candidates.push(
      { x: desired.x + ring * stepX, y: desired.y },
      { x: desired.x, y: desired.y + ring * stepY },
      { x: desired.x - ring * stepX, y: desired.y },
      { x: desired.x, y: desired.y - ring * stepY },
      { x: desired.x + ring * stepX, y: desired.y + ring * stepY },
      { x: desired.x - ring * stepX, y: desired.y + ring * stepY },
      { x: desired.x + ring * stepX, y: desired.y - ring * stepY },
      { x: desired.x - ring * stepX, y: desired.y - ring * stepY },
    );
  }

  return candidates.find((candidate) => !collides(candidate, size, nodes)) ?? desired;
}
