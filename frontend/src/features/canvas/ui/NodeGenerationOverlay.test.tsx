import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NodeGenerationOverlay } from "./NodeGenerationOverlay";

describe("NodeGenerationOverlay progress states", () => {
  it("shows the backend percentage and exposes it to assistive tech", () => {
    render(<NodeGenerationOverlay startedAt={Date.now()} progress={0.42} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("data-village-node-progress", "determinate");
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.queryByText("%")).toBeInTheDocument();
  });

  it("renders a real 0% instead of falling back to an estimate", () => {
    render(<NodeGenerationOverlay startedAt={Date.now() - 300_000} progress={0} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("falls back to elapsed time, never a fabricated percentage, without backend progress", () => {
    // Started 90s ago: the old contract would have shown a capped estimate.
    render(<NodeGenerationOverlay startedAt={Date.now() - 90_000} progress={null} />);

    const bar = screen.getByRole("progressbar");
    // No aria-valuenow at all — an indeterminate progressbar per WAI-ARIA.
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveAttribute("data-village-node-progress", "indeterminate");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    // The reading on screen is elapsed time, not a made-up "96".
    expect(screen.getByText("1m30s")).toBeInTheDocument();
    expect(screen.queryByText("%")).not.toBeInTheDocument();
  });

  it("treats undefined progress the same as null", () => {
    render(<NodeGenerationOverlay startedAt={Date.now() - 5_000} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText("5s")).toBeInTheDocument();
  });
});

describe("NodeGenerationOverlay cancellation", () => {
  it("exposes one clickable stop action without blocking the progress state", () => {
    const onCancel = vi.fn();
    render(
      <NodeGenerationOverlay
        startedAt={Date.now()}
        progress={0.42}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    fireEvent.click(screen.getByRole("button", { name: "终止节点任务" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps the stop action reachable in the indeterminate state", () => {
    const onCancel = vi.fn();
    render(
      <NodeGenerationOverlay
        startedAt={Date.now()}
        progress={null}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "终止节点任务" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("locks the action while cancellation is pending", () => {
    render(
      <NodeGenerationOverlay
        progress={0.5}
        onCancel={() => undefined}
        cancelPending
      />,
    );

    expect(screen.getByRole("button", { name: "正在终止节点任务" })).toBeDisabled();
  });
});
