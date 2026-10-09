// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { buildWorkflowStepRetryCommand } from "./useWorkflowFailureRetry";
import type { WorkflowRun } from "@/types/workflow-runtime";

describe("workflow failure retry", () => {
  it("submits a failed atomic step as one whole-step retry", () => {
    const run = { id: "run-step-retry", revision: 12 } as unknown as WorkflowRun;

    expect(buildWorkflowStepRetryCommand(run, " quality_review ")).toEqual({
      command: "retry",
      step_id: "quality_review",
      retry_scope: "whole_step",
      idempotency_key: "ui-step-retry:run-step-retry:12:quality_review",
      expected_revision: 12,
    });
  });
});
