// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";
import {
  ancestorNodeIds,
  upstreamGenerationGate,
  upstreamGenerationPending,
} from "./generationDependencies";
import {
  clearGenerationIntent,
  markGenerationIntent,
  withGlobalGenerationSlot,
} from "./generationConcurrency";

/** 造一条 A → B 的单向连线，节点数据按需覆盖。 */
function seedGraph(
  nodes: Array<{ id: string; data?: Record<string, unknown> }>,
  edges: Array<[string, string]>,
) {
  useCanvasStore.getState().setCanvasData(
    nodes.map((node) => ({
      id: node.id,
      type: CANVAS_NODE_TYPES.imageGen,
      position: { x: 0, y: 0 },
      data: node.data ?? {},
    })) as never,
    edges.map(([source, target]) => ({ id: `${source}-${target}`, source, target })) as never,
  );
}

describe("generationDependencies", () => {
  it("没有上游就是 go（画布上第一个节点随便并发）", () => {
    seedGraph([{ id: "solo" }], []);
    expect(upstreamGenerationGate("solo")).toBe("go");
    expect(upstreamGenerationPending("solo")).toBe(false);
  });

  it("上游挂着自动出图标记 → waiting（它的提交马上要来，下游等一小会儿）", () => {
    seedGraph(
      [
        { id: "shot-1", data: { canvas_auto_generate_once: true } },
        { id: "shot-2" },
      ],
      [["shot-1", "shot-2"]],
    );
    expect(upstreamGenerationGate("shot-2")).toBe("waiting");
  });

  it("上游已经占着信号量的槽 → running（等它出完图再提交）", async () => {
    seedGraph([{ id: "a" }, { id: "b" }], [["a", "b"]]);
    expect(upstreamGenerationGate("b")).toBe("go");

    let release: (() => void) | undefined;
    const inFlight = withGlobalGenerationSlot(
      () => new Promise<void>((resolve) => {
        release = resolve;
      }),
      undefined,
      { nodeId: "a" },
    );
    // acquire 是同步占位的，所以 a 一提交，b 立刻就该被挡住。
    expect(upstreamGenerationGate("b")).toBe("running");
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    release?.();
    await inFlight;
    expect(upstreamGenerationGate("b")).toBe("go");
  });

  it("上游的准备期也算「在出图」：handleSubmit 一登记，下游就等", () => {
    // 真机踩过的坑：节点从「决定出图」到「进信号量」之间有一大段异步准备，
    // 这段窗口里上游既没排队标记也不在信号量，下游会跟着一起提交（A→B 的链并发跑掉）。
    seedGraph([{ id: "prep-a" }, { id: "prep-b" }], [["prep-a", "prep-b"]]);
    expect(upstreamGenerationGate("prep-b")).toBe("go");

    markGenerationIntent("prep-a");
    expect(upstreamGenerationGate("prep-b")).toBe("running");

    clearGenerationIntent("prep-a");
    expect(upstreamGenerationGate("prep-b")).toBe("go");
  });

  it("隔代也算数：C → 中间 → 叶子，C 在出图时叶子也等（传递闭包）", () => {
    seedGraph(
      [
        { id: "chain-root", data: { expression_auto_generate: true } },
        { id: "chain-mid" },
        { id: "chain-leaf" },
      ],
      [["chain-root", "chain-mid"], ["chain-mid", "chain-leaf"]],
    );
    expect(ancestorNodeIds(
      useCanvasStore.getState().nodes,
      useCanvasStore.getState().edges,
      "chain-leaf",
    )).toEqual(["chain-mid", "chain-root"]);
    expect(upstreamGenerationGate("chain-leaf")).toBe("waiting");
  });

  it("只看上游：下游节点在出图不影响我", () => {
    seedGraph(
      [
        { id: "up", data: {} },
        { id: "down", data: { canvas_auto_generate_once: true } },
      ],
      [["up", "down"]],
    );
    expect(upstreamGenerationGate("up")).toBe("go");
  });

  it("画布上持久化下来的 isGenerating 不算数（页面被杀过一次就会留这种痕迹）", () => {
    seedGraph(
      [
        { id: "stale", data: { isGenerating: true, generationStartedAt: 1 } },
        { id: "down" },
      ],
      [["stale", "down"]],
    );
    // 信号量里没有它、也没有自动出图标记 → 下游照常并发，不会被一个死任务挡住十几分钟。
    expect(upstreamGenerationGate("down")).toBe("go");
  });

  it("连线成环也不会转不出来", () => {
    seedGraph([{ id: "a" }, { id: "b" }], [["a", "b"], ["b", "a"]]);
    expect(ancestorNodeIds(useCanvasStore.getState().nodes, useCanvasStore.getState().edges, "a"))
      .toEqual(["b"]);
    expect(upstreamGenerationGate("a")).toBe("go");
  });
});
