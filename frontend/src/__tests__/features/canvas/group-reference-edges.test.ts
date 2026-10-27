// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import {
  getDownstreamSpawnTypes,
  nodeHasSourceHandle,
} from "@/features/canvas/domain/nodeRegistry";

function node(
  id: string,
  type: string,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: {},
    ...extra,
  } as unknown as never;
}

function seed(nodes: ReturnType<typeof node>[]) {
  useCanvasStore.setState({ nodes, edges: [], selectedNodeId: null });
}

function pairs() {
  return useCanvasStore
    .getState()
    .edges.map((edge) => `${edge.source}->${edge.target}`)
    .sort();
}

describe("组作为引用源", () => {
  beforeEach(() => {
    seed([
      node("g1", CANVAS_NODE_TYPES.group),
      node("img1", CANVAS_NODE_TYPES.upload, { parentId: "g1" }),
      node("txt1", CANVAS_NODE_TYPES.textAnnotation, { parentId: "g1" }),
      node("g2", CANVAS_NODE_TYPES.group, { parentId: "g1" }),
      node("img2", CANVAS_NODE_TYPES.upload, { parentId: "g2" }),
      node("gen1", CANVAS_NODE_TYPES.imageGen),
    ]);
  });

  it("组在能力表里是可引用的源节点，右侧会出现「引用该节点生成」", () => {
    expect(nodeHasSourceHandle(CANVAS_NODE_TYPES.group)).toBe(true);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.group).length).toBeGreaterThan(0);
  });

  it("addEdge 展开到组内每个有输出的成员，不连组本身", () => {
    const edgeId = useCanvasStore.getState().addEdge("g1", "gen1");

    expect(pairs()).toEqual(["img1->gen1", "img2->gen1", "txt1->gen1"]);
    expect(edgeId).toBe("e-img1-gen1");
    expect(useCanvasStore.getState().edges.some((edge) => edge.source === "g1")).toBe(false);
  });

  it("手动拖线 onConnect 同样展开", () => {
    useCanvasStore.getState().onConnect({
      source: "g1",
      target: "gen1",
      sourceHandle: "source",
      targetHandle: "target",
    });

    expect(pairs()).toEqual(["img1->gen1", "img2->gen1", "txt1->gen1"]);
  });

  it("组里没有可当输入的成员时，不产生任何边", () => {
    seed([
      node("empty-group", CANVAS_NODE_TYPES.group),
      node("gen1", CANVAS_NODE_TYPES.imageGen),
    ]);

    expect(useCanvasStore.getState().addEdge("empty-group", "gen1")).toBeNull();
    expect(useCanvasStore.getState().edges).toEqual([]);
  });
});
