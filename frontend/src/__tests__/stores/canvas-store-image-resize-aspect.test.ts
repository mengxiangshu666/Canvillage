// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/stores/canvasStore";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";

/**
 * 缩放图片节点结束后，节点框必须吸附回图片真实比例（aspectRatio），否则 object-contain
 * 显示的图片会在偏离比例的节点里露出底色形成黑边。回归断言：显式 width/height 与
 * style 必须同步更新（React Flow 渲染时显式尺寸优先于 style）。
 */
describe("image node resize snaps to aspect ratio (no letterbox)", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  function node(id: string) {
    return useCanvasStore.getState().nodes.find((n) => n.id === id)!;
  }

  it("snaps a distorted (too-tall) resized box back to the image aspect ratio", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "img",
          type: CANVAS_NODE_TYPES.exportImage,
          position: { x: 0, y: 0 },
          width: 400,
          height: 400,
          style: { width: 400, height: 400 },
          data: { imageUrl: "x.png", aspectRatio: "2:1" },
        },
      ],
      [],
    );

    // Simulate a NodeResizer resize-end producing a square (distorted) box.
    useCanvasStore.getState().onNodesChange([
      {
        id: "img",
        type: "dimensions",
        resizing: false,
        setAttributes: true,
        dimensions: { width: 400, height: 400 },
      },
    ]);

    const n = node("img");
    // 2:1 fitted inside a 400x400 box → 400x200, removing top/bottom black bars.
    expect(n.width).toBe(400);
    expect(n.height).toBe(200);
    expect(n.style?.width).toBe(400);
    expect(n.style?.height).toBe(200);
    expect((n.data as { isSizeManuallyAdjusted?: boolean }).isSizeManuallyAdjusted).toBe(true);
  });

  it("leaves an already-aspect-correct resized box untouched", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "img",
          type: CANVAS_NODE_TYPES.exportImage,
          position: { x: 0, y: 0 },
          width: 600,
          height: 300,
          style: { width: 600, height: 300 },
          data: { imageUrl: "x.png", aspectRatio: "2:1" },
        },
      ],
      [],
    );

    useCanvasStore.getState().onNodesChange([
      {
        id: "img",
        type: "dimensions",
        resizing: false,
        setAttributes: true,
        dimensions: { width: 600, height: 300 },
      },
    ]);

    const n = node("img");
    expect(n.width).toBe(600);
    expect(n.height).toBe(300);
  });

  it("reshapes an empty image node immediately when its configured ratio changes", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "empty-image",
          type: CANVAS_NODE_TYPES.imageGen,
          position: { x: 0, y: 0 },
          width: 580,
          height: 360,
          style: { width: 580, height: 360 },
          data: { imageUrl: null, aspectRatio: "16:9" },
        },
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData("empty-image", { aspectRatio: "9:16" });

    const portrait = node("empty-image");
    expect(portrait.width).toBe(343);
    expect(portrait.height).toBe(609);
    expect(Number(portrait.width) / Number(portrait.height)).toBeCloseTo(9 / 16, 2);

    useCanvasStore.getState().updateNodeData("empty-image", { aspectRatio: "16:9" });

    const landscape = node("empty-image");
    expect(Number(landscape.width) / Number(landscape.height)).toBeCloseTo(16 / 9, 2);
  });

  it("applies the same empty-state ratio projection to video nodes", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "empty-video",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          width: 580,
          height: 380,
          style: { width: 580, height: 380 },
          data: { videoUrl: null, aspectRatio: "16:9" },
        },
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData("empty-video", { aspectRatio: "9:16" });

    const portrait = node("empty-video");
    expect(Number(portrait.width) / Number(portrait.height)).toBeCloseTo(9 / 16, 2);
    expect(Number(portrait.width)).toBeLessThan(Number(portrait.height));
  });

  it("reshapes an empty video node from a fixed pixel size slot", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "fixed-slot-video",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          width: 580,
          height: 380,
          style: { width: 580, height: 380 },
          data: { videoUrl: null, aspectRatio: "16:9", sizeSlot: null },
        },
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData("fixed-slot-video", { sizeSlot: "992x432" });

    const nodeWithFixedSlot = node("fixed-slot-video");
    expect(Number(nodeWithFixedSlot.width) / Number(nodeWithFixedSlot.height)).toBeCloseTo(
      992 / 432,
      2,
    );
  });

  it("normalizes a saved empty node to its configured size slot during hydration", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "saved-empty-video",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          width: 580,
          height: 380,
          style: { width: 580, height: 380 },
          data: { videoUrl: null, aspectRatio: "16:9", sizeSlot: "992x432" },
        },
      ],
      [],
    );

    const hydrated = node("saved-empty-video");
    expect(Number(hydrated.width) / Number(hydrated.height)).toBeCloseTo(992 / 432, 2);
  });

  it("hydrates a completed video frame from persisted media dimensions without letterboxing", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "completed-video",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          width: 580,
          height: 380,
          style: { width: 580, height: 380 },
          data: {
            videoUrl: "shot.mp4",
            aspectRatio: "16:9",
            widthPx: 1080,
            heightPx: 1920,
            isSizeManuallyAdjusted: false,
          },
        },
      ],
      [],
    );

    const hydrated = node("completed-video");
    expect(Number(hydrated.width) / Number(hydrated.height)).toBeCloseTo(9 / 16, 2);
    expect(hydrated.style?.width).toBe(hydrated.width);
    expect(hydrated.style?.height).toBe(hydrated.height);
  });

  it("hydrates a completed image from persisted actual dimensions", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "completed-image",
          type: CANVAS_NODE_TYPES.imageGen,
          position: { x: 0, y: 0 },
          width: 580,
          height: 360,
          style: { width: 580, height: 360 },
          data: {
            imageUrl: "shot.png",
            aspectRatio: "16:9",
            actualWidth: 1080,
            actualHeight: 1920,
            isSizeManuallyAdjusted: false,
          },
        },
      ],
      [],
    );

    const hydrated = node("completed-image");
    expect(Number(hydrated.width) / Number(hydrated.height)).toBeCloseTo(9 / 16, 2);
    expect(hydrated.style?.width).toBe(hydrated.width);
    expect(hydrated.style?.height).toBe(hydrated.height);
  });

  it("does not reshape an existing image when persisted actual dimensions are absent", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "legacy-image",
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 0, y: 0 },
          width: 260,
          height: 160,
          style: { width: 260, height: 160 },
          data: { imageUrl: "legacy.png", aspectRatio: "16:9" },
        },
      ],
      [],
    );

    const hydrated = node("legacy-image");
    expect(hydrated.width).toBe(260);
    expect(hydrated.height).toBe(160);
  });

  it("keeps a user's video frame size when media metadata arrives or hydrates", () => {
    useCanvasStore.getState().setCanvasData(
      [
        {
          id: "manual-video",
          type: CANVAS_NODE_TYPES.video,
          position: { x: 0, y: 0 },
          width: 700,
          height: 500,
          style: { width: 700, height: 500 },
          data: {
            videoUrl: "shot.mp4",
            aspectRatio: "16:9",
            widthPx: 1080,
            heightPx: 1920,
            isSizeManuallyAdjusted: true,
          },
        },
      ],
      [],
    );

    useCanvasStore.getState().updateNodeData("manual-video", {
      widthPx: 1920,
      heightPx: 1080,
    });

    const manual = node("manual-video");
    expect(manual.width).toBe(700);
    expect(manual.height).toBe(500);
    expect(manual.style?.width).toBe(700);
    expect(manual.style?.height).toBe(500);
  });
});
