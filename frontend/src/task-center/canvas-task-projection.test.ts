import { describe, expect, it, vi } from "vitest";

import { CANVAS_PATCH_EVENT } from "@/features/superchat/canvas-patch-events";
import {
  canvasPatchFromCompletedTask,
  emitCompletedTaskCanvasPatch,
} from "./canvas-task-projection";
import type { TaskState } from "./types";

function task(overrides: Partial<TaskState> = {}): TaskState {
  return {
    task_key: "task:freezone_gen:project:project-1:0:job-1",
    task_id: "task-1",
    task_type: "freezone_gen",
    username: "local",
    project: "demo",
    project_id: "project-1",
    episode: 0,
    beat_num: null,
    scope: "job-1",
    status: "completed",
    progress: 1,
    current_task: "完成",
    result: {
      output_url: "/static/result.png",
      canvas_receipt: {
        schema: "canvas_command_receipt.v2",
        server_applied: true,
        project_id: "project-1",
        canvas_id: "canvas-1",
        command_id: "task-result:freezone_gen:job-1",
        revision: 8,
      },
    },
    metadata: { canvas_id: "canvas-1", node_id: "node-1" },
    error: null,
    logs: [],
    created_at: "2026-08-17T00:00:00Z",
    updated_at: "2026-08-17T00:00:10Z",
    completed_at: "2026-08-17T00:00:10Z",
    ...overrides,
  };
}

describe("canvas task projection", () => {
  it("projects an authoritative task receipt into the canvas reconcile contract", () => {
    expect(canvasPatchFromCompletedTask(task())).toEqual({
      projectId: "project-1",
      canvasId: "canvas-1",
      revision: 8,
      commandId: "task-result:freezone_gen:job-1",
      schema: "canvas_command_receipt.v2",
      serverApplied: true,
      snapshotRequired: false,
      uiReconcileRequired: true,
      structureStatus: "task_result_committed",
    });
  });

  it("emits one live canvas patch and ignores tasks without a receipt", () => {
    const listener = vi.fn();
    window.addEventListener(CANVAS_PATCH_EVENT, listener);

    expect(emitCompletedTaskCanvasPatch(task())).toBe(true);
    expect(
      emitCompletedTaskCanvasPatch(task({ result: { output_url: "/static/result.png" } })),
    ).toBe(false);
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(CANVAS_PATCH_EVENT, listener);
  });
});
