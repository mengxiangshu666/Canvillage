// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiCall = vi.hoisted(() => vi.fn());

vi.mock("@/api/client", () => ({ apiCall }));

import {
  commandWorkflowRun,
  issueWorkflowComposeAuthorization,
  listWorkflowDefinitions,
  repairWorkflowCanvasAssetBinding,
  revalidateWorkflowCanvasAssetBinding,
} from "@/api/workflow-runtime";

describe("workflow runtime API", () => {
  beforeEach(() => apiCall.mockReset());

  it("loads server-owned workflow definitions", async () => {
    apiCall.mockResolvedValue([]);

    await listWorkflowDefinitions("project / 1");

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project%20%2F%201/workflows",
      { signal: undefined },
    );
  });

  it.each(["pause", "resume", "cancel", "retry", "steer", "dismiss_failed_items"] as const)(
    "sends %s through the durable command endpoint",
    async (command) => {
      apiCall.mockResolvedValue({});
      const payload = {
        command,
        idempotency_key: `command-${command}`,
        expected_revision: 7,
        ...(command === "retry" || command === "dismiss_failed_items" ? {
          step_id: "media_generation",
          item_ids: ["shot-2"],
        } : {}),
        ...(command === "retry" ? { retry_scope: "failed_items_only" as const } : {}),
        ...(command === "steer" ? { direction: "加快节奏" } : {}),
      };

      await commandWorkflowRun("project-1", "run / 1", payload);

      expect(apiCall).toHaveBeenCalledWith(
        "projects/project-1/workflow-runs/run%20%2F%201/command",
        { method: "POST", json: payload },
      );
    },
  );

  it("issues a browser-scoped final-compose authorization", async () => {
    apiCall.mockResolvedValue({ id: "wca_test" });

    await issueWorkflowComposeAuthorization("project-1", "run / 1", {
      canvas_id: "canvas-1",
      step_id: "final_film",
    });

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-1/workflow-runs/run%20%2F%201/compose-authorizations",
      {
        method: "POST",
        json: {
          canvas_id: "canvas-1",
          step_id: "final_film",
        },
      },
    );
  });

  it("uses the server-authoritative asset binding repair endpoint", async () => {
    const payload = {
      canvas_id: "canvas-1",
      step_id: "storyboard_images",
      command_id: "repair-1",
      expected_run_revision: 7,
    };
    apiCall.mockResolvedValue({});

    await repairWorkflowCanvasAssetBinding("project-1", "run / 1", payload);

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-1/workflow-runs/run%20%2F%201/canvas-asset-binding-repair",
      { method: "POST", json: payload },
    );
  });

  it("uses the readonly readiness revalidation endpoint", async () => {
    const payload = {
      canvas_id: "canvas-1",
      step_id: "storyboard_images",
      command_id: "revalidate-1",
      expected_run_revision: 7,
    };
    apiCall.mockResolvedValue({});

    await revalidateWorkflowCanvasAssetBinding("project-1", "run / 1", payload);

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-1/workflow-runs/run%20%2F%201/canvas-asset-binding-revalidate",
      { method: "POST", json: payload },
    );
  });
});
