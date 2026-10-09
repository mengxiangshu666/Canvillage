import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-media-query", () => ({
  useMediaQuery: () => true,
}));

vi.mock("@/features/superchat/superchat-panel", () => ({
  SuperChatPanel: ({ canvasId, canvasRevision }: { canvasId?: string; canvasRevision?: number }) => (
    <div
      data-testid="persistent-superchat"
      data-agent-drag-handle="true"
      data-canvas={canvasId}
      data-revision={canvasRevision}
    />
  ),
}));

import {
  FreezoneChatDock,
  resolveVillageAgentCompanionAnchor,
} from "./FreezoneShell";

describe("FreezoneChatDock panel lifetime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 900 });
  });
  afterEach(() => {
    document.documentElement.style.removeProperty("--village-agent-companion-left");
    document.documentElement.style.removeProperty("--village-agent-companion-top");
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("keeps the stateful 小树 panel mounted while the desktop drawer is closed", () => {
    const onOpenChange = vi.fn();
    const view = render(
      <FreezoneChatDock
        open
        onOpenChange={onOpenChange}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="Toggle"
      />,
    );
    const panel = view.getByTestId("persistent-superchat");

    view.rerender(
      <FreezoneChatDock
        open={false}
        onOpenChange={onOpenChange}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="Toggle"
      />,
    );
    act(() => vi.advanceTimersByTime(500));

    expect(view.getByTestId("persistent-superchat")).toBe(panel);
    expect(panel).toHaveAttribute("data-canvas", "canvas-a");
    expect(panel).toHaveAttribute("data-revision", "12");
  });

  it("leaves the closed Village Agent entry to 搭子 instead of rendering a duplicate button", () => {
    const view = render(
      <FreezoneChatDock
        open={false}
        onOpenChange={vi.fn()}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="打开小树"
      />,
    );

    expect(view.queryByRole("button", { name: "打开小树" })).not.toBeInTheDocument();
  });

  it("anchors 搭子 to the real Agent edge and clears the temporary anchor on close", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 900,
      top: 20,
      width: 500,
      height: 820,
      right: 1400,
      bottom: 840,
      x: 900,
      y: 20,
      toJSON: () => ({}),
    });
    const view = render(
      <FreezoneChatDock
        open
        onOpenChange={vi.fn()}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="打开小树"
      />,
    );

    expect(document.documentElement.style.getPropertyValue("--village-agent-companion-left")).toBe("858px");
    expect(document.documentElement.style.getPropertyValue("--village-agent-companion-top")).toBe("72px");

    view.rerender(
      <FreezoneChatDock
        open={false}
        onOpenChange={vi.fn()}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="打开小树"
      />,
    );
    expect(document.documentElement.style.getPropertyValue("--village-agent-companion-left")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--village-agent-companion-top")).toBe("");
  });

  it("recomputes the edge anchor when a floating Agent moves", () => {
    expect(resolveVillageAgentCompanionAnchor(
      { left: 420, top: 120 },
      { width: 1440, height: 900 },
    )).toEqual({ left: 378, top: 172 });
    expect(resolveVillageAgentCompanionAnchor(
      { left: 680, top: 240 },
      { width: 1440, height: 900 },
    )).toEqual({ left: 638, top: 292 });
  });

  it("moves the floating Agent on the compositor and commits once on release", () => {
    window.localStorage.setItem('village.canvas.agent.presentation', 'floating');
    const view = render(
      <FreezoneChatDock
        open
        onOpenChange={vi.fn()}
        canvasId="canvas-a"
        canvasRevision={12}
        projectStyleId="style-a"
        title="小树"
        description="小树抽屉"
        toggleLabel="打开小树"
      />,
    );
    const frame = view.container.querySelector<HTMLElement>(
      '[data-agent-presentation="floating"]',
    )!;

    act(() => {
      view.getByTestId('persistent-superchat').dispatchEvent(
        new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: 120, clientY: 120 }),
      );
      window.dispatchEvent(
        new PointerEvent('pointermove', { clientX: 220, clientY: 180 }),
      );
      vi.advanceTimersByTime(20);
    });

    expect(frame.style.transform).toContain('translate3d(100px, 0px, 0)');
    expect(document.documentElement.dataset.villageInteracting).toBe('true');

    act(() => {
      window.dispatchEvent(new PointerEvent('pointerup'));
    });
    expect(frame.style.transform).toBe('');
    expect(document.documentElement.dataset.villageInteracting).toBeUndefined();
  });
});
