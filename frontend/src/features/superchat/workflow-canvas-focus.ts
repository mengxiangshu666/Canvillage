import { useCanvasStore } from "@/stores/canvasStore";

export function focusWorkflowCanvasNodes(
  targetNodeIds: readonly string[],
): boolean {
  const store = useCanvasStore.getState();
  const existingTargetIds = targetNodeIds.filter((candidate) => (
    store.nodes.some((node) => node.id === candidate)
  ));
  const nodeId = existingTargetIds[0];
  if (!nodeId) return false;

  // React Flow derives `selectedNodeId` from each node's `selected` flag, so a
  // recovery focus must update both representations to stay highlighted.
  const selectedTargetIds = new Set([nodeId]);
  store.onNodesChange(
    store.nodes
      .filter((node) => Boolean(node.selected) !== selectedTargetIds.has(node.id))
      .map((node) => ({
        id: node.id,
        type: "select" as const,
        selected: selectedTargetIds.has(node.id),
      })),
  );
  store.setSelectedNode(nodeId);
  store.requestFocusNodes(existingTargetIds);
  return true;
}
