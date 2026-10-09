import { beforeEach, describe, expect, it } from "vitest";

import {
  acknowledgeCanvasReceiptDelivery,
  CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS,
  deferCanvasReceiptDelivery,
  enqueueCanvasReceiptDelivery,
  loadCanvasReceiptOutbox,
  resetCanvasReceiptDeliveryRetries,
} from "./canvas-command-receipt-outbox";
import { buildCanvasCommandReceipt } from "./canvas-command-receipts";

describe("canvas command receipt outbox", () => {
  beforeEach(() => localStorage.clear());

  it("persists one delivery until the server acknowledges it", () => {
    const receipt = buildCanvasCommandReceipt({
      commandId: "command-a",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      stage: "result",
      attempt: 2,
      success: true,
      createdAt: 100,
    });
    expect(receipt).not.toBeNull();

    enqueueCanvasReceiptDelivery("scope-a", {
      receipt: receipt!,
      scope: { kind: "project", id: "project-a", canvas_id: "canvas-a" },
      turnId: "turn-a",
      queuedAt: 100,
    });
    enqueueCanvasReceiptDelivery("scope-a", {
      receipt: receipt!,
      scope: { kind: "project", id: "project-a", canvas_id: "canvas-a" },
      turnId: "turn-a",
      queuedAt: 101,
    });

    expect(loadCanvasReceiptOutbox("scope-a", 101)).toHaveLength(1);
    acknowledgeCanvasReceiptDelivery("scope-a", receipt!.receiptId);
    expect(loadCanvasReceiptOutbox("scope-a", 101)).toEqual([]);
  });

  it("drops expired deliveries instead of retrying forever", () => {
    const receipt = buildCanvasCommandReceipt({
      commandId: "command-old",
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "ack",
      createdAt: 1,
    });
    enqueueCanvasReceiptDelivery("scope-old", {
      receipt: receipt!,
      scope: { kind: "project", id: "project-a", canvas_id: "canvas-a" },
      turnId: "turn-old",
      queuedAt: 1,
    });

    expect(loadCanvasReceiptOutbox("scope-old", 24 * 60 * 60 * 1000 + 2)).toEqual([]);
  });

  it("persists retry state per receipt and stops automatic retries at the limit", () => {
    const makeReceipt = (commandId: string) => buildCanvasCommandReceipt({
      commandId,
      projectId: "project-a",
      canvasId: "canvas-a",
      stage: "result",
      success: true,
      createdAt: 100,
    })!;
    for (const commandId of ["command-a", "command-b"]) {
      enqueueCanvasReceiptDelivery("scope-retries", {
        receipt: makeReceipt(commandId),
        scope: { kind: "project", id: "project-a", canvas_id: "canvas-a" },
        turnId: "turn-a",
        queuedAt: 100,
      });
    }

    let now = 100;
    for (let attempt = 1; attempt <= CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS; attempt += 1) {
      const deferred = deferCanvasReceiptDelivery("scope-retries", "command-a:result", now);
      expect(deferred?.failedAttempts).toBe(attempt);
      if (deferred?.nextAttemptAt) now = deferred.nextAttemptAt;
    }
    deferCanvasReceiptDelivery("scope-retries", "command-a:result", now + 10_000);

    const [failed, untouched] = loadCanvasReceiptOutbox("scope-retries", now + 10_000);
    expect(failed).toMatchObject({
      failedAttempts: CANVAS_RECEIPT_MAX_FAILED_ATTEMPTS,
    });
    expect(failed.nextAttemptAt).toBeUndefined();
    expect(untouched.failedAttempts).toBeUndefined();
    expect(untouched.nextAttemptAt).toBeUndefined();

    resetCanvasReceiptDeliveryRetries("scope-retries", now + 10_000);
    expect(loadCanvasReceiptOutbox("scope-retries", now + 10_000)).toEqual([
      expect.not.objectContaining({ failedAttempts: expect.anything() }),
      expect.not.objectContaining({ failedAttempts: expect.anything() }),
    ]);
  });
});
