import { describe, expect, it } from "vitest";

import type { WorkflowRun } from "@/types/workflow-runtime";

import {
  workflowMediaAuthorizationRequestFromRun,
} from "./workflow-failure-dismissal";
import {
  buildWorkflowMediaContinuation,
  WORKFLOW_MEDIA_AUTHORIZATION_DISPLAY_TEXT,
} from "./workflow-media-authorization";

function mediaRun(
  stepId: "storyboard_images" | "shot_videos" = "storyboard_images",
): WorkflowRun {
  const errorCode = stepId === "storyboard_images"
    ? "workflow_storyboard_paid_media_not_authorized"
    : "workflow_shot_video_paid_media_not_authorized";
  return {
    id: "wfr-media-ui",
    workflow_id: "freezone-final-film",
    workflow_version: 2,
    project_id: "demo",
    canvas_id: "canvas-1",
    run_mode: "auto",
    status: "failed",
    current_frontier: [stepId],
    step_states: {
      [stepId]: {
        id: stepId,
        label: stepId === "storyboard_images" ? "分镜图" : "逐镜视频",
        type: "media",
        handler: stepId,
        depends_on: [],
        status: "failed",
        attempt: 1,
        execution_mode: "itemized",
        error: "需要授权",
        started_at: "",
        completed_at: "",
      },
    },
    inputs: {},
    artifacts: {
      [stepId]: {
        recovery: {
          schema: "workflow_step_recovery.v1",
          workflow_run_id: "wfr-media-ui",
          action: "request_media_authorization",
          title: "等待付费媒体授权",
          instruction: "确认后恢复原 Run。",
          next_action: `recover:request_media_authorization:${stepId}`,
          rerun_scope: "current_step",
          item_ids: [],
          job_ids: [],
          auto_retry_allowed: false,
          requires_paid_media: true,
          error_code: errorCode,
          step_id: stepId,
        },
      },
    },
    error: "需要授权",
    revision: 12,
    event_seq: 20,
    idempotency_key: "media-ui",
    created_at: "",
    updated_at: "",
  };
}

function parseEnvelope(value: string): Record<string, unknown> {
  const match = value.match(
    /^\[CANVAS_AGENT_REQUEST_V2\]([\s\S]+)\[\/CANVAS_AGENT_REQUEST_V2\]$/,
  );
  expect(match).not.toBeNull();
  return JSON.parse(match![1]) as Record<string, unknown>;
}

describe("workflow media authorization UI contract", () => {
  it("accepts only the exact media recovery pair", () => {
    const run = mediaRun();
    expect(workflowMediaAuthorizationRequestFromRun(run)).toEqual({
      step_id: "storyboard_images",
      error_code: "workflow_storyboard_paid_media_not_authorized",
      media_kind: "image",
      recovery_action: "request_media_authorization",
      retry_scope: "whole_step",
      item_ids: [],
    });

    const stale = structuredClone(run);
    const recovery = stale.artifacts.storyboard_images as {
      recovery: { error_code: string };
    };
    recovery.recovery.error_code = "workflow_shot_video_paid_media_not_authorized";
    expect(workflowMediaAuthorizationRequestFromRun(stale)).toBeNull();
  });

  it("binds a paid failed-video retry to the exact failed item", () => {
    const run = mediaRun("shot_videos");
    const artifact = run.artifacts.shot_videos as {
      recovery: Record<string, unknown>;
    };
    artifact.recovery = {
      ...artifact.recovery,
      action: "retry_failed_items",
      error_code: "workflow_shot_video_failed",
      next_action: "recover:retry_failed_items:shot_videos",
      rerun_scope: "failed_items_only",
      item_ids: ["shot-2"],
      instruction: "只重试失败视频 item。",
    };

    expect(workflowMediaAuthorizationRequestFromRun(run)).toEqual({
      step_id: "shot_videos",
      error_code: "workflow_shot_video_failed",
      media_kind: "video",
      recovery_action: "retry_failed_items",
      retry_scope: "failed_items_only",
      item_ids: ["shot-2"],
    });

    const continuation = buildWorkflowMediaContinuation({
      run,
      stepId: "shot_videos",
      canvasContext: '{"canvas_id":"canvas-1"}',
      skillIds: ["one-click-film"],
    });
    const payload = parseEnvelope(continuation.transportText);
    expect(String(payload.request)).toContain("retry_failed_items");
    expect(String(payload.request)).toContain("shot-2");
  });

  it("uses auto mode with a one-start structured budget only", () => {
    const continuation = buildWorkflowMediaContinuation({
      run: mediaRun("shot_videos"),
      stepId: "shot_videos",
      canvasContext: '{"canvas_id":"canvas-1"}',
      skillIds: ["one-click-film"],
    });
    const payload = parseEnvelope(continuation.transportText);

    expect(continuation.displayText).toBe(
      WORKFLOW_MEDIA_AUTHORIZATION_DISPLAY_TEXT,
    );
    expect(payload.run_mode).toBe("auto");
    expect(payload.task_authorization).toMatchObject({
      scope: "current_turn",
      run_mode: "auto",
      allow_paid_media: true,
      max_paid_starts: 1,
      require_video_confirmation: false,
    });
    expect(payload.workflow_runtime).toMatchObject({
      workflow_run_id: "wfr-media-ui",
      status: "failed",
      revision: 12,
    });
    expect(continuation.displayText).not.toContain("pmg_");
  });
});
