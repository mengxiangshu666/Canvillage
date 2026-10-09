// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  createCanvasUserTemplate,
  type CanvasUserTemplateDefinition,
} from '@/features/canvas/application/userCanvasTemplates';
import {
  isUpstreamConnectionAllowed,
  nodeHasSourceHandle,
  nodeHasTargetHandle,
} from '@/features/canvas/domain/nodeRegistry';
import { useCanvasStore } from '@/stores/canvasStore';

const MAX_HISTORY_STEPS = 50;

export function addCanvasUserTemplateToCanvas(
  template: CanvasUserTemplateDefinition,
  position: { x: number; y: number },
): { title: string; nodeIds: string[] } | null {
  const state = useCanvasStore.getState();
  const creation = createCanvasUserTemplate(template, position);
  if (!creation) {
    return null;
  }
  const nodeById = new Map(creation.nodes.map((node) => [node.id, node] as const));
  const isValid = creation.edges.every((edge) => {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    return Boolean(
      source
      && target
      && nodeHasSourceHandle(source.type)
      && nodeHasTargetHandle(target.type)
      && isUpstreamConnectionAllowed(source.type, target.type),
    );
  });
  if (!isValid) {
    return null;
  }

  useCanvasStore.setState({
    nodes: [...state.nodes, ...creation.nodes],
    edges: [...state.edges, ...creation.edges],
    selectedNodeId: creation.nodeIds[creation.nodeIds.length - 1] ?? null,
    history: {
      past: [...state.history.past, {
        nodes: state.nodes,
        edges: state.edges,
      }].slice(-MAX_HISTORY_STEPS),
      future: [],
    },
    dragHistorySnapshot: null,
    userEditsSinceHydrate: state.userEditsSinceHydrate + 1,
    lastMutationSource: 'user_edit',
  });
  return { title: creation.title, nodeIds: creation.nodeIds };
}
