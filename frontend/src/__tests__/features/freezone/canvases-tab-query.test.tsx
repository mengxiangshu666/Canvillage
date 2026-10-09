// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFreezoneCanvases = vi.fn();
const deleteFreezoneCanvas = vi.fn();
const createBlankFreezoneCanvas = vi.fn();
const writeUrl = vi.fn();

vi.mock("@/api/canvas", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/canvas")>()),
  listFreezoneCanvases: (...args: unknown[]) => listFreezoneCanvases(...args),
  deleteFreezoneCanvas: (...args: unknown[]) => deleteFreezoneCanvas(...args),
  createBlankFreezoneCanvas: (...args: unknown[]) => createBlankFreezoneCanvas(...args),
}));

vi.mock("@/lib/url-params", () => ({
  writeUrl: (...args: unknown[]) => writeUrl(...args),
}));

import { CanvasesTab } from "@/features/freezone/CanvasesTab";
import { CANVAS_PATCH_EVENT } from "@/features/superchat/canvas-patch-events";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("CanvasesTab queries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createBlankFreezoneCanvas.mockResolvedValue({ ok: true });
  });

  it("shares one canvas list request across matching tabs", async () => {
    listFreezoneCanvases.mockResolvedValue([]);

    render(
      <>
        <CanvasesTab project="demo" currentCanvasId="user_admin_demo" hasPresetLabel={false} />
        <CanvasesTab project="demo" currentCanvasId="user_admin_demo" hasPresetLabel={false} />
      </>,
      { wrapper },
    );

    await screen.findAllByText("freezone.canvases.myCanvasSection");
    expect(listFreezoneCanvases).toHaveBeenCalledTimes(1);
  });

  it("refreshes canvas summaries after an Agent patch for the project", async () => {
    listFreezoneCanvases.mockResolvedValue([]);

    render(
      <CanvasesTab project="demo" currentCanvasId="default" hasPresetLabel={false} />,
      { wrapper },
    );
    await screen.findByText("freezone.canvases.myCanvasSection");
    expect(listFreezoneCanvases).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new CustomEvent(CANVAS_PATCH_EVENT, {
        detail: {
          projectId: "demo",
          canvasId: "default",
          revision: 2,
        },
      }));
    });

    await vi.waitFor(() => expect(listFreezoneCanvases).toHaveBeenCalledTimes(2));
  });

  it("switches canvas only through a real non-current canvas action", async () => {
    listFreezoneCanvases.mockResolvedValue([
      {
        id: "default",
        canvas_scope: "default",
        modified_at: "2026-08-11T10:00:00Z",
        size: 128,
        node_count: 2,
        edge_count: 1,
        preview_nodes: [
          { id: "a", type: "text", x: 0, y: 0, width: 240, height: 120 },
          { id: "b", type: "video", x: 360, y: 120, width: 320, height: 180 },
        ],
      },
      {
        id: "scratch",
        canvas_scope: "blank",
        modified_at: "2026-08-11T11:00:00Z",
        size: 64,
        node_count: 0,
        edge_count: 0,
        preview_nodes: [],
      },
    ]);

    render(<CanvasesTab project="demo" currentCanvasId="default" hasPresetLabel={false} />, { wrapper });

    fireEvent.click(await screen.findByTitle("freezone.canvases.expandOtherCanvases"));
    const openCanvas = await screen.findByRole("button", { name: /打开画布/ });
    fireEvent.click(openCanvas);

    expect(writeUrl).toHaveBeenCalledWith({ canvas: "scratch" });
    expect(screen.queryByRole("button", { name: /打开画布：.*默认/ })).toBeNull();
    expect(screen.getByText("当前")).toBeInTheDocument();
  });

  it("creates a named canvas through the visible create control", async () => {
    listFreezoneCanvases.mockResolvedValue([
      {
        id: "default",
        canvas_scope: "default",
        modified_at: "2026-08-11T10:00:00Z",
        size: 128,
      },
    ]);

    render(<CanvasesTab project="demo" currentCanvasId="default" hasPresetLabel={false} />, { wrapper });

    const nameInput = await screen.findByRole("textbox");
    fireEvent.change(nameInput, { target: { value: "产品分镜" } });
    fireEvent.click(screen.getByRole("button", { name: "freezone.canvases.create" }));

    await waitFor(() => expect(createBlankFreezoneCanvas).toHaveBeenCalledTimes(1));
    expect(createBlankFreezoneCanvas).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ name: "产品分镜" }),
    );
    expect(writeUrl).toHaveBeenCalledWith({
      canvas: expect.stringMatching(/^canvas_/),
    });
  });
});
