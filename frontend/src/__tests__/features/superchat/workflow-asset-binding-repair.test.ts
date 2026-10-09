import { describe, expect, it } from "vitest";

import type { CanvasNodeData } from "@/features/canvas/domain/canvasNodes";
import type { WorkflowStepRecovery } from "@/features/superchat/workflow-failure-dismissal";
import {
  planWorkflowAssetBindingRepairs,
  workflowAssetBindingClearPatch,
} from "@/features/superchat/workflow-asset-binding-repair";
import type { CanvasNode } from "@/stores/canvasStore";

function node(
  id: string,
  data: Partial<CanvasNodeData> = {},
): CanvasNode {
  return {
    id,
    type: "imageGenNode",
    position: { x: 0, y: 0 },
    data,
  } as CanvasNode;
}

function recovery(
  overrides: Partial<WorkflowStepRecovery> = {},
): WorkflowStepRecovery {
  return {
    schema: "workflow_step_recovery.v1",
    workflow_run_id: "run-1",
    step_id: "storyboard_images",
    error_code: "workflow_storyboard_canvas_asset_ambiguous",
    action: "repair_canvas_asset_binding",
    next_action: "recover:repair_canvas_asset_binding:storyboard_images",
    rerun_scope: "canvas_asset_binding",
    item_ids: [],
    job_ids: [],
    target_node_ids: ["asset-empty", "asset-ready"],
    asset_ids: ["scene:darkroom"],
    requires_paid_media: false,
    auto_retry_allowed: false,
    instruction: "keep one ready node",
    ...overrides,
  };
}

describe("workflow asset binding repair", () => {
  it("keeps the only usable image and detaches empty duplicates", () => {
    const plans = planWorkflowAssetBindingRepairs(recovery(), [
      node("asset-empty", { scriptAssetId: "scene:darkroom" }),
      node("asset-ready", {
        scriptAssetId: "scene:darkroom",
        imageUrl: "/projects/p/darkroom.png",
        label: "暗房",
      }),
    ]);

    expect(plans).toEqual([{
      assetId: "scene:darkroom",
      keepNodeId: "asset-ready",
      keepNodeLabel: "暗房",
      detachNodeIds: ["asset-empty"],
    }]);
  });

  it("refuses to guess when more than one duplicate has a usable image", () => {
    const plans = planWorkflowAssetBindingRepairs(recovery(), [
      node("asset-a", {
        scriptAssetId: "scene:darkroom",
        imageUrl: "/projects/p/a.png",
      }),
      node("asset-b", {
        scriptAssetId: "scene:darkroom",
        imageUrl: "/projects/p/b.png",
      }),
    ]);

    expect(plans).toEqual([]);
  });

  it("does not detach a duplicate while it is generating media", () => {
    const plans = planWorkflowAssetBindingRepairs(recovery(), [
      node("asset-empty", {
        scriptAssetId: "scene:darkroom",
        isGenerating: true,
      }),
      node("asset-ready", {
        scriptAssetId: "scene:darkroom",
        imageUrl: "/projects/p/darkroom.png",
      }),
    ]);

    expect(plans).toEqual([]);
  });

  it("requires the recovery contract to cover every duplicate node", () => {
    const plans = planWorkflowAssetBindingRepairs(
      recovery({ target_node_ids: ["asset-ready"] }),
      [
        node("asset-empty", { scriptAssetId: "scene:darkroom" }),
        node("asset-ready", {
          scriptAssetId: "scene:darkroom",
          imageUrl: "/projects/p/darkroom.png",
        }),
      ],
    );

    expect(plans).toEqual([]);
  });

  it("clears claim fields without deleting the node content", () => {
    expect(workflowAssetBindingClearPatch()).toMatchObject({
      scriptAssetId: null,
      scriptAssetOwnerId: null,
      scriptAssetRevision: null,
      scriptAssetContentHash: null,
      scriptAssetIdentityLocks: null,
      scriptAssetDependencies: null,
    });
  });
});
