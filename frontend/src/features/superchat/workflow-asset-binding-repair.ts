import type { WorkflowStepRecovery } from "./workflow-failure-dismissal";

export interface WorkflowAssetBindingRepairPlan {
  assetId: string;
  keepNodeId: string;
  keepNodeLabel: string;
  detachNodeIds: string[];
}

export interface WorkflowAssetBindingClearPatch {
  scriptAssetId: null;
  scriptAssetOwnerId: null;
  scriptAssetRevision: null;
  scriptAssetContentHash: null;
  scriptAssetIdentityLocks: null;
  scriptAssetDependencies: null;
  assetId: null;
  assetRevision: null;
  identityLocks: null;
  dependencies: null;
  [key: string]: null;
}

interface AssetBindingNode {
  id: string;
  data?: Record<string, unknown> | null;
}

function nodeData(node: AssetBindingNode): Record<string, unknown> {
  return (node.data ?? {}) as Record<string, unknown>;
}

function nodeImageUrl(node: AssetBindingNode): string {
  const data = nodeData(node);
  const value = data.imageUrl ?? data.previewImageUrl;
  return typeof value === "string" ? value.trim() : "";
}

function nodeHasActiveGeneration(node: AssetBindingNode): boolean {
  const data = nodeData(node);
  return data.isGenerating === true || data.canvas_auto_generate_once === true;
}

function nodeHasUsableImage(node: AssetBindingNode): boolean {
  return (
    nodeImageUrl(node).length > 0
    && !nodeData(node).generationError
    && !nodeHasActiveGeneration(node)
  );
}

function nodeLabel(node: AssetBindingNode): string {
  const data = nodeData(node);
  const value = data.label ?? data.displayName;
  return typeof value === "string" && value.trim() ? value.trim() : node.id;
}

/**
 * Build a conservative repair only for the exact duplicate-binding failure.
 *
 * The backend needs one node per script asset. We may detach duplicate claim
 * fields, but we must not guess between two usable images or interrupt a node
 * that is already generating media.
 */
export function planWorkflowAssetBindingRepairs(
  recovery: WorkflowStepRecovery | null | undefined,
  nodes: readonly AssetBindingNode[],
): WorkflowAssetBindingRepairPlan[] {
  if (
    recovery?.action !== "repair_canvas_asset_binding"
    || recovery.error_code !== "workflow_storyboard_canvas_asset_ambiguous"
    || !recovery.asset_ids?.length
    || !recovery.target_node_ids?.length
  ) {
    return [];
  }

  const targetNodeIds = new Set(recovery.target_node_ids);
  const plans: WorkflowAssetBindingRepairPlan[] = [];
  for (const assetId of recovery.asset_ids) {
    const bound = nodes.filter(
      (node) => String(nodeData(node).scriptAssetId || "").trim() === assetId,
    );
    if (bound.length < 2) return [];
    if (!bound.every((node) => targetNodeIds.has(node.id))) return [];

    const keepers = bound.filter(nodeHasUsableImage);
    if (keepers.length !== 1) return [];
    const keep = keepers[0];
    const detach = bound.filter((node) => node.id !== keep.id);
    if (detach.some(nodeHasActiveGeneration)) return [];

    plans.push({
      assetId,
      keepNodeId: keep.id,
      keepNodeLabel: nodeLabel(keep),
      detachNodeIds: detach.map((node) => node.id),
    });
  }
  return plans;
}

/** Remove only the asset-claim fields; the node and its user content stay in place. */
export function workflowAssetBindingClearPatch(): WorkflowAssetBindingClearPatch {
  return {
    scriptAssetId: null,
    scriptAssetOwnerId: null,
    scriptAssetRevision: null,
    scriptAssetContentHash: null,
    scriptAssetIdentityLocks: null,
    scriptAssetDependencies: null,
    assetId: null,
    assetRevision: null,
    identityLocks: null,
    dependencies: null,
  };
}
