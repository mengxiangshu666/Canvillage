// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';

import { useCanvasStore } from '@/stores/canvasStore';
import {
  graphSliceOf,
  scriptShotVideoChain,
  type CanvasGraphSlice,
} from './scriptShotVideos';

/**
 * The slice of the canvas a script node's views actually read.
 *
 * The three review views used to subscribe to `{ nodes, edges }` — a fresh
 * object on every store write, so `useShallow` compared nothing and every drag
 * frame re-rendered the whole script table and re-ran its derivations.
 *
 * Here the selector returns a *flat* array of the chain's node/edge objects.
 * React Flow keeps object identity for nodes that did not change, so dragging an
 * unrelated node leaves the array item-for-item equal and the views skip the
 * render; any real change on the chain (storyboard settles, video finishes,
 * keyframe reviewed) replaces exactly that object.
 */
export function useScriptGraphSlice(scriptNodeId: string): CanvasGraphSlice {
  const chain = useCanvasStore(
    useShallow((state) => scriptShotVideoChain(scriptNodeId, state.nodes, state.edges)),
  );
  return useMemo(() => graphSliceOf(chain), [chain]);
}
