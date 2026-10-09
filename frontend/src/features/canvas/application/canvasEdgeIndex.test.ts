// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { CanvasEdge } from '@/features/canvas/domain/canvasNodes';
import { selectConnectedCanvasEdges } from './canvasEdgeIndex';

describe('canvas edge adjacency index', () => {
  it('indexes an edge array once and reuses stable node slices', () => {
    let sourceReads = 0;
    let targetReads = 0;
    const edge = {
      id: 'edge-a-b',
      get source() {
        sourceReads += 1;
        return 'a';
      },
      get target() {
        targetReads += 1;
        return 'b';
      },
    } as CanvasEdge;
    const edges = [edge];

    const forA = selectConnectedCanvasEdges(edges, 'a');
    const forB = selectConnectedCanvasEdges(edges, 'b');
    const forAAgain = selectConnectedCanvasEdges(edges, 'a');

    expect(forA).toEqual([edge]);
    expect(forB).toEqual([edge]);
    expect(forAAgain).toBe(forA);
    expect(sourceReads).toBe(1);
    expect(targetReads).toBe(1);
  });

  it('does not duplicate self-loop edges and isolates a replacement edge array', () => {
    const selfLoop = { id: 'loop', source: 'a', target: 'a' } as CanvasEdge;
    const firstEdges = [selfLoop];
    const secondEdges = [...firstEdges, { id: 'a-c', source: 'a', target: 'c' } as CanvasEdge];

    expect(selectConnectedCanvasEdges(firstEdges, 'a')).toEqual([selfLoop]);
    expect(selectConnectedCanvasEdges(firstEdges, 'missing')).toEqual([]);
    expect(selectConnectedCanvasEdges(secondEdges, 'a')).toHaveLength(2);
    expect(selectConnectedCanvasEdges(secondEdges, 'c')).toEqual([secondEdges[1]]);
  });
});
