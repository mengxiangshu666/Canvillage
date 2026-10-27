// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import type { CanvasEdge, CanvasNode } from '../domain/canvasNodes';
import {
  __nodeIndexBuildCount,
  __resetNodeIndexCaches,
  nodeIndexFor,
  selectNodeById,
  selectNodesByIds,
  upstreamNodeIdsInEdgeOrder,
  upstreamNodesFromIndex,
} from './nodeIndex';

function node(id: string, overrides: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id,
    type: 'imageGen',
    position: { x: 0, y: 0 },
    data: {},
    ...overrides,
  } as CanvasNode;
}

function edge(id: string, source: string, target: string): CanvasEdge {
  return { id, source, target } as CanvasEdge;
}

beforeEach(() => {
  __resetNodeIndexCaches();
});

describe('nodeIndex', () => {
  it('builds the id index once per nodes array and resolves by id', () => {
    const nodes = [node('a'), node('b'), node('c')];

    expect(selectNodeById({ nodes }, 'b')?.id).toBe('b');
    expect(selectNodeById({ nodes }, 'missing')).toBeNull();
    expect(selectNodeById({ nodes }, null)).toBeNull();
    expect(selectNodesByIds({ nodes }, ['c', 'missing', 'a']).map((item) => item.id)).toEqual([
      'c',
      'a',
    ]);

    // Same array reference: the map is reused, not rebuilt.
    nodeIndexFor(nodes);
    nodeIndexFor(nodes);
    expect(__nodeIndexBuildCount()).toBe(1);

    // A new array (a drag frame) pays one rebuild, then is shared again.
    const nextFrame = nodes.map((item) => (item.id === 'b' ? { ...item, position: { x: 8, y: 0 } } : item));
    expect(selectNodeById({ nodes: nextFrame }, 'a')?.id).toBe('a');
    expect(selectNodeById({ nodes: nextFrame }, 'b')?.position).toEqual({ x: 8, y: 0 });
    expect(__nodeIndexBuildCount()).toBe(2);
  });

  it('keeps the upstream id sequence stable across position-only frames', () => {
    const edges = [edge('e1', 'up-1', 'target'), edge('e2', 'up-2', 'target')];
    const first = upstreamNodeIdsInEdgeOrder(edges, 'target');
    expect(first).toEqual(['up-1', 'up-2']);

    // Position frames replace nodes, never edges — the cached sequence is reused.
    expect(upstreamNodeIdsInEdgeOrder(edges, 'target')).toBe(first);

    const rerouted = [edge('e1', 'up-3', 'target'), edge('e2', 'up-2', 'target')];
    expect(upstreamNodeIdsInEdgeOrder(rerouted, 'target')).toEqual(['up-3', 'up-2']);
  });

  it('returns current node objects, so data updates are never served from a stale cache', () => {
    const edges = [edge('e1', 'up-1', 'target')];
    const nodes = [node('up-1', { data: { imageUrl: '/old.png' } }), node('target')];
    expect(upstreamNodesFromIndex(nodes, edges, 'target')[0]?.data.imageUrl).toBe('/old.png');

    const updated = nodes.map((item) => (
      item.id === 'up-1' ? { ...item, data: { imageUrl: '/new.png' } } : item
    ));
    expect(upstreamNodesFromIndex(updated, edges, 'target')[0]?.data.imageUrl).toBe('/new.png');
  });

  it('dedupes duplicate edges to the same source and preserves edge order', () => {
    const edges = [
      edge('e1', 'b', 'target'),
      edge('e2', 'a', 'target'),
      edge('e3', 'b', 'target'),
      edge('e4', 'a', 'other'),
    ];
    expect(upstreamNodeIdsInEdgeOrder(edges, 'target')).toEqual(['b', 'a']);
  });
});
