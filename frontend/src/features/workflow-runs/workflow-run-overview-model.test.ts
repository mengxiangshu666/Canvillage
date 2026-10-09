import { describe, expect, it } from "vitest";

import type { WorkflowRun, WorkflowStepState } from "@/types/workflow-runtime";

import {
  sortWorkflowRunsNewestFirst,
  workflowRunActiveStep,
  workflowRunFailedStep,
  workflowRunProgress,
  workflowRunRecoveryGuidance,
  workflowRunRetryTarget,
  workflowRunStatusLabel,
  workflowRunTitle,
  workflowRunOverviewVisibleRuns,
} from "./workflow-run-overview-model";

function step(
  id: string,
  status: WorkflowStepState["status"],
  extra: Partial<WorkflowStepState> = {},
): WorkflowStepState {
  return {
    id,
    label: id,
    type: "local_task",
    handler: `handler.${id}`,
    depends_on: [],
    status,
    attempt: 1,
    error: "",
    started_at: "",
    completed_at: "",
    ...extra,
  };
}

function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "wfr_overview",
    workflow_id: "freezone-final-film",
    workflow_version: 2,
    project_id: "project-1",
    canvas_id: "canvas-1",
    run_mode: "draft",
    status: "running",
    current_frontier: [],
    step_states: {},
    inputs: {},
    artifacts: {},
    error: "",
    revision: 1,
    event_seq: 1,
    idempotency_key: "idem",
    created_at: "2026-09-30T01:00:00Z",
    updated_at: "2026-09-30T01:00:00Z",
    ...overrides,
  } as WorkflowRun;
}

describe("workflow run overview model", () => {
  it("names known workflows and falls back to the raw id", () => {
    expect(workflowRunTitle("freezone-final-film")).toBe("一键成片");
    expect(workflowRunTitle("freezone-script-contract")).toBe("脚本合同");
    expect(workflowRunTitle("custom-workflow")).toBe("custom-workflow");
    expect(workflowRunTitle("")).toBe("工作流");
    expect(workflowRunStatusLabel("failed")).toBe("执行失败");
  });

  it("sorts newest first without mutating the input", () => {
    const older = run({ id: "older", created_at: "2026-09-29T01:00:00Z" });
    const newer = run({ id: "newer", created_at: "2026-09-30T01:00:00Z" });
    const input = [older, newer];

    expect(sortWorkflowRunsNewestFirst(input).map((item) => item.id)).toEqual([
      "newer",
      "older",
    ]);
    expect(input.map((item) => item.id)).toEqual(["older", "newer"]);
  });

  it("keeps older in-flight runs visible alongside the three newest runs", () => {
    const oldRunning = run({
      id: "old-running",
      status: "running",
      created_at: "2026-09-20T01:00:00Z",
    });
    const newest = [1, 2, 3, 4].map((day) =>
      run({
        id: `done-${day}`,
        status: "completed",
        created_at: `2026-09-${String(day + 25).padStart(2, "0")}T01:00:00Z`,
      }),
    );

    expect(
      workflowRunOverviewVisibleRuns([oldRunning, ...newest]).map((item) => item.id),
    ).toEqual(["done-4", "done-3", "done-2", "old-running"]);
  });

  it("keeps an explicitly requested completed run visible outside the recent window", () => {
    const requested = run({
      id: "requested-run",
      created_at: "2026-08-01T00:00:00Z",
    });
    const recent = [1, 2, 3].map((day) =>
      run({
        id: `recent-${day}`,
        created_at: `2026-10-0${day}T00:00:00Z`,
      }),
    );

    expect(
      workflowRunOverviewVisibleRuns([requested, ...recent], 3, "requested-run").map(
        (item) => item.id,
      ),
    ).toEqual(["recent-3", "recent-2", "recent-1", "requested-run"]);
  });

  it("keeps paused runs visible when they fall outside the recent limit", () => {
    const oldPaused = run({
      id: "old-paused",
      status: "paused",
      created_at: "2026-09-20T01:00:00Z",
    });
    const newer = [1, 2, 3].map((day) =>
      run({
        id: `done-${day}`,
        status: "completed",
        created_at: `2026-09-${String(day + 25).padStart(2, "0")}T01:00:00Z`,
      }),
    );

    expect(
      workflowRunOverviewVisibleRuns([oldPaused, ...newer]).map((item) => item.id),
    ).toContain("old-paused");
  });

  it("keeps older failed runs visible with their recovery information", () => {
    const oldFailed = run({
      id: "old-failed",
      status: "failed",
      error_code: "workflow_script_task_failed",
      error: "脚本步骤失败，保留原 Run 可重试。",
      created_at: "2026-09-20T01:00:00Z",
    });
    const newer = [1, 2, 3].map((day) =>
      run({
        id: `done-${day}`,
        status: "completed",
        created_at: `2026-09-${String(day + 25).padStart(2, "0")}T01:00:00Z`,
      }),
    );

    expect(
      workflowRunOverviewVisibleRuns([oldFailed, ...newer]).map((item) => item.id),
    ).toContain("old-failed");
  });

  it("counts completed steps and prefers the running step", () => {
    const withSteps = run({
      current_frontier: ["plan"],
      step_states: {
        understand: step("understand", "completed"),
        plan: step("plan", "pending", { label: "冻结生产计划" }),
        script: step("script", "running", { label: "生成脚本合同" }),
      },
    });

    expect(workflowRunProgress(withSteps)).toEqual({
      completed: 1,
      total: 3,
      ratio: 1 / 3,
    });
    expect(workflowRunActiveStep(withSteps)?.id).toBe("script");
  });

  it("falls back to the frontier, then to the first unfinished step", () => {
    const frontierRun = run({
      current_frontier: ["plan"],
      step_states: {
        understand: step("understand", "completed"),
        plan: step("plan", "pending"),
      },
    });
    expect(workflowRunActiveStep(frontierRun)?.id).toBe("plan");

    const noFrontier = run({
      step_states: {
        understand: step("understand", "completed"),
        plan: step("plan", "pending"),
      },
    });
    expect(workflowRunActiveStep(noFrontier)?.id).toBe("plan");

    const finished = run({
      step_states: { understand: step("understand", "completed") },
    });
    expect(workflowRunActiveStep(finished)).toBeNull();
  });

  it("offers whole-step retry only for retryable non-media failures", () => {
    const scriptFailure = run({
      status: "failed",
      error_code: "workflow_script_contract_blocked",
      step_states: {
        understand: step("understand", "completed"),
        script_contract: step("script_contract", "failed", {
          label: "生成脚本合同",
          retry_policy: "manual",
          requires: ["request", "project_context", "model:director"],
        }),
      },
    });

    expect(workflowRunFailedStep(scriptFailure)?.id).toBe("script_contract");
    expect(workflowRunRetryTarget(scriptFailure)).toEqual({
      stepId: "script_contract",
      label: "生成脚本合同",
    });

    const mediaFailure = run({
      status: "failed",
      step_states: {
        storyboard_images: step("storyboard_images", "failed", {
          retry_policy: "automatic",
          requires: ["script_contract", "task_authorization:auto"],
        }),
      },
    });
    expect(workflowRunRetryTarget(mediaFailure)).toBeNull();

    const neverRetry = run({
      status: "failed",
      step_states: {
        understand: step("understand", "failed", { retry_policy: "never" }),
      },
    });
    expect(workflowRunRetryTarget(neverRetry)).toBeNull();

    const completed = run({ status: "completed" });
    expect(workflowRunRetryTarget(completed)).toBeNull();
  });

  it("shows only recovery instructions bound to the current failed step and run", () => {
    const failed = run({
      status: "failed",
      step_states: {
        script_contract: step("script_contract", "failed"),
      },
      artifacts: {
        script_contract: {
          recovery: {
            schema: "workflow_step_recovery.v1",
            workflow_run_id: "wfr_overview",
            step_id: "script_contract",
            action: "repair_script_contract",
            instruction: "先补齐脚本中的缺失字段，再继续原 Run。",
          },
        },
      },
    });

    expect(workflowRunRecoveryGuidance(failed)).toEqual({
      title: "先修复脚本合同",
      instruction: "先补齐脚本中的缺失字段，再继续原 Run。",
    });

    const stale = structuredClone(failed);
    (stale.artifacts.script_contract as { recovery: { workflow_run_id: string } })
      .recovery.workflow_run_id = "wfr-other";
    expect(workflowRunRecoveryGuidance(stale)).toBeNull();
    expect(workflowRunRecoveryGuidance(run({ status: "completed" }))).toBeNull();
  });
});
