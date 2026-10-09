import { describe, expect, it, vi } from "vitest";

const { openGlobalErrorDialog } = vi.hoisted(() => ({
  openGlobalErrorDialog: vi.fn(),
}));
vi.mock("@/features/app/errorDialogEvents", () => ({ openGlobalErrorDialog }));

import { showErrorDialog } from "@/features/canvas/application/errorDialog";

describe("showErrorDialog", () => {
  it("does not show empty errors", async () => {
    await showErrorDialog("  ", "错误");
    expect(openGlobalErrorDialog).not.toHaveBeenCalled();
  });

  it("still shows real failures", async () => {
    await showErrorDialog("上游服务失败", "错误");

    expect(openGlobalErrorDialog).toHaveBeenCalledWith({
      title: "错误",
      message: "上游服务失败",
      details: undefined,
      copyText: undefined,
    });
  });
});
