// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type CanvasNode } from './canvasNodes';
import {
  estimateNewNodePlacementSize,
  resolveCollisionFreeNodePlacement,
} from './nodePlacement';

function node(id: string, x: number, y: number, width = 520, height = 380): CanvasNode {
  return {
    id,
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x, y },
    data: { imageUrl: null, previewImageUrl: null, aspectRatio: '1:1' },
    measured: { width, height },
  } as CanvasNode;
}

describe('collision-free node placement', () => {
  it('keeps a deliberate empty-space placement unchanged', () => {
    const desired = { x: 1200, y: 800 };
    expect(
      resolveCollisionFreeNodePlacement(desired, CANVAS_NODE_TYPES.imageGen, [node('a', 0, 0)]),
    ).toEqual(desired);
  });

  it('moves a new node to the nearest clear slot when the drop overlaps', () => {
    const desired = { x: 100, y: 100 };
    const resolved = resolveCollisionFreeNodePlacement(
      desired,
      CANVAS_NODE_TYPES.imageGen,
      [node('a', 100, 100)],
    );
    const size = estimateNewNodePlacementSize(CANVAS_NODE_TYPES.imageGen);
    expect(resolved).toEqual({ x: desired.x + size.width + 24, y: desired.y });
  });

  it('does not compare a new top-level node with children in a group coordinate space', () => {
    const desired = { x: 100, y: 100 };
    expect(
      resolveCollisionFreeNodePlacement(desired, CANVAS_NODE_TYPES.imageGen, [
        { ...node('child', 100, 100), parentId: 'group-1' },
      ]),
    ).toEqual(desired);
  });
});
