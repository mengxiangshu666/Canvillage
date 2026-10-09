import { describe, expect, it } from "vitest";

import { buildCanvasCommandReceipt } from "./canvas-command-receipts";

describe("canvas command receipts", () => {
  it("builds a deterministic ack/progress/result identity", () => {
    const receipt = buildCanvasCommandReceipt({
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      revision: 4,
      stage: "result",
      success: true,
      applied: 2,
      applied_ops: 2,
      requested: 3,
      skipped: 1,
      createdIds: ["node-a"],
      created_node_ids: ["node-a"],
      createdAt: 123,
    });

    expect(receipt).toMatchObject({
      schema: "canvas_command_receipt.v1",
      receiptId: "command-a:result",
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      revision: 4,
      stage: "result",
      success: true,
      applied: 2,
      requested: 3,
      skipped: 1,
      createdIds: ["node-a"],
      createdAt: 123,
    });
  });

  it("accepts wire aliases and synchronizes canonical output fields", () => {
    const wireCreatedIds = ["node-wire"];
    const receipt = buildCanvasCommandReceipt({
      commandId: "command-wire",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "result",
      applied_ops: 2,
      created_node_ids: wireCreatedIds,
      createdAt: 123,
    });

    expect(receipt).toMatchObject({
      applied: 2,
      applied_ops: 2,
      createdIds: ["node-wire"],
      created_node_ids: ["node-wire"],
    });
    expect(receipt?.createdIds).not.toBe(wireCreatedIds);
    expect(receipt?.created_node_ids).not.toBe(wireCreatedIds);
    expect(receipt?.createdIds).not.toBe(receipt?.created_node_ids);
  });

  it("gives replay attempts distinct terminal identities", () => {
    const failed = buildCanvasCommandReceipt({
      commandId: "command-retry",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "result",
      attempt: 1,
      success: false,
    });
    const succeeded = buildCanvasCommandReceipt({
      commandId: "command-retry",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "result",
      attempt: 2,
      success: true,
    });

    expect(failed?.receiptId).toBe("command-retry:result:1:failure");
    expect(succeeded?.receiptId).toBe("command-retry:result:2:success");
    expect(succeeded?.attempt).toBe(2);
  });

  it("prefers canonical fields when both canonical and wire inputs are present", () => {
    const canonicalCreatedIds = ["node-canonical"];
    const receipt = buildCanvasCommandReceipt({
      commandId: "command-canonical",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "result",
      applied: 3,
      applied_ops: 99,
      createdIds: canonicalCreatedIds,
      created_node_ids: ["node-wire"],
      createdAt: 123,
    });

    expect(receipt).toMatchObject({
      applied: 3,
      applied_ops: 3,
      createdIds: ["node-canonical"],
      created_node_ids: ["node-canonical"],
    });
    expect(receipt?.createdIds).not.toBe(canonicalCreatedIds);
    expect(receipt?.created_node_ids).not.toBe(canonicalCreatedIds);
  });

  it("rejects receipts that cannot be routed to one canvas command", () => {
    expect(buildCanvasCommandReceipt({
      commandId: "",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "ack",
    })).toBeNull();
  });
});
