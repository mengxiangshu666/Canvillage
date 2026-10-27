// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CanvasAllFeaturesPanel } from "@/features/canvas/ui/CanvasAllFeaturesPanel";
import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";

function makeNode(type: string, data: Record<string, unknown>): CanvasNode {
  return {
    id: `${type}-1`,
    type,
    position: { x: 0, y: 0 },
    data,
  } as unknown as CanvasNode;
}

function renderPanel(node: CanvasNode) {
  const handlers = {
    onClose: vi.fn(),
    onOpenUpscale: vi.fn(),
    onOpenMultiAngleEditor: vi.fn(),
    onOpenLightEditor: vi.fn(),
    onOpenScene360: vi.fn(),
    onOpenOutpaint: vi.fn(),
    onOpenRedraw: vi.fn(),
    onOpenErase: vi.fn(),
    onOpenRotate: vi.fn(),
    onOpenExtractFrames: vi.fn(),
  };
  render(<CanvasAllFeaturesPanel node={node} {...handlers} />);
  return handlers;
}

describe("CanvasAllFeaturesPanel", () => {
  it("图片节点按图片编辑分组列出工具，点高清走放大浮层", () => {
    const node = makeNode(CANVAS_NODE_TYPES.imageGen, {
      displayName: "封面",
      imageUrl: "/cover.png",
    });
    const handlers = renderPanel(node);

    expect(screen.getByText("图片编辑")).toBeTruthy();
    expect(screen.queryByText("视频编辑")).toBeNull();
    for (const label of ["高清", "情绪调节", "多角度", "打光", "扩图", "裁剪", "标注"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }

    fireEvent.click(screen.getByText("高清"));

    expect(handlers.onClose).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenUpscale).toHaveBeenCalledWith("imageGenNode-1");
  });

  it("视频节点按视频编辑分组列出工具，点逐帧拉片走抽帧", () => {
    const node = makeNode(CANVAS_NODE_TYPES.video, {
      displayName: "镜头一",
      videoUrl: "/shot.mp4",
    });
    const handlers = renderPanel(node);

    expect(screen.getByText("视频编辑")).toBeTruthy();
    expect(screen.queryByText("图片编辑")).toBeNull();

    fireEvent.click(screen.getByText("逐帧拉片"));

    expect(handlers.onClose).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenExtractFrames).toHaveBeenCalledWith("videoNode-1");
  });

  it("没有编辑工具的节点给出说明", () => {
    const node = makeNode("textNode", { displayName: "说明" });
    renderPanel(node);

    expect(
      screen.getByText("这个节点没有可用的编辑工具，先选中图片或视频节点再打开。"),
    ).toBeTruthy();
  });
});
