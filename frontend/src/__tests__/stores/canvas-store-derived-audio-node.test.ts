// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import {
  CANVAS_NODE_TYPES,
  DEFAULT_NODE_WIDTH,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

const SOURCE_ID = "audio-source";
const SOURCE_URL = "/project-assets/demo/audio/voice.mp3";
const TRIMMED_URL = "/project-assets/demo/audio/voice_trim_ab12cd34.mp3";

function seedSourceNode(): CanvasNode {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: SOURCE_ID,
        type: CANVAS_NODE_TYPES.audio,
        position: { x: 120, y: 80 },
        data: {
          displayName: "视频_背景音",
          audioUrl: SOURCE_URL,
          durationMs: 15_000,
          sourceFileName: "voice.mp3",
        },
      },
    ],
    [],
  );
  return useCanvasStore.getState().nodes[0]!;
}

describe("canvasStore.addDerivedAudioNode — 裁剪结果落新节点", () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it("creates a second audio node and leaves the source node untouched", () => {
    const sourceBefore = seedSourceNode();
    const historyBefore = useCanvasStore.getState().history.past.length;

    const newNodeId = useCanvasStore.getState().addDerivedAudioNode(SOURCE_ID, {
      displayName: "视频_背景音（裁剪）",
      audioUrl: TRIMMED_URL,
      durationMs: 2_000,
      sourceFileName: "voice_trim_ab12cd34.mp3",
    });

    const state = useCanvasStore.getState();
    expect(newNodeId).not.toBeNull();
    expect(state.nodes).toHaveLength(2);

    // 用户反馈的核心：裁剪不能作用在原来的节点上。
    const sourceAfter = state.nodes.find((node) => node.id === SOURCE_ID);
    expect(sourceAfter).toEqual(sourceBefore);

    const derived = state.nodes.find((node) => node.id === newNodeId)!;
    expect(derived.type).toBe(CANVAS_NODE_TYPES.audio);
    expect(derived.data).toMatchObject({
      displayName: "视频_背景音（裁剪）",
      audioUrl: TRIMMED_URL,
      durationMs: 2_000,
      sourceFileName: "voice_trim_ab12cd34.mp3",
    });
    // 落在源节点右侧，不与源节点重叠；新节点直接选中。
    expect(derived.position.y).toBe(sourceBefore.position.y);
    expect(derived.position.x).toBeGreaterThanOrEqual(
      sourceBefore.position.x + DEFAULT_NODE_WIDTH,
    );
    expect(state.selectedNodeId).toBe(newNodeId);
    // 一次可撤销的编辑。
    expect(state.history.past.length).toBe(historyBefore + 1);
  });

  it("nulls missing optional audio metadata instead of leaving undefined", () => {
    seedSourceNode();

    const newNodeId = useCanvasStore.getState().addDerivedAudioNode(SOURCE_ID, {
      audioUrl: TRIMMED_URL,
    })!;

    const derived = useCanvasStore
      .getState()
      .nodes.find((node) => node.id === newNodeId)!;
    expect(derived.data.durationMs).toBeNull();
    expect(derived.data.sourceFileName).toBeNull();
  });

  it("does nothing when the source node is gone", () => {
    const newNodeId = useCanvasStore.getState().addDerivedAudioNode("missing", {
      audioUrl: TRIMMED_URL,
    });

    expect(newNodeId).toBeNull();
    expect(useCanvasStore.getState().nodes).toHaveLength(0);
  });
});
