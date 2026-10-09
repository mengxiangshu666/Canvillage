import { describe, expect, it } from 'vitest';
import { Position } from '@xyflow/react';

import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import {
  buildOrthogonalRoute,
  selectCanvasHasDraggingNode,
  selectCanvasRoutingSnapshot,
} from './edgeRouting';

function node(id: string, x: number, y: number): CanvasNode {
  return {
    id,
    type: 'textAnnotationNode',
    position: { x, y },
    data: {},
  } as CanvasNode;
}

describe('canvas edge routing snapshot', () => {
  it('caches transient dragging detection by nodes-array identity', () => {
    const idle = [node('idle', 0, 0)];
    const dragging = [{ ...node('dragging', 0, 0), dragging: true }];

    expect(selectCanvasHasDraggingNode(idle)).toBe(false);
    expect(selectCanvasHasDraggingNode(idle)).toBe(false);
    expect(selectCanvasHasDraggingNode(dragging)).toBe(true);
    expect(selectCanvasHasDraggingNode(dragging)).toBe(true);
  });
  it('caches obstacle geometry and preserves the direct route result', () => {
    const nodes = [node('source', 0, 0), node('obstacle', 180, -40), node('target', 420, 120)];
    const snapshot = selectCanvasRoutingSnapshot(nodes);

    expect(selectCanvasRoutingSnapshot(nodes)).toBe(snapshot);

    const input = {
      sourceId: 'source',
      targetId: 'target',
      sourceX: 120,
      sourceY: 80,
      sourcePosition: Position.Right,
      targetX: 420,
      targetY: 180,
      targetPosition: Position.Left,
      nodes,
      smartAvoidance: true,
    } as const;

    expect(buildOrthogonalRoute(input)).toEqual(
      buildOrthogonalRoute({ ...input, routingSnapshot: snapshot }),
    );
    expect(snapshot.obstaclesByLeft.map((item) => item.id)).toEqual([
      'source',
      'obstacle',
      'target',
    ]);
    expect(snapshot.obstaclesByRight.map((item) => item.id)).toEqual([
      'source',
      'obstacle',
      'target',
    ]);
  });

  it('keeps indexed interval selection equivalent with sparse distant obstacles', () => {
    const source = node('source', 0, 0);
    const target = node('target', 420, 120);
    const local = node('local', 180, -40);
    const distant = node('distant', 8_000, 8_000);
    const nodes = [source, target, local, distant];
    const snapshot = selectCanvasRoutingSnapshot(nodes);
    const input = {
      sourceId: source.id,
      targetId: target.id,
      sourceX: 120,
      sourceY: 80,
      sourcePosition: Position.Right,
      targetX: 420,
      targetY: 180,
      targetPosition: Position.Left,
      nodes,
      smartAvoidance: true,
    } as const;

    expect(buildOrthogonalRoute(input)).toEqual(
      buildOrthogonalRoute({ ...input, routingSnapshot: snapshot }),
    );
  });

  it('keeps the no-snapshot fallback correct when distant obstacles come first', () => {
    const source = node('source', 0, 0);
    const target = node('target', 420, 120);
    const distant = node('distant', -8_000, -8_000);
    const local = node('local', 180, -40);
    const nodes = [source, target, distant, local];
    const input = {
      sourceId: source.id,
      targetId: target.id,
      sourceX: 120,
      sourceY: 80,
      sourcePosition: Position.Right,
      targetX: 420,
      targetY: 180,
      targetPosition: Position.Left,
      nodes,
      smartAvoidance: true,
    } as const;

    expect(buildOrthogonalRoute(input)).toEqual(
      buildOrthogonalRoute({
        ...input,
        nodes: [source, target, local],
        routingSnapshot: selectCanvasRoutingSnapshot([source, target, local]),
      }),
    );
  });

  it('keeps far-away obstacles out of the per-lane intersection scan', () => {
    const source = node('source', 0, 0);
    const target = node('target', 420, 120);
    const farAway = node('far-away', 2_000, 2_000);
    const input = {
      sourceId: source.id,
      targetId: target.id,
      sourceX: 120,
      sourceY: 80,
      sourcePosition: Position.Right,
      targetX: 420,
      targetY: 180,
      targetPosition: Position.Left,
      nodes: [source, target],
      smartAvoidance: true,
    };

    const baseline = buildOrthogonalRoute(input);
    const withFarAwayObstacle = buildOrthogonalRoute({
      ...input,
      nodes: [...input.nodes, farAway],
      routingSnapshot: selectCanvasRoutingSnapshot([...input.nodes, farAway]),
    });

    expect(withFarAwayObstacle).toEqual(baseline);
  });

  it('still routes around an obstacle inside the source-target span', () => {
    const source = node('source', 0, 0);
    const target = node('target', 420, 120);
    const obstacle = node('obstacle', 180, -40);
    const input = {
      sourceId: source.id,
      targetId: target.id,
      sourceX: 120,
      sourceY: 80,
      sourcePosition: Position.Right,
      targetX: 420,
      targetY: 180,
      targetPosition: Position.Left,
      smartAvoidance: true,
    } as const;

    const direct = buildOrthogonalRoute({
      ...input,
      nodes: [source, target],
    });
    const detoured = buildOrthogonalRoute({
      ...input,
      nodes: [source, obstacle, target],
      routingSnapshot: selectCanvasRoutingSnapshot([source, obstacle, target]),
    });

    expect(detoured).not.toEqual(direct);
  });
});
