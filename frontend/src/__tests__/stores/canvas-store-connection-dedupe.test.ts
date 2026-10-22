// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

const nodes = [
  {
    id: "source",
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x: 0, y: 0 },
    data: { displayName: "图片节点", model: "direct/image-test" },
  },
  {
    id: "target",
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x: 500, y: 0 },
    data: { displayName: "图片节点", model: "direct/image-test" },
  },
];

describe("canvasStore connection dedupe", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("collapses legacy duplicate plain edges and keeps the canonical target handle", () => {
    useCanvasStore.getState().setCanvasData(nodes, [
      {
        id: "edge-source-handle",
        source: "source",
        target: "target",
        sourceHandle: "source",
        targetHandle: "source",
      },
      {
        id: "edge-target-handle",
        source: "source",
        target: "target",
        sourceHandle: "source",
        targetHandle: "target",
      },
    ]);

    expect(useCanvasStore.getState().edges.map((edge) => edge.id)).toEqual([
      "edge-target-handle",
    ]);
  });

  it("rejects a second manual connection between the same two nodes", () => {
    useCanvasStore.getState().setCanvasData(nodes, [
      {
        id: "edge-target-handle",
        source: "source",
        target: "target",
        sourceHandle: "source",
        targetHandle: "target",
      },
    ]);

    useCanvasStore.getState().onConnect({
      source: "source",
      target: "target",
      sourceHandle: "source",
      targetHandle: "source",
    });

    expect(useCanvasStore.getState().edges).toHaveLength(1);
  });

  it("preserves semantic edges that intentionally share a node pair", () => {
    useCanvasStore.getState().setCanvasData(nodes, [
      {
        id: "role-a",
        source: "source",
        target: "target",
        sourceHandle: "source",
        targetHandle: "target",
        data: { role: "reference" },
      },
      {
        id: "role-b",
        source: "source",
        target: "target",
        sourceHandle: "source",
        targetHandle: "target",
        data: { role: "continuity" },
      },
    ]);

    expect(useCanvasStore.getState().edges).toHaveLength(2);
  });
});

