// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

function edgesOf(nodeId: string) {
  return useCanvasStore
    .getState()
    .edges.filter((edge) => edge.source === nodeId || edge.target === nodeId);
}

describe("canvasStore.convertNodeType — 换类型后同步清理不再合法的边", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("Upload 转成音频后，原来连向图片节点的边被清掉", () => {
    const store = useCanvasStore.getState();
    const upload = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});
    const image = store.addNode(CANVAS_NODE_TYPES.imageGen, { x: 400, y: 0 }, {});

    useCanvasStore.getState().onConnect({
      source: upload,
      target: image,
      sourceHandle: "source",
      targetHandle: "target",
    });
    expect(edgesOf(upload)).toHaveLength(1);

    expect(
      useCanvasStore
        .getState()
        .convertNodeType(upload, CANVAS_NODE_TYPES.audio, { audioUrl: "a.mp3" }),
    ).toBe(true);
    expect(edgesOf(upload)).toEqual([]);
  });

  it("换类型后仍然合法的边原样保留", () => {
    const store = useCanvasStore.getState();
    const upload = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});
    const video = store.addNode(CANVAS_NODE_TYPES.video, { x: 400, y: 0 }, {});

    useCanvasStore.getState().onConnect({
      source: upload,
      target: video,
      sourceHandle: "source",
      targetHandle: "target",
    });
    expect(edgesOf(upload)).toHaveLength(1);

    useCanvasStore
      .getState()
      .convertNodeType(upload, CANVAS_NODE_TYPES.audio, { audioUrl: "a.mp3" });
    expect(edgesOf(upload)).toHaveLength(1);
  });

  it("清理后的边可通过撤销与原节点类型一起恢复", () => {
    const store = useCanvasStore.getState();
    const upload = store.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 0 }, {});
    const image = store.addNode(CANVAS_NODE_TYPES.imageGen, { x: 400, y: 0 }, {});
    useCanvasStore.getState().onConnect({
      source: upload,
      target: image,
      sourceHandle: "source",
      targetHandle: "target",
    });

    useCanvasStore
      .getState()
      .convertNodeType(upload, CANVAS_NODE_TYPES.audio, { audioUrl: "a.mp3" });
    expect(edgesOf(upload)).toEqual([]);

    useCanvasStore.getState().undo();
    expect(edgesOf(upload)).toHaveLength(1);
    expect(useCanvasStore.getState().nodes.find((node) => node.id === upload)?.type).toBe(
      CANVAS_NODE_TYPES.upload,
    );
  });
});
