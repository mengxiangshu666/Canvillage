import { createElement, memo } from "react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { VideoNodeData } from "@/features/canvas/domain/canvasNodes";
import {
  areCanvasNodePropsEqual,
  areVideoNodePropsEqual,
  type VideoNodeRenderProps,
  videoNodePreload,
} from "./videoNodeRenderProps";

const data = { prompt: "same reference" } as unknown as VideoNodeData;

describe("areVideoNodePropsEqual", () => {
  it("shares the same drag-only comparison for other canvas node types", () => {
    const data = { prompt: "same reference" };
    expect(
      areCanvasNodePropsEqual(
        { id: "image-1", data, width: 580, height: 380 },
        { id: "image-1", data, width: 580, height: 380, positionAbsoluteX: 20 },
      ),
    ).toBe(true);
    expect(
      areCanvasNodePropsEqual(
        { id: "image-1", data, width: 580, height: 380 },
        { id: "image-1", data: { ...data }, width: 580, height: 380 },
      ),
    ).toBe(false);
  });

  it("only preloads video metadata for the selected node", () => {
    expect(videoNodePreload(false)).toBe("none");
    expect(videoNodePreload(undefined)).toBe("none");
    expect(videoNodePreload(true)).toBe("metadata");
  });

  it("ignores React Flow position and drag-only changes", () => {
    const previous = {
      id: "video-1",
      data,
      selected: false,
      width: 580,
      height: 380,
      positionAbsoluteX: 10,
      positionAbsoluteY: 20,
      dragging: false,
    };
    const next = {
      ...previous,
      positionAbsoluteX: 210,
      positionAbsoluteY: 240,
      dragging: true,
    };

    expect(areVideoNodePropsEqual(previous, next)).toBe(true);
  });

  it("rerenders when content, selection, or dimensions change", () => {
    const base = { id: "video-1", data, selected: false, width: 580, height: 380 };
    expect(areVideoNodePropsEqual(base, { ...base, data: { ...data } as never })).toBe(false);
    expect(areVideoNodePropsEqual(base, { ...base, selected: true })).toBe(false);
    expect(areVideoNodePropsEqual(base, { ...base, width: 600 })).toBe(false);
  });

  it("keeps the rendered component stable across repeated drag frames", () => {
    let renderCount = 0;
    const Probe = memo((props: VideoNodeRenderProps) => {
      renderCount += 1;
      return createElement("span", null, props.id);
    }, areVideoNodePropsEqual);
    const base = { id: "video-1", data, selected: false, width: 580, height: 380 };
    const view = render(createElement(Probe, base));
    expect(renderCount).toBe(1);

    for (let frame = 1; frame <= 12; frame += 1) {
      view.rerender(createElement(Probe, {
        ...base,
        positionAbsoluteX: frame * 10,
        positionAbsoluteY: frame * 4,
        dragging: true,
      } as VideoNodeRenderProps));
    }
    expect(renderCount).toBe(1);

    view.rerender(createElement(Probe, { ...base, data: { ...data } as never }));
    expect(renderCount).toBe(2);
  });
});
