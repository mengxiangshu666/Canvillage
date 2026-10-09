// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  loadPinnedCanvasNodeIds,
  pinCanvasNodeForAgent,
  resolveCanvasAgentScope,
  setPinnedCanvasNodesForAgent,
  unpinCanvasNodeForAgent,
} from "./canvas-agent-pin-store";

describe("canvas agent pin store", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("uses the mounted runtime scope when the URL has no canvas parameter", () => {
    expect(resolveCanvasAgentScope(
      { projectId: "project-runtime", canvasId: "canvas-runtime" },
      { project: "project-url", canvas: null },
    )).toEqual({ projectId: "project-runtime", canvasId: "canvas-runtime" });
  });

  it("keeps URL scope only as a legacy fallback", () => {
    expect(resolveCanvasAgentScope(
      {},
      { project: "project-url", canvas: "canvas-url" },
    )).toEqual({ projectId: "project-url", canvasId: "canvas-url" });
  });

  it("pins, dedupes, and unpins node ids for a project/canvas", () => {
    const projectId = "proj-1";
    const canvasId = "canvas-1";
    expect(pinCanvasNodeForAgent({ projectId, canvasId, nodeId: "n1" }).added).toBe(true);
    expect(pinCanvasNodeForAgent({ projectId, canvasId, nodeId: "n1" }).added).toBe(false);
    expect(pinCanvasNodeForAgent({ projectId, canvasId, nodeId: "n2" }).ids).toEqual(["n1", "n2"]);
    expect(loadPinnedCanvasNodeIds(projectId, canvasId)).toEqual(["n1", "n2"]);
    expect(unpinCanvasNodeForAgent({ projectId, canvasId, nodeId: "n1" })).toEqual(["n2"]);
  });

  it("emits pin change events for cross-component sync", () => {
    const spy = vi.fn();
    window.addEventListener("village-canvas:canvas-agent-pins-changed", spy);
    setPinnedCanvasNodesForAgent({ projectId: "p", canvasId: "c", ids: ["a", "b"] });
    expect(spy).toHaveBeenCalled();
    const detail = (spy.mock.calls[0]?.[0] as CustomEvent).detail;
    expect(detail.ids).toEqual(["a", "b"]);
    window.removeEventListener("village-canvas:canvas-agent-pins-changed", spy);
  });
});
