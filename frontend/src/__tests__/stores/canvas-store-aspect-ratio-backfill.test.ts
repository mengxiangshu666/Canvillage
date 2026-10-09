// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";

/**
 * 加载时对空 aspectRatio 的回填必须用**该节点类型自己的**默认比例。
 *
 * 事故：视频目录还没到时打开画布，VideoNode 的派生比例退化成空字符串并被
 * autosave 落盘；下次加载时 store 用全局 DEFAULT_ASPECT_RATIO（1:1，本是给图片
 * 节点用的）回填，视频节点于是从 16:9 变成 1:1。而 1:1 恰好是多数视频模型的
 * 合法档位，这个值永远不会自我纠正。实测：2026-09-14 用户画布 rev1634 三个
 * 视频节点被写成 1:1。
 */
describe("empty aspectRatio backfills from the node type's own default", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  function node(id: string) {
    return useCanvasStore.getState().nodes.find((n) => n.id === id)!;
  }

  it("backfills a video node to 16:9 instead of the global 1:1", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "video-empty-aspect",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          data: { aspectRatio: "" },
        },
      ],
      [],
    );

    expect((node("video-empty-aspect").data as { aspectRatio?: string }).aspectRatio).toBe("16:9");
  });

  it("keeps the image-node fallback at 1:1", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "image-empty-aspect",
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 0, y: 0 },
          data: { aspectRatio: "" },
        },
      ],
      [],
    );

    expect((node("image-empty-aspect").data as { aspectRatio?: string }).aspectRatio).toBe("1:1");
  });

  it("does not overwrite an already-declared aspect ratio", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "video-declared-aspect",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          data: { aspectRatio: "9:16" },
        },
      ],
      [],
    );

    expect((node("video-declared-aspect").data as { aspectRatio?: string }).aspectRatio).toBe(
      "9:16",
    );
  });
});
