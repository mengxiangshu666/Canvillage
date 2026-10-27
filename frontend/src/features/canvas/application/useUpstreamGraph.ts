// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';

import { useCanvasStore } from '@/stores/canvasStore';
import type { CanvasNode } from '../domain/canvasNodes';
import { extractUpstreamContent } from './graphContentResolver';
import { extractUpstreamImages } from './graphImageResolver';
import { upstreamNodesFromIndex } from './nodeIndex';
import type { UpstreamContent } from './ports';

/**
 * Subscribe to ONLY this node's direct (one-hop) upstream nodes, in edge-
 * connection order — not the whole `nodes` array.
 *
 * Why this exists (perf): React Flow rebuilds the `nodes` array on every drag
 * frame, but `applyNodeChanges` / `updateNodeData` reuse object identity for the
 * nodes that didn't change. A node component that subscribed to the full array
 * therefore re-rendered (and re-walked the graph) on *any* change anywhere on the
 * canvas.
 *
 * The selector is now a cheap lookup instead of a graph walk: the id sequence is
 * cached per `edges` reference (drags never touch edges) and the node objects
 * come from the shared id index. Result arrays are shallow-compared, so an
 * unrelated node's drag leaves this subscription referentially stable.
 */
export function useUpstreamNodes(nodeId: string): CanvasNode[] {
  return useCanvasStore(
    useShallow((state) => upstreamNodesFromIndex(state.nodes, state.edges, nodeId)),
  );
}

/** One-hop upstream contents (text / image / video / audio), per-node subscribed. */
export function useUpstreamContents(nodeId: string): UpstreamContent[] {
  const upstreamNodes = useUpstreamNodes(nodeId);
  return useMemo(() => upstreamNodes.map(extractUpstreamContent), [upstreamNodes]);
}

/** One-hop upstream referenceable image URLs (deduped), per-node subscribed. */
export function useUpstreamImages(nodeId: string): string[] {
  const upstreamNodes = useUpstreamNodes(nodeId);
  return useMemo(
    () => [...new Set(upstreamNodes.flatMap((node) => extractUpstreamImages(node)))],
    [upstreamNodes],
  );
}
