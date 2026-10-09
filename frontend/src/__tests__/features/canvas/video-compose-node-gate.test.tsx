// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { VideoComposeNode } from "@/features/canvas/nodes/VideoComposeNode";
import { useCanvasStore } from "@/stores/canvasStore";

/**
 * 合成节点的准入门槛必须和后端契约一致：`freezone.py` 的合成闸门只要求
 * `resolved_tracks` 非空 + 有一条视频轨，也就是**一条视频就够**。
 *
 * 这里曾经写死 2，比后端更严，直接把自带起步路线（script-voice-video /
 * music-driven-video）的终局合成节点锁死 —— 那两条路线按作者原样连好之后，
 * 合成节点本来就只接 1 路视频，按钮永远打不开。
 */

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: ({ id, type }: { id?: string; type?: string }) => (
      <div data-testid={`handle-${type ?? "unknown"}-${id ?? "default"}`} />
    ),
    useUpdateNodeInternals: () => vi.fn(),
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === "videoCompose.node.counts") {
        return `视频 ${options?.video} · 音频 ${options?.audio}`;
      }
      if (key === "videoCompose.node.hint") {
        return "连接至少 1 个已生成视频的节点后可打开";
      }
      return key;
    },
  }),
}));

vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: ({ titleText }: { titleText: string }) => <div>{titleText}</div>,
}));

vi.mock("@/features/canvas/compose/VideoComposeModal", () => ({
  VideoComposeModal: () => <div data-testid="compose-modal" />,
}));

const COMPOSE_ID = "compose-node";

function renderCompose() {
  return render(
    <VideoComposeNode
      id={COMPOSE_ID}
      type={CANVAS_NODE_TYPES.videoCompose}
      data={{ displayName: "视频合成" }}
      selected={false}
      dragging={false}
      draggable
      selectable
      deletable
      zIndex={0}
      isConnectable
      positionAbsoluteX={0}
      positionAbsoluteY={0}
    />,
  );
}

function seed(upstream: Array<Record<string, unknown>>) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: COMPOSE_ID,
        type: CANVAS_NODE_TYPES.videoCompose,
        position: { x: 400, y: 0 },
        data: { displayName: "视频合成" },
      } as never,
      ...(upstream as never[]),
    ],
    upstream.map((node, index) => ({
      id: `edge-${index}`,
      source: node.id as string,
      target: COMPOSE_ID,
      sourceHandle: "source",
      targetHandle: "target",
    })) as never[],
  );
}

function openButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "videoCompose.node.open" }) as HTMLButtonElement;
}

beforeEach(() => {
  useCanvasStore.getState().setCanvasData([], []);
});

describe("video compose node entry gate", () => {
  it("opens with a single generated video, matching the backend floor", () => {
    seed([
      {
        id: "video-a",
        type: CANVAS_NODE_TYPES.video,
        position: { x: 0, y: 0 },
        data: { videoUrl: "/static/projects/p/shot.mp4" },
      },
    ]);

    renderCompose();

    expect(openButton().disabled).toBe(false);
    expect(screen.getByText("视频 1 · 音频 0")).toBeTruthy();
    // 门槛已满足，不应再显示「连接至少…」的拦截说明。
    expect(screen.queryByText("连接至少 1 个已生成视频的节点后可打开")).toBeNull();
  });

  it("counts only upstream nodes that actually carry a video url", () => {
    seed([
      {
        id: "video-empty",
        type: CANVAS_NODE_TYPES.video,
        position: { x: 0, y: 0 },
        data: { videoUrl: null },
      },
      {
        id: "audio-a",
        type: CANVAS_NODE_TYPES.audio,
        position: { x: 0, y: 120 },
        data: { audioUrl: "/static/projects/p/bgm.mp3" },
      },
    ]);

    renderCompose();

    expect(openButton().disabled).toBe(true);
    expect(screen.getByText("视频 0 · 音频 1")).toBeTruthy();
    expect(screen.getByText("连接至少 1 个已生成视频的节点后可打开")).toBeTruthy();
  });
});
