import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-media-query", () => ({
  useMediaQuery: () => false,
}));

vi.mock("@/lib/product-mode", () => ({
  canvasOnlyProduct: true,
}));

vi.mock("@/features/superchat/superchat-panel", () => ({
  SuperChatPanel: () => <div data-testid="mobile-superchat" />,
}));

import { FreezoneChatDock } from "./FreezoneShell";

describe("FreezoneChatDock mobile Agent shell", () => {
  it("marks the mobile sheet for the v4 full-screen contract", () => {
    render(
      <FreezoneChatDock
        open
        onOpenChange={vi.fn()}
        canvasId="canvas-mobile"
        canvasRevision={3}
        projectStyleId="style-mobile"
        title="小树"
        description="小树移动端抽屉"
        toggleLabel="打开小树"
      />,
    );

    const drawer = document.querySelector(
      '[data-agent-drawer="village-agent-v4-libtv"]',
    );
    expect(drawer).toHaveAttribute("data-agent-presentation", "mobile");
    expect(drawer?.querySelector('[data-testid="mobile-superchat"]')).not.toBeNull();
  });
});
