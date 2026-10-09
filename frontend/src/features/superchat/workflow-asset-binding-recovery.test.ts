import { describe, expect, it, vi } from "vitest";

import type { FreezoneCanvasPayload } from "@/api/canvas";
import type {
  WorkflowCanvasAssetBindingReadinessResult,
  WorkflowCanvasAssetBindingRepairResult,
  WorkflowRun,
} from "@/types/workflow-runtime";
import {
  repairAndRevalidateWorkflowAssetBinding,
  type WorkflowAssetBindingRecoveryDependencies,
} from "@/features/superchat/workflow-asset-binding-recovery";

function failedRun(): WorkflowRun {
  return {
    id: "run-asset",
    project_id: "project-1",
    canvas_id: "canvas-1",
    status: "failed",
    revision: 7,
    source_turn_id: "turn-1",
    step_states: {
      storyboard_images: {
        id: "storyboard_images",
        label: "分镜图",
        status: "failed",
        attempt: 1,
      },
    },
    artifacts: {
      storyboard_images: {
        recovery: {
          schema: "workflow_step_recovery.v1",
          workflow_run_id: "run-asset",
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
          instruction: "先修正资产节点",
        },
      },
    },
  } as unknown as WorkflowRun;
}

function revalidatedRun(): WorkflowRun {
  const run = failedRun();
  return {
    ...run,
    revision: 8,
    artifacts: {
      storyboard_images: {
        recovery: {
          schema: "workflow_step_recovery.v1",
          workflow_run_id: "run-asset",
          step_id: "storyboard_images",
          error_code: "workflow_storyboard_paid_media_not_authorized",
          action: "request_media_authorization",
          next_action: "recover:request_media_authorization:storyboard_images",
          rerun_scope: "current_step",
          item_ids: [],
          job_ids: [],
          target_node_ids: ["asset-empty", "asset-ready"],
          asset_ids: ["scene:darkroom"],
          requires_paid_media: true,
          auto_retry_allowed: false,
          instruction: "先取得本轮显式媒体授权",
          authorization_request: {
            schema: "workflow_media_authorization_request.v1",
            run_id: "run-asset",
            step_id: "storyboard_images",
            requires_user_action: true,
          },
        },
      },
    },
  } as unknown as WorkflowRun;
}

function repairResult(): WorkflowCanvasAssetBindingRepairResult {
  return {
    schema: "workflow_canvas_asset_binding_repair.v1",
    status: "repaired",
    run_id: "run-asset",
    run_revision: 7,
    step_id: "storyboard_images",
    kept_node_ids: ["asset-ready"],
    detached_node_ids: ["asset-empty"],
    media_replay_started: false,
    next_action: "revalidate_readiness",
    canvas_receipt: {},
  };
}

function readinessResult(): WorkflowCanvasAssetBindingReadinessResult {
  return {
    schema: "workflow_canvas_asset_binding_readiness.v1",
    ready: true,
    reason_code: "workflow_asset_binding_ready",
    run_id: "run-asset",
    step_id: "storyboard_images",
    run_revision: 8,
    run_revision_before: 7,
    canvas_revision: 4,
    resolved_node_ids: ["asset-ready"],
    issues: [],
    fingerprint: "a".repeat(64),
    status: "authorization_required",
    recovery: {
      action: "request_media_authorization",
    },
    authorization_request: {
      schema: "workflow_media_authorization_request.v1",
      run_id: "run-asset",
      step_id: "storyboard_images",
      requires_user_action: true,
    },
    media_submission_started: false,
  };
}

function remoteCanvas(): FreezoneCanvasPayload {
  return {
    canvas_id: "canvas-1",
    project_id: "project-1",
    revision: 4,
    nodes: [
      {
        id: "asset-ready",
        type: "imageGenNode",
        position: { x: 0, y: 0 },
        data: {
          scriptAssetId: "scene:darkroom",
          imageUrl: "/projects/p/darkroom.png",
        },
      },
    ],
    edges: [],
  } as unknown as FreezoneCanvasPayload;
}

function dependencies(
  overrides: Partial<WorkflowAssetBindingRecoveryDependencies> = {},
): WorkflowAssetBindingRecoveryDependencies {
  return {
    flushCanvas: vi.fn(async () => true),
    repairBinding: vi.fn(async () => repairResult()),
    getCanvas: vi.fn(async () => remoteCanvas()),
    applyRemoteCanvas: vi.fn(() => true),
    revalidateBinding: vi.fn(async () => readinessResult()),
    getRun: vi.fn(async () => revalidatedRun()),
    focusNodes: vi.fn(() => true),
    ...overrides,
  };
}

describe("server-authoritative asset binding recovery", () => {
  it("flushes, repairs, applies the server canvas, revalidates, then rereads the run", async () => {
    const calls: string[] = [];
    const deps = dependencies({
      flushCanvas: vi.fn(async () => {
        calls.push("flush");
        return true;
      }),
      repairBinding: vi.fn(async (_project, _runId, payload) => {
        calls.push("repair");
        expect(payload).toEqual({
          canvas_id: "canvas-1",
          step_id: "storyboard_images",
          command_id: "repair-1",
          expected_run_revision: 7,
          source_turn_id: "turn-1",
        });
        return repairResult();
      }),
      getCanvas: vi.fn(async () => {
        calls.push("canvas");
        return remoteCanvas();
      }),
      applyRemoteCanvas: vi.fn(() => {
        calls.push("apply");
        return true;
      }),
      revalidateBinding: vi.fn(async (_project, _runId, payload) => {
        calls.push("revalidate");
        expect(payload.command_id).toBe("revalidate-1");
        return readinessResult();
      }),
      getRun: vi.fn(async () => {
        calls.push("run");
        return revalidatedRun();
      }),
      focusNodes: vi.fn((nodeIds) => {
        calls.push("focus");
        expect(nodeIds).toEqual(["asset-ready"]);
        return true;
      }),
    });

    const outcome = await repairAndRevalidateWorkflowAssetBinding({
      projectId: "project-1",
      run: failedRun(),
      repairCommandId: "repair-1",
      revalidateCommandId: "revalidate-1",
    }, deps);

    expect(outcome).toEqual({
      status: "authorization_required",
      run: revalidatedRun(),
    });
    expect(calls).toEqual([
      "flush",
      "repair",
      "canvas",
      "apply",
      "focus",
      "revalidate",
      "run",
    ]);
  });

  it("stops before repair when the canvas cannot be flushed", async () => {
    const repairBinding = vi.fn(async () => repairResult());
    const deps = dependencies({
      flushCanvas: vi.fn(async () => false),
      repairBinding,
    });

    await expect(repairAndRevalidateWorkflowAssetBinding({
      projectId: "project-1",
      run: failedRun(),
      repairCommandId: "repair-1",
      revalidateCommandId: "revalidate-1",
    }, deps)).rejects.toThrow("画布还有未保存修改");

    expect(repairBinding).not.toHaveBeenCalled();
    expect(deps.revalidateBinding).not.toHaveBeenCalled();
  });

  it("returns not_ready without rereading or authorizing a run", async () => {
    const notReady = {
      ...readinessResult(),
      ready: false,
      reason_code: "workflow_asset_binding_not_ready",
      status: "not_ready",
      authorization_request: null,
    } as unknown as WorkflowCanvasAssetBindingReadinessResult;
    const getRun = vi.fn(async () => revalidatedRun());
    const deps = dependencies({
      revalidateBinding: vi.fn(async () => notReady),
      getRun,
    });

    const outcome = await repairAndRevalidateWorkflowAssetBinding({
      projectId: "project-1",
      run: failedRun(),
      repairCommandId: "repair-1",
      revalidateCommandId: "revalidate-1",
    }, deps);

    expect(outcome).toEqual({ status: "not_ready", readiness: notReady });
    expect(getRun).not.toHaveBeenCalled();
  });

  it("rejects a repair receipt that claims media replay", async () => {
    const getCanvas = vi.fn(async () => remoteCanvas());
    const deps = dependencies({
      repairBinding: vi.fn(async () => ({
        ...repairResult(),
        media_replay_started: true,
      } as unknown as WorkflowCanvasAssetBindingRepairResult)),
      getCanvas,
    });

    await expect(repairAndRevalidateWorkflowAssetBinding({
      projectId: "project-1",
      run: failedRun(),
      repairCommandId: "repair-1",
      revalidateCommandId: "revalidate-1",
    }, deps)).rejects.toThrow("服务端资产整理回执");

    expect(getCanvas).not.toHaveBeenCalled();
    expect(deps.revalidateBinding).not.toHaveBeenCalled();
  });
});
