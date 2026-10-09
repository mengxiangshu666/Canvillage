// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Position } from '@xyflow/react';

import { DEFAULT_NODE_WIDTH, type CanvasNode } from '@/features/canvas/domain/canvasNodes';

interface Point {
  x: number;
  y: number;
}

interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface NodeObstacle extends Rect {
  id: string;
}

export interface CanvasRoutingSnapshot {
  readonly obstacles: readonly NodeObstacle[];
  /** Obstacles sorted for bounded interval queries during edge routing. */
  readonly obstaclesByLeft: readonly NodeObstacle[];
  readonly obstaclesByRight: readonly NodeObstacle[];
}

interface RouteResult {
  path: string;
  labelX: number;
  labelY: number;
}

interface BuildOrthogonalRouteInput {
  sourceId?: string;
  targetId?: string;
  sourceX: number;
  sourceY: number;
  sourcePosition: Position;
  targetX: number;
  targetY: number;
  targetPosition: Position;
  nodes: CanvasNode[];
  smartAvoidance: boolean;
  routingSnapshot?: CanvasRoutingSnapshot;
}

const DEFAULT_NODE_HEIGHT = 200;
const EXPANDED_NODE_PADDING = 14;
const ENTRY_OFFSET = 24;
const LANE_GAP = 20;
const EPS = 0.0001;

function getOutDirection(position: Position, fallbackSign: number): number {
  if (position === Position.Right) {
    return 1;
  }
  if (position === Position.Left) {
    return -1;
  }
  return fallbackSign >= 0 ? 1 : -1;
}

function getInDirection(position: Position, fallbackSign: number): number {
  if (position === Position.Left) {
    return -1;
  }
  if (position === Position.Right) {
    return 1;
  }
  return fallbackSign <= 0 ? -1 : 1;
}

function nodeToRect(node: CanvasNode): Rect {
  const width =
    node.measured?.width ??
    (typeof node.style?.width === 'number' ? node.style.width : null) ??
    DEFAULT_NODE_WIDTH;
  const height =
    node.measured?.height ??
    (typeof node.style?.height === 'number' ? node.style.height : null) ??
    DEFAULT_NODE_HEIGHT;
  return {
    left: node.position.x - EXPANDED_NODE_PADDING,
    top: node.position.y - EXPANDED_NODE_PADDING,
    right: node.position.x + width + EXPANDED_NODE_PADDING,
    bottom: node.position.y + height + EXPANDED_NODE_PADDING,
  };
}

function buildRectangles(nodes: CanvasNode[], sourceId?: string, targetId?: string): NodeObstacle[] {
  return nodes
    .filter((node) => node.id !== sourceId && node.id !== targetId)
    .map((node) => ({ ...nodeToRect(node), id: node.id }));
}

const routingSnapshotByNodes = new WeakMap<CanvasNode[], CanvasRoutingSnapshot>();
const draggingNodeByNodes = new WeakMap<CanvasNode[], boolean>();

/** Build obstacle geometry once per immutable nodes-array identity. */
export function selectCanvasRoutingSnapshot(nodes: CanvasNode[]): CanvasRoutingSnapshot {
  const cached = routingSnapshotByNodes.get(nodes);
  if (cached) return cached;
  const obstacles = nodes.map((node) => ({ ...nodeToRect(node), id: node.id }));
  const snapshot: CanvasRoutingSnapshot = {
    obstacles,
    obstaclesByLeft: [...obstacles].sort(
      (left, right) => left.left - right.left || left.id.localeCompare(right.id),
    ),
    obstaclesByRight: [...obstacles].sort(
      (left, right) => left.right - right.right || left.id.localeCompare(right.id),
    ),
  };
  routingSnapshotByNodes.set(nodes, snapshot);
  return snapshot;
}

/**
 * Read the transient drag flag once per immutable nodes-array identity.
 * React Flow replaces that array on every drag frame; caching this scan keeps
 * each edge subscription O(1) while still letting the first edge observe the
 * authoritative interaction state.
 */
export function selectCanvasHasDraggingNode(nodes: CanvasNode[]): boolean {
  const cached = draggingNodeByNodes.get(nodes);
  if (cached !== undefined) return cached;
  const dragging = nodes.some((node) => node.dragging === true);
  draggingNodeByNodes.set(nodes, dragging);
  return dragging;
}

function verticalIntersectsRect(x: number, y1: number, y2: number, rect: Rect): boolean {
  if (x <= rect.left + EPS || x >= rect.right - EPS) {
    return false;
  }
  const top = Math.min(y1, y2);
  const bottom = Math.max(y1, y2);
  return bottom > rect.top + EPS && top < rect.bottom - EPS;
}

function horizontalIntersectsRect(y: number, x1: number, x2: number, rect: Rect): boolean {
  if (y <= rect.top + EPS || y >= rect.bottom - EPS) {
    return false;
  }
  const left = Math.min(x1, x2);
  const right = Math.max(x1, x2);
  return right > rect.left + EPS && left < rect.right - EPS;
}

function polylineIntersectsAnyRect(
  points: Point[],
  rects: readonly NodeObstacle[],
  sourceId?: string,
  targetId?: string,
): boolean {
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const isVertical = Math.abs(from.x - to.x) < EPS;

    for (const rect of rects) {
      if (rect.id === sourceId || rect.id === targetId) continue;
      if (isVertical) {
        if (verticalIntersectsRect(from.x, from.y, to.y, rect)) {
          return true;
        }
      } else if (Math.abs(from.y - to.y) < EPS) {
        if (horizontalIntersectsRect(from.y, from.x, to.x, rect)) {
          return true;
        }
      }
    }
  }
  return false;
}

/**
 * Every segment produced by buildPointsForLane stays inside this horizontal
 * span. Obstacles outside it cannot intersect the route, so avoid revisiting
 * them for every candidate lane. Keep the same inclusive boundary semantics
 * as candidate generation so route tie-breaking remains unchanged.
 */
function upperBoundByLeft(rects: readonly NodeObstacle[], value: number): number {
  let low = 0;
  let high = rects.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (rects[middle].left <= value) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  return low;
}

function lowerBoundByRight(rects: readonly NodeObstacle[], value: number): number {
  let low = 0;
  let high = rects.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (rects[middle].right < value) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  return low;
}

function selectObstaclesForRouteSpan(
  rects: readonly NodeObstacle[],
  minX: number,
  maxX: number,
  snapshot?: CanvasRoutingSnapshot,
): readonly NodeObstacle[] {
  if (snapshot) {
    const byLeft = snapshot.obstaclesByLeft;
    const byRight = snapshot.obstaclesByRight;
    const leftEnd = upperBoundByLeft(byLeft, maxX);
    const rightStart = lowerBoundByRight(byRight, minX);
    const leftCandidates = leftEnd;
    const rightCandidates = byRight.length - rightStart;

    // Query the smaller side of the interval intersection. This keeps a long
    // canvas with sparse local obstacles from paying for every node per edge.
    if (leftCandidates <= rightCandidates) {
      const relevant: NodeObstacle[] = [];
      for (let index = 0; index < leftEnd; index += 1) {
        const rect = byLeft[index];
        if (rect && rect.right >= minX) relevant.push(rect);
      }
      return relevant;
    }
    const relevant: NodeObstacle[] = [];
    for (let index = rightStart; index < byRight.length; index += 1) {
      const rect = byRight[index];
      if (rect && rect.left <= maxX) relevant.push(rect);
    }
    return relevant;
  }

  // The fallback list is not guaranteed to be sorted. Filtering the complete
  // list is both correct for arbitrary node order and only used when a caller
  // did not provide the shared indexed snapshot.
  return rects.filter((rect) => rect.right >= minX && rect.left <= maxX);
}

function getMidpoint(points: Point[]): Point {
  let totalLength = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    totalLength += Math.hypot(to.x - from.x, to.y - from.y);
  }

  if (totalLength < EPS) {
    return points[0] ?? { x: 0, y: 0 };
  }

  let traversed = 0;
  const half = totalLength / 2;
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const segmentLength = Math.hypot(to.x - from.x, to.y - from.y);
    if (traversed + segmentLength >= half) {
      const ratio = (half - traversed) / segmentLength;
      return {
        x: from.x + (to.x - from.x) * ratio,
        y: from.y + (to.y - from.y) * ratio,
      };
    }
    traversed += segmentLength;
  }

  return points[points.length - 1] ?? { x: 0, y: 0 };
}

function toSvgPath(points: Point[]): string {
  if (points.length === 0) {
    return '';
  }
  const [first, ...rest] = points;
  return `M ${first.x} ${first.y} ${rest.map((point) => `L ${point.x} ${point.y}`).join(' ')}`;
}

function buildPointsForLane(
  sourceX: number,
  sourceY: number,
  sourceOutX: number,
  targetX: number,
  targetY: number,
  targetInX: number,
  laneY: number
): Point[] {
  return [
    { x: sourceX, y: sourceY },
    { x: sourceOutX, y: sourceY },
    { x: sourceOutX, y: laneY },
    { x: targetInX, y: laneY },
    { x: targetInX, y: targetY },
    { x: targetX, y: targetY },
  ];
}

function candidatePenalty(laneY: number, sourceY: number, targetY: number): number {
  const midY = (sourceY + targetY) / 2;
  return (
    Math.abs(laneY - midY) * 0.45 +
    Math.abs(laneY - sourceY) * 0.3 +
    Math.abs(laneY - targetY) * 0.25
  );
}

function pickLaneY(
  sourceX: number,
  sourceY: number,
  sourceOutX: number,
  targetX: number,
  targetY: number,
  targetInX: number,
  rects: readonly NodeObstacle[],
  sourceId?: string,
  targetId?: string,
  snapshot?: CanvasRoutingSnapshot,
): number {
  const minX = Math.min(sourceOutX, targetInX, sourceX, targetX);
  const maxX = Math.max(sourceOutX, targetInX, sourceX, targetX);
  const routeRects = selectObstaclesForRouteSpan(rects, minX, maxX, snapshot);
  const candidates = new Set<number>([sourceY, targetY, (sourceY + targetY) / 2]);

  for (const rect of routeRects) {
    if (rect.id === sourceId || rect.id === targetId) continue;
    candidates.add(rect.top - LANE_GAP);
    candidates.add(rect.bottom + LANE_GAP);
  }

  const sorted = Array.from(candidates).sort(
    (left, right) => candidatePenalty(left, sourceY, targetY) - candidatePenalty(right, sourceY, targetY)
  );

  for (const laneY of sorted) {
    const points = buildPointsForLane(sourceX, sourceY, sourceOutX, targetX, targetY, targetInX, laneY);
    if (!polylineIntersectsAnyRect(points, routeRects, sourceId, targetId)) {
      return laneY;
    }
  }

  // Every route segment stays within the same horizontal span, so an obstacle
  // outside routeRects cannot block either fallback lane. Reuse the bounded
  // set instead of scanning the complete canvas again for every edge.
  const usableRects = routeRects.filter((rect) => rect.id !== sourceId && rect.id !== targetId);
  const upperBound = usableRects.length > 0 ? Math.min(...usableRects.map((rect) => rect.top)) - LANE_GAP : sourceY - 80;
  const lowerBound =
    usableRects.length > 0 ? Math.max(...usableRects.map((rect) => rect.bottom)) + LANE_GAP : targetY + 80;
  return candidatePenalty(upperBound, sourceY, targetY) <= candidatePenalty(lowerBound, sourceY, targetY)
    ? upperBound
    : lowerBound;
}

export function buildOrthogonalRoute(input: BuildOrthogonalRouteInput): RouteResult {
  const {
    sourceId,
    targetId,
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    nodes,
    smartAvoidance,
    routingSnapshot,
  } = input;

  const horizontalSign = targetX - sourceX >= 0 ? 1 : -1;
  const sourceOutDirection = getOutDirection(sourcePosition, horizontalSign);
  const targetInDirection = getInDirection(targetPosition, horizontalSign);
  const sourceOutX = sourceX + sourceOutDirection * ENTRY_OFFSET;
  const targetInX = targetX + targetInDirection * ENTRY_OFFSET;

  let laneY = (sourceY + targetY) / 2;
  if (smartAvoidance) {
    const rects = routingSnapshot?.obstacles ?? buildRectangles(nodes, sourceId, targetId);
    laneY = pickLaneY(
      sourceX,
      sourceY,
      sourceOutX,
      targetX,
      targetY,
      targetInX,
      rects,
      sourceId,
      targetId,
      routingSnapshot,
    );
  }

  const points = buildPointsForLane(sourceX, sourceY, sourceOutX, targetX, targetY, targetInX, laneY);
  const midpoint = getMidpoint(points);
  return {
    path: toSvgPath(points),
    labelX: midpoint.x,
    labelY: midpoint.y,
  };
}
