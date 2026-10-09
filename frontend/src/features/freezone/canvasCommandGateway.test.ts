import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_COMMAND_RECEIPT_EVENT,
  emitCanvasCommandReceipt,
  type CanvasCommandReceipt,
} from "@/features/superchat/canvas-command-receipts";
import {
  buildCanvasCommandResultReceipt,
  createCanvasCommandGateway,
  loadCanvasAgentCommandIds,
  persistAppliedCanvasCommand,
  saveCanvasAgentCommandIds,
  type CanvasAgentCommandEnvelope,
} from "./canvasCommandGateway";

const envelope: CanvasAgentCommandEnvelope = {
  schema: "canvas_chat_commands.v1",
  project_id: "project-a",
  canvas_id: "canvas-a",
  command_id: "cmd-a",
  canvas_command_emitted: true,
  commands: [{ type: "create_canvas_node", node_type: "textAnnotationNode" }],
};

function captureReceipts(): { receipts: CanvasCommandReceipt[]; cleanup: () => void } {
  const receipts: CanvasCommandReceipt[] = [];
  const onReceipt = (event: Event) => {
    receipts.push((event as CustomEvent<CanvasCommandReceipt>).detail);
  };
  window.addEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt);
  return {
    receipts,
    cleanup: () => window.removeEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt),
  };
}

describe("canvasCommandGateway", () => {
  afterEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it("reserves one in-flight command and rejects duplicate triggers before side effects", () => {
    const { receipts, cleanup } = captureReceipts();
    const gateway = createCanvasCommandGateway({
      projectId: "project-a",
      canvasId: "canvas-a",
      executedIds: new Set(),
      currentRevision: () => null,
      setToast: vi.fn(),
      saveExecutedIds: vi.fn(),
      optimisticGraphPresent: () => true,
    });
    try {
      expect(gateway.reserve(envelope)).toBe(true);
      expect(gateway.reserve(envelope)).toBe(false);
      expect(receipts).toContainEqual(expect.objectContaining({
        commandId: "cmd-a",
        stage: "progress",
        duplicate: true,
      }));
    } finally {
      cleanup();
    }
  });

  it("persists executed command ids through the configured store", () => {
    const saveExecutedIds = vi.fn((ids: Set<string>) =>
      saveCanvasAgentCommandIds("project-a", "canvas-a", ids),
    );
    const gateway = createCanvasCommandGateway({
      projectId: "project-a",
      canvasId: "canvas-a",
      executedIds: new Set(),
      currentRevision: () => null,
      setToast: vi.fn(),
      saveExecutedIds,
      optimisticGraphPresent: () => true,
    });

    gateway.markExecuted("cmd-a");
    expect(saveExecutedIds).toHaveBeenCalledTimes(1);
    expect(loadCanvasAgentCommandIds("project-a", "canvas-a").has("cmd-a")).toBe(true);

    gateway.forgetExecuted("cmd-a");
    expect(loadCanvasAgentCommandIds("project-a", "canvas-a").has("cmd-a")).toBe(false);
  });

  it("keeps command attempt counters inside the gateway", () => {
    const gateway = createCanvasCommandGateway({
      projectId: "project-a",
      canvasId: "canvas-a",
      executedIds: new Set(),
      currentRevision: () => null,
      setToast: vi.fn(),
      saveExecutedIds: vi.fn(),
      optimisticGraphPresent: () => true,
    });

    expect(gateway.nextAttempt("cmd-a")).toBe(1);
    expect(gateway.nextAttempt("cmd-a")).toBe(2);
    expect(gateway.nextAttempt("cmd-b")).toBe(1);
  });

  it("still reserves a browser proposal whose optional revision trails the live canvas", () => {
    // The stale-revision skip only reclassifies frames the server already
    // owns. A marked client proposal stays applicable no matter how far the
    // canvas revision has advanced, so the new check must not reject it.
    const setToast = vi.fn();
    const gateway = createCanvasCommandGateway({
      projectId: "project-a",
      canvasId: "canvas-a",
      executedIds: new Set(),
      currentRevision: () => 25,
      setToast,
      saveExecutedIds: vi.fn(),
      optimisticGraphPresent: () => true,
    });

    expect(gateway.reserve({ ...envelope, revision: 3 })).toBe(true);
    expect(setToast).not.toHaveBeenCalled();
  });

  it("waits out loading state and accepts a newer revision as durable evidence", async () => {
    vi.useFakeTimers();
    let status = "loading";
    let revision: number | null = 10;
    const flush = vi.fn(async () => false);
    const resultPromise = persistAppliedCanvasCommand({
      beforeRevision: 10,
      getStatus: () => status,
      getRevision: () => revision,
      flush,
      delays: [0, 10],
    });

    await vi.advanceTimersByTimeAsync(1);
    status = "ready";
    revision = 11;
    await vi.advanceTimersByTimeAsync(10);

    await expect(resultPromise).resolves.toEqual({ saved: true, revision: 11 });
    expect(flush).toHaveBeenCalledTimes(1);
  });
});

describe("buildCanvasCommandResultReceipt", () => {
  it("never reports a persisted success for an optimistic preview", () => {
    const built = buildCanvasCommandResultReceipt({
      commandId: "cmd-optimistic",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      revision: 7,
      attempt: 1,
      applied: 1,
      requested: 1,
      optimistic: true,
    });
    expect(built.success).toBeUndefined();
    expect(built.optimistic).toBe(true);
    // The revision belongs to the previous persisted command; a preview must
    // not carry it as if it were this command's durable revision.
    expect(built.revision).toBeUndefined();

    // Pin the wire shape too: the emitted event must not claim completion.
    const receipts: CanvasCommandReceipt[] = [];
    const onReceipt = (event: Event) => {
      receipts.push((event as CustomEvent<CanvasCommandReceipt>).detail);
    };
    window.addEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt);
    try {
      emitCanvasCommandReceipt(built);
    } finally {
      window.removeEventListener(CANVAS_COMMAND_RECEIPT_EVENT, onReceipt);
    }
    expect(receipts).toHaveLength(1);
    expect(receipts[0]).toMatchObject({ stage: "result", optimistic: true });
    expect(receipts[0].success).toBeUndefined();
    expect(receipts[0].revision).toBeUndefined();
  });

  it("still reports a persisted success once the server revision is known", () => {
    const built = buildCanvasCommandResultReceipt({
      commandId: "cmd-persisted",
      projectId: "project-a",
      canvasId: "canvas-a",
      turnId: "turn-a",
      revision: 12,
      attempt: 1,
      applied: 2,
      requested: 2,
      optimistic: false,
    });
    expect(built.success).toBe(true);
    expect(built.optimistic).toBeUndefined();
    expect(built.revision).toBe(12);
  });
});
