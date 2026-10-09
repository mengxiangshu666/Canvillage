// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

const { downloadUrlAsFileMock } = vi.hoisted(() => ({
  downloadUrlAsFileMock: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/browserDownload", () => ({
  downloadUrlAsFile: downloadUrlAsFileMock,
}));

import { canvasEventBus } from "@/features/canvas/application/canvasServices";
import type { VideoFrameCaptureReceipt } from "@/features/canvas/application/ports";
import type { CanvasNode } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";
import type { ChatScope, ServerFrame } from "./types";
import {
  dispatchFeToolCall,
  FE_TOOL_BRIDGE_VERSION,
  FE_TOOL_LIFECYCLE_EVENT,
  listRegisteredFeTools,
  registerFeTool,
  type FeToolLifecycle,
} from "./fe-tool-bridge";

const scope: ChatScope = { kind: "project", id: "project-a", canvas_id: "canvas-a" };

function frame(
  callId: string,
  name: string,
  input: Record<string, unknown> = {},
  overrides: Partial<Extract<ServerFrame, { type: "fe_tool.call" }>> = {},
): ServerFrame {
  return {
    type: "fe_tool.call",
    schema: FE_TOOL_BRIDGE_VERSION,
    call_id: callId,
    turn_id: "turn-a",
    project_id: "project-a",
    canvas_id: "canvas-a",
    name,
    input,
    timeout_ms: 500,
    agent_event: { event_id: `event-${callId}` },
    ...overrides,
  };
}

async function lifecycleFor(call: ServerFrame): Promise<FeToolLifecycle[]> {
  const events: FeToolLifecycle[] = [];
  const listener = (raw: Event) => {
    events.push((raw as CustomEvent<FeToolLifecycle>).detail);
  };
  window.addEventListener(FE_TOOL_LIFECYCLE_EVENT, listener);
  dispatchFeToolCall(call, scope);
  await vi.waitFor(() => expect(events[events.length - 1]?.phase).toBe("result"));
  window.removeEventListener(FE_TOOL_LIFECYCLE_EVENT, listener);
  return events;
}

describe("FE Tool Bridge", () => {
  afterEach(() => {
    useCanvasStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      pendingFocusNodeId: null,
    });
    vi.useRealTimers();
    downloadUrlAsFileMock.mockClear();
  });

  it("registers one real handler for every supported browser UI action", () => {
    expect(listRegisteredFeTools().map((item) => item.name).sort()).toEqual([
      "village.ui.close_tool_dialog",
      "village.ui.fit_view",
      "village.ui.focus_node",
      "village.ui.open_tool_dialog",
      "village.ui.select_node",
      "village.ui.video_capture_frame",
      "village.ui.video_download",
      "village.ui.video_fullscreen",
      "village.ui.video_set_operation",
    ]);
  });

  it("rejects ordinary tool frames, unknown tools and cross-canvas calls", () => {
    expect(dispatchFeToolCall({ type: "tool.call", name: "village.ui.fit_view" }, scope)).toBe(false);
    expect(dispatchFeToolCall(frame("unknown", "village.ui.missing"), scope)).toBe(false);
    expect(dispatchFeToolCall(frame("wrong-canvas", "village.ui.fit_view", {}, {
      canvas_id: "canvas-b",
    }), scope)).toBe(false);
  });

  it("selects and focuses an existing node with ack/progress/result evidence", async () => {
    useCanvasStore.setState({
      nodes: [{ id: "node-a", type: "textAnnotationNode", position: { x: 0, y: 0 }, data: {} } as CanvasNode],
    });

    const selected = await lifecycleFor(frame("select", "village.ui.select_node", { node_id: "node-a" }));
    expect(selected.map((event) => event.phase)).toEqual(["ack", "progress", "result"]);
    expect(selected[0].eventId).toBe("event-select:ack");
    expect(selected[selected.length - 1]?.result).toEqual({ node_id: "node-a", selected: true });
    expect(useCanvasStore.getState().selectedNodeId).toBe("node-a");

    const focused = await lifecycleFor(frame("focus", "village.ui.focus_node", { node_id: "node-a" }));
    expect(focused[focused.length - 1]?.result).toEqual({ node_id: "node-a", focused: true });
    expect(useCanvasStore.getState().pendingFocusNodeId).toBe("node-a");
  });

  it("publishes fit-view and tool-dialog operations through the real canvas event bus", async () => {
    useCanvasStore.setState({
      nodes: [{ id: "node-a", type: "imageNode", position: { x: 0, y: 0 }, data: {} } as CanvasNode],
    });
    const fit = vi.fn();
    const open = vi.fn();
    const close = vi.fn();
    const unsubscribers = [
      canvasEventBus.subscribe("canvas/ui-fit-view", fit),
      canvasEventBus.subscribe("tool-dialog/open", open),
      canvasEventBus.subscribe("tool-dialog/close", close),
    ];
    try {
      await lifecycleFor(frame("fit", "village.ui.fit_view", { duration_ms: 360, max_zoom: 1.2 }));
      await lifecycleFor(frame("open", "village.ui.open_tool_dialog", {
        node_id: "node-a",
        tool_type: "crop",
      }));
      await lifecycleFor(frame("close", "village.ui.close_tool_dialog"));
    } finally {
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    }
    expect(fit).toHaveBeenCalledWith({ duration: 360, maxZoom: 1.2 });
    expect(open).toHaveBeenCalledWith({ nodeId: "node-a", toolType: "crop" });
    expect(close).toHaveBeenCalledWith(undefined);
  });

  it("routes video node actions through the event bus and preserves the real receipt", async () => {
    useCanvasStore.setState({
      nodes: [{
        id: "video-a",
        type: "videoNode",
        position: { x: 0, y: 0 },
        data: { videoUrl: "https://example.test/shot.mp4" },
      } as CanvasNode],
    });
    const capture = vi.fn((payload: {
      nodeId: string;
      mode: "first" | "last" | "current";
      onComplete?: (receipt: VideoFrameCaptureReceipt) => void;
    }) => {
      payload.onComplete?.({
        ok: true,
        nodeId: payload.nodeId,
        mode: payload.mode,
        createdNodeId: "frame-a",
        outputUrl: "https://example.test/frame.png",
      });
    });
    const operation = vi.fn((payload: {
      nodeId: string;
      operation: "clip" | "subtitle-smart" | "subtitle-box";
      onComplete?: (receipt: { ok: boolean; nodeId: string; operation: string; error?: string }) => void;
    }) => {
      payload.onComplete?.({ ok: true, operation: payload.operation, nodeId: "video-a" });
    });
    const unsubscribers = [
      canvasEventBus.subscribe("video-node/capture-frame", capture),
      canvasEventBus.subscribe("video-node/set-operation", operation),
    ];
    try {
      const captureEvents = await lifecycleFor(frame("capture", "village.ui.video_capture_frame", {
        node_id: "video-a",
        mode: "first",
      }));
      expect(captureEvents[captureEvents.length - 1]?.result).toMatchObject({ ok: true, createdNodeId: "frame-a" });

      const operationEvents = await lifecycleFor(frame("clip", "village.ui.video_set_operation", {
        node_id: "video-a",
        operation: "clip",
      }));
      expect(operationEvents[operationEvents.length - 1]?.result).toMatchObject({ ok: true, operation: "clip" });
    } finally {
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    }
    expect(capture).toHaveBeenCalledWith(expect.objectContaining({ nodeId: "video-a", mode: "first" }));
    expect(operation).toHaveBeenCalledWith(expect.objectContaining({ nodeId: "video-a", operation: "clip" }));
  });

  it("downloads and opens a completed video node through real browser actions", async () => {
    useCanvasStore.setState({
      nodes: [{
        id: "video-a",
        type: "videoNode",
        position: { x: 0, y: 0 },
        data: { videoUrl: "https://example.test/shot.mp4", displayName: "shot" },
      } as CanvasNode],
    });
    const viewer = vi.fn();
    const unsubscribe = canvasEventBus.subscribe("video-viewer/open", viewer);
    try {
      const downloaded = await lifecycleFor(frame("download", "village.ui.video_download", {
        node_id: "video-a",
      }));
      expect(downloaded[downloaded.length - 1]?.result).toEqual({
        node_id: "video-a",
        downloaded: true,
        filename: "shot.mp4",
      });
      const fullscreen = await lifecycleFor(frame("fullscreen", "village.ui.video_fullscreen", {
        node_id: "video-a",
      }));
      expect(fullscreen[fullscreen.length - 1]?.result).toMatchObject({
        node_id: "video-a",
        opened: true,
        fullscreen_requested: true,
      });
    } finally {
      unsubscribe();
    }
    expect(downloadUrlAsFileMock).toHaveBeenCalledWith("https://example.test/shot.mp4", "shot.mp4");
    expect(viewer).toHaveBeenCalledWith({ videoUrl: "https://example.test/shot.mp4", title: "shot" });
  });

  it("deduplicates completed call ids and reports handler failures and timeouts", async () => {
    const failed = await lifecycleFor(frame("missing-node", "village.ui.select_node", { node_id: "missing" }));
    expect(failed[failed.length - 1]?.error).toContain("canvas node not found");
    expect(dispatchFeToolCall(frame("missing-node", "village.ui.select_node", { node_id: "missing" }), scope)).toBe(false);

    const unregister = registerFeTool({
      name: "village.ui.test_timeout",
      description: "test timeout",
      handler: () => new Promise(() => undefined),
    });
    try {
      const timedOut = await lifecycleFor(frame("timeout", "village.ui.test_timeout", {}, { timeout_ms: 250 }));
      expect(timedOut[timedOut.length - 1]?.error).toBe("FE tool timeout");
    } finally {
      unregister();
    }
  });
});
