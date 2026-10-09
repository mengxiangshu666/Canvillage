import { describe, expect, it, vi } from "vitest";

import {
  GENERATION_BATCH_MAX,
  GENERATION_CONCURRENCY_DEFAULT,
  clampGenerationBatchCount,
  generationQueueUnitHint,
  globalGenerationQueueSnapshot,
  isGenerationNodeBusy,
  runGenerationQueue,
  withGlobalGenerationSlot,
} from "./generationConcurrency";

describe("generationConcurrency", () => {
  it("raises the accepted batch ceiling to twelve", () => {
    expect(GENERATION_BATCH_MAX).toBe(12);
    expect(clampGenerationBatchCount(0)).toBe(1);
    expect(clampGenerationBatchCount(8)).toBe(8);
    expect(clampGenerationBatchCount(99)).toBe(12);
  });

  it("默认并发上限是 500，超过后才提示排队", () => {
    expect(GENERATION_CONCURRENCY_DEFAULT).toBe(500);
    expect(generationQueueUnitHint(1, "张")).toBe("");
    expect(generationQueueUnitHint(4, "张")).toBe("");
    expect(generationQueueUnitHint(12, "条")).toBe("");
    expect(generationQueueUnitHint(501, "条")).toBe("（每批500条）");
  });

  it("互不相干的节点全部同时提交，一个都不排队", async () => {
    let active = 0;
    let peak = 0;
    await Promise.all(Array.from({ length: 9 }, (_, index) => withGlobalGenerationSlot(async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 2));
      active -= 1;
    }, undefined, { nodeId: `node-${index}` })));
    expect(peak).toBe(9);
    expect(globalGenerationQueueSnapshot()).toEqual({
      active: 0,
      queued: 0,
      blocked: 0,
      nodes: [],
    });
  });

  it("有顺序的按顺序来：上游占着槽时下游不提交，上游一放槽下游接上", async () => {
    const timeline: string[] = [];
    const submit = (nodeId: string, deps: string[]) => withGlobalGenerationSlot(async () => {
      timeline.push(`start:${nodeId}`);
      await new Promise((resolve) => setTimeout(resolve, 10));
      timeline.push(`end:${nodeId}`);
    }, undefined, {
      nodeId,
      gate: () => (deps.some((dep) => isGenerationNodeBusy(dep)) ? "running" : "go"),
    });

    await Promise.all([submit("A", []), submit("B", ["A"]), submit("C", [])]);
    // B 用的是 A 出的图，A 结束前它一次都不能提交；C 与 A 无关，可以跟 A 并发。
    expect(timeline.indexOf("start:B")).toBeGreaterThan(timeline.indexOf("end:A"));
    expect(timeline.slice(0, 2).sort()).toEqual(["start:A", "start:C"]);
    expect(globalGenerationQueueSnapshot().active).toBe(0);
  });

  it("被挡住的等待者不占坑：排在他后面的独立节点照常派位", async () => {
    const started: string[] = [];
    const controller = new AbortController();
    const submit = (nodeId: string) => withGlobalGenerationSlot(async () => {
      started.push(nodeId);
    }, controller.signal, {
      nodeId,
      gate: () => (nodeId === "waiting-upstream" ? "running" : "go"),
    });

    const blockedRun = submit("waiting-upstream").catch((error: unknown) => error);
    await Promise.all([submit("free-1"), submit("free-2")]);
    expect(started.sort()).toEqual(["free-1", "free-2"]);
    expect(globalGenerationQueueSnapshot()).toEqual({
      active: 0,
      queued: 1,
      blocked: 1,
      nodes: [],
    });
    // 收尾：取消掉那个一直等上游的提交，队列要干净地放掉它。
    controller.abort();
    await blockedRun;
    expect(globalGenerationQueueSnapshot()).toEqual({
      active: 0,
      queued: 0,
      blocked: 0,
      nodes: [],
    });
  });

  it("上游放行后，被挡住的提交靠定时重派位自己醒来", async () => {
    let upstreamRunning = true;
    const started: string[] = [];
    const pending = withGlobalGenerationSlot(async () => {
      started.push("B");
    }, undefined, {
      nodeId: "B",
      gate: () => (upstreamRunning ? "running" : "go"),
    });

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(started).toEqual([]);
    upstreamRunning = false;
    await vi.waitFor(() => expect(started).toEqual(["B"]), { timeout: 3_000, interval: 100 });
    await pending;
  });

  it("单节点批量默认吃全局并发度", async () => {
    let active = 0;
    let peak = 0;
    const results = await runGenerationQueue([0, 1, 2, 3, 4, 5], async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 2));
      active -= 1;
    });
    expect(peak).toBe(6);
    expect(results.every((result) => result.status === "fulfilled")).toBe(true);
  });

  it("显式指定并发度时仍然照办", async () => {
    let active = 0;
    let peak = 0;
    const seen: number[] = [];
    const progress: number[] = [];
    const results = await runGenerationQueue(
      Array.from({ length: 12 }, (_, index) => index),
      async (item) => {
        active += 1;
        peak = Math.max(peak, active);
        seen.push(item);
        await new Promise((resolve) => setTimeout(resolve, 2));
        active -= 1;
      },
      3,
      (completed) => progress.push(completed),
    );
    expect(peak).toBe(3);
    expect(seen.sort((a, b) => a - b)).toEqual(Array.from({ length: 12 }, (_, index) => index));
    expect(results.every((result) => result.status === "fulfilled")).toBe(true);
    expect(progress[progress.length - 1]).toBe(12);
  });

  it("continues queued jobs after one failure without retrying it", async () => {
    const attempts = new Map<number, number>();
    const results = await runGenerationQueue([0, 1, 2, 3, 4, 5], async (item) => {
      attempts.set(item, (attempts.get(item) ?? 0) + 1);
      if (item === 2) throw new Error("provider rejected");
    }, 3);
    expect(results[2].status).toBe("rejected");
    expect(attempts.size).toBe(6);
    expect([...attempts.values()]).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it("does not start remaining queued workers after cancellation", async () => {
    const controller = new AbortController();
    const started: number[] = [];
    let releaseFirstWave: (() => void) | undefined;
    const firstWave = new Promise<void>((resolve) => {
      releaseFirstWave = resolve;
    });

    const run = runGenerationQueue(
      [0, 1, 2, 3, 4, 5],
      async (item) => {
        started.push(item);
        await firstWave;
      },
      2,
      undefined,
      controller.signal,
    );

    await vi.waitFor(() => expect(started).toHaveLength(2));
    controller.abort();
    releaseFirstWave?.();
    const results = await run;

    expect(started).toEqual([0, 1]);
    expect(results).toHaveLength(6);
    expect(results.slice(2).every((result) => result.status === "rejected")).toBe(true);
  });
});
