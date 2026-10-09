// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import type { CanvasEdge } from '@/features/canvas/domain/canvasNodes';
import { MAX_DATA_FLOW_EDGES, selectDataFlowEdgeIds } from './edgeDataFlowBudget';

function edge(id: string, source: string, target: string): CanvasEdge {
  return { id, source, target } as CanvasEdge;
}

describe('selectDataFlowEdgeIds', () => {
  it('returns an empty set without a selection', () => {
    const edges = [edge('a', 'n1', 'n2')];
    expect(selectDataFlowEdgeIds(edges, null).size).toBe(0);
    expect(selectDataFlowEdgeIds(edges, '').size).toBe(0);
  });

  it('caps the flow set so a hub node cannot light every incoming edge', () => {
    const edges = Array.from({ length: 12 }, (_, index) =>
      edge(`e-${index}`, 'hub', `leaf-${index}`),
    );
    const ids = selectDataFlowEdgeIds(edges, 'hub');

    expect(ids.size).toBe(MAX_DATA_FLOW_EDGES);
    // Stable order: the first ones in edges order, so the set does not flicker
    // between frames while the node stays selected.
    expect([...ids]).toEqual(['e-0', 'e-1', 'e-2', 'e-3']);
  });

  it('keeps only edges touching the selected node, on either end', () => {
    const edges = [
      edge('up', 'src', 'hub'),
      edge('down', 'hub', 'dst'),
      edge('unrelated', 'src', 'dst'),
    ];
    expect([...selectDataFlowEdgeIds(edges, 'hub')]).toEqual(['up', 'down']);
  });

  it('reuses the cached set for the same edges identity and selection', () => {
    const edges = [edge('a', 'hub', 'x')];
    const first = selectDataFlowEdgeIds(edges, 'hub');
    expect(selectDataFlowEdgeIds(edges, 'hub')).toBe(first);
    // A different selection must invalidate, even with the same edges array.
    expect(selectDataFlowEdgeIds(edges, 'x')).not.toBe(first);
  });
});
