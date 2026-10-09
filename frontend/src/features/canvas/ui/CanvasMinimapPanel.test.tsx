// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CanvasMinimapPanel } from "./CanvasMinimapPanel";

vi.mock("@xyflow/react", () => ({
  MiniMap: ({ zoomable, pannable, zoomStep, ariaLabel, className }: {
    zoomable?: boolean;
    pannable?: boolean;
    zoomStep?: number;
    ariaLabel?: string;
    className?: string;
  }) => (
    <div
      data-testid="xyflow-minimap"
      data-zoomable={String(Boolean(zoomable))}
      data-pannable={String(Boolean(pannable))}
      data-zoom-step={String(zoomStep)}
      aria-label={ariaLabel}
      className={className}
    />
  ),
}));

vi.mock("./CanvasMinimapBookmarksOverlay", () => ({
  CanvasMinimapBookmarksOverlay: () => <div data-testid="minimap-bookmarks" />,
}));

describe("CanvasMinimapPanel", () => {
  it("keeps the closed task view out of the canvas", () => {
    const { container } = render(
      <CanvasMinimapPanel
        open={false}
        expanded={false}
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("uses one shell with native pan and wheel zoom", () => {
    const onClose = vi.fn();
    const onExpandedChange = vi.fn();
    render(
      <CanvasMinimapPanel
        open
        expanded={false}
        onClose={onClose}
        onExpandedChange={onExpandedChange}
      />,
    );

    const shell = screen.getByLabelText("任务视图");
    expect(shell).toHaveAttribute("data-expanded", "false");
    expect(screen.getByTestId("xyflow-minimap")).toHaveAttribute("data-pannable", "true");
    expect(screen.getByTestId("xyflow-minimap")).toHaveAttribute("data-zoomable", "true");
    expect(screen.getByTestId("xyflow-minimap")).toHaveAttribute("data-zoom-step", "0.65");

    fireEvent.click(screen.getByRole("button", { name: "放大任务视图" }));
    expect(onExpandedChange).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "关闭任务视图" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps viewport bookmarks inside the expanded shell", () => {
    const { rerender } = render(
      <CanvasMinimapPanel
        open
        expanded
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("任务视图")).toHaveAttribute("data-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "展开视口书签" }));
    expect(screen.getByTestId("minimap-bookmarks")).toBeInTheDocument();

    rerender(
      <CanvasMinimapPanel
        open
        expanded={false}
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("minimap-bookmarks")).not.toBeInTheDocument();

    rerender(
      <CanvasMinimapPanel
        open
        expanded
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("minimap-bookmarks")).not.toBeInTheDocument();
  });

  it("suspends the expensive minimap while nodes are being dragged", () => {
    const { rerender } = render(
      <CanvasMinimapPanel
        open
        expanded={false}
        suspended
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("任务视图")).toHaveAttribute("data-suspended", "true");
    expect(screen.queryByTestId("xyflow-minimap")).not.toBeInTheDocument();
    expect(screen.getByTestId("canvas-minimap-suspended")).toBeInTheDocument();

    rerender(
      <CanvasMinimapPanel
        open
        expanded={false}
        suspended={false}
        onClose={vi.fn()}
        onExpandedChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("xyflow-minimap")).toBeInTheDocument();
  });
});
