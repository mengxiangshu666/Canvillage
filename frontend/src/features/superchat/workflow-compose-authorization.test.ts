import { describe, expect, it } from "vitest";

import type { WorkflowRun } from "@/types/workflow-runtime";

import {
  workflowComposeAuthorizationRequestFromRun,
} from "./workflow-failure-dismissal";
import {
  buildWorkflowComposeContinuation,
  WORKFLOW_COMPOSE_AUTHORIZATION_DISPLAY_TEXT,
} from "./workflow-compose-authorization";

const SIGNATURE = "c".repeat(64);

function composeRun(): WorkflowRun {
  return {
    id: "wfr-compose-ui",
    workflow_id: "freezone-final-film",
    workflow_version: 2,
    project_id: "demo",
    canvas_id: "canvas-1",
    run_mode: "auto",
    status: "failed",
    current_frontier: ["final_film"],
    step_states: {
      final_film: {
        id: "final_film",
        label: "最终合成",
        type: "media",
        handler: "final_film",
        depends_on: ["shot_videos"],
        status: "failed",
        attempt: 1,
        error: "需要授权",
        started_at: "",
        completed_at: "",
      },
    },
    inputs: {},
    artifacts: {
      shot_videos: { result_signature: SIGNATURE },
      final_film: {
        recovery: {
          schema: "workflow_step_recovery.v1",
          workflow_run_id: "wfr-compose-ui",
          action: "request_compose_authorization",
          title: "等待最终合成授权",
          instruction: "确认后恢复原 Run。",
          next_action: "recover:request_compose_authorization:final_film",
          auto_retry_allowed: false,
          requires_paid_media: false,
          error_code: "workflow_final_film_not_authorized",
          step_id: "final_film",
          authorization_request: {
            schema: "workflow_compose_authorization_request.v1",
            run_id: "wfr-compose-ui",
            step_id: "final_film",
            source_result_signature: SIGNATURE,
            requires_user_action: true,
          },
        },
      },
    },
    error: "需要授权",
    revision: 7,
    event_seq: 10,
    idempotency_key: "compose-ui",
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

describe("workflow compose authorization UI contract", () => {
  it("accepts only the exact final_film authorization request", () => {
    const run = composeRun();

    expect(workflowComposeAuthorizationRequestFromRun(run)).toEqual({
      schema: "workflow_compose_authorization_request.v1",
      run_id: "wfr-compose-ui",
      step_id: "final_film",
      source_result_signature: SIGNATURE,
      requires_user_action: true,
    });

    const stale = structuredClone(run);
    const recovery = stale.artifacts.final_film as {
      recovery: { authorization_request: { run_id: string } };
    };
    recovery.recovery.authorization_request.run_id = "wfr-other";
    expect(workflowComposeAuthorizationRequestFromRun(stale)).toBeNull();
  });

  it("keeps the ticket in the structured transport envelope only", () => {
    const continuation = buildWorkflowComposeContinuation({
      run: composeRun(),
      composeAuthorizationId: "wca_ui_test",
      canvasContext: '{"canvas_id":"canvas-1"}',
      skillIds: ["one-click-film"],
      runMode: "auto",
    });
    const payload = parseEnvelope(continuation.transportText);

    expect(continuation.displayText).toBe(
      WORKFLOW_COMPOSE_AUTHORIZATION_DISPLAY_TEXT,
    );
    expect(continuation.displayText).not.toContain("wca_ui_test");
    expect(payload.task_authorization).toMatchObject({
      scope: "current_turn",
      compose_authorization_id: "wca_ui_test",
    });
    expect(payload.workflow_runtime).toMatchObject({
      workflow_run_id: "wfr-compose-ui",
      status: "failed",
      revision: 7,
    });
    expect(payload.request).toContain("wfr-compose-ui");
  });
});
