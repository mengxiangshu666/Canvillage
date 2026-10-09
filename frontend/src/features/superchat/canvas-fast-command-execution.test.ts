// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";

import { executeCanvasFastCommandPlan } from "./canvas-fast-command-execution";
import type { CanvasFastCommandPlan } from "./canvas-fast-command";

const plan: CanvasFastCommandPlan = {
  reply: "已创建文本节点",
  envelope: {
    schema: "canvas_chat_commands.v1",
    command_id: "fast-command-1",
    project_id: "project-a",
    canvas_id: "canvas-a",
    canvas_command_emitted: true,
    commands: [{ type: "create_canvas_node", node_type: "textAnnotationNode" }],
  },
};

describe("executeCanvasFastCommandPlan", () => {
  it("subscribes before dispatch and returns the persisted result receipt", async () => {
    const order: string[] = [];
    const waitForResult = vi.fn(() => {
      order.push("wait");
      return Promise.resolve({
        schema: "canvas_command_receipt.v1" as const,
        receiptId: "receipt-1",
        commandId: "fast-command-1",
        projectId: "project-a",
        canvasId: "canvas-a",
        stage: "result" as const,
        success: true,
        applied: 1,
        revision: 42,
        createdIds: ["node-created"],
        createdAt: 1,
      });
    });
    const emit = vi.fn(() => { order.push("emit"); });

    await expect(executeCanvasFastCommandPlan(plan, 4_000, { emit, waitForResult })).resolves.toMatchObject({
      commandId: "fast-command-1",
      revision: 42,
      createdIds: ["node-created"],
    });
    expect(order).toEqual(["wait", "emit"]);
    expect(waitForResult).toHaveBeenCalledWith("fast-command-1", 4_000);
    expect(emit).toHaveBeenCalledWith(plan.envelope);
  });

  it("rejects failed and empty application receipts", async () => {
    const emit = vi.fn();
    await expect(executeCanvasFastCommandPlan(plan, 100, {
      emit,
      waitForResult: vi.fn(() => Promise.resolve({
        schema: "canvas_command_receipt.v1" as const,
        receiptId: "failure",
        commandId: "fast-command-1",
        projectId: "project-a",
        canvasId: "canvas-a",
        stage: "result" as const,
        success: false,
        error: "canvas_persistence_failed",
        createdAt: 1,
      })),
    })).rejects.toThrow("canvas_persistence_failed");

    await expect(executeCanvasFastCommandPlan(plan, 100, {
      emit,
      waitForResult: vi.fn(() => Promise.resolve({
        schema: "canvas_command_receipt.v1" as const,
        receiptId: "empty",
        commandId: "fast-command-1",
        projectId: "project-a",
        canvasId: "canvas-a",
        stage: "result" as const,
        success: true,
        applied: 0,
        createdAt: 1,
      })),
    })).rejects.toThrow("画布没有应用任何操作");
  });
});
