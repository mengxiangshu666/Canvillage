// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { describe, expect, it } from "vitest";

import {
  emptyCanvasAgentTelemetry,
  recordBackgroundTask,
  recordCanvasPatch,
  recordCanvasReceipt,
} from "./agent-run-telemetry";

describe("Agent canvas run telemetry", () => {
  it("tracks background media tasks without keeping the chat turn busy", () => {
    const telemetry = recordBackgroundTask(
      recordBackgroundTask(emptyCanvasAgentTelemetry("turn-a"), "turn-a"),
      "turn-a",
    );

    expect(telemetry).toMatchObject({
      turnId: "turn-a",
      backgroundTaskCount: 2,
    });
  });

  it("counts realtime patches and keeps the highest canvas revision", () => {
    const first = recordCanvasPatch(emptyCanvasAgentTelemetry("turn-a"), {
      projectId: "project-a",
      canvasId: "canvas-a",
      revision: 4,
      commandId: "command-a",
      turnId: "turn-a",
      commands: [{ type: "create_image_prompt_node" }, { type: "connect_nodes" }],
    });
    const second = recordCanvasPatch(first, {
      projectId: "project-a",
      canvasId: "canvas-a",
      revision: 3,
      commandId: "command-b",
      turnId: "turn-a",
    });

    expect(second).toMatchObject({
      patchCount: 2,
      commandCount: 3,
      revision: 4,
      lastCommandId: "command-b",
    });
  });

  it("counts only final receipts as applied work and isolates turns", () => {
    const pending = recordCanvasReceipt(emptyCanvasAgentTelemetry("turn-a"), {
      schema: "canvas_command_receipt.v1",
      receiptId: "command-a:ack",
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      stage: "ack",
      createdAt: 1,
    });
    expect(pending.receiptCount).toBe(0);

    const completed = recordCanvasReceipt(pending, {
      schema: "canvas_command_receipt.v1",
      receiptId: "command-a:result",
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      revision: 6,
      stage: "result",
      success: true,
      applied: 2,
      createdIds: ["node-a", "node-b"],
      createdAt: 2,
    });
    const nextTurn = recordCanvasReceipt(completed, {
      schema: "canvas_command_receipt.v1",
      receiptId: "command-b:result",
      commandId: "command-b",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-b",
      stage: "result",
      success: false,
      error: "node not found",
      createdAt: 3,
    });

    expect(completed).toMatchObject({
      turnId: "turn-a",
      receiptCount: 1,
      appliedCount: 2,
      createdNodeCount: 2,
      revision: 6,
    });
    expect(nextTurn).toMatchObject({
      turnId: "turn-b",
      receiptCount: 1,
      appliedCount: 0,
      createdNodeCount: 0,
      failedCount: 1,
    });
  });

  it("keeps skipped command counts without generating follow-up prompts", () => {
    const telemetry = recordCanvasReceipt(emptyCanvasAgentTelemetry("turn-a"), {
      schema: "canvas_command_receipt.v1",
      receiptId: "command-a:result",
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      stage: "result",
      success: true,
      applied: 2,
      requested: 3,
      skipped: 1,
      createdIds: ["node-a"],
      createdAt: 1,
    });

    expect(telemetry).toMatchObject({
      appliedCount: 2,
      skippedCount: 1,
      failedCount: 0,
    });
  });
});
