// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FreezoneGenerationHistoryRecord } from "@/api/ops";

const translations: Record<string, string> = {
  "canvas.history.tabs.image": "图片历史",
  "canvas.history.tabs.video": "视频历史",
  "canvas.history.tabs.audio": "音频历史",
  "canvas.history.tabs.world": "世界历史",
};

const i18nState = vi.hoisted(() => ({ language: "zh" }));
const canvasState = vi.hoisted(() => ({ nodes: [] as unknown[] }));
const historyState = vi.hoisted(() => ({
  records: [] as unknown[],
  isLoading: false,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
    i18n: { language: i18nState.language, resolvedLanguage: i18nState.language },
  }),
}));

vi.mock("@/stores/canvasStore", () => ({
  useCanvasStore: (selector: (state: { nodes: unknown[] }) => unknown) => selector(canvasState),
}));

vi.mock("@/features/canvas/hooks/useCanvasGenerationHistory", () => ({
  useCanvasGenerationHistory: () => historyState,
}));
vi.mock("@/features/canvas/ui/ImageViewerModal", () => ({ ImageViewerModal: () => null }));
vi.mock("@/features/canvas/ui/VideoViewerModal", () => ({ VideoViewerModal: () => null }));
vi.mock("@/features/viewer-kit/three-d/ThreeDDirectorDialog", () => ({
  ThreeDDirectorDialog: () => null,
}));
vi.mock("@/features/viewer-kit/three-d/directorManifest", () => ({
  buildStandaloneWorldManifest: () => null,
}));

import { CanvasHistoryAssetsModal } from "@/features/canvas/ui/CanvasHistoryAssetsModal";

function record(
  partial: Partial<FreezoneGenerationHistoryRecord>,
): FreezoneGenerationHistoryRecord {
  return {
    id: "rec-1",
    status: "completed",
    recorded_at: "2026-06-15T00:00:00Z",
    media_type: "image",
    result: {},
    ...partial,
  } as FreezoneGenerationHistoryRecord;
}

function renderModal() {
  render(
    <CanvasHistoryAssetsModal
      onClose={vi.fn()}
      onUseAsset={vi.fn()}
      onDeleteNode={vi.fn()}
    />,
  );
}

afterEach(() => {
  i18nState.language = "zh";
  canvasState.nodes = [];
  historyState.records = [];
});

describe("历史资产卡片 · 产物事实条", () => {
  it("记录里带了体积/尺寸时,卡片显示「尺寸 · 体积」而不是空白", () => {
    historyState.records = [
      record({
        result: {
          output_url: "/static/p/a.png",
          byteSize: 2048,
          mimeType: "image/png",
          width: 1280,
          height: 720,
        },
      }),
    ];

    renderModal();

    const strips = screen.getAllByTestId("canvas-resource-meta");
    expect(strips).toHaveLength(1);
    expect(strips[0].textContent).toBe("1280×720 · 2 KB");
    // 容器类型不做成可见文字,挂在 title 上悬停可见。
    expect(strips[0].getAttribute("title")).toBe("image/png");
  });

  it("旧记录没有这些键时,不显示事实条(而不是显示 0)", () => {
    historyState.records = [
      record({ result: { output_url: "/static/p/b.png" } }),
    ];

    renderModal();

    expect(screen.queryByTestId("canvas-resource-meta")).toBeNull();
  });

  it("result 里的无关键(提示词/种子)不会被当成事实", () => {
    historyState.records = [
      record({
        result: { output_url: "/static/p/c.png", prompt: "2 KB 的猫", seed: 2048 },
      }),
    ];

    renderModal();

    // 提示词本身当然照常显示,但它不该让事实条出现。
    expect(screen.queryByTestId("canvas-resource-meta")).toBeNull();
  });

  it("视频的时长走事实条,单位是分:秒", () => {
    historyState.records = [
      record({
        media_type: "video",
        result: { output_url: "/static/p/d.mp4", byteSize: 4604642, durationSec: 10.125 },
      }),
    ];

    renderModal();
    // 默认停在图片页签,视频记录在视频页签里。
    fireEvent.click(screen.getByRole("button", { name: /视频历史/ }));

    const strips = screen.getAllByTestId("canvas-resource-meta");
    expect(strips).toHaveLength(1);
    expect(strips[0].textContent).toBe("4.4 MB · 0:10");
  });

  it("时长对账不符时,卡片红标「请求 vs 实得」", () => {
    historyState.records = [
      record({
        media_type: "video",
        result: {
          output_url: "/static/p/e.mp4",
          durationSec: 15.05,
          durationCheck: {
            requestedSeconds: 30,
            actualSeconds: 15.05,
            match: false,
          },
        },
      }),
    ];

    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /视频历史/ }));

    const check = screen.getByTestId("canvas-duration-check");
    expect(check.textContent).toContain("时长对账：请求 30s · 实得 15.05s");
  });

  it("对账一致或旧记录没有对账键时,不显示时长对账标", () => {
    historyState.records = [
      record({
        media_type: "video",
        result: {
          output_url: "/static/p/f.mp4",
          durationSec: 30,
          durationCheck: { requestedSeconds: 30, actualSeconds: 30, match: true },
        },
      }),
      record({
        id: "rec-2",
        media_type: "video",
        result: { output_url: "/static/p/g.mp4", durationSec: 4 },
      }),
    ];

    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /视频历史/ }));

    expect(screen.queryByTestId("canvas-duration-check")).toBeNull();
  });
});
